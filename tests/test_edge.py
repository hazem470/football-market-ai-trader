"""The edge engine: raw edge is not tradeable edge."""
from __future__ import annotations

import pytest

from src.strategy.edge import CostConfig, calculate_edge, required_probability_for_edge


def test_realistic_edge_is_below_raw_edge(market_factory):
    market = market_factory(bid=0.40, ask=0.44)
    breakdown = calculate_edge(0.60, market, CostConfig())
    assert breakdown.raw_edge == pytest.approx(0.60 - 0.44)
    assert breakdown.realistic_edge < breakdown.raw_edge


def test_edge_decomposition_matches_the_documented_table(market_factory):
    market = market_factory(bid=0.475, ask=0.485)
    config = CostConfig(fee_rate=0.005, assumed_slippage=0.008, model_uncertainty=0.02,
                        data_uncertainty=0.01, execution_uncertainty=0.005)
    breakdown = calculate_edge(0.63, market, config)
    assert breakdown.market_price == pytest.approx(0.485)
    expected = (
        0.63 - 0.485 - (0.485 - 0.475) / 2 - 0.008 - 0.005 * 0.485 - 0.02 - 0.01 - 0.005
    )
    assert breakdown.realistic_edge == pytest.approx(expected, abs=1e-9)


def test_edge_incomplete_without_snapshot(market_factory):
    market = market_factory()
    market.snapshot = None
    breakdown = calculate_edge(0.6, market, CostConfig())
    assert not breakdown.is_complete
    assert "snapshot" in breakdown.incomplete_reason


def test_edge_incomplete_without_executable_price(market_factory):
    market = market_factory(bid=None, ask=None)
    market.snapshot.price = None
    market.snapshot.mid = None
    breakdown = calculate_edge(0.6, market, CostConfig())
    assert not breakdown.is_complete


def test_spread_widens_the_penalty(market_factory):
    tight = calculate_edge(0.6, market_factory(bid=0.456, ask=0.464), CostConfig())
    wide = calculate_edge(0.6, market_factory(bid=0.40, ask=0.52), CostConfig())
    assert wide.realistic_edge < tight.realistic_edge
    assert wide.notes


def test_thin_liquidity_is_noted(market_factory):
    breakdown = calculate_edge(0.6, market_factory(liquidity=50.0), CostConfig())
    assert any("liquidity" in note for note in breakdown.notes)


def test_edge_can_be_negative(market_factory):
    breakdown = calculate_edge(0.10, market_factory(bid=0.45, ask=0.47), CostConfig())
    assert breakdown.realistic_edge < 0


def test_breakdown_table_renders_all_rows(market_factory):
    table = calculate_edge(0.6, market_factory(), CostConfig()).table()
    for label in ("Model probability", "Realized" if False else "REALISTIC EDGE", "Spread cost",
                  "Model uncertainty", "Data uncertainty", "Execution uncertainty"):
        assert label in table


def test_required_probability_is_the_inverse():
    config = CostConfig()
    needed = required_probability_for_edge(0.40, 0.08, config)
    market = __import__("tests.conftest", fromlist=["make_market"]).make_market(
        bid=0.399, ask=0.40, spread=0.001,
    )
    breakdown = calculate_edge(needed, market, config)
    assert breakdown.realistic_edge == pytest.approx(0.08, abs=0.02)


def test_breakdown_serialises(market_factory):
    payload = calculate_edge(0.6, market_factory(), CostConfig()).as_dict()
    assert payload["complete"] is True
    assert payload["side"] == "BUY"
    assert set(payload) >= {"raw_edge", "realistic_edge", "spread_cost", "slippage_cost", "fee_cost"}
