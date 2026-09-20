"""Order-book analysis: executable price, slippage and liquidity checks.

The execution engine must never size an order from a mid price alone. These
helpers answer "what would I actually pay, and how much could I actually get?"
"""
from __future__ import annotations

from dataclasses import dataclass, field

from src.data.providers.polymarket import ClobProvider
from src.markets.schema import MarketSnapshot


@dataclass
class BookLevel:
    price: float
    size: float

    @property
    def notional(self) -> float:
        return self.price * self.size


@dataclass
class BookAnalysis:
    token_id: str
    best_bid: float | None = None
    best_ask: float | None = None
    bid_depth_notional: float = 0.0
    ask_depth_notional: float = 0.0
    spread: float | None = None
    levels: list[BookLevel] = field(default_factory=list)

    @property
    def mid(self) -> float | None:
        if self.best_bid is None or self.best_ask is None:
            return None
        return round((self.best_bid + self.best_ask) / 2, 6)

    @property
    def tradeable(self) -> bool:
        return self.best_bid is not None and self.best_ask is not None and self.best_ask > self.best_bid

    def available_notional(self, side: str) -> float:
        return self.ask_depth_notional if side.lower() == "buy" else self.bid_depth_notional


def analyze_book(book: dict, token_id: str = "") -> BookAnalysis:
    analysis = BookAnalysis(token_id=token_id or str(book.get("asset_id", "")))
    bids = [
        BookLevel(float(level["price"]), float(level.get("size", 0)))
        for level in (book.get("bids") or [])
        if level.get("price") is not None
    ]
    asks = [
        BookLevel(float(level["price"]), float(level.get("size", 0)))
        for level in (book.get("asks") or [])
        if level.get("price") is not None
    ]
    bids.sort(key=lambda level: level.price, reverse=True)
    asks.sort(key=lambda level: level.price)
    if bids:
        analysis.best_bid = bids[0].price
        analysis.bid_depth_notional = sum(level.notional for level in bids)
    if asks:
        analysis.best_ask = asks[0].price
        analysis.ask_depth_notional = sum(level.notional for level in asks)
    analysis.levels = asks[:20] + bids[:20]
    if analysis.best_bid is not None and analysis.best_ask is not None:
        analysis.spread = round(analysis.best_ask - analysis.best_bid, 6)
    return analysis


def fetch_book_analysis(clob: ClobProvider, token_id: str) -> BookAnalysis:
    return analyze_book(clob.book(token_id), token_id)


def simulate_fill(
    book: dict,
    side: str,
    notional: float,
    fee_rate: float = 0.0,
) -> dict:
    """Walk the real book to estimate the volume-weighted average fill price.

    Returns ``{"filled_notional", "filled_size", "avg_price", "slippage", "levels_consumed"}``.
    No fills are invented: if the book is too thin, ``filled_notional`` is smaller
    than requested and the caller must react (partial fill / reject).
    """
    levels = [
        BookLevel(float(level["price"]), float(level.get("size", 0)))
        for level in (book.get("asks" if side.lower() == "buy" else "bids") or [])
        if level.get("price") is not None
    ]
    levels.sort(key=lambda level: level.price, reverse=(side.lower() != "buy"))
    if not levels:
        return {
            "filled_notional": 0.0, "filled_size": 0.0, "avg_price": None,
            "slippage": None, "levels_consumed": 0, "fee": 0.0, "unfilled_notional": notional,
        }

    best_price = levels[0].price
    remaining = max(0.0, notional)
    filled_size = 0.0
    filled_notional = 0.0
    consumed = 0
    for level in levels:
        if remaining <= 1e-9:
            break
        level_notional = level.notional
        take = min(remaining, level_notional)
        take_size = take / level.price
        filled_size += take_size
        filled_notional += take
        remaining -= take
        consumed += 1

    avg_price = filled_notional / filled_size if filled_size else None
    slippage = None
    if avg_price is not None:
        slippage = (avg_price - best_price) if side.lower() == "buy" else (best_price - avg_price)
        slippage = max(0.0, round(slippage, 6))
    fee = filled_notional * fee_rate
    return {
        "filled_notional": round(filled_notional, 6),
        "filled_size": round(filled_size, 6),
        "avg_price": round(avg_price, 6) if avg_price else None,
        "slippage": slippage,
        "levels_consumed": consumed,
        "fee": round(fee, 6),
        "unfilled_notional": round(remaining, 6),
    }


def snapshot_from_book(book: dict, liquidity: float | None = None,
                       volume_24h: float | None = None) -> MarketSnapshot:
    analysis = analyze_book(book)
    return MarketSnapshot(
        ts=__import__("time").time(),
        price=analysis.mid,
        bid=analysis.best_bid,
        ask=analysis.best_ask,
        mid=analysis.mid,
        spread=analysis.spread,
        liquidity=liquidity,
        volume_24h=volume_24h,
        source="clob",
    )
