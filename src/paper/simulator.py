"""Paper-trading venue + portfolio simulator.

Behaves as close to live as we can without spending money: it consumes the REAL
order book, applies the REAL spread, walks the book for slippage, models partial
fills and rejections, and enforces the same risk limits the live path uses.

All randomness is seeded so a paper run is reproducible.
"""
from __future__ import annotations

import random
import time
from dataclasses import dataclass, field

from src.execution.base import ExecutionVenue, OrderRequest, OrderResult, OrderStatus
from src.markets.orderbook import simulate_fill


@dataclass
class PaperConfig:
    starting_balance: float = 100.0
    rejection_rate: float = 0.02
    min_fill_ratio: float = 0.25
    fixed_slippage: float = 0.005
    slippage_model: str = "spread"
    fee_rate: float = 0.0
    seed: int = 20260101
    latency_ms: float = 250.0


@dataclass
class SimulatedPosition:
    position_id: str
    market_id: str
    token_id: str
    side: str
    size: float
    entry_price: float
    opened_at: float
    signal_id: str = ""
    match_key: str = ""
    market_type: str = ""
    selection: str = ""
    model_probability: float = 0.0
    exit_price: float | None = None
    closed_at: float | None = None
    realized_pnl: float = 0.0
    exposure_group: str = ""

    @property
    def notional(self) -> float:
        return self.size * self.entry_price

    @property
    def is_open(self) -> bool:
        return self.closed_at is None

    def unrealized(self, current_price: float | None) -> float:
        if current_price is None:
            return 0.0
        return (current_price - self.entry_price) * self.size

    def close(self, price: float, when: float | None = None) -> float:
        self.exit_price = price
        self.closed_at = when or time.time()
        self.realized_pnl = (price - self.entry_price) * self.size
        return self.realized_pnl

    def as_dict(self) -> dict:
        return {
            "position_id": self.position_id, "market_id": self.market_id, "token_id": self.token_id,
            "side": self.side, "size": round(self.size, 6),
            "entry_price": round(self.entry_price, 6),
            "exit_price": round(self.exit_price, 6) if self.exit_price is not None else None,
            "opened_at": self.opened_at, "closed_at": self.closed_at,
            "realized_pnl": round(self.realized_pnl, 6),
            "notional": round(self.notional, 6),
            "signal_id": self.signal_id, "match_key": self.match_key,
            "market_type": self.market_type, "selection": self.selection,
            "model_probability": self.model_probability,
            "status": "CLOSED" if self.closed_at else "OPEN",
        }


@dataclass
class Portfolio:
    """Virtual capital account."""

    starting_balance: float = 100.0
    cash: float = 100.0
    realized_pnl: float = 0.0
    fees_paid: float = 0.0
    positions: list[SimulatedPosition] = field(default_factory=list)

    @property
    def open_positions(self) -> list[SimulatedPosition]:
        return [p for p in self.positions if p.is_open]

    @property
    def closed_positions(self) -> list[SimulatedPosition]:
        return [p for p in self.positions if not p.is_open]

    @property
    def open_exposure(self) -> float:
        return sum(p.notional for p in self.open_positions)

    @property
    def equity(self) -> float:
        return self.cash + self.open_exposure

    def unrealized_pnl(self, prices: dict[str, float]) -> float:
        return sum(p.unrealized(prices.get(p.market_id)) for p in self.open_positions)

    def apply_fill(self, position: SimulatedPosition) -> None:
        self.cash -= position.notional
        self.positions.append(position)

    def settle(self, position: SimulatedPosition, price: float, fee: float = 0.0) -> float:
        pnl = position.close(price)
        self.cash += position.size * price - fee
        self.realized_pnl += pnl - fee
        self.fees_paid += fee
        return pnl - fee

    def resolve(self, market_id: str, outcome: int) -> float:
        """Settle all open positions in a market at resolution (1 or 0)."""
        settled = 0.0
        for position in self.open_positions:
            if position.market_id != market_id:
                continue
            settled += self.settle(position, float(outcome))
        return settled

    def as_dict(self, prices: dict[str, float] | None = None) -> dict:
        prices = prices or {}
        return {
            "starting_balance": round(self.starting_balance, 4),
            "cash": round(self.cash, 4),
            "equity": round(self.equity, 4),
            "realized_pnl": round(self.realized_pnl, 4),
            "unrealized_pnl": round(self.unrealized_pnl(prices), 4),
            "open_exposure": round(self.open_exposure, 4),
            "open_positions": len(self.open_positions),
            "closed_positions": len(self.closed_positions),
            "fees_paid": round(self.fees_paid, 6),
            "total_pnl": round(self.realized_pnl + self.unrealized_pnl(prices), 4),
        }


