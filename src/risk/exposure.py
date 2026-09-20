"""Exposure accounting: daily exposure, open positions, loss budget."""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import datetime, timezone

from src.risk.limits import RiskLimits


def start_of_day_utc(when: float | None = None) -> float:
    when = when or time.time()
    moment = datetime.fromtimestamp(when, tz=timezone.utc)
    midnight = moment.replace(hour=0, minute=0, second=0, microsecond=0)
    return midnight.timestamp()


@dataclass
class ExposureSnapshot:
    open_positions: int = 0
    open_exposure: float = 0.0
    today_exposure: float = 0.0
    today_realized_pnl: float = 0.0
    balance: float = 0.0
    correlated_exposure: dict[str, float] = field(default_factory=dict)

    @property
    def today_loss(self) -> float:
        """Positive number = how much has been lost today."""
        return max(0.0, -self.today_realized_pnl)

    def as_dict(self) -> dict:
        return {
            "open_positions": self.open_positions,
            "open_exposure": round(self.open_exposure, 4),
            "today_exposure": round(self.today_exposure, 4),
            "today_realized_pnl": round(self.today_realized_pnl, 4),
            "today_loss": round(self.today_loss, 4),
            "balance": round(self.balance, 4),
            "correlated_exposure": {k: round(v, 4) for k, v in self.correlated_exposure.items()},
        }


class ExposureTracker:
    """Maintains exposure state, backed by the database when available."""

    def __init__(self, limits: RiskLimits, database=None) -> None:
        self.limits = limits
        self.db = database
        self._correlated: dict[str, float] = {}

    # ------------------------------------------------------------------ state
    def snapshot(self, mode: str | None = None, balance: float | None = None) -> ExposureSnapshot:
        day_start = start_of_day_utc()
        if self.db is None:
            return ExposureSnapshot(balance=balance or self.limits.capital,
                                    correlated_exposure=dict(self._correlated))
        open_positions = self.db.list_positions(status="OPEN", mode=mode)
        open_exposure = sum(float(p.get("size") or 0) * float(p.get("entry_price") or 0) for p in open_positions)
        today_positions = self.db.query(
            "SELECT COALESCE(SUM(size * entry_price), 0) AS exposure FROM positions WHERE opened_at >= ?",
            (day_start,),
        )
        today_exposure = float(today_positions[0]["exposure"]) if today_positions else 0.0
        realized = self.db.realized_pnl_since(day_start, mode=mode)
        correlated: dict[str, float] = {}
        for position in open_positions:
            payload = position.get("payload_json")
            group = ""
            if payload:
                try:
                    import json

                    group = str(json.loads(payload).get("exposure_group", ""))
                except Exception:
                    group = ""
            key = group or position.get("match_key") or position.get("market_id") or "unknown"
            notional = float(position.get("size") or 0) * float(position.get("entry_price") or 0)
            correlated[key] = correlated.get(key, 0.0) + notional
        self._correlated = correlated
        return ExposureSnapshot(
            open_positions=len(open_positions),
            open_exposure=open_exposure,
            today_exposure=today_exposure,
            today_realized_pnl=realized,
            balance=balance if balance is not None else self.limits.capital,
            correlated_exposure=correlated,
        )

    def register(self, group: str, notional: float) -> None:
        self._correlated[group] = self._correlated.get(group, 0.0) + notional

    def release(self, group: str, notional: float) -> None:
        remaining = self._correlated.get(group, 0.0) - notional
        if remaining <= 1e-9:
            self._correlated.pop(group, None)
        else:
            self._correlated[group] = remaining

    # ------------------------------------------------------------------ checks
    def within_daily_loss(self, snapshot: ExposureSnapshot) -> tuple[bool, str]:
        if snapshot.today_loss > self.limits.max_daily_loss:
            return False, (
                f"daily loss {snapshot.today_loss:.2f} exceeds limit {self.limits.max_daily_loss:.2f}"
            )
        return True, ""

    def within_daily_exposure(self, snapshot: ExposureSnapshot, new_notional: float) -> tuple[bool, str]:
        projected = snapshot.today_exposure + new_notional
        if projected > self.limits.max_daily_exposure:
            return False, (
                f"projected daily exposure {projected:.2f} exceeds limit "
                f"{self.limits.max_daily_exposure:.2f}"
            )
        return True, ""

    def within_position_count(self, snapshot: ExposureSnapshot) -> tuple[bool, str]:
        if snapshot.open_positions >= self.limits.max_open_positions:
            return False, (
                f"open positions {snapshot.open_positions} at limit {self.limits.max_open_positions}"
            )
        return True, ""

    def within_correlated(self, snapshot: ExposureSnapshot, group: str, new_notional: float) -> tuple[bool, str]:
        current = snapshot.correlated_exposure.get(group, 0.0)
        projected = current + new_notional
        if projected > self.limits.max_correlated_exposure:
            return False, (
                f"correlated exposure for '{group}' would reach {projected:.2f} "
                f"(limit {self.limits.max_correlated_exposure:.2f})"
            )
        return True, ""
