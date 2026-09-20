"""Dynamic Polymarket football market discovery.

Design rule (Section 60 of the build spec): we never assume a market exists.
We discover what is ACTUALLY tradeable, classify it, and only then ask what
data the model needs.

Nothing here is hardcoded to a market id, a token id or a fixture.
"""
from __future__ import annotations

import json
import re
import time
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

from src.config.settings import Settings
from src.data.providers.polymarket import ClobProvider, GammaProvider
from src.markets.classifier import Classification, classify_market, classify_selection_kind
from src.markets.filters import FilterConfig, FilterResult, apply_filters
from src.markets.schema import Market, MarketSnapshot, MarketType

#: "Team A vs. Team B" / "Team A vs Team B" / "TeamA v TeamB"
VS_RE = re.compile(r"^(?P<home>.+?)\s+(?:vs\.?|v)\s+(?P<away>.+?)(?:\s*[-:].*)?$", re.IGNORECASE)
#: Dates embedded in questions like "Will X win on 2026-03-13?"
DATE_IN_QUESTION_RE = re.compile(r"(\d{4}-\d{2}-\d{2})")


@dataclass
class DiscoveryStats:
    events_scanned: int = 0
    markets_scanned: int = 0
    soccer_events: int = 0
    classified: int = 0
    unknown: int = 0
    price_enriched: int = 0
    price_failed: int = 0
    duration_ms: float = 0.0
    soccer_tag_id: str = ""

    def as_dict(self) -> dict:
        return self.__dict__.copy()


