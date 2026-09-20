"""Tradeability filters are hard gates: every rejection reason must be reachable."""
from __future__ import annotations

import time

from src.markets.filters import FilterConfig, apply_filters, passes_filters
from src.markets.schema import MarketType
from tests.conftest import _utc_iso


def config(**overrides) -> FilterConfig:
    base = FilterConfig(
        min_liquidity=100.0, max_spread=0.04, min_hours_to_resolution=1.0,
        max_hours_to_resolution=240.0, min_minutes_to_start=20.0, max_price_age_seconds=60.0,
    )
    for key, value in overrides.items():
        setattr(base, key, value)
    return base


def test_clean_market_passes(market_factory):
    assert passes_filters(market_factory(), config()) is None


def test_missing_token_is_rejected(market_factory):
    market = market_factory()
    market.yes_token_id = ""
    assert passes_filters(market, config()).reason == "no_token_id"


def test_unsupported_market_type_is_rejected(market_factory):
    assert passes_filters(market_factory(market_type=MarketType.CARDS), config()).reason == "unsupported_market_type"


def test_low_classification_confidence_is_rejected(market_factory):
    market = market_factory()
    market.classification_confidence = 0.3
    assert passes_filters(market, config()).reason == "low_classification_confidence"


def test_stale_price_is_rejected(market_factory):
    market = market_factory(ts=time.time() - 600)
    assert passes_filters(market, config()).reason == "stale_price"


def test_low_liquidity_is_rejected(market_factory):
    assert passes_filters(market_factory(liquidity=5.0), config()).reason == "low_liquidity"


def test_wide_spread_is_rejected(market_factory):
    assert passes_filters(market_factory(bid=0.05, ask=0.60), config()).reason == "wide_spread"


def test_resolving_too_soon_is_rejected(market_factory):
    market = market_factory(end_time=_utc_iso(time.time() + 600))
    assert passes_filters(market, config()).reason == "resolving_too_soon"


def test_resolving_too_far_is_rejected(market_factory):
    market = market_factory(end_time=_utc_iso(time.time() + 400 * 3600))
    assert passes_filters(market, config()).reason == "resolving_too_far"


def test_missing_resolution_time_is_rejected(market_factory):
    market = market_factory(end_time="not-a-date")
    assert passes_filters(market, config()).reason == "no_resolution_time"


def test_starting_too_soon_is_rejected(market_factory):
    market = market_factory(start_time=_utc_iso(time.time() + 300))
    assert passes_filters(market, config()).reason == "starting_too_soon"


def test_player_market_without_player_is_rejected(market_factory):
    market = market_factory(market_type=MarketType.PLAYER_GOAL, player="")
    assert passes_filters(market, config()).reason == "player_unknown"


def test_unresolvable_teams_are_rejected(market_factory):
    market = market_factory(home="", away="")
    assert passes_filters(market, config()).reason == "teams_unknown"


def test_no_snapshot_is_rejected(market_factory):
    market = market_factory()
    market.snapshot = None
    assert passes_filters(market, config()).reason == "no_price_snapshot"


def test_apply_filters_partitions_and_counts(market_factory):
    markets = [
        market_factory(market_id="ok"),
        market_factory(market_id="stale", ts=time.time() - 600),
        market_factory(market_id="thin", liquidity=1.0),
        market_factory(market_id="wide", bid=0.01, ask=0.9),
    ]
    result = apply_filters(markets, config())
    assert result.accepted_count == 1
    assert result.rejected_count == 3
    reasons = result.rejection_reasons()
    assert reasons["stale_price"] == 1
    assert reasons["low_liquidity"] == 1
    assert reasons["wide_spread"] == 1


def test_allowed_market_types_whitelist(market_factory):
    result = apply_filters(
        [market_factory(market_type=MarketType.MATCH_RESULT), market_factory(market_id="b", market_type=MarketType.BTTS)],
        config(allowed_market_types=("BTTS",)),
    )
    assert result.accepted_count == 1
    assert result.accepted[0].market_type is MarketType.BTTS
