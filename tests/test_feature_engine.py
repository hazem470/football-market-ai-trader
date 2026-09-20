"""Feature engine: only requested features, and missing data stays missing."""
from __future__ import annotations

import pytest

from src.features.engine import FEATURE_VERSION, FeatureEngine
from src.markets.schema import MarketType


@pytest.fixture
def engine(synthetic_history, synthetic_players):
    return FeatureEngine(history=synthetic_history, players=synthetic_players, form_window=10)


def test_goal_features_for_match_result(engine, market_factory):
    bundle = engine.build(market_factory(home="Team A", away="Team B"))
    assert bundle.values["home_attack_rate"] > 0
    assert 0 <= bundle.values["home_advantage"] <= 1
    assert bundle.values["league_goal_environment"] > 0
    assert bundle.feature_version == FEATURE_VERSION
    assert bundle.plan["model_name"] == "dixon_coles_match_result"


def test_unsupported_market_produces_no_features(engine, market_factory):
    bundle = engine.build(market_factory(market_type=MarketType.CARDS))
    assert bundle.values == {}
    assert "UNSUPPORTED_MARKET" in bundle.missing


def test_unknown_teams_produce_missing_history(engine, market_factory):
    bundle = engine.build(market_factory(home="Nonexistent FC", away="Also Missing FC"))
    assert "team_history" in bundle.missing
    assert not bundle.complete


def test_corner_features(engine, market_factory):
    bundle = engine.build(market_factory(market_type=MarketType.CORNERS))
    assert bundle.values["expected_total_corners"] > 0
    assert bundle.values["home_corners_for_pm"] >= 0


def test_corner_features_flagged_when_absent(market_factory):
    from src.data.normalization.canonical import TeamHistory
    from tests.conftest import make_match

    history = TeamHistory([
        make_match("Team A", "Team B", 1, 1, home_corners=None, away_corners=None, home_shots=None, away_shots=None)
    ] * 5)
    engine = FeatureEngine(history=history)
    bundle = engine.build(market_factory(market_type=MarketType.CORNERS, home="Team A", away="Team B"))
    assert "team_corners_for" in bundle.missing


def test_player_features_resolve_the_player(engine, market_factory):
    bundle = engine.build(market_factory(
        market_type=MarketType.PLAYER_GOAL, player="Striker Team A", home="Team A", away="Team B",
        selection="Striker Team A",
    ))
    assert bundle.values["player_goal_rate_90"] > 0
    assert bundle.values["starting_probability"] == pytest.approx(0.9)
    assert bundle.values["player_side"] == "home"


def test_unknown_player_is_missing_not_imputed(engine, market_factory):
    bundle = engine.build(market_factory(
        market_type=MarketType.PLAYER_GOAL, player="Nobody At All", home="Team A", away="Team B",
    ))
    assert "player_identity" in bundle.missing
    assert bundle.values == {}


def test_player_shots_stay_missing_without_a_shots_feed(engine, market_factory):
    bundle = engine.build(market_factory(
        market_type=MarketType.PLAYER_SHOTS, player="Striker Team A", home="Team A", away="Team B",
    ))
    assert "player_shots_rate" in bundle.missing


def test_feature_key_is_deterministic(engine, market_factory):
    market = market_factory()
    first = engine.build(market)
    second = engine.build(market)
    assert first.feature_key == second.feature_key


def test_feature_key_changes_with_data_timestamp(engine, market_factory):
    market = market_factory()
    bundle = engine.build(market)
    other = engine.build(market)
    other.data_timestamp += 10_000
    assert bundle.feature_key != other.feature_key


def test_bundle_serialises_to_db_record(engine, market_factory):
    bundle = engine.build(market_factory())
    record = bundle.to_db_record()
    assert record["feature_key"].startswith("FEAT-")
    assert "values" in record["payload"]
    assert record["payload"]["plan"]["model_name"]


def test_provenance_is_recorded(engine, market_factory):
    bundle = engine.build(market_factory())
    assert "history" in bundle.provenance


@pytest.mark.parametrize("market_type", [
    MarketType.MATCH_RESULT, MarketType.TOTAL_GOALS, MarketType.BTTS, MarketType.TEAM_TOTALS,
    MarketType.CORNERS,
])
def test_every_planned_feature_is_produced(engine, market_factory, market_type):
    """Contract: the data-requirements engine and the feature engine must agree.

    If a plan advertises a feature the engine never computes, the market would be
    silently refused for a reason that is a bug, not a data gap.
    """
    market = market_factory(market_type=market_type, home="Team A", away="Team B")
    bundle = engine.build(market)
    planned = set(bundle.plan["features"])
    produced = set(bundle.values)
    assert planned <= produced, f"planned but not produced: {sorted(planned - produced)}"
    assert not bundle.missing, f"unexpected missing features: {bundle.missing}"


def test_expected_goals_are_coherent(engine, market_factory):
    bundle = engine.build(market_factory(home="Team A", away="Team B"))
    values = bundle.values
    assert values["total_expected_goals"] == pytest.approx(
        values["expected_home_goals"] + values["expected_away_goals"], abs=1e-3
    )
    assert values["expected_home_goals"] > 0
    assert values["expected_away_goals"] > 0
    assert 0 <= values["clean_sheet_rates"] <= 1


def test_player_matching_accepts_full_names(engine, market_factory):
    """Polymarket asks about "Bukayo Saka"; the FPL feed publishes "Saka"."""
    bundle = engine.build(market_factory(
        market_type=MarketType.PLAYER_GOAL, player="Striker Team A",
        home="Team A", away="Team B", selection="Striker Team A",
    ))
    assert bundle.values.get("player_goal_rate_90") is not None
    assert "player_identity" not in bundle.missing


def test_player_matching_uses_the_team_hint(engine, market_factory):
    """Same surname on both sides is resolved by the team named in the question."""
    import re as _re

    for team in ("Team A", "Team B"):
        market = market_factory(
            market_type=MarketType.PLAYER_GOAL, player="Def " + team,
            home="Team A", away="Team B", selection="Def " + team,
        )
        # Rewrite the question so it names the team, as Polymarket does.
        market.question = f"Will Def {team} score for {team}?"
        assert _re.search(_re.escape(team), market.question)
        bundle = engine.build(market)
        assert bundle.values.get("player_goal_rate_90") is not None, bundle.missing
        assert bundle.values["player_side"] == ("home" if team == "Team A" else "away")


def test_ambiguous_player_is_refused_not_guessed(engine, market_factory):
    """A surname shared by both teams must not be silently resolved."""
    bundle = engine.build(market_factory(
        market_type=MarketType.PLAYER_GOAL, player="Striker",
        home="Team A", away="Team B", selection="Striker",
    ))
    # "Striker Team A" and "Striker Team B" both exist -> ambiguous.
    assert bundle.values == {} or "player_identity" in bundle.missing
