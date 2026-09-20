"""Circuit breaker / kill switch.

Triggers stop NEW orders (and optionally cancel pending ones). Monitoring
continues so the operator can see what happened.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum


class BreakerState(str, Enum):
    CLOSED = "CLOSED"          # normal operation
    OPEN = "OPEN"              # tripped: no new orders
    HALF_OPEN = "HALF_OPEN"    # recovery probe window


@dataclass
class BreakerConfig:
    enable: bool = True
    max_consecutive_errors: int = 5
    max_stale_data_minutes: float = 180.0
    abnormal_price_move: float = 0.25
    max_order_rejections: int = 3
    max_daily_loss_multiplier: float = 1.0


@dataclass
class BreakerEvent:
    ts: float
    reason: str
    detail: str = ""
    severity: str = "CRITICAL"

    def as_dict(self) -> dict:
        return {"ts": self.ts, "reason": self.reason, "detail": self.detail, "severity": self.severity}


@dataclass
class CircuitBreaker:
    config: BreakerConfig = field(default_factory=BreakerConfig)
    state: BreakerState = BreakerState.CLOSED
    events: list[BreakerEvent] = field(default_factory=list)
    consecutive_errors: int = 0
    consecutive_rejections: int = 0
    manual_stop: bool = False
    tripped_at: float | None = None

    # ---------------------------------------------------------------- triggers
    def trip(self, reason: str, detail: str = "", severity: str = "CRITICAL") -> BreakerEvent:
        event = BreakerEvent(ts=time.time(), reason=reason, detail=detail, severity=severity)
        self.events.append(event)
        if self.config.enable:
            self.state = BreakerState.OPEN
            self.tripped_at = event.ts
        return event

    def record_error(self, detail: str = "") -> BreakerEvent | None:
        self.consecutive_errors += 1
        if self.config.enable and self.consecutive_errors >= self.config.max_consecutive_errors:
            return self.trip("consecutive_errors", f"{self.consecutive_errors} errors; last={detail}")
        return None

    def record_success(self) -> None:
        self.consecutive_errors = 0
        self.consecutive_rejections = 0
        if self.state is BreakerState.HALF_OPEN:
            self.state = BreakerState.CLOSED

    def record_order_rejection(self, detail: str = "") -> BreakerEvent | None:
        self.consecutive_rejections += 1
        if self.config.enable and self.consecutive_rejections >= self.config.max_order_rejections:
            return self.trip("repeated_order_rejection",
                             f"{self.consecutive_rejections} rejections; last={detail}")
        return None

    def check_stale_data(self, age_seconds: float, label: str = "market data") -> BreakerEvent | None:
        limit = self.config.max_stale_data_minutes * 60
        if age_seconds > limit:
            return self.trip("stale_data", f"{label} age {age_seconds / 60:.1f}min > {limit / 60:.1f}min")
        return None

    def check_price_move(self, previous: float, current: float, token: str = "") -> BreakerEvent | None:
        if previous <= 0:
            return None
        move = abs(current - previous) / previous
        if move > self.config.abnormal_price_move:
            return self.trip("abnormal_price_move",
                             f"{token or 'token'} moved {move:.1%} ({previous:.3f} -> {current:.3f})",
                             severity="WARNING")
        return None

    def check_daily_loss(self, loss: float, limit: float) -> BreakerEvent | None:
        if limit > 0 and loss > limit * self.config.max_daily_loss_multiplier:
            return self.trip("daily_loss_limit", f"day loss {loss:.2f} > {limit:.2f}")
        return None

    def stop(self, reason: str = "manual stop") -> BreakerEvent:
        self.manual_stop = True
        return self.trip("manual_stop", reason)

    def reset(self, operator: str = "") -> None:
        self.state = BreakerState.CLOSED
        self.consecutive_errors = 0
        self.consecutive_rejections = 0
        self.manual_stop = False
        self.tripped_at = None
        self.events.append(BreakerEvent(ts=time.time(), reason="reset", detail=operator, severity="INFO"))

    # ------------------------------------------------------------------ verdict
    @property
    def is_open(self) -> bool:
        return self.state is BreakerState.OPEN or self.manual_stop

    def allows_new_orders(self) -> tuple[bool, str]:
        if self.manual_stop:
            return False, "manual stop active - no new orders"
        if self.state is BreakerState.OPEN:
            last = self.events[-1].reason if self.events else "unknown"
            return False, f"circuit breaker OPEN ({last}) - no new orders"
        return True, "ok"

    def as_dict(self) -> dict:
        return {
            "state": self.state.value,
            "manual_stop": self.manual_stop,
            "consecutive_errors": self.consecutive_errors,
            "consecutive_rejections": self.consecutive_rejections,
            "tripped_at": self.tripped_at,
            "events": [e.as_dict() for e in self.events[-20:]],
        }

    def render(self) -> str:
        lines = [f"Circuit breaker: {self.state.value} (manual_stop={self.manual_stop})"]
        for event in self.events[-10:]:
            stamp = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(event.ts))
            lines.append(f"  {stamp} [{event.severity}] {event.reason}: {event.detail}")
        return "\n".join(lines)
