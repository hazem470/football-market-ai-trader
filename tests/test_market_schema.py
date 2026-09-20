"""Canonical market model: naming, match keys, freshness, tradeability."""
from __future__ import annotations

import time

from src.markets.schema import (
    MarketSnapshot,
    MarketType,
    make_match_key,
    normalize_player_name,
    normalize_team_name,
)


def test_team_normalisation_strips_club_suffixes():
    assert normalize_team_name("Arsenal FC") == "arsenal"
    assert normalize_team_name("Arsenal") == "arsenal"
    assert normalize_team_name("Brighton & Hove Albion") == "brighton and hove albion"


def test_team_normalisation_keeps_manchester_clubs_distinct():
    assert normalize_team_name("Manchester City") != normalize_team_name("Manchester United")


def test_player_normalisation():
    assert normalize_player_name("Kylian Mbappé") == "kylian mbappe"
    assert normalize_player_name("Kylian Mbappe") == normalize_player_name("Kylian Mbappé")
    assert normalize_player_name("  Bukayo   Saka ") == "bukayo saka"


def test_match_key_includes_day_so_reverse_fixtures_differ():
    first = make_match_key("Arsenal", "Chelsea", "2025-09-01")
    second = make_match_key("Chelsea", "Arsenal", "2026-03-01")
    assert first != second
    assert first.endswith("20250901")


def test_match_key_parses_dd_mm_yyyy():
    assert make_match_key("A", "B", "15/08/2025").endswith("20250815")


def test_snapshot_derives_spread_and_mid():
    snapshot = MarketSnapshot(ts=time.time(), bid=0.40, ask=0.44)
    assert snapshot.spread == 0.04
    assert snapshot.mid == 0.42
    assert snapshot.executable_buy_price == 0.44
    assert snapshot.executable_sell_price == 0.40


def test_snapshot_executable_price_prefers_ask_then_price():
    assert MarketSnapshot(ts=0, price=0.3).executable_buy_price == 0.3
    assert MarketSnapshot(ts=0, bid=0.3, ask=0.4, price=0.35).executable_buy_price == 0.4


def test_tradeability_requires_token_support_and_confidence(market_factory):
    good = market_factory()
    assert good.tradeable
    good.market_type = MarketType.UNKNOWN
    assert not good.tradeable
    good.market_type = MarketType.MATCH_RESULT
    good.yes_token_id = ""
    assert not good.tradeable


def test_freshness_uses_snapshot_timestamp(market_factory):
    old = market_factory(ts=time.time() - 300)
    fresh, age = old.freshness(time.time(), max_age_seconds=60)
    assert not fresh and age > 200
    recent = market_factory(ts=time.time())
    fresh, age = recent.freshness(time.time(), max_age_seconds=60)
    assert fresh and age < 5


def test_label_and_match_key_composition(market_factory):
    market = market_factory(home="Arsenal", away="Chelsea", selection="Arsenal")
    assert "Arsenal vs Chelsea" in market.label
    assert "Arsenal" in market.label
    assert market.match_key.startswith("arsenal|chelsea")


def test_market_type_player_flag():
    assert MarketType.PLAYER_GOAL.needs_player
    assert MarketType.PLAYER_SHOTS.needs_player
    assert not MarketType.MATCH_RESULT.needs_player
    assert not MarketType.CORNERS.needs_player


def test_to_row_and_snapshot_row_are_serialisable(market_factory):
    market = market_factory()
    row = market.to_row()
    assert row["market_id"] == "MKT-1"
    assert row["market_type"] == "MATCH_RESULT"
    snapshot_row = market.to_snapshot_row()
    assert snapshot_row["token_id"] == "token-MKT-1"
