"""Validation must turn missing critical data into an explicit refusal."""
from __future__ import annotations

import time

from src.data.normalization.canonical import Normalizer
from src.data.validation.validators import (
    Severity,
    stale_timestamp,
    validate_market,
    validate_matches,
    validate_player,
)
from src.markets.schema import MarketType
from tests.conftest import make_match


def test_short_history_is_critical():
    report = validate_matches([make_match("A", "B", 1, 0)], min_matches=30)
    assert not report.ok
    assert any(i.field == "history" for i in report.critical)


def test_long_history_passes(synthetic_history):
    report = validate_matches(synthetic_history.matches, min_matches=50)
    assert report.ok


def test_impossible_goal_values_are_critical():
    matches = [make_match(f"T{i}", f"U{i}", 1, 0) for i in range(40)]
    matches.append(Normalizer.match_from_raw({
        "home_team": "X", "away_team": "Y", "home_goals": 99, "away_goals": 0, "match_date": "2025-01-01",
    }))
    report = validate_matches(matches, min_matches=10)
    assert not report.ok
    assert any(i.field == "goals" for i in report.critical)


def test_valid_market_passes(market_factory):
    report = validate_market(market_factory())
    assert report.ok


def test_market_without_token_is_critical(market_factory):
    market = market_factory()
    market.yes_token_id = ""
    assert not validate_market(market).ok


def test_unknown_market_type_is_critical(market_factory):
    assert not validate_market(market_factory(market_type=MarketType.UNKNOWN)).ok


def test_unsupported_market_type_is_critical(market_factory):
    report = validate_market(market_factory(market_type=MarketType.CARDS))
    assert not report.ok
    assert any("no data path" in i.detail for i in report.critical)


def test_stale_price_is_critical(market_factory):
    report = validate_market(market_factory(ts=time.time() - 300), max_price_age_seconds=60)
    assert not report.ok


def test_out_of_range_bid_ask_is_critical(market_factory):
    report = validate_market(market_factory(bid=0.9, ask=0.2))
    assert not report.ok
    assert any(i.field == "order_book" and i.severity is Severity.CRITICAL for i in report.issues)


def test_player_market_without_player_is_critical(market_factory):
    assert not validate_market(market_factory(market_type=MarketType.PLAYER_GOAL, player="")).ok


def test_player_without_minutes_is_critical():
    record = Normalizer.player_from_raw({
        "player_key": "x", "display_name": "X", "team": "T", "minutes": 0, "availability": 1.0,
    })
    assert not validate_player(record).ok


def test_player_requiring_shots_without_shots_is_critical():
    record = Normalizer.player_from_raw({
        "player_key": "x", "display_name": "X", "team": "T", "minutes": 900.0,
        "availability": 1.0, "xg_per_90": 0.4, "status": "a",
    })
    report = validate_player(record, require_shots=True)
    assert not report.ok
    assert any(i.field == "shots_per_90" for i in report.critical)


def test_unknown_availability_is_critical():
    record = Normalizer.player_from_raw({
        "player_key": "x", "display_name": "X", "team": "T", "minutes": 900.0,
        "availability": None, "xg_per_90": 0.4, "status": "UNKNOWN",
    })
    assert not validate_player(record).ok


def test_stale_timestamp_helper():
    assert stale_timestamp(time.time() - 500, 60) is not None
    assert stale_timestamp(time.time(), 60) is None


def test_report_summary_is_informative(market_factory):
    summary = validate_market(market_factory(market_type=MarketType.UNKNOWN)).summary()
    assert "critical=" in summary