@dataclass
class PaperVenue(ExecutionVenue):
    """Simulated exchange. Uses the real book when available."""

    config: PaperConfig = field(default_factory=PaperConfig)
    #: Any object exposing `.book(token_id)` / `.snapshot(token_id)` - normally the
    #: CLOB provider. Annotated so dataclass actually creates the field; an
    #: unannotated assignment here was silently dropped and broke paper mode.
    clob: object | None = None
    portfolio: Portfolio = field(default_factory=Portfolio)
    name: str = "paper"
    is_live: bool = False
    rng: random.Random = field(default=None)  # type: ignore[assignment]
    fills: list[dict] = field(default_factory=list)

    def __post_init__(self) -> None:
        if self.rng is None:
            self.rng = random.Random(self.config.seed)
        if self.portfolio.starting_balance != self.config.starting_balance:
            self.portfolio = Portfolio(starting_balance=self.config.starting_balance,
                                       cash=self.config.starting_balance)

    # ------------------------------------------------------------------ trading
    def submit(self, request: OrderRequest) -> OrderResult:
        # simulated exchange rejection
        if self.rng.random() < self.config.rejection_rate:
            return OrderResult(order_id="", status=OrderStatus.REJECTED,
                               message="simulated exchange rejection", raw={"simulated": True})

        price = request.price
        slippage = 0.0
        filled_size = request.size
        book_depth_limited = False

        if self.clob is not None and request.token_id:
            try:
                book = self.clob.book(request.token_id)
                preview = simulate_fill(book, request.side, request.size * request.price,
                                        fee_rate=self.config.fee_rate)
                if preview["avg_price"] is not None and preview["filled_size"] > 0:
                    price = preview["avg_price"]
                    slippage = preview["slippage"] or 0.0
                    if preview["filled_size"] < request.size:
                        filled_size = preview["filled_size"]
                        book_depth_limited = True
                else:
                    return OrderResult(order_id="", status=OrderStatus.REJECTED,
                                       message="no liquidity available in the book",
                                       raw={"simulated": True})
            except Exception as exc:
                return OrderResult(order_id="", status=OrderStatus.REJECTED,
                                   message=f"book unavailable in simulation: {exc}",
                                   raw={"simulated": True})
        else:
            slippage = (
                self.config.fixed_slippage if self.config.slippage_model == "fixed"
                else round(self.rng.uniform(0, self.config.fixed_slippage), 6)
            )
            price = min(0.999, price + slippage)

        # Partial-fill handling. A real exchange fills whatever the book can
        # absorb and reports the shortfall, so a book-limited fill is returned as
        # PARTIAL. Only when the simulated liquidity is *not* the binding factor
        # (i.e. the requested size itself is notional dust) do we refuse.
        if filled_size < request.size * self.config.min_fill_ratio and not book_depth_limited:
            return OrderResult(
                order_id="", status=OrderStatus.REJECTED,
                message=(f"only {filled_size:.2f} of {request.size:.2f} shares would fill "
                         f"(below min_fill_ratio {self.config.min_fill_ratio}) - order rejected"),
                raw={"simulated": True, "available_size": round(filled_size, 6)},
            )

        notional = filled_size * price
        if notional > self.portfolio.cash + 1e-9:
            return OrderResult(order_id="", status=OrderStatus.REJECTED,
                               message=f"insufficient simulated cash: need {notional:.2f}, "
                                       f"have {self.portfolio.cash:.2f}",
                               raw={"simulated": True})

        fee = notional * self.config.fee_rate
        position = SimulatedPosition(
            position_id=f"PAP-{int(time.time() * 1000)}-{self.rng.randrange(1000):03d}",
            market_id=request.market_id,
            token_id=request.token_id,
            side=request.side,
            size=filled_size,
            entry_price=price,
            opened_at=time.time(),
            signal_id=request.signal_id,
            exposure_group=request.client_order_id,
        )
        self.portfolio.apply_fill(position)
        self.portfolio.fees_paid += fee

        status = OrderStatus.PARTIAL if book_depth_limited else OrderStatus.FILLED
        result = OrderResult(
            order_id=position.position_id, status=status, filled_size=filled_size,
            avg_price=price, fee=fee, slippage=slippage,
            message=("partially filled against available depth" if book_depth_limited else "filled"),
            raw={"simulated": True, "book_depth_limited": book_depth_limited},
        )
        self.fills.append({"ts": time.time(), **result.as_dict(), "notional": round(notional, 4)})
        return result

    def close_position(self, position: SimulatedPosition, price: float | None = None) -> float:
        """Exit at the current executable SELL price."""
        exit_price = price
        if exit_price is None and self.clob is not None and position.token_id:
            try:
                snapshot = self.clob.snapshot(position.token_id)
                exit_price = snapshot.executable_sell_price
            except Exception:
                exit_price = None
        if exit_price is None:
            exit_price = position.entry_price
        return self.portfolio.settle(position, exit_price)

    def cancel(self, order_id: str) -> bool:
        return True  # simulated: nothing resting

    def open_orders(self, market_id: str | None = None) -> list[dict]:
        return []

    def preflight(self) -> tuple[bool, list[str]]:
        problems = []
        if self.config.starting_balance <= 0:
            problems.append("paper starting_balance must be > 0")
        return (not problems), problems

    def describe(self) -> dict:
        return {
            "name": self.name, "live": False, "config": self.config.__dict__,
            "portfolio": self.portfolio.as_dict(),
        }
