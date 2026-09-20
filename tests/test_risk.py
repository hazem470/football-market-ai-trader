"""Risk engine: limits are hard, sizing is fractional Kelly, correlation bucketed."""
from __future__ import annotations

import pytest

from src.risk.circuit_breaker import BreakerConfig, BreakerState, CircuitBreaker
from src.risk.correlation import effective_exposure, group_key_for, overlap_factor
from src.risk.engine import RiskContext, RiskEngine
from src.risk.exposure import ExposureSnapshot, ExposureTracker, start_of_day_utc
from src.risk.limits import RiskLimits
from src.risk.sizing import compute_size, kelly_fraction_for_binary
from src.strategy.edge import CostConfig, calculate_edge


@pytest.fixture
def limits() -> RiskLimits:
    return RiskLimits(capital=500, max_trade=10, max_daily_exposure=50, max_daily_loss=20,
                      max_open_positions=3, min_liquidity=100, max_spread=0.04,
                      min_edge=0.08, min_confidence=0.75, kelly_fraction=0.25,
                      max_correlated_exposure=25.0)


@pytest.fixture
def engine(limits, database) -> RiskEngine:
    tracker = ExposureTracker(limits, database=database)
    return RiskEngine(limits=limits, tracker=tracker, breaker=CircuitBreaker(), mode="paper")


def _edge(probability: float, market_factory, **kwargs):
    market = kwargs.pop("market", None) or market_factory(bid=0.28, ask=0.30, liquidity=5000.0)
    return calculate_edge(probability, market, CostConfig()).realistic_edge, market


def test_limits_validation_catches_inconsistent_values():
    problems = RiskLimits(capital=100, max_trade=500).validate()
    assert any("max_trade" in p for p in problems)
    assert RiskLimits().validate() == []


def test_kelly_fraction_zero_without_edge():
    assert kelly_fraction_for_binary(0.4, 0.5) == 0.0


def test_kelly_fraction_formula():
    assert kelly_fraction_for_binary(0.6, 0.5) == pytest.approx(0.2)


def test_compute_size_caps_at_max_trade():
    result = compute_size(probability=0.95, price=0.5, bankroll=1000, fraction=1.0, max_trade=10)
    assert result.capped_size == 10
    assert result.binding_constraint == "max_trade"


def test_compute_size_respects_fraction():
    small = compute_size(0.6, 0.5, 1000, fraction=0.1, max_trade=100)
    large = compute_size(0.6, 0.5, 1000, fraction=0.5, max_trade=100)
    assert large.capped_size > small.capped_size


def test_compute_size_skips_below_minimum():
    result = compute_size(0.501, 0.5, 100, fraction=0.25, max_trade=10, min_trade=5)
    assert result.capped_size == 0.0
    assert result.binding_constraint == "min_trade"


def test_circuit_breaker_trips_after_consecutive_errors():
    breaker = CircuitBreaker(config=BreakerConfig(max_consecutive_errors=3))
    for _ in range(2):
        breaker.record_error("boom")
    assert breaker.state is BreakerState.CLOSED
    breaker.record_error("boom")
    assert breaker.state is BreakerState.OPEN
    assert not breaker.allows_new_orders()[0]


def test_circuit_breaker_manual_stop_wins():
    breaker = CircuitBreaker()
    breaker.stop("operator")
    allowed, reason = breaker.allows_new_orders()
    assert not allowed and "manual" in reason


def test_circuit_breaker_stale_data_trigger():
    breaker = CircuitBreaker(config=BreakerConfig(max_stale_data_minutes=10))
    assert breaker.check_stale_data(3600) is not None
    assert breaker.state is BreakerState.OPEN


def test_circuit_breaker_price_spike():
    breaker = CircuitBreaker(config=BreakerConfig(abnormal_price_move=0.1))
    assert breaker.check_price_move(0.5, 0.8) is not None


