"""Model maths: fitted probabilities must be coherent, and refusals must be explicit."""
from __future__ import annotations

import math

import pytest

from src.features.engine import FeatureEngine
from src.markets.schema import MarketType
from src.models.base import InsufficientModelData, Prediction
from src.models.corners import CornersModel
from src.models.goals import BttsModel, TeamTotalsModel, TotalGoalsModel
from src.models.match_result import MatchResultModel
from src.models.player_assist import PlayerAssistModel
from src.models.player_goal import PlayerGoalInvolvementModel, PlayerGoalModel
from src.models.player_shots import PlayerShotsModel
from src.models.poisson_core import RateDistribution, ScoreMatrix, poisson_cdf, poisson_pmf
from src.models.registry import ModelRegistry


def test_poisson_pmf_sums_to_one():
    assert sum(poisson_pmf(k, 1.4) for k in range(0, 30)) == pytest.approx(1.0, abs=1e-6)


def test_poisson_cdf_monotone():
    values = [poisson_cdf(k, 2.0) for k in range(0, 15)]
    assert values == sorted(values)


def test_score_matrix_probabilities_sum_to_one():
    matrix = ScoreMatrix(home_lambda=1.5, away_lambda=1.1, max_goals=10).normalize()
    total = sum(sum(row) for row in matrix.matrix)
    assert total == pytest.approx(1.0, abs=1e-6)


def test_score_matrix_market_probabilities_are_consistent():
    matrix = ScoreMatrix(home_lambda=1.6, away_lambda=1.0, max_goals=10)
    home = matrix.probability_home_win()
    draw = matrix.probability_draw()
    away = matrix.probability_away_win()
    assert home + draw + away == pytest.approx(1.0, abs=1e-6)
    assert matrix.probability_total_over(2.5) + matrix.probability_total_under(2.5) == pytest.approx(1.0)
    assert 0 <= matrix.probability_btts() <= 1


def test_dixon_coles_correction_keeps_distribution_valid():
    matrix = ScoreMatrix(home_lambda=1.4, away_lambda=1.2, max_goals=10)
    before = matrix.probability_draw()
    matrix.apply_dixon_coles(-0.05)
    assert sum(sum(row) for row in matrix.matrix) == pytest.approx(1.0, abs=1e-6)
    assert matrix.probability_draw() != pytest.approx(before)


def test_strength_model_requires_enough_history():
    from tests.conftest import make_match

    with pytest.raises(InsufficientModelData):
        MatchResultModel(min_matches=500).fit([make_match("A", "B", 1, 0)])


def test_match_result_model_fits_and_predicts(synthetic_history):
    model = MatchResultModel(min_matches=100).fit(synthetic_history.matches, league_label="E0")
    engine = FeatureEngine(history=synthetic_history)
    market = __import__("tests.conftest", fromlist=["make_market"]).make_market(
        home="Team A", away="Team B", market_type=MarketType.MATCH_RESULT,
    )
    bundle = engine.build(market)
    bundle.context["selection_side"] = "HOME"
    prediction = model.predict(bundle)
    assert not prediction.insufficient_data
    assert 0 < prediction.probability < 1
    assert prediction.diagnostics["p_home"] + prediction.diagnostics["p_draw"] + \
        prediction.diagnostics["p_away"] == pytest.approx(1.0, abs=1e-5)


def test_match_result_model_refuses_unknown_teams(synthetic_history):
    model = MatchResultModel(min_matches=100).fit(synthetic_history.matches, league_label="E0")
    engine = FeatureEngine(history=synthetic_history)
    from tests.conftest import make_market

    bundle = engine.build(make_market(home="Ghost FC", away="Phantom FC"))
    prediction = model.predict(bundle)
    assert prediction.insufficient_data
    assert prediction.notes


def test_total_goals_model_respects_the_line(synthetic_history):
    model = TotalGoalsModel(min_matches=100).fit(synthetic_history.matches)
    engine = FeatureEngine(history=synthetic_history)
    from tests.conftest import make_market

    bundle = engine.build(make_market(home="Team A", away="Team B", market_type=MarketType.TOTAL_GOALS))
    bundle.context.update({"line": 2.5, "selection_kind": "OVER"})
    over = model.predict(bundle)
    bundle.context["selection_kind"] = "UNDER"
    under = model.predict(bundle)
    assert over.probability + under.probability == pytest.approx(1.0, abs=1e-5)


