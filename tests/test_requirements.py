"""The data-requirements engine drives everything downstream."""
from __future__ import annotations

import pytest

from src.features.requirements import build_plan, requirements_for
from src.markets.schema import MarketType


def test_supported_market_has_requirements_and_model(market_factory):
    plan = build_plan(market_factory(market_type=MarketType.MATCH_RESULT))
    assert plan.model_name == "dixon_coles_match_result"
    assert plan.requirements
    assert "home_attack_rate" in plan.features
    assert plan.critical_keys


def test_unsupported_market_plan_is_empty_and_explains_itself(market_factory):
    plan = build_plan(market_factory(market_type=MarketType.CARDS))
    assert plan.requirements == []
    assert plan.features == []
    assert plan.model_name == ""
    assert any("UNSUPPORTED" in note for note in plan.notes)


def test_player_market_requirements_include_availability(market_factory):
    plan = build_plan(market_factory(market_type=MarketType.PLAYER_GOAL, player="Bukayo Saka"))
    keys = plan.critical_keys
    assert "player_availability" in keys
    assert "player_identity" in keys
    assert "opponent_defence_strength" in keys


def test_shots_market_requires_a_shots_feed(market_factory):
    plan = build_plan(market_factory(market_type=MarketType.PLAYER_SHOTS, player="X"))
    assert any("shots" in r.key for r in plan.requirements)


def test_corners_market_requires_corner_rates(market_factory):
    plan = build_plan(market_factory(market_type=MarketType.CORNERS))
    assert "team_corners_for" in plan.critical_keys
    assert "expected_total_corners" in plan.features


def test_period_note_added_for_half_markets(market_factory):
    market = market_factory(market_type=MarketType.MATCH_RESULT)
    market.period = "FIRST_HALF"
    plan = build_plan(market)
    assert any("FIRST_HALF" in note for note in plan.notes)


def test_plan_serialises(market_factory):
    payload = build_plan(market_factory()).as_dict()
    assert payload["market_type"] == "MATCH_RESULT"
    assert isinstance(payload["requirements"], list)
    assert all({"key", "description", "critical", "provider_hint"} <= set(r) for r in payload["requirements"])


@pytest.mark.parametrize("market_type", list(MarketType))
def test_every_market_type_has_a_definite_plan(market_type, market_factory):
    """No market family may crash the planner: it either has a plan or is refused."""
    plan = build_plan(market_factory(market_type=market_type))
    if market_type in (MarketType.MATCH_RESULT, MarketType.TOTAL_GOALS, MarketType.BTTS,
                       MarketType.CORNERS, MarketType.TEAM_TOTALS, MarketType.PLAYER_GOAL,
                       MarketType.PLAYER_ASSIST, MarketType.PLAYER_GOALS_PLUS_ASSISTS,
                       MarketType.PLAYER_SHOTS):
        assert plan.model_name
    else:
        assert plan.notes


def test_requirements_for_unknown_is_empty():
    assert requirements_for(MarketType.UNKNOWN) == []