def test_circuit_breaker_daily_loss():
    breaker = CircuitBreaker()
    assert breaker.check_daily_loss(25, 20) is not None


def test_circuit_breaker_reset_reopens():
    breaker = CircuitBreaker()
    breaker.stop()
    breaker.reset("operator")
    assert breaker.allows_new_orders()[0]


def test_circuit_breaker_rejections_trigger():
    breaker = CircuitBreaker(config=BreakerConfig(max_order_rejections=2))
    breaker.record_order_rejection("a")
    breaker.record_order_rejection("b")
    assert breaker.state is BreakerState.OPEN


def test_correlation_groups_by_match(market_factory):
    market = market_factory(home="Arsenal", away="Chelsea")
    assert group_key_for(market).startswith("match:arsenal|chelsea")


def test_correlation_overlap_identical_family_is_full():
    assert overlap_factor("TOTAL_GOALS", "TOTAL_GOALS") == 1.0


def test_correlation_overlap_known_pair():
    assert overlap_factor("TOTAL_GOALS", "BTTS") > overlap_factor("CORNERS", "TOTAL_GOALS")


def test_effective_exposure_weights_by_overlap():
    existing = [{"family": "MATCH_RESULT", "notional": 10.0},
                {"family": "CORNERS", "notional": 10.0}]
    result = effective_exposure(existing, "TOTAL_GOALS", 10.0)
    assert result["effective_notional"] > 10.0
    assert result["effective_notional"] < 40.0
    assert len(result["components"]) == 2


def test_exposure_tracker_counts_open_positions(database, limits, market_factory):
    for index in range(2):
        database.insert_position({
            "market_id": f"M{index}", "size": 10.0, "entry_price": 0.5, "status": "OPEN", "mode": "paper",
        })
    snapshot = ExposureTracker(limits, database).snapshot(mode="paper")
    assert snapshot.open_positions == 2
    assert snapshot.open_exposure == pytest.approx(10.0)


def test_exposure_tracker_daily_loss(database, limits):
    database.insert_position({
        "market_id": "M", "size": 10.0, "entry_price": 0.5, "status": "CLOSED",
        "closed_at": __import__("time").time(), "realized_pnl": -15.0, "mode": "paper",
    })
    tracker = ExposureTracker(limits, database)
    snapshot = tracker.snapshot(mode="paper")
    assert snapshot.today_loss == pytest.approx(15.0)
    assert tracker.within_daily_loss(snapshot)[0] is True
    snapshot.today_realized_pnl = -25.0
    assert tracker.within_daily_loss(snapshot)[0] is False


def test_exposure_tracker_rejects_excess_daily_exposure(limits):
    tracker = ExposureTracker(limits)
    snapshot = ExposureSnapshot(today_exposure=45.0)
    ok, reason = tracker.within_daily_exposure(snapshot, 10.0)
    assert not ok and "exposure" in reason


def test_exposure_tracker_rejects_position_count(limits):
    tracker = ExposureTracker(limits)
    ok, reason = tracker.within_position_count(ExposureSnapshot(open_positions=3))
    assert not ok


def test_exposure_tracker_rejects_correlated_bucket(limits):
    tracker = ExposureTracker(limits)
    snapshot = ExposureSnapshot(correlated_exposure={"match:a|b": 20.0})
    ok, reason = tracker.within_correlated(snapshot, "match:a|b", 10.0)
    assert not ok and "correlated" in reason


def test_start_of_day_is_utc_midnight():
    value = start_of_day_utc()
    from datetime import datetime, timezone

    moment = datetime.fromtimestamp(value, tz=timezone.utc)
    assert (moment.hour, moment.minute, moment.second) == (0, 0, 0)


