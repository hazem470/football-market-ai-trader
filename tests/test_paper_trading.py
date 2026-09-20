"""Paper trading: virtual capital, real book, honest accounting."""
from __future__ import annotations

import pytest

from src.execution.base import OrderRequest, OrderStatus
from src.paper.simulator import PaperConfig, PaperVenue, SimulatedPosition


def venue(**overrides) -> PaperVenue:
    config = PaperConfig(starting_balance=100.0, rejection_rate=0.0, min_fill_ratio=0.25,
                         fixed_slippage=0.005, seed=1)
    for key, value in overrides.items():
        setattr(config, key, value)
    return PaperVenue(config=config)


def request(price=0.5, size=10.0, token="tok") -> OrderRequest:
    return OrderRequest(market_id="M1", token_id=token, side="BUY", price=price, size=size,
                        signal_id="SIG-1")


def test_portfolio_starts_with_the_configured_balance():
    paper = venue()
    assert paper.portfolio.cash == pytest.approx(100.0)
    assert paper.portfolio.equity == pytest.approx(100.0)


def test_fill_reduces_cash_and_records_a_position():
    paper = venue()
    result = paper.submit(request(price=0.5, size=10.0))
    assert result.status in (OrderStatus.FILLED, OrderStatus.PARTIAL)
    assert len(paper.portfolio.open_positions) == 1
    assert paper.portfolio.cash < 100.0


def test_fill_price_includes_slippage_when_no_book():
    paper = venue(slippage_model="fixed", fixed_slippage=0.01)
    result = paper.submit(request(price=0.5))
    assert result.avg_price == pytest.approx(0.51)
    assert result.slippage == pytest.approx(0.01)


def test_simulated_rejection_is_reported_not_raised():
    paper = venue(rejection_rate=1.0)
    result = paper.submit(request())
    assert result.status is OrderStatus.REJECTED
    assert "simulated" in result.message


def test_insufficient_cash_is_rejected():
    paper = venue()
    result = paper.submit(request(price=0.9, size=500.0))
    assert result.status is OrderStatus.REJECTED
    assert "insufficient" in result.message


def test_book_depth_limits_the_fill():
    class ThinBook:
        def book(self, _token):
            return {"bids": [], "asks": [{"price": "0.50", "size": "20"}]}

    paper = venue()
    paper.clob = ThinBook()
    result = paper.submit(request(price=0.5, size=100.0))
    assert result.status is OrderStatus.PARTIAL
    assert result.filled_size < 100.0


def test_empty_book_is_rejected():
    class EmptyBook:
        def book(self, _token):
            return {"bids": [], "asks": []}

    paper = venue()
    paper.clob = EmptyBook()
    result = paper.submit(request())
    assert result.status is OrderStatus.REJECTED
    assert "liquidity" in result.message


def test_settle_a_winning_position():
    paper = venue()
    paper.submit(request(price=0.5, size=20.0))
    position = paper.portfolio.open_positions[0]
    pnl = paper.portfolio.settle(position, 1.0)
    assert pnl > 0
    assert position.realized_pnl == pytest.approx((1.0 - position.entry_price) * position.size)
    assert not paper.portfolio.open_positions


def test_settle_a_losing_position():
    paper = venue()
    paper.submit(request(price=0.5, size=20.0))
    position = paper.portfolio.open_positions[0]
    pnl = paper.portfolio.settle(position, 0.0)
    assert pnl < 0


def test_resolution_settles_every_position_in_that_market():
    paper = venue()
    paper.submit(request(price=0.5, size=5.0))
    paper.submit(request(price=0.5, size=5.0))
    settled = paper.portfolio.resolve("M1", 1)
    assert settled > 0
    assert not paper.portfolio.open_positions


def test_unrealized_pnl_tracks_mark_price():
    paper = venue()
    paper.submit(request(price=0.5, size=10.0))
    position = paper.portfolio.open_positions[0]
    assert position.unrealized(0.8) > 0
    assert position.unrealized(0.2) < 0


def test_equity_accounting_is_consistent():
    paper = venue()
    paper.submit(request(price=0.5, size=10.0))
    expected = paper.portfolio.cash + paper.portfolio.open_exposure
    assert paper.portfolio.equity == pytest.approx(expected)


def test_paper_results_are_reproducible_for_a_fixed_seed():
    first = venue(rejection_rate=0.5)
    second = venue(rejection_rate=0.5)
    outcomes = [
        first.submit(request(price=0.5)).status.value,
        second.submit(request(price=0.5)).status.value,
    ]
    assert outcomes[0] == outcomes[1]


def test_preflight_flags_zero_balance():
    paper = venue(starting_balance=0.0)
    ok, problems = paper.preflight()
    assert not ok and problems


def test_describe_reports_the_portfolio():
    paper = venue()
    payload = paper.describe()
    assert payload["live"] is False
    assert payload["portfolio"]["starting_balance"] == pytest.approx(100.0)


def test_position_dict_is_serialisable():
    position = SimulatedPosition(position_id="P", market_id="M", token_id="T", side="BUY",
                                 size=1.0, entry_price=0.5, opened_at=0.0)
    payload = position.as_dict()
    assert payload["status"] == "OPEN"
    assert payload["notional"] == pytest.approx(0.5)
