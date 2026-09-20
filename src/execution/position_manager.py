"""Position management: exits, edge decay, stop rules, resolution.

Exit rules are explicit and documented here. Nothing is "arbitrary": each rule
has a named condition and a reason string that lands in the signal trace.

Rules implemented (all configurable, all documented):
  1. EDGE_DISAPPEARED      - the realistic edge on the position folded to <= exit_min_edge
  2. PROBABILITY_REVERSED  - the model's calibrated probability moved against the position
                             by more than `probability_reversal_threshold`
  3. STOP_LOSS             - unrealised loss exceeds `stop_loss_fraction` of notional
  4. TAKE_PROFIT           - unrealised gain exceeds `take_profit_fraction` of notional
  5. TIME_EXIT             - fewer than `min_minutes_to_resolution` remain before resolution
  6. CIRCUIT_BREAKER       - the breaker tripped: optionally flatten everything
  7. RESOLUTION            - the market resolved; settle at the outcome
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum


class ExitReason(str, Enum):
    EDGE_DISAPPEARED = "EDGE_DISAPPEARED"
    PROBABILITY_REVERSED = "PROBABILITY_REVERSED"
    STOP_LOSS = "STOP_LOSS"
    TAKE_PROFIT = "TAKE_PROFIT"
    TIME_EXIT = "TIME_EXIT"
    CIRCUIT_BREAKER = "CIRCUIT_BREAKER"
    RESOLUTION = "RESOLUTION"
    NONE = "NONE"


@dataclass
class ExitRules:
    exit_min_edge: float = 0.0
    probability_reversal_threshold: float = 0.12
    stop_loss_fraction: float = 0.5
    take_profit_fraction: float = 0.8
    min_minutes_to_resolution: float = 10.0
    flatten_on_circuit_breaker: bool = False
    cost_config: dict = field(default_factory=dict)


@dataclass
class ManagedPosition:
    position_id: str
    market_id: str
    token_id: str
    entry_price: float
    size: float
    opened_at: float
    match_key: str = ""
    market_type: str = ""
    selection: str = ""
    model_probability: float = 0.0
    exposure_group: str = ""
    current_price: float | None = None
    current_probability: float | None = None
    resolution_time: float | None = None
    closed_at: float | None = None
    exit_price: float | None = None
    realized_pnl: float = 0.0
    exit_reason: str = ExitReason.NONE.value
    mode: str = "paper"

    @property
    def notional(self) -> float:
        return self.size * self.entry_price

    @property
    def is_open(self) -> bool:
        return self.closed_at is None

    def unrealized_pnl(self, price: float | None = None) -> float:
        mark = price if price is not None else self.current_price
        if mark is None:
            return 0.0
        return (mark - self.entry_price) * self.size

    def unrealized_fraction(self, price: float | None = None) -> float:
        if self.notional <= 0:
            return 0.0
        return self.unrealized_pnl(price) / self.notional

    def as_dict(self) -> dict:
        return {
            "position_id": self.position_id, "market_id": self.market_id,
            "token_id": self.token_id, "entry_price": round(self.entry_price, 6),
            "size": round(self.size, 6), "notional": round(self.notional, 4),
            "opened_at": self.opened_at, "closed_at": self.closed_at,
            "current_price": round(self.current_price, 6) if self.current_price else None,
            "exit_price": round(self.exit_price, 6) if self.exit_price else None,
            "unrealized_pnl": round(self.unrealized_pnl(), 4),
            "unrealized_fraction": round(self.unrealized_fraction(), 4),
            "realized_pnl": round(self.realized_pnl, 4),
            "model_probability": self.model_probability,
            "current_probability": self.current_probability,
            "market_type": self.market_type, "selection": self.selection,
            "match_key": self.match_key, "exposure_group": self.exposure_group,
            "exit_reason": self.exit_reason, "status": "OPEN" if self.is_open else "CLOSED",
            "mode": self.mode,
        }


@dataclass
class ExitDecision:
    should_exit: bool
    reason: ExitReason = ExitReason.NONE
    detail: str = ""
    urgency: str = "normal"      # normal | immediate

    def as_dict(self) -> dict:
        return {"should_exit": self.should_exit, "reason": self.reason.value,
                "detail": self.detail, "urgency": self.urgency}


def evaluate_exit(
    position: ManagedPosition,
    rules: ExitRules,
    current_edge: float | None = None,
    breaker_open: bool = False,
    now: float | None = None,
) -> ExitDecision:
    now = now or time.time()

    if not position.is_open:
        return ExitDecision(False, ExitReason.NONE, "position already closed")

    if breaker_open and rules.flatten_on_circuit_breaker:
        return ExitDecision(True, ExitReason.CIRCUIT_BREAKER,
                            "circuit breaker open and flatten_on_circuit_breaker is enabled",
                            urgency="immediate")

    if position.current_price is not None:
        fraction = position.unrealized_fraction()
        if fraction <= -abs(rules.stop_loss_fraction):
            return ExitDecision(True, ExitReason.STOP_LOSS,
                                f"unrealised loss {fraction:.1%} breached stop at "
                                f"{-abs(rules.stop_loss_fraction):.1%}")
        if fraction >= rules.take_profit_fraction:
            return ExitDecision(True, ExitReason.TAKE_PROFIT,
                                f"unrealised gain {fraction:.1%} reached take-profit at "
                                f"{rules.take_profit_fraction:.1%}")

    if current_edge is not None and current_edge <= rules.exit_min_edge:
        return ExitDecision(True, ExitReason.EDGE_DISAPPEARED,
                            f"remaining edge {current_edge:+.2%} at or below exit threshold "
                            f"{rules.exit_min_edge:.2%}")

    if position.current_probability is not None and position.model_probability:
        reversal = position.model_probability - position.current_probability
        if reversal >= rules.probability_reversal_threshold:
            return ExitDecision(True, ExitReason.PROBABILITY_REVERSED,
                                f"model probability fell {reversal:.1%} since entry "
                                f"(threshold {rules.probability_reversal_threshold:.1%})")

    if position.resolution_time is not None:
        minutes_left = (position.resolution_time - now) / 60.0
        if minutes_left <= rules.min_minutes_to_resolution:
            return ExitDecision(True, ExitReason.TIME_EXIT,
                                f"{minutes_left:.1f} minutes to resolution "
                                f"(floor {rules.min_minutes_to_resolution:.0f})")

    return ExitDecision(False, ExitReason.NONE, "no exit condition met")


def apply_exit(position: ManagedPosition, price: float, reason: ExitReason,
               when: float | None = None) -> float:
    position.exit_price = price
    position.closed_at = when or time.time()
    position.realized_pnl = (price - position.entry_price) * position.size
    position.exit_reason = reason.value
    return position.realized_pnl


@dataclass
class PositionManager:
    """Tracks managed positions and applies the exit rules."""

    rules: ExitRules = field(default_factory=ExitRules)
    database: object | None = None
    positions: dict[str, ManagedPosition] = field(default_factory=dict)
    exits: list[dict] = field(default_factory=list)

    def register(self, position: ManagedPosition) -> None:
        self.positions[position.position_id] = position

    def open_positions(self) -> list[ManagedPosition]:
        return [p for p in self.positions.values() if p.is_open]

    def refresh(self, market_id: str, price: float | None, probability: float | None = None) -> list[ManagedPosition]:
        updated = []
        for position in self.open_positions():
            if position.market_id != market_id:
                continue
            if price is not None:
                position.current_price = price
            if probability is not None:
                position.current_probability = probability
            updated.append(position)
        return updated

    def sweep(self, market_id: str, current_edge: float | None = None,
              breaker_open: bool = False, now: float | None = None) -> list[tuple[ManagedPosition, ExitDecision]]:
        decisions: list[tuple[ManagedPosition, ExitDecision]] = []
        for position in self.open_positions():
            if position.market_id != market_id:
                continue
            decision = evaluate_exit(position, self.rules, current_edge=current_edge,
                                     breaker_open=breaker_open, now=now)
            if decision.should_exit:
                decisions.append((position, decision))
        return decisions

    def record_exit(self, position: ManagedPosition, decision: ExitDecision) -> None:
        self.exits.append({"ts": time.time(), "position": position.as_dict(),
                           "decision": decision.as_dict()})
        if self.database is not None:
            self.database.update_position(
                position.position_id,
                closed_at=position.closed_at,
                exit_price=position.exit_price,
                realized_pnl=position.realized_pnl,
                status="CLOSED",
            )

    def summary(self) -> dict:
        opened = list(self.positions.values())
        closed = [p for p in opened if not p.is_open]
        return {
            "open": len([p for p in opened if p.is_open]),
            "closed": len(closed),
            "realized_pnl": round(sum(p.realized_pnl for p in closed), 4),
            "unrealized_pnl": round(sum(p.unrealized_pnl() for p in self.open_positions()), 4),
            "exposure": round(sum(p.notional for p in self.open_positions()), 4),
            "exit_reasons": {
                reason: sum(1 for p in closed if p.exit_reason == reason)
                for reason in {p.exit_reason for p in closed}
            },
        }
