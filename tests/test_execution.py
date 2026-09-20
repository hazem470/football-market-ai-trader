"""Execution: price rechecks, duplicate protection, no blind retries."""
from __future__ import annotations

import time

import pytest

from src.execution.base import ExecutionVenue, OrderRequest, OrderResult, OrderStatus
from src.execution.order_manager import OrderManager, make_client_order_id
from src.paper.simulator import PaperConfig, PaperVenue
from src.strategy.signals import Signal, SignalState


class RecordingVenue(ExecutionVenue):
    name = "recording"

    def __init__(self, result: OrderResult | None = None) -> None:
        self.submitted: list[OrderRequest] = []
        self._result = result or OrderResult(order_id="O1", status=OrderStatus.FILLED,
                                             filled_size=10.0, avg_price=0.3)

    def submit(self, request: OrderRequest) -> OrderResult:
        self.submitted.append(request)
        return self._result

    def cancel(self, order_id: str) -> bool:
        return True

    def open_orders(self, market_id: str | None = None) -> list[dict]:
        return []


class FixedBook:
    def __init__(self, bid: float, ask: float) -> None:
        self._bid, self._ask = bid, ask

    def snapshot(self, _token, **_kwargs):
        from src.markets.schema import MarketSnapshot

        return MarketSnapshot(ts=time.time(), bid=self._bid, ask=self._ask)

    def book(self, _token):
        return {"bids": [{"price": str(self._bid), "size": "1000"}],
                "asks": [{"price": str(self._ask), "size": "1000"}]}


def buy_signal(market_id="M1", price=0.30, edge=0.1) -> Signal:
    return Signal(signal_id="SIG-1", state=SignalState.BUY, market_id=market_id, market_price=price,
                  edge=edge, calibrated_probability=0.5, confidence=0.9)


def test_client_order_id_is_deterministic():
    assert make_client_order_id("SIG-1") == make_client_order_id("SIG-1")
    assert make_client_order_id("SIG-1") != make_client_order_id("SIG-2")


def test_non_actionable_signal_is_not_submitted(market_factory):
    venue = RecordingVenue()
    manager = OrderManager(venue=venue, require_book=False)
    result = manager.execute_signal(Signal(state=SignalState.NO_TRADE), market_factory(), 10.0)
    assert result.status is OrderStatus.REJECTED
    assert not venue.submitted


def test_duplicate_position_blocks_a_second_order(market_factory, database):
    database.insert_position({"market_id": "M1", "size": 1.0, "entry_price": 0.3,
                              "status": "OPEN", "mode": "paper"})
    manager = OrderManager(venue=RecordingVenue(), database=database, require_book=False)
    result = manager.execute_signal(buy_signal(), market_factory(), 10.0, mode="paper")
    assert result.status is OrderStatus.REJECTED
    assert "duplicate" in result.message


def test_duplicate_open_order_blocks_submission(market_factory, database):
    database.insert_order({"market_id": "M1", "status": "OPEN", "size": 1.0, "price": 0.3})
    manager = OrderManager(venue=RecordingVenue(), database=database, require_book=False)
    result = manager.execute_signal(buy_signal(), market_factory(), 10.0)
    assert "duplicate" in result.message


def test_recent_submission_for_the_same_signal_is_blocked(market_factory):
    manager = OrderManager(venue=RecordingVenue(), require_book=False)
    manager.submissions.append({"ts": time.time(), "signal_id": "SIG-1", "market_id": "M1",
                                "status": "FILLED", "size": 1.0, "price": 0.3, "mode": "paper"})
    result = manager.execute_signal(buy_signal(), market_factory(), 10.0)
    assert "already submitted" in result.message


def test_price_drift_rejects_the_order(market_factory):
    manager = OrderManager(venue=RecordingVenue(), clob=FixedBook(0.48, 0.52), max_price_drift=0.02)
    result = manager.execute_signal(buy_signal(price=0.30), market_factory(), 10.0)
    assert result.status is OrderStatus.REJECTED
    assert "price moved" in result.message


def test_price_is_refreshed_before_submission(market_factory):
    venue = RecordingVenue()
    manager = OrderManager(venue=venue, clob=FixedBook(0.29, 0.30))
    market = market_factory()
    manager.execute_signal(buy_signal(price=0.301), market, 10.0)
    assert venue.submitted
    assert venue.submitted[0].price == pytest.approx(0.30)


def test_book_unavailable_rejects_rather_than_guessing(market_factory):
    class BrokenBook:
        def snapshot(self, *_a, **_k):
            raise RuntimeError("clob down")

    manager = OrderManager(venue=RecordingVenue(), clob=BrokenBook())
    result = manager.execute_signal(buy_signal(), market_factory(), 10.0)
    assert result.status is OrderStatus.REJECTED
    assert "recheck failed" in result.message


def test_thin_book_depth_is_rejected(market_factory):
    class Thin:
        def snapshot(self, _token, **_kwargs):
            from src.markets.schema import MarketSnapshot

            return MarketSnapshot(ts=time.time(), bid=0.29, ask=0.30)

        def book(self, _token):
            return {"bids": [], "asks": [{"price": "0.30", "size": "5"}]}

    manager = OrderManager(venue=RecordingVenue(), clob=Thin())
    result = manager.execute_signal(buy_signal(), market_factory(), 10.0)
    assert result.status is OrderStatus.REJECTED
    assert "depth" in result.message


def test_bookless_venue_is_refused_by_default(market_factory):
    """A venue that cannot report a book must not let an order through silently."""
    manager = OrderManager(venue=RecordingVenue())
    result = manager.execute_signal(buy_signal(), market_factory(), 10.0)
    assert result.status is OrderStatus.REJECTED
    assert "no order book" in result.message


def test_dry_run_never_reaches_the_venue(market_factory):
    """require_book=False is the explicit opt-out for a venue that has no book;
    it is never the default."""
    venue = RecordingVenue()
    manager = OrderManager(venue=venue, dry_run=True, require_book=False)
    result = manager.execute_signal(buy_signal(), market_factory(), 10.0)
    assert not venue.submitted
    assert result.order_id.startswith("DRY-")


def test_successful_order_is_persisted(market_factory, database):
    manager = OrderManager(venue=RecordingVenue(), database=database, require_book=False)
    manager.execute_signal(buy_signal(), market_factory(), 10.0, mode="paper")
    orders = database.list_orders()
    assert orders and orders[0]["signal_id"] == "SIG-1"


def test_size_is_converted_to_shares_at_the_execution_price(market_factory):
    venue = RecordingVenue()
    manager = OrderManager(venue=venue, clob=FixedBook(0.29, 0.30))
    manager.execute_signal(buy_signal(price=0.30), market_factory(), 30.0)
    assert venue.submitted[0].size == pytest.approx(100.0)


def test_paper_venue_round_trip_through_the_order_manager(market_factory):
    paper = PaperVenue(config=PaperConfig(starting_balance=100.0, rejection_rate=0.0))
    manager = OrderManager(venue=paper, clob=FixedBook(0.29, 0.30))
    result = manager.execute_signal(buy_signal(price=0.30), market_factory(), 10.0, mode="paper")
    assert result.status in (OrderStatus.FILLED, OrderStatus.PARTIAL)
    assert paper.portfolio.open_positions
