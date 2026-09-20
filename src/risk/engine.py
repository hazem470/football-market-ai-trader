"""Risk engine: the single gate every order must pass.

Order of evaluation (short-circuits on the first DENY):
  1. circuit breaker
  2. data freshness
  3. edge / confidence thresholds
  4. liquidity / spread
  5. position-count, daily-exposure, daily-loss limits
  6. correlated exposure
  7. sizing (fractional Kelly, hard-capped)
"""
from __future__ import annotations

from dataclasses import dataclass, field

from src.markets.schema import Market
from src.risk.circuit_breaker import CircuitBreaker
from src.risk.correlation import group_key_for
from src.risk.exposure import ExposureTracker
from src.risk.limits import RiskDecision, RiskLimits
from src.risk.sizing import compute_size
from src.strategy.edge import EdgeBreakdown


@dataclass
class RiskContext:
    """Everything the risk engine needs that it cannot read from the DB."""

    balance: float
    price_age_seconds: float = 0.0
    confidence: float = 0.0
    exposure_group: str = ""
    min_trade: float = 1.0


@dataclass
class RiskEngine:
    limits: RiskLimits
    tracker: ExposureTracker | None = None
    breaker: CircuitBreaker = field(default_factory=CircuitBreaker)
    mode: str = "paper"

    def __post_init__(self) -> None:
        if self.tracker is None:
            self.tracker = ExposureTracker(self.limits)

    # ------------------------------------------------------------------ public
    def evaluate(self, market: Market, edge: EdgeBreakdown, context: RiskContext,
                 side: str = "BUY") -> RiskDecision:
        decision = RiskDecision()
        limits = self.limits

        # 1 - circuit breaker
        allowed, reason = self.breaker.allows_new_orders()
        decision.checks["circuit_breaker"] = "OPEN" if not allowed else "CLOSED"
        if not allowed:
            return decision.deny(reason)

        # 2 - data freshness
        if context.price_age_seconds > limits.max_price_age_seconds:
            decision.checks["price_freshness"] = "STALE"
            return decision.deny(
                f"price is {context.price_age_seconds:.0f}s old "
                f"(limit {limits.max_price_age_seconds:.0f}s)"
            )
        decision.checks["price_freshness"] = "OK"

        # 3 - edge / confidence
        if not edge.is_complete:
            decision.checks["edge"] = "INCOMPLETE"
            return decision.deny(f"edge could not be computed: {edge.incomplete_reason}")
        if edge.realistic_edge < limits.min_edge:
            decision.checks["edge"] = "LOW"
            return decision.deny(
                f"realistic edge {edge.realistic_edge:+.2%} below minimum {limits.min_edge:.2%}"
            )
        decision.checks["edge"] = "OK"
        if context.confidence < limits.min_confidence:
            decision.checks["confidence"] = "LOW"
            return decision.deny(
                f"confidence {context.confidence:.2f} below minimum {limits.min_confidence:.2f}"
            )
        decision.checks["confidence"] = "OK"

        # 4 - liquidity / spread
        snapshot = market.snapshot
        if snapshot is None:
            return decision.deny("no order-book snapshot")
        if snapshot.liquidity is not None and snapshot.liquidity < limits.min_liquidity:
            decision.checks["liquidity"] = "LOW"
            return decision.deny(
                f"liquidity {snapshot.liquidity:.2f} below minimum {limits.min_liquidity:.2f}"
            )
        decision.checks["liquidity"] = "OK"
        if snapshot.spread is not None:
            if snapshot.spread > limits.max_spread:
                decision.checks["spread"] = "WIDE"
                return decision.deny(
                    f"spread {snapshot.spread:.4f} above maximum {limits.max_spread:.4f}"
                )
        decision.checks["spread"] = "OK"

        # 5 - sizing first (needed by exposure checks)
        sizing = compute_size(
            probability=edge.calibrated_probability,
            price=edge.market_price,
            bankroll=context.balance,
            fraction=limits.kelly_fraction,
            max_trade=limits.max_trade,
            min_trade=context.min_trade,
        )
        if sizing.capped_size <= 0:
            decision.checks["sizing"] = "ZERO"
            return decision.deny(
                sizing.notes[0] if sizing.notes else "position sizing returned zero"
            )
        decision.checks["sizing"] = "OK"
        new_notional = sizing.capped_size

        # 6 - portfolio limits
        snapshot_exposure = self.tracker.snapshot(mode=self.mode, balance=context.balance)  # type: ignore[union-attr]
        ok, why = self.tracker.within_position_count(snapshot_exposure)  # type: ignore[union-attr]
        if not ok:
            decision.checks["open_positions"] = "AT_LIMIT"
            return decision.deny(why)
        decision.checks["open_positions"] = "OK"

        ok, why = self.tracker.within_daily_loss(snapshot_exposure)  # type: ignore[union-attr]
        if not ok:
            decision.checks["daily_loss"] = "BREACHED"
            return decision.deny(why)
        decision.checks["daily_loss"] = "OK"

        ok, why = self.tracker.within_daily_exposure(snapshot_exposure, new_notional)  # type: ignore[union-attr]
        if not ok:
            decision.checks["daily_exposure"] = "BREACHED"
            return decision.deny(why)
        decision.checks["daily_exposure"] = "OK"

        # 7 - correlation
        group = context.exposure_group or group_key_for(market)
        ok, why = self.tracker.within_correlated(snapshot_exposure, group, new_notional)  # type: ignore[union-attr]
        if not ok:
            decision.checks["correlation"] = "BREACHED"
            return decision.deny(why)
        decision.checks["correlation"] = "OK"

        # 8 - final notional check against the hard trade cap
        if new_notional > limits.max_trade + 1e-9:
            decision.checks["max_trade"] = "BREACHED"
            return decision.deny(f"size {new_notional:.2f} exceeds max_trade {limits.max_trade:.2f}")
        decision.checks["max_trade"] = "OK"
        if new_notional > context.balance:
            decision.checks["balance"] = "INSUFFICIENT"
            return decision.deny(f"size {new_notional:.2f} exceeds available balance {context.balance:.2f}")
        decision.checks["balance"] = "OK"

        decision.verdict = decision.verdict.ALLOW
        decision.size = round(new_notional, 4)
        decision.reasons.append(
            f"sized {new_notional:.2f} via {limits.kelly_fraction:.2f}-Kelly "
            f"(full Kelly {sizing.full_kelly:.4f})"
        )
        if sizing.binding_constraint:
            decision.reasons.append(f"binding constraint: {sizing.binding_constraint}")
        return decision

    def record_error(self, detail: str = "") -> None:
        self.breaker.record_error(detail)

    def record_success(self) -> None:
        self.breaker.record_success()

    def on_rejection(self, detail: str = "") -> None:
        self.breaker.record_order_rejection(detail)

    def stop(self, reason: str = "manual stop") -> None:
        self.breaker.stop(reason)

    def as_dict(self) -> dict:
        return {
            "limits": {
                "capital": self.limits.capital,
                "max_trade": self.limits.max_trade,
                "max_daily_exposure": self.limits.max_daily_exposure,
                "max_daily_loss": self.limits.max_daily_loss,
                "max_open_positions": self.limits.max_open_positions,
                "min_edge": self.limits.min_edge,
                "min_confidence": self.limits.min_confidence,
                "kelly_fraction": self.limits.kelly_fraction,
            },
            "breaker": self.breaker.as_dict(),
            "exposure": self.tracker.snapshot(mode=self.mode).as_dict() if self.tracker else {},  # type: ignore[union-attr]
        }
