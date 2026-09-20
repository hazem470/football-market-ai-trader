"""Execution interface shared by paper, shadow and live venues."""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum


class OrderStatus(str, Enum):
    PENDING = "PENDING"
    OPEN = "OPEN"
    PARTIAL = "PARTIAL"
    FILLED = "FILLED"
    REJECTED = "REJECTED"
    CANCELLED = "CANCELLED"


@dataclass
class OrderRequest:
    market_id: str
    token_id: str
    side: str                  # BUY | SELL
    price: float
    size: float                # in shares
    order_type: str = "GTC"
    signal_id: str = ""
    client_order_id: str = ""
    max_slippage: float = 0.01
    mode: str = "paper"


@dataclass
class OrderResult:
    order_id: str
    status: OrderStatus = OrderStatus.PENDING
    filled_size: float = 0.0
    avg_price: float = 0.0
    fee: float = 0.0
    slippage: float = 0.0
    message: str = ""
    exchange_order_id: str = ""
    raw: dict = field(default_factory=dict)

    @property
    def filled(self) -> bool:
        return self.status is OrderStatus.FILLED and self.filled_size > 0

    @property
    def notional(self) -> float:
        return self.filled_size * self.avg_price

    def as_dict(self) -> dict:
        return {
            "order_id": self.order_id,
            "status": self.status.value,
            "filled_size": round(self.filled_size, 6),
            "avg_price": round(self.avg_price, 6),
            "fee": round(self.fee, 6),
            "slippage": round(self.slippage, 6),
            "message": self.message,
            "exchange_order_id": self.exchange_order_id,
        }


class ExecutionVenue(ABC):
    """A place orders can go: a simulator, or the real exchange."""

    name = "venue"
    is_live = False

    @abstractmethod
    def submit(self, request: OrderRequest) -> OrderResult: ...

    @abstractmethod
    def cancel(self, order_id: str) -> bool: ...

    @abstractmethod
    def open_orders(self, market_id: str | None = None) -> list[dict]: ...

    def preflight(self) -> tuple[bool, list[str]]:
        return True, []

    def describe(self) -> dict:
        return {"name": self.name, "live": self.is_live}