def test_btts_model_complementary(synthetic_history):
    model = BttsModel(min_matches=100).fit(synthetic_history.matches)
    engine = FeatureEngine(history=synthetic_history)
    from tests.conftest import make_market

    bundle = engine.build(make_market(home="Team A", away="Team B", market_type=MarketType.BTTS))
    bundle.context["selection_kind"] = "OVER"
    yes = model.predict(bundle).probability
    bundle.context["selection_kind"] = "UNDER"
    no = model.predict(bundle).probability
    assert yes + no == pytest.approx(1.0, abs=1e-5)


def test_team_totals_model_accepts_home_and_away(synthetic_history):
    model = TeamTotalsModel(min_matches=100).fit(synthetic_history.matches)
    engine = FeatureEngine(history=synthetic_history)
    from tests.conftest import make_market

    bundle = engine.build(make_market(home="Team A", away="Team B", market_type=MarketType.TEAM_TOTALS))
    bundle.context.update({"line": 1.5, "selection_kind": "OVER", "team_side": "HOME"})
    home = model.predict(bundle)
    bundle.context["team_side"] = "AWAY"
    away = model.predict(bundle)
    assert 0 < home.probability < 1 and 0 < away.probability < 1
    assert home.diagnostics["team_side"] == "HOME"


def test_corners_model_requires_corner_history():
    from src.data.normalization.canonical import TeamHistory
    from tests.conftest import make_match

    history = TeamHistory([make_match("A", "B", 1, 0, home_corners=None, away_corners=None)] * 100)
    with pytest.raises(InsufficientModelData):
        CornersModel(min_matches=50).fit(history.matches)


def test_corners_model_predicts_both_sides(synthetic_history):
    model = CornersModel(min_matches=50).fit(synthetic_history.matches)
    engine = FeatureEngine(history=synthetic_history)
    from tests.conftest import make_market

    bundle = engine.build(make_market(home="Team A", away="Team B", market_type=MarketType.CORNERS))
    bundle.context.update({"line": 9.5, "selection_kind": "OVER"})
    over = model.predict(bundle).probability
    bundle.context["selection_kind"] = "UNDER"
    under = model.predict(bundle).probability
    assert over + under == pytest.approx(1.0, abs=1e-5)


def test_corners_model_refuses_without_line(synthetic_history):
    model = CornersModel(min_matches=50).fit(synthetic_history.matches)
    engine = FeatureEngine(history=synthetic_history)
    from tests.conftest import make_market

    bundle = engine.build(make_market(market_type=MarketType.CORNERS, home="Team A", away="Team B"))
    bundle.context.pop("line", None)
    assert model.predict(bundle).insufficient_data


def test_player_goal_model_matches_the_rate_formula():

    model = PlayerGoalModel()
    bundle = _player_bundle(rate=0.5, start=1.0, minutes=90.0, team_attack=1.4, opp_defence=1.4)
    prediction = model.predict(bundle)
    assert prediction.probability == pytest.approx(1 - math.exp(-0.5), abs=1e-5)


def test_player_goal_model_refuses_on_missing_features():
    bundle = _player_bundle(rate=None, start=1.0, minutes=90.0, team_attack=1.4, opp_defence=1.4)
    prediction = PlayerGoalModel().predict(bundle)
    assert prediction.insufficient_data
    assert "player_goal_rate_90" in prediction.notes[0]


def test_player_goal_model_refuses_unavailable_player():
    bundle = _player_bundle(rate=0.5, start=1.0, minutes=90.0, team_attack=1.4, opp_defence=1.4, availability=0.0)
    assert PlayerGoalModel().predict(bundle).insufficient_data


def test_player_goal_model_refuses_low_starting_probability():
    bundle = _player_bundle(rate=0.5, start=0.02, minutes=90.0, team_attack=1.4, opp_defence=1.4)
    assert PlayerGoalModel().predict(bundle).insufficient_data