def test_risk_allows_a_genuinely_good_signal(engine, market_factory):
    edge_value, market = _edge(0.65, market_factory)
    decision = engine.evaluate(
        market, calculate_edge(0.65, market, CostConfig()),
        RiskContext(balance=500, price_age_seconds=1, confidence=0.9),
    )
    assert decision.allowed
    assert 0 < decision.size <= engine.limits.max_trade
    assert decision.checks["edge"] == "OK"


def test_risk_denies_thin_edge(engine, market_factory):
    market = market_factory(bid=0.28, ask=0.30)
    decision = engine.evaluate(market, calculate_edge(0.32, market, CostConfig()),
                               RiskContext(balance=500, price_age_seconds=1, confidence=0.95))
    assert not decision.allowed
    assert any("edge" in reason for reason in decision.reasons)


def test_risk_denies_low_confidence(engine, market_factory):
    market = market_factory(bid=0.28, ask=0.30)
    decision = engine.evaluate(market, calculate_edge(0.7, market, CostConfig()),
                               RiskContext(balance=500, price_age_seconds=1, confidence=0.4))
    assert not decision.allowed
    assert decision.checks["confidence"] == "LOW"


def test_risk_denies_stale_price(engine, market_factory):
    market = market_factory()
    decision = engine.evaluate(market, calculate_edge(0.7, market, CostConfig()),
                               RiskContext(balance=500, price_age_seconds=600, confidence=0.9))
    assert not decision.allowed
    assert decision.checks["price_freshness"] == "STALE"


def test_risk_denies_low_liquidity(engine, market_factory):
    market = market_factory(bid=0.28, ask=0.30, liquidity=10.0)
    decision = engine.evaluate(market, calculate_edge(0.7, market, CostConfig()),
                               RiskContext(balance=500, price_age_seconds=1, confidence=0.9))
    assert not decision.allowed
    assert decision.checks["liquidity"] == "LOW"


def test_risk_denies_wide_spread(engine, market_factory):
    market = market_factory(bid=0.20, ask=0.45)
    decision = engine.evaluate(market, calculate_edge(0.7, market, CostConfig()),
                               RiskContext(balance=500, price_age_seconds=1, confidence=0.9))
    assert not decision.allowed
    assert decision.checks["spread"] == "WIDE"


def test_risk_denies_when_circuit_breaker_open(engine, market_factory):
    engine.stop("test")
    market = market_factory(bid=0.28, ask=0.30)
    decision = engine.evaluate(market, calculate_edge(0.7, market, CostConfig()),
                               RiskContext(balance=500, price_age_seconds=1, confidence=0.9))
    assert not decision.allowed
    assert decision.checks["circuit_breaker"] == "OPEN"


def test_risk_denies_incomplete_edge(engine, market_factory):
    market = market_factory()
    market.snapshot = None
    decision = engine.evaluate(market, calculate_edge(0.7, market, CostConfig()),
                               RiskContext(balance=500, price_age_seconds=1, confidence=0.9))
    assert not decision.allowed


def test_risk_denies_when_balance_too_small(engine, market_factory):
    market = market_factory(bid=0.28, ask=0.30)
    decision = engine.evaluate(market, calculate_edge(0.9, market, CostConfig()),
                               RiskContext(balance=0.5, price_age_seconds=1, confidence=0.95))
    assert not decision.allowed


def test_risk_size_never_exceeds_max_trade(engine, market_factory):
    market = market_factory(bid=0.01, ask=0.02)
    decision = engine.evaluate(market, calculate_edge(0.9, market, CostConfig()),
                               RiskContext(balance=100000, price_age_seconds=1, confidence=0.99))
    assert decision.size <= engine.limits.max_trade


def test_decision_serialises(engine, market_factory):
    market = market_factory(bid=0.28, ask=0.30)
    payload = engine.evaluate(market, calculate_edge(0.65, market, CostConfig()),
                              RiskContext(balance=500, price_age_seconds=1, confidence=0.9)).as_dict()
    assert payload["verdict"] in ("ALLOW", "DENY")
    assert isinstance(payload["checks"], dict)
