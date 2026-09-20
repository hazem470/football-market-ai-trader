"""Signal states and the auditable signal record."""
from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from src.strategy.edge import EdgeBreakdown


class SignalState(str, Enum):
    BUY = "BUY"
    SELL = "SELL"
    NO_TRADE = "NO_TRADE"
    INSUFFICIENT_DATA = "INSUFFICIENT_DATA"
    UNSUPPORTED = "UNSUPPORTED"

    @property
    def is_actionable(self) -> bool:
        return self in (SignalState.BUY, SignalState.SELL)


def new_signal_id(when: float | None = None) -> str:
    when = when or time.time()
    stamp = time.strftime("%Y-%m%d", time.localtime(when))
    return f"SIG-{stamp}-{uuid.uuid4().hex[:6].upper()}"


@dataclass
class Signal:
    """One fully traceable trading decision."""

    signal_id: str = field(default_factory=new_signal_id)
    created_at: float = field(default_factory=time.time)
    state: SignalState = SignalState.NO_TRADE
    market_id: str = ""
    match_key: str = ""
    market_type: str = ""
    selection: str = ""
    label: str = ""
    model_name: str = ""
    model_probability: float = 0.0
    calibrated_probability: float = 0.0
    market_price: float = 0.0
    edge: float = 0.0
    confidence: float = 0.0
    recommended_size: float = 0.0
    explanation: str = ""
    edge_breakdown: dict = field(default_factory=dict)
    trace: dict[str, Any] = field(default_factory=dict)
    reasons: list[str] = field(default_factory=list)

    @property
    def actionable(self) -> bool:
        return self.state.is_actionable

    def to_db_row(self) -> dict:
        return {
            "signal_id": self.signal_id,
            "market_id": self.market_id,
            "match_key": self.match_key,
            "created_at": self.created_at,
            "state": self.state.value,
            "market_type": self.market_type,
            "selection": self.selection,
            "model_probability": self.model_probability,
            "calibrated_probability": self.calibrated_probability,
            "market_price": self.market_price,
            "edge": self.edge,
            "confidence": self.confidence,
            "recommended_size": self.recommended_size,
            "explanation": self.explanation,
            "trace": self.trace,
        }

    def render(self) -> str:
        lines = [
            f"Signal ID      : {self.signal_id}",
            f"Created        : {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(self.created_at))}",
            f"Market         : {self.label or self.market_id}",
            f"Market type    : {self.market_type}",
            f"Selection      : {self.selection}",
            f"Model          : {self.model_name}",
            f"Model prob     : {self.model_probability:.2%}",
            f"Calibrated prob: {self.calibrated_probability:.2%}",
            f"Market price   : {self.market_price:.2%}",
            f"Realistic edge : {self.edge:+.2%}",
            f"Confidence     : {self.confidence:.2%}",
            f"Size           : {self.recommended_size:.2f}",
            f"Decision       : {self.state.value}",
            f"Reason         : {self.explanation}",
        ]
        if self.reasons:
            lines.append("Additional     : " + "; ".join(self.reasons))
        return "\n".join(lines)

    @classmethod
    def blocked(cls, state: SignalState, reason: str, **kwargs) -> Signal:
        signal = cls(state=state, explanation=reason, **kwargs)
        signal.reasons.append(reason)
        return signal

    @staticmethod
    def from_edge(edge: EdgeBreakdown, **kwargs) -> Signal:
        return Signal(
            model_probability=edge.model_probability,
            calibrated_probability=edge.calibrated_probability,
            market_price=edge.market_price,
            edge=edge.realistic_edge,
            edge_breakdown=edge.as_dict(),
            **kwargs,
        )
