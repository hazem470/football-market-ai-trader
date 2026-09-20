"""Database layer: auditability of every trading decision."""
from __future__ import annotations

import time

import pytest

from src.storage.database import SCHEMA_VERSION, Database


def test_schema_contains_every_required_table(database):
    tables = set(database.table_names())
    required = {
        "users", "providers", "matches", "players", "markets", "market_snapshots", "features",
        "predictions", "signals", "orders", "positions", "trades", "pnl", "risk_events",
        "notifications", "backtests", "system_logs", "schema_meta",
    }
    assert required <= tables


def test_schema_version_is_recorded(database):
    row = database.query_one("SELECT value FROM schema_meta WHERE key='schema_version'")
    assert int(row["value"]) == SCHEMA_VERSION


def test_match_upsert_is_idempotent(database, match_factory):
    match = match_factory("Arsenal", "Chelsea", 2, 1).to_db_row()
    database.upsert_match(match)
    match["home_goals"] = 3
    database.upsert_match(match)
    assert database.match_count() == 1
    assert database.list_matches(finished_only=False)[0]["home_goals"] == 3


def test_market_upsert_and_listing(database, market_factory):
    market = market_factory()
    database.upsert_market(market.to_row())
    stored = database.list_markets()
    assert stored and stored[0]["market_id"] == "MKT-1"
    assert stored[0]["market_type"] == "MATCH_RESULT"


def test_snapshot_insert_and_read(database, market_factory):
    market = market_factory()
    database.upsert_market(market.to_row())
    database.insert_snapshot(market.to_snapshot_row())
    rows = database.query("SELECT * FROM market_snapshots WHERE market_id = ?", ("MKT-1",))
    assert rows and rows[0]["token_id"] == "token-MKT-1"


def test_signal_round_trip_with_trace(database, market_factory):
    from src.strategy.signals import Signal, SignalState

    signal = Signal(state=SignalState.NO_TRADE, market_id="MKT-1", market_type="MATCH_RESULT",
                    edge=0.02, trace={"feature_key": "FEAT-abc", "plan": {"model_name": "m"}})
    signal_id = database.insert_signal(signal.to_db_row())
    stored = database.query_one("SELECT * FROM signals WHERE signal_id = ?", (signal_id,))
    assert stored["state"] == "NO_TRADE"
    assert "FEAT-abc" in stored["trace_json"]


def test_order_lifecycle(database):
    order_id = database.insert_order({"market_id": "M1", "side": "BUY", "price": 0.3,
                                      "size": 10.0, "status": "PENDING", "mode": "paper"})
    database.update_order(order_id, status="FILLED", filled_size=10.0, avg_fill_price=0.31)
    stored = database.query_one("SELECT * FROM orders WHERE order_id = ?", (order_id,))
    assert stored["status"] == "FILLED"
    assert stored["avg_fill_price"] == pytest.approx(0.31)


def test_position_and_exposure_queries(database):
    database.insert_position({"market_id": "M1", "size": 10.0, "entry_price": 0.5,
                              "status": "OPEN", "mode": "paper"})
    database.insert_position({"market_id": "M2", "size": 4.0, "entry_price": 0.5,
                              "status": "OPEN", "mode": "paper"})
    assert database.open_exposure(mode="paper") == pytest.approx(7.0)
    assert len(database.list_positions(status="OPEN")) == 2


def test_realized_pnl_since(database):
    now = time.time()
    database.insert_position({"market_id": "M1", "size": 10.0, "entry_price": 0.5, "status": "CLOSED",
                              "closed_at": now, "realized_pnl": 3.0, "mode": "paper"})
    assert database.realized_pnl_since(now - 60, mode="paper") == pytest.approx(3.0)
    assert database.realized_pnl_since(now + 60) == pytest.approx(0.0)


def test_trade_and_pnl_records(database):
    database.insert_trade({"market_id": "M1", "action": "CLOSE", "price": 0.8, "size": 10.0,
                           "pnl": 3.0, "mode": "paper"})
    database.record_pnl({"mode": "paper", "balance": 103.0, "realized_pnl": 3.0})
    assert database.list_trades()
    assert database.latest_pnl()["balance"] == pytest.approx(103.0)


def test_risk_events_and_logs(database):
    database.record_risk_event({"kind": "daily_loss", "severity": "CRITICAL", "detail": "broke"})
    database.log("ERROR", "test", "event", "message")
    assert database.list_risk_events()[0]["kind"] == "daily_loss"
    assert database.list_logs()[0]["message"] == "message"


def test_backtest_persistence(database):
    database.save_backtest("BT-1", {"min_edge": 0.08}, {"MODEL_PERFORMANCE": {}})
    assert database.list_backtests()[0]["run_id"] == "BT-1"


def test_provider_health_upsert(database):
    database.upsert_provider_health("fpl", "player_stats", "HEALTHY")
    database.upsert_provider_health("fpl", "player_stats", "CRITICAL", "timeout")
    rows = database.list_providers()
    assert rows[0]["health"] == "CRITICAL"
    assert rows[0]["last_error"] == "timeout"


def test_transaction_rolls_back_on_error(database):
    with pytest.raises(ValueError):
        with database.transaction() as conn:
            conn.execute("INSERT INTO system_logs (ts, level, component, event, message) "
                         "VALUES (?,?,?,?,?)", (time.time(), "INFO", "t", "e", "m"))
            raise ValueError("abort")
    assert database.list_logs() == []


def test_in_memory_database_works():
    with Database(":memory:") as db:
        assert "signals" in db.table_names()
