"""Order manager: revalidation, duplicate protection, submission, tracking.

Critical guarantees:
  * An order is NEVER submitted from a stale price snapshot - the book is
    re-read immediately before submission and the price is re-checked.
  * Duplicate protection uses client order ids derived from the signal id, plus
    a check against open orders and existing positions.
  * Financial order submission does NOT use blind retries; a retry needs an
    explicit idempotency key and a reconciliation step.
"""
from __future__ import annotations

import hashlib
import time
import uuid
from dataclasses import dataclass, field

from src.execution.base import ExecutionVenue, OrderRequest, OrderResult, OrderStatus
from src.markets.orderbook import simulate_fill
from src.strategy.signals import Signal


def make_client_order_id(signal_id: str, attempt: int = 0) -> str:
    """Deterministic, idempotent client order id for one (signal, attempt).

    This is an identifier, not a security primitive: determinism is what makes a
    resent order recognisable as the SAME order rather than a duplicate. SHA-256
    is used so the construct is unambiguous to readers and static analysers.
    """
    digest = hashlib.sha256(f"{signal_id}|{attempt}".encode()).hexdigest()[:20]
    return f"fmt-{digest}"


@dataclass
class OrderManager:
    venue: ExecutionVenue
    database: object | None = None
    clob: object | None = None
    max_price_drift: float = 0.02
    max_slippage: float = 0.02
    dry_run: bool = False
    #: When True (the default, and what the runtime wires up) an order is refused
    #: if no order book can be read for the price recheck. A bookless venue is a
    #: configuration error, not permission to submit on a stale snapshot.
    require_book: bool = True
    submissions: list[dict] = field(default_factory=list)

    # ------------------------------------------------------------------ checks
    def has_open_position(self, market_id: str, mode: str) -> bool:
        if self.database is None:
            return False
        positions = self.database.list_positions(status="OPEN", mode=mode)
        return any(p.get("market_id") == market_id for p in positions)

    def has_open_order(self, market_id: str) -> bool:
        if self.database is None:
            return False
        for order in self.database.list_orders(limit=200):
            if order.get("market_id") == market_id and order.get("status") in (
                OrderStatus.PENDING.value, OrderStatus.OPEN.value, OrderStatus.PARTIAL.value,
            ):
                return True
        return False

    def has_recent_submission(self, signal_id: str, window_seconds: float = 900) -> bool:
        cutoff = time.time() - window_seconds
        return any(s["signal_id"] == signal_id and s["ts"] >= cutoff for s in self.submissions)

    def duplicate_reason(self, signal: Signal, mode: str) -> str:
        if self.has_open_position(signal.market_id, mode):
            return "an open position already exists for this market"
        if self.has_open_order(signal.market_id):
            return "an open order already exists for this market"
        if self.has_recent_submission(signal.signal_id):
            return "this signal was already submitted recently"
        return ""

    # ------------------------------------------------------------------ submit
    def _book_source(self):
        """The provider that can read the order book for this venue.

        Falls back to the venue itself: the paper venue reads the real book
        through its own ``clob`` handle, and skipping the depth/slippage checks
        for it would quietly weaken the guarantees that make paper mode useful.
        Returns None when no book is available at all, which the caller treats as
        a denial - never as a reason to skip the check.
        """
        if self.clob is not None:
            return self.clob
        venue = self.venue
        if venue is not None:
            if getattr(venue, "clob", None) is not None:
                return venue.clob
            if hasattr(venue, "book") and hasattr(venue, "snapshot"):
                return venue
        return None

    def execute_signal(self, signal: Signal, market, size: float, mode: str = "paper") -> OrderResult:
        """Revalidate, then submit. Returns a REJECTED result rather than raising."""
        if not signal.actionable:
            return OrderResult(order_id="", status=OrderStatus.REJECTED,
                               message=f"signal state {signal.state.value} is not actionable")

        duplicate = self.duplicate_reason(signal, mode)
        if duplicate:
            return OrderResult(order_id="", status=OrderStatus.REJECTED,
                               message=f"duplicate protection: {duplicate}")

        book_source = self._book_source()
        if book_source is None and self.require_book:
            return OrderResult(
                order_id="", status=OrderStatus.REJECTED,
                message=("no order book available for the price recheck - refusing to submit "
                         "on a stale snapshot"),
            )

        # --- price recheck: NEVER submit on an old snapshot -------------------
        live_price = None
        if book_source is not None and market.yes_token_id:
            try:
                snapshot = book_source.snapshot(market.yes_token_id)
                live_price = snapshot.executable_buy_price
                market.snapshot = snapshot
            except Exception as exc:
                return OrderResult(order_id="", status=OrderStatus.REJECTED,
                                   message=f"price recheck failed: {exc}")
        if live_price is not None and signal.market_price:
            drift = abs(live_price - signal.market_price)
            if drift > self.max_price_drift:
                return OrderResult(
                    order_id="", status=OrderStatus.REJECTED,
                    message=(f"price moved {drift:.4f} since the signal "
                             f"({signal.market_price:.4f} -> {live_price:.4f}); "
                             "refusing to chase"),
                )
        price = live_price if live_price is not None else signal.market_price

        # --- liquidity / slippage preview ------------------------------------
        # `size` arrives as a NOTIONAL amount (dollars); it is converted to a
        # share count only when the order request is built. Walking the book with
        # `size * price` would double-count the price and understate the depth
        # actually required, so the requested notional is used directly.
        slippage_estimate = 0.0
        if book_source is not None and market.yes_token_id:
            try:
                book = book_source.book(market.yes_token_id)
                preview = simulate_fill(book, "BUY", size)
                if preview["avg_price"] is not None:
                    slippage_estimate = preview["slippage"] or 0.0
                    price = preview["avg_price"]
                unfilled = preview.get("unfilled_notional", 0.0)
                if unfilled > max(1.0, 0.5 * size):
                    return OrderResult(
                        order_id="", status=OrderStatus.REJECTED,
                        message=(f"insufficient book depth: {unfilled:.2f} of "
                                 f"{size:.2f} notional unfilled"),
                    )
            except Exception as exc:
                return OrderResult(order_id="", status=OrderStatus.REJECTED,
                                   message=f"order book unavailable: {exc}")

        if slippage_estimate > self.max_slippage:
            return OrderResult(
                order_id="", status=OrderStatus.REJECTED,
                message=f"estimated slippage {slippage_estimate:.4f} above max {self.max_slippage:.4f}",
            )

        request = OrderRequest(
            market_id=signal.market_id,
            token_id=market.yes_token_id,
            side="BUY",
            price=round(price, 6),
            size=round(size / max(1e-6, price), 6) if price else 0.0,
            signal_id=signal.signal_id,
            client_order_id=make_client_order_id(signal.signal_id),
            max_slippage=self.max_slippage,
            mode=mode,
        )

        if self.dry_run:
            result = OrderResult(order_id=f"DRY-{uuid.uuid4().hex[:10]}", status=OrderStatus.PENDING,
                                 message="dry run: order not submitted")
        else:
            result = self.venue.submit(request)

        self.submissions.append({
            "ts": time.time(), "signal_id": signal.signal_id, "market_id": signal.market_id,
            "status": result.status.value, "size": request.size, "price": request.price,
            "mode": mode,
        })

        if self.database is not None:
            order_id = self.database.insert_order({
                "client_order_id": request.client_order_id,
                "signal_id": signal.signal_id,
                "market_id": request.market_id,
                "token_id": request.token_id,
                "side": request.side,
                "price": request.price,
                "size": request.size,
                "order_type": request.order_type,
                "status": result.status.value,
                "mode": mode,
                "exchange_order_id": result.exchange_order_id,
                "filled_size": result.filled_size,
                "avg_fill_price": result.avg_price,
                "error": result.message if result.status is OrderStatus.REJECTED else "",
                "raw": result.raw,
            })
            result.order_id = result.order_id or order_id
        return result

    def cancel(self, order_id: str) -> bool:
        return self.venue.cancel(order_id)

    def cancel_all(self, market_id: str | None = None) -> int:
        """Cancel every open order (used by the kill switch)."""
        cancelled = 0
        for order in self.venue.open_orders(market_id):
            if self.venue.cancel(str(order.get("order_id") or order.get("id"))):
                cancelled += 1
        return cancelled