@dataclass
class MarketDiscovery:
    """Discovers, classifies and enriches Polymarket football markets."""

    gamma: GammaProvider
    clob: ClobProvider
    settings: Settings | None = None
    enrich_prices: bool = True
    max_price_enrichment: int = 120
    stats: DiscoveryStats = field(default_factory=DiscoveryStats)

    # ------------------------------------------------------------------ public
    def discover(self, tag_id: str | int | None = None) -> list[Market]:
        started = time.perf_counter()
        resolved_tag = str(tag_id) if tag_id else self._resolve_soccer_tag()
        self.stats = DiscoveryStats(soccer_tag_id=resolved_tag)

        events = self.gamma.iter_events(resolved_tag, closed=False)
        self.stats.events_scanned = len(events)

        markets: list[Market] = []
        seen: set[str] = set()
        for event in events:
            if not self._is_soccer_event(event):
                continue
            self.stats.soccer_events += 1
            for market in self._markets_from_event(event):
                if market.market_id in seen:
                    continue
                seen.add(market.market_id)
                markets.append(market)
                self.stats.markets_scanned += 1

        markets = self._enrich_metadata(markets)
        markets = self._enrich_prices(markets)

        self.stats.classified = sum(1 for m in markets if m.market_type is not MarketType.UNKNOWN)
        self.stats.unknown = sum(1 for m in markets if m.market_type is MarketType.UNKNOWN)
        self.stats.duration_ms = (time.perf_counter() - started) * 1000
        return markets

    def discover_tradeable(self, tag_id: str | int | None = None) -> tuple[FilterResult, MarketDiscovery]:
        markets = self.discover(tag_id)
        config = self.filter_config()
        result = apply_filters(markets, config)
        return result, self

    def filter_config(self) -> FilterConfig:
        settings = self.settings
        if settings is None:
            return FilterConfig()
        return FilterConfig(
            min_liquidity=float(settings.get("markets.min_liquidity", 100.0)),
            min_volume_24h=float(settings.get("markets.min_volume_24h", 0.0)),
            max_spread=float(settings.get("markets.max_spread", 0.04)),
            min_hours_to_resolution=float(settings.get("markets.min_hours_to_resolution", 1.0)),
            max_hours_to_resolution=float(settings.get("trading.max_hours_to_resolution", 240.0)),
            min_minutes_to_start=float(settings.get("trading.min_minutes_to_start", 20.0)),
            max_price_age_seconds=float(settings.get("trading.max_price_age_seconds", 60.0)),
            allowed_market_types=None,
        )

    # ------------------------------------------------------------------ internals
    def _resolve_soccer_tag(self) -> str:
        tag = self.gamma.find_soccer_tag()
        if tag and tag.get("id"):
            return str(tag["id"])
        if self.settings is not None:
            return str(self.settings.get("markets.fallback_sports_tag_id", 100350))
        return "100350"

    @staticmethod
    def _is_soccer_event(event: dict) -> bool:
        """Decide whether an event is association football.

        Polymarket tags events with a 'soccer'/'football' tag slug. When tags are
        absent we fall back to the market payloads: a soccer market carries
        `soccer_*` sportsMarketType values or a football-specific group title,
        which American-football/basketball markets never have. We deliberately do
        NOT fall back to "title contains vs." - NBA and NFL titles match that too.
        """
        tags = event.get("tags") or []
        for tag in tags:
            slug = str(tag.get("slug", "")).lower()
            label = str(tag.get("label", "")).lower()
            if slug in ("soccer", "football") or label in ("soccer", "football"):
                return True

        for market in event.get("markets") or []:
            if not isinstance(market, dict):
                continue
            sports_type = str(market.get("sportsMarketType") or "").lower()
            if sports_type.startswith("soccer_"):
                return True
            if sports_type in ("moneyline", "spreads", "total_goals", "both_teams_to_score",
                               "total_corners", "first_half_moneyline",
                               "both_teams_to_score_first_half",
                               "both_teams_to_score_second_half", "first_half_totals",
                               "first_half_spreads", "first_half_team_totals"):
                text = " ".join(str(market.get(k, "")) for k in
                                ("question", "groupItemTitle", "description")).lower()
                if any(token in text for token in ("corner", " btts", "both teams to score",
                                                   " o/u", "draw", "half")):
                    return True
        return False

    def _markets_from_event(self, event: dict) -> list[Market]:
        out: list[Market] = []
        for raw in event.get("markets") or []:
            market = self.parse_market(raw, event)
            if market is not None:
                out.append(market)
        return out

    def parse_market(self, raw: dict, event: dict | None = None) -> Market | None:
        """Convert a Gamma market payload into a normalized :class:`Market`."""
        event = event or {}
        question = str(raw.get("question") or "").strip()
        group = str(raw.get("groupItemTitle") or "").strip()
        title = str(event.get("title") or "")
        description = str(raw.get("description") or "")
        sports_type = str(raw.get("sportsMarketType") or raw.get("sports_market_type") or "")

        participants = self._participants(event, title)
        home_team, away_team = participants
        player = self._player_from_question(question, home_team, away_team)

        classification: Classification = classify_market(
            question=question,
            title=title,
            group_item_title=group,
            description=description,
            sports_market_type=sports_type,
        )

        selection_kind = classify_selection_kind(classification.market_type, group or question, home_team, away_team)
        selection = group or self._selection_from_question(question)

        clob_tokens = _json_list(raw.get("clobTokenIds"))
        yes_token = str(clob_tokens[0]) if clob_tokens else ""
        no_token = str(clob_tokens[1]) if len(clob_tokens) > 1 else ""

        snapshot = MarketSnapshot(
            ts=time.time(),
            price=_float(raw.get("lastTradePrice")),
            bid=_float(raw.get("bestBid")),
            ask=_float(raw.get("bestAsk")),
            spread=_float(raw.get("spread")),
            liquidity=_float(raw.get("liquidityNum") or raw.get("liquidity")),
            volume_24h=_float(raw.get("volume24hr") or raw.get("volume24hrClob")),
            source="gamma",
        )

        league = self._league(event)

        return Market(
            market_id=str(raw.get("id") or ""),
            question=question,
            slug=str(raw.get("slug") or ""),
            event_id=str(event.get("id") or ""),
            event_title=title,
            league=league,
            home_team=home_team,
            away_team=away_team,
            player=player,
            market_type=classification.market_type,
            market_type_raw=classification.raw_type or sports_type,
            classification_confidence=classification.confidence,
            selection=selection,
            selection_kind=selection_kind,
            line=classification.line,
            period=classification.period,
            yes_token_id=yes_token,
            no_token_id=no_token,
            tick_size=_float(raw.get("orderPriceMinTickSize")),
            min_order_size=_float(raw.get("orderMinSize")),
            neg_risk=bool(raw.get("negRisk")),
            start_time=str(raw.get("gameStartTime") or raw.get("startDateIso") or event.get("startTime") or ""),
            end_time=str(raw.get("endDateIso") or raw.get("endDate") or event.get("endDate") or ""),
            resolution_source=str(raw.get("resolutionSource") or event.get("resolutionSource") or ""),
            resolution_rules=description,
            snapshot=snapshot,
            raw=raw,
        )

    @staticmethod
    def _league(event: dict) -> str:
        series = event.get("series")
        if isinstance(series, list) and series:
            first = series[0]
            if isinstance(first, dict):
                return str(first.get("title") or first.get("slug") or "")
        if isinstance(series, dict):
            return str(series.get("title") or series.get("slug") or "")
        return str(event.get("seriesSlug") or "")

    def _participants(self, event: dict, title: str) -> tuple[str, str]:
        match = VS_RE.match(title.strip())
        if match:
            home = match.group("home").strip()
            away = match.group("away").strip()
            # Strip trailing qualifiers such as "- Halftime Result"
            away = re.sub(r"\s*-\s*(halftime|second half|exact score|more markets|total corners|first team to score).*$",
                          "", away, flags=re.IGNORECASE).strip()
            return home, away
        # Tickers look like "isp-ddy-ddy-2026-03-13": the team codes are
        # abbreviated beyond reliable recovery, so we report "unknown" rather
        # than invent a home/away assignment.
        return "", ""

    @staticmethod
    def _player_from_question(question: str, home_team: str, away_team: str) -> str:
        """Extract the player name from a player-prop question, if any."""
        patterns = (
            r"^Will\s+(?P<name>.+?)\s+score",
            r"^Will\s+(?P<name>.+?)\s+provide",
            r"^Will\s+(?P<name>.+?)\s+have",
            r"^Will\s+(?P<name>.+?)\s+record",
            r"^Will\s+(?P<name>.+?)\s+register",
            r"^Will\s+(?P<name>.+?)\s+make",
            r"(?P<name>.+?)\s+to score",
            r"(?P<name>.+?)\s+to record",
        )
        for pattern in patterns:
            match = re.search(pattern, question, re.IGNORECASE)
            if match:
                name = match.group("name").strip(" ?.,")
                if name and name.lower() not in ("both teams", "either team"):
                    return name
        return ""

    @staticmethod
    def _selection_from_question(question: str) -> str:
        for pattern in (r"^(?P<sel>.+?)\s+to\s+win", r"^Will\s+(?P<sel>.+?)\s+win"):
            match = re.search(pattern, question, re.IGNORECASE)
            if match:
                return match.group("sel").strip(" ?.")
        return question[:80]

    def _enrich_metadata(self, markets: list[Market]) -> list[Market]:
        """Fill gaps for events whose list payload omitted market detail."""
        missing = [m for m in markets if not m.yes_token_id and m.event_id]
        if not missing:
            return markets
        by_event: dict[str, list[Market]] = {}
        for market in missing:
            by_event.setdefault(market.event_id, []).append(market)
        index = {m.market_id: m for m in markets}
        for event_id in list(by_event)[:40]:
            try:
                event = self.gamma.fetch_event(event_id)
            except Exception:
                continue
            for raw in event.get("markets") or []:
                market_id = str(raw.get("id") or "")
                if market_id in index:
                    refreshed = self.parse_market(raw, event)
                    if refreshed is not None:
                        index[market_id] = refreshed
        return list(index.values())

    def _enrich_prices(self, markets: list[Market]) -> list[Market]:
        """Replace Gamma's quoted spread with the real order book where possible."""
        if not self.enrich_prices:
            return markets
        candidates: Iterable[Market] = [
            m for m in markets if m.yes_token_id and m.market_type is not MarketType.UNKNOWN
        ]
        for market in list(candidates)[: self.max_price_enrichment]:
            try:
                snapshot = self.clob.snapshot(
                    market.yes_token_id,
                    liquidity=market.snapshot.liquidity if market.snapshot else None,
                    volume_24h=market.snapshot.volume_24h if market.snapshot else None,
                    source="clob",
                )
                if snapshot.bid is None and snapshot.ask is None:
                    self.stats.price_failed += 1
                    continue
                market.snapshot = snapshot
                self.stats.price_enriched += 1
            except Exception:
                self.stats.price_failed += 1
                continue
        return markets


def _json_list(value: Any) -> list:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, list) else []
        except json.JSONDecodeError:
            return [part.strip() for part in value.split(",") if part.strip()]
    return []


def _float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if out == out else None
