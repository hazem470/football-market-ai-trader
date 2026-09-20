"""Tradeability filters applied right after discovery.

Everything here is a *hard* filter: a market that fails any of these is dropped
before the model is ever asked for a probability.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field

from src.markets.schema import Market


@dataclass
class FilterConfig:
    min_liquidity: float = 100.0
    min_volume_24h: float = 0.0
    max_spread: float = 0.04
    min_hours_to_resolution: float = 1.0
    max_hours_to_resolution: float = 240.0
    min_minutes_to_start: float = 20.0
    max_price_age_seconds: float = 60.0
    require_supported_type: bool = True
    min_classification_confidence: float = 0.5
    allowed_market_types: tuple[str, ...] | None = None


@dataclass
class Rejection:
    market_id: str
    reason: str
    detail: str = ""


@dataclass
class FilterResult:
    accepted: list[Market] = field(default_factory=list)
    rejected: list[Rejection] = field(default_factory=list)

    @property
    def accepted_count(self) -> int:
        return len(self.accepted)

    @property
    def rejected_count(self) -> int:
        return len(self.rejected)

    def rejection_reasons(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for rejection in self.rejected:
            counts[rejection.reason] = counts.get(rejection.reason, 0) + 1
        return counts


def _parse_iso(ts: str) -> float | None:
    """Parse the several timestamp shapes Polymarket actually emits.

    Seen in production payloads: ISO-8601 with 'Z', ISO with an offset, and a
    bare 'YYYY-MM-DD HH:MM:SS+00' (space instead of 'T'). A naive fromisoformat
    silently returns None for the last form, which would make every market look
    like it had no resolution time - so normalise before parsing.
    """
    if not ts:
        return None
    text = ts.strip()
    if not text:
        return None
    formatted = text.replace("Z", "+00:00")
    if "T" not in formatted and " " in formatted:
        # 'YYYY-MM-DD HH:MM:SS          +00' -> 'YYYY-MM-DDTHH:MM:SS+00:00'
        date_part, _, time_part = formatted.partition(" ")
        time_part = time_part.strip().replace(" ", "")
        if time_part.endswith(("+00", "-00")):
            time_part = f"{time_part}:00"
        formatted = f"{date_part}T{time_part}" if time_part else date_part
    if " " in formatted:
        formatted = formatted.replace(" ", "T")
    # 'YYYY-MM-DD' alone is a valid date
    from datetime import datetime

    for parser in (datetime.fromisoformat,):
        try:
            return parser(formatted).timestamp()
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(f"{formatted}T00:00:00+00:00").timestamp()
    except ValueError:
        return None


def passes_filters(market: Market, config: FilterConfig, now: float | None = None) -> Rejection | None:
    """Return a :class:`Rejection` if the market must not be traded, else None."""
    now = time.time() if now is None else now

    if not market.yes_token_id:
        return Rejection(market.market_id, "no_token_id", "market has no CLOB token")

    if config.require_supported_type and not market.market_type.is_supported:
        return Rejection(market.market_id, "unsupported_market_type", market.market_type.value)

    if market.classification_confidence < config.min_classification_confidence:
        return Rejection(
            market.market_id, "low_classification_confidence", f"{market.classification_confidence:.2f}"
        )

    if config.allowed_market_types and market.market_type.value not in config.allowed_market_types:
        return Rejection(market.market_id, "market_type_not_allowed", market.market_type.value)

    snapshot = market.snapshot
    if snapshot is None:
        return Rejection(market.market_id, "no_price_snapshot")

    age = now - snapshot.ts
    if age > config.max_price_age_seconds:
        return Rejection(market.market_id, "stale_price", f"age={age:.0f}s")

    if snapshot.liquidity is not None and snapshot.liquidity < config.min_liquidity:
        return Rejection(market.market_id, "low_liquidity", f"{snapshot.liquidity:.2f}")

    if snapshot.volume_24h is not None and snapshot.volume_24h < config.min_volume_24h:
        return Rejection(market.market_id, "low_volume", f"{snapshot.volume_24h:.2f}")

    spread = snapshot.spread
    if spread is None and snapshot.bid is not None and snapshot.ask is not None:
        spread = snapshot.ask - snapshot.bid
    if spread is None:
        return Rejection(market.market_id, "no_spread_data")
    if spread > config.max_spread:
        return Rejection(market.market_id, "wide_spread", f"{spread:.4f}")

    end_ts = _parse_iso(market.end_time)
    if end_ts is None:
        return Rejection(market.market_id, "no_resolution_time")
    hours_to_end = (end_ts - now) / 3600.0
    if hours_to_end < config.min_hours_to_resolution:
        return Rejection(market.market_id, "resolving_too_soon", f"{hours_to_end:.2f}h")
    if hours_to_end > config.max_hours_to_resolution:
        return Rejection(market.market_id, "resolving_too_far", f"{hours_to_end:.1f}h")

    start_ts = _parse_iso(market.start_time)
    if start_ts is not None:
        minutes_to_start = (start_ts - now) / 60.0
        if 0 < minutes_to_start < config.min_minutes_to_start:
            return Rejection(market.market_id, "starting_too_soon", f"{minutes_to_start:.0f}min")

    if market.market_type.needs_player and not market.player:
        return Rejection(market.market_id, "player_unknown", "player market without a player identity")

    if not (market.home_team and market.away_team):
        return Rejection(market.market_id, "teams_unknown", "cannot resolve participants")

    return None


def apply_filters(markets: list[Market], config: FilterConfig, now: float | None = None) -> FilterResult:
    result = FilterResult()
    for market in markets:
        rejection = passes_filters(market, config, now=now)
        if rejection is None:
            result.accepted.append(market)
        else:
            result.rejected.append(rejection)
    return result
