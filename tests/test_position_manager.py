"""Exit rules: every rule has a name, a trigger and an explanation."""
from __future__ import annotations

import time

import pytest

from src.execution.position_manager import (
    ExitReason,
    ExitRules,
    ManagedPosition,
    PositionManager,
    apply_exit,
    evaluate_exit,
)


def position(**overrides) -> ManagedPosition:
    base = dict(position_id="P1", market_id="M1", token_id="T1", entry_price=0.5, size=10.0,
                opened_at=time.time() - 3600, model_probability=0.6, current_price=0.5)
    base.update(overrides)
    return ManagedPosition(**base)


def test_no_exit_when_nothing_triggers():
    decision = evaluate_exit(position(), ExitRules())
    assert not decision.should_exit
    assert decision.reason is ExitReason.NONE


def test_stop_loss_triggers():
    decision = evaluate_exit(position(current_price=0.2), ExitRules(stop_loss_fraction=0.5))
    assert decision.should_exit
    assert decision.reason is ExitReason.STOP_LOSS


def test_take_profit_triggers():
    decision = evaluate_exit(position(current_price=0.95), ExitRules(take_profit_fraction=0.5))
    assert decision.should_exit
    assert decision.reason is ExitReason.TAKE_PROFIT


def test_edge_disappearance_triggers():
    decision = evaluate_exit(position(), ExitRules(exit_min_edge=0.05), current_edge=0.01)
    assert decision.should_exit
    assert decision.reason is ExitReason.EDGE_DISAPPEARED


def test_probability_reversal_triggers():
    decision = evaluate_exit(position(current_probability=0.4), ExitRules(probability_reversal_threshold=0.1))
    assert decision.should_exit
    assert decision.reason is ExitReason.PROBABILITY_REVERSED


def test_time_exit_triggers_near_resolution():
    decision = evaluate_exit(position(resolution_time=time.time() + 120),
                             ExitRules(min_minutes_to_resolution=10))
    assert decision.should_exit
    assert decision.reason is ExitReason.TIME_EXIT


def test_circuit_breaker_flattens_only_when_configured():
    strict = evaluate_exit(position(), ExitRules(flatten_on_circuit_breaker=True), breaker_open=True)
    relaxed = evaluate_exit(position(), ExitRules(flatten_on_circuit_breaker=False), breaker_open=True)
    assert strict.should_exit and strict.reason is ExitReason.CIRCUIT_BREAKER
    assert not relaxed.should_exit


def test_closed_position_never_exits_again():
    closed = position(closed_at=time.time())
    decision = evaluate_exit(closed, ExitRules(stop_loss_fraction=0.1))
    assert not decision.should_exit


def test_apply_exit_records_pnl():
    subject = position(entry_price=0.5, size=10.0)
    pnl = apply_exit(subject, 0.8, ExitReason.TAKE_PROFIT)
    assert pnl == pytest.approx(3.0)
    assert subject.exit_reason == "TAKE_PROFIT"
    assert not subject.is_open


def test_position_manager_registers_and_sweeps():
    manager = PositionManager(rules=ExitRules(stop_loss_fraction=0.5))
    subject = position(current_price=0.2)
    manager.register(subject)
    decisions = manager.sweep("M1")
    assert len(decisions) == 1
    assert decisions[0][1].reason is ExitReason.STOP_LOSS


def test_position_manager_refresh_updates_marks():
    manager = PositionManager()
    subject = position()
    manager.register(subject)
    manager.refresh("M1", 0.75, 0.8)
    assert subject.current_price == pytest.approx(0.75)
    assert subject.current_probability == pytest.approx(0.8)


def test_position_manager_summary_counts_reasons():
    manager = PositionManager()
    opened = position(position_id="A")
    apply_exit(opened, 0.9, ExitReason.TAKE_PROFIT)
    closed = position(position_id="B")
    apply_exit(closed, 0.1, ExitReason.STOP_LOSS)
    manager.register(opened)
    manager.register(closed)
    summary = manager.summary()
    assert summary["closed"] == 2
    assert summary["exit_reasons"]["TAKE_PROFIT"] == 1
    assert summary["exit_reasons"]["STOP_LOSS"] == 1


def test_position_manager_persists_the_exit(database):
    manager = PositionManager(database=database)
    database.insert_position({"position_id": "P1", "market_id": "M1", "size": 10.0,
                             "entry_price": 0.5, "status": "OPEN", "mode": "paper"})
    subject = position()
    apply_exit(subject, 0.9, ExitReason.TAKE_PROFIT)
    manager.record_exit(subject, evaluate_exit(subject, ExitRules()))
    stored = database.query_one("SELECT * FROM positions WHERE position_id = ?", ("P1",))
    assert stored["status"] == "CLOSED"
    assert stored["realized_pnl"] == pytest.approx(4.0)


def test_unrealized_fraction_is_relative_to_notional():
    subject = position(entry_price=0.5, size=10.0, current_price=0.6)
    assert subject.unrealized_fraction() == pytest.approx(0.2)