def test_player_assist_model_has_lower_confidence_than_goal_model():
    goal = PlayerGoalModel().predict(_player_bundle(rate=0.4, start=1.0, minutes=90.0,
                                                   team_attack=1.4, opp_defence=1.4))
    assist = PlayerAssistModel().predict(_player_bundle(rate=0.4, start=1.0, minutes=90.0,
                                                        team_attack=1.4, opp_defence=1.4))
    assert assist.confidence < goal.confidence


def test_goal_involvement_uses_both_rates():
    bundle = _player_bundle(rate=0.3, start=1.0, minutes=90.0, team_attack=1.4, opp_defence=1.4)
    bundle.values["player_assist_rate_90"] = 0.2
    prediction = PlayerGoalInvolvementModel().predict(bundle)
    assert prediction.probability == pytest.approx(1 - math.exp(-0.5), abs=1e-5)


def test_player_shots_model_refuses_without_a_shots_feed():
    bundle = _player_bundle(rate=0.5, start=1.0, minutes=90.0, team_attack=1.4, opp_defence=1.4)
    bundle.values["player_shot_rate_90"] = None
    prediction = PlayerShotsModel().predict(bundle)
    assert prediction.insufficient_data
    assert "shots" in prediction.notes[0]


def test_player_shots_model_predicts_when_configured():
    bundle = _player_bundle(rate=0.5, start=1.0, minutes=90.0, team_attack=1.4, opp_defence=1.4)
    bundle.values["player_shot_rate_90"] = 2.0
    bundle.context["line"] = 1.5
    bundle.context["selection_kind"] = "OVER"
    prediction = PlayerShotsModel().predict(bundle)
    assert not prediction.insufficient_data
    assert 0 < prediction.probability < 1


def test_rate_distribution_poisson_matches_manual():
    distribution = RateDistribution(mean=1.0)
    assert distribution.probability_at_least(1) == pytest.approx(1 - math.exp(-1), abs=1e-6)


def test_rate_distribution_over_under_complementary():
    distribution = RateDistribution(mean=2.5, dispersion=0.35)
    assert distribution.probability_over(2.5) + distribution.probability_under(2.5) == pytest.approx(1.0, abs=1e-5)


def test_registry_refuses_unsupported_type(synthetic_history):
    registry = ModelRegistry()
    from tests.conftest import make_market

    engine = FeatureEngine(history=synthetic_history)
    bundle = engine.build(make_market(market_type=MarketType.CARDS))
    prediction = registry.predict(MarketType.CARDS.value, "premier league", bundle, synthetic_history.matches)
    assert prediction.insufficient_data


def test_registry_records_fit_errors():
    from tests.conftest import make_match

    registry = ModelRegistry(model_config={"match_result": {"min_history_matches": 10_000}})
    engine = FeatureEngine()
    from tests.conftest import make_market

    bundle = engine.build(make_market())
    registry.predict(MarketType.MATCH_RESULT.value, "e0", bundle, [make_match("A", "B", 1, 0)])
    assert registry.fit_errors


def test_registry_caches_fitted_models(synthetic_history):
    registry = ModelRegistry(model_config={"match_result": {"min_history_matches": 100}})
    first = registry.get_or_fit(MarketType.MATCH_RESULT.value, "e0", synthetic_history.matches)
    second = registry.get_or_fit(MarketType.MATCH_RESULT.value, "e0", synthetic_history.matches)
    assert first is second


def test_prediction_insufficient_helper():
    prediction = Prediction.insufficient("m", "TOTAL_GOALS", "because")
    assert prediction.insufficient_data
    assert prediction.probability == 0.0 and prediction.confidence == 0.0
    assert prediction.notes == ["because"]


def _player_bundle(rate, start, minutes, team_attack, opp_defence, availability=1.0):
    from src.features.engine import FeatureSet

    bundle = FeatureSet(market_id="M", market_type="PLAYER_GOAL", match_key="k")
    bundle.values = {
        "player_goal_rate_90": rate,
        "starting_probability": start,
        "expected_minutes": minutes,
        "player_availability": availability,
        "team_attack_rate": team_attack,
        "opponent_defence_rate": opp_defence,
    }
    bundle.context = {"home_norm": "a", "away_norm": "b"}
    return bundle
