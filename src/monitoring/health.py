"""Data-quality / provider health monitoring.

Health feeds the risk layer: CRITICAL data means NO TRADE.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum


class Health(str, Enum):
    HEALTHY = "HEALTHY"
    WARNING = "WARNING"
    CRITICAL = "CRITICAL"

    @property
    def rank(self) -> int:
        return {Health.HEALTHY: 0, Health.WARNING: 1, Health.CRITICAL: 2}[self]


@dataclass
class HealthCheck:
    name: str
    state: Health = Health.HEALTHY
    detail: str = ""
    checked_at: float = field(default_factory=time.time)

    @property
    def age_seconds(self) -> float:
        return max(0.0, time.time() - self.checked_at)


class HealthRegistry:
    """Tracks provider/data health and aggregates a single verdict."""

    def __init__(self) -> None:
        self._checks: dict[str, HealthCheck] = {}

    def record(self, name: str, state: Health, detail: str = "") -> HealthCheck:
        check = HealthCheck(name=name, state=state, detail=detail)
        self._checks[name] = check
        return check

    def ok(self, name: str, detail: str = "") -> HealthCheck:
        return self.record(name, Health.HEALTHY, detail)

    def warn(self, name: str, detail: str = "") -> HealthCheck:
        return self.record(name, Health.WARNING, detail)

    def critical(self, name: str, detail: str = "") -> HealthCheck:
        return self.record(name, Health.CRITICAL, detail)

    def get(self, name: str) -> HealthCheck | None:
        return self._checks.get(name)

    @property
    def checks(self) -> list[HealthCheck]:
        return sorted(self._checks.values(), key=lambda c: c.name)

    def overall(self) -> Health:
        state = Health.HEALTHY
        for check in self._checks.values():
            if check.state.rank > state.rank:
                state = check.state
        return state

    def blocking(self) -> list[HealthCheck]:
        """CRITICAL checks - any of these forbids trading."""
        return [c for c in self.checks if c.state is Health.CRITICAL]

    def as_dict(self) -> dict:
        return {
            "overall": self.overall().value,
            "checks": [
                {
                    "name": c.name,
                    "state": c.state.value,
                    "detail": c.detail,
                    "age_seconds": round(c.age_seconds, 1),
                }
                for c in self.checks
            ],
        }

    def trading_allowed(self) -> tuple[bool, str]:
        blocking = self.blocking()
        if blocking:
            names = ", ".join(f"{c.name}({c.detail})" for c in blocking)
            return False, f"CRITICAL data health: {names}"
        return True, "ok"
