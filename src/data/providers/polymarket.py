"""Polymarket providers: Gamma (metadata) and CLOB (prices/order book).

Public, unauthenticated endpoints. No credentials are used or needed here.
Verified against https://docs.polymarket.com/market-data/discover-markets
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

from src.data.providers.base import Provider, ProviderError
from src.markets.schema import MarketSnapshot
from src.monitoring.health import Health

GAMMA_HOST = "https://gamma-api.polymarket.com"
CLOB_HOST = "https://clob.polymarket.com"


@dataclass
class GammaProvider(Provider):
    """Event/market discovery via the Gamma API."""

    name: str = "polymarket_gamma"
    kind: str = "market_metadata"
    host: str = GAMMA_HOST
    page_limit: int = 100
    max_pages: int = 10

    # -------------------------------------------------------------- discovery
    def list_sports(self) -> list[dict]:
        return self._fetch_json(f"{self.host}/sports") or []

    def find_soccer_tag(self) -> dict | None:
        """Resolve the 'soccer' tag dynamically - never hardcode the tag id."""
        try:
            tag = self._fetch_json(f"{self.host}/tags/slug/soccer")
            if isinstance(tag, dict) and tag.get("id"):
                return tag
        except ProviderError:
            pass
        sports = self.list_sports()
        for sport in sports:
            tags = str(sport.get("tags", "")).split(",")
            if (sport.get("sport") or "").lower() in ("epl", "mls", "ucl"):
                return {"id": tags[3] if len(tags) > 3 else tags[-1], "slug": "soccer", "label": "Soccer"}
        return None

    def list_soccer_leagues(self) -> list[dict]:
        sports = self.list_sports()
        out = []
        for sport in sports:
            slug = (sport.get("sport") or "").lower()
            name = (sport.get("name") or "").lower()
            if slug in ("epl", "mls", "ucl", "uel", "laliga", "seriea", "bundesliga", "ligue1") or any(
                token in name
                for token in (
                    "premier league", "mls", "champions league", "europa league", "serie a",
                    "la liga", "bundesliga", "ligue 1", "eredivisie", "primeira", "super lig",
                    "indian super league", "k-league", "j1", "libertadores",
                )
            ):
                out.append(sport)
        return out

    def iter_events(self, tag_id: int | str, closed: bool = False) -> list[dict]:
        """Page through soccer events using the keyset cursor endpoint."""
        events: list[dict] = []
        cursor: str | None = None
        for _ in range(self.max_pages):
            params: dict[str, Any] = {"tag_id": tag_id, "closed": str(closed).lower(), "limit": self.page_limit}
            if cursor:
                params["after_cursor"] = cursor
            payload = self._fetch_json(f"{self.host}/events/keyset", params)
            if not isinstance(payload, dict):
                raise ProviderError(f"{self.name}: unexpected events payload type {type(payload)}")
            page = payload.get("events") or []
            events.extend(page)
            cursor = payload.get("next_cursor")
            if not cursor or not page:
                break
        return events

    def fetch_event(self, event_id: str) -> dict:
        data = self._fetch_json(f"{self.host}/events/{event_id}")
        if not isinstance(data, dict):
            raise ProviderError(f"{self.name}: event {event_id} not a mapping")
        return data

    def search_markets(self, query: str, limit: int = 20) -> list[dict]:
        payload = self._fetch_json(f"{self.host}/public-search", {"q": query, "limit_per_type": limit})
        if isinstance(payload, dict):
            return payload.get("events", []) or payload.get("markets", [])
        return []

    # ---------------------------------------------------------------- health
    def health_check(self) -> Health:
        try:
            sports = self.list_sports()
        except ProviderError as exc:
            return self._mark_and_return(Health.CRITICAL, f"gamma unreachable: {exc}")
        if not sports:
            return self._mark_and_return(Health.WARNING, "gamma returned no sports")
        return self._mark_and_return(Health.HEALTHY, f"{len(sports)} sports")

    def _mark_and_return(self, state: Health, detail: str) -> Health:
        self._mark_health(state, detail)
        return state


@dataclass
class ClobProvider(Provider):
    """Order book / price data via the CLOB API (public endpoints)."""

    name: str = "polymarket_clob"
    kind: str = "market_prices"
    host: str = CLOB_HOST

    def midpoint(self, token_id: str) -> float | None:
        try:
            data = self._fetch_json(f"{self.host}/midpoint", {"token_id": token_id})
        except ProviderError:
            return None
        try:
            return float(data["mid"])  # type: ignore[index]
        except (KeyError, TypeError, ValueError):
            return None

    def price(self, token_id: str, side: str = "buy") -> float | None:
        try:
            data = self._fetch_json(f"{self.host}/price", {"token_id": token_id, "side": side})
        except ProviderError:
            return None
        try:
            return float(data["price"])  # type: ignore[index]
        except (KeyError, TypeError, ValueError):
            return None

    def book(self, token_id: str) -> dict:
        data = self._fetch_json(f"{self.host}/book", {"token_id": token_id})
        if not isinstance(data, dict):
            raise ProviderError(f"{self.name}: invalid book payload")
        return data

    def spread(self, token_id: str) -> float | None:
        try:
            data = self._fetch_json(f"{self.host}/spread", {"token_id": token_id})
        except ProviderError:
            return None
        try:
            return float(data["spread"])  # type: ignore[index]
        except (KeyError, TypeError, ValueError):
            return None

    def fee_rate_bps(self, token_id: str) -> int | None:
        try:
            data = self._fetch_json(f"{self.host}/fee-rate", {"token_id": token_id})
        except ProviderError:
            return None
        try:
            return int(data["base_fee"])  # type: ignore[index]
        except (KeyError, TypeError, ValueError):
            return None

    def snapshot(self, token_id: str, liquidity: float | None = None,
                 volume_24h: float | None = None, source: str = "clob") -> MarketSnapshot:
        """Full, executable snapshot: best bid/ask from the real order book."""
        book = self.book(token_id)
        bids = book.get("bids") or []
        asks = book.get("asks") or []
        best_bid = max((float(b["price"]) for b in bids), default=None)
        best_ask = min((float(a["price"]) for a in asks), default=None)
        bid_size = None
        ask_size = None
        if best_bid is not None:
            bid_size = sum(float(b.get("size", 0)) for b in bids if float(b["price"]) == best_bid)
        if best_ask is not None:
            ask_size = sum(float(a.get("size", 0)) for a in asks if float(a["price"]) == best_ask)
        mid = (best_bid + best_ask) / 2 if best_bid is not None and best_ask is not None else None
        return MarketSnapshot(
            ts=time.time(),
            price=mid,
            bid=best_bid,
            ask=best_ask,
            mid=mid,
            liquidity=liquidity,
            volume_24h=volume_24h,
            best_bid_size=bid_size,
            best_ask_size=ask_size,
            source=source,
        )

    def depth_within(self, token_id: str, side: str, max_slippage: float) -> float:
        """Notional size available within `max_slippage` of the best price."""
        book = self.book(token_id)
        levels = book.get("asks" if side.lower() == "buy" else "bids") or []
        if not levels:
            return 0.0
        prices = [float(level["price"]) for level in levels]
        best = min(prices) if side.lower() == "buy" else max(prices)
        limit = best + max_slippage if side.lower() == "buy" else best - max_slippage
        total = 0.0
        for level in levels:
            price = float(level["price"])
            within = price <= limit if side.lower() == "buy" else price >= limit
            if within:
                total += price * float(level.get("size", 0))
        return total

    def health_check(self) -> Health:
        try:
            data = self._fetch_json(f"{self.host}/sampling-markets", {"limit": 1})
        except ProviderError as exc:
            self._mark_health(Health.CRITICAL, f"clob unreachable: {exc}")
            return Health.CRITICAL
        ok = bool(data)
        state = Health.HEALTHY if ok else Health.WARNING
        self._mark_health(state, "sampling-markets ok" if ok else "empty response")
        return state
