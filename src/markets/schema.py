"""Canonical market model.

Everything downstream depends on this module and ONLY this module: provider
payload shapes never leak past `discovery.py`.
"""
from __future__ import annotations

import json
import math
import re
import unicodedata
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class MarketType(str, Enum):
    """Market families the engine understands.

    Discovered dynamically from Polymarket metadata - never hardcoded as a
    static list of live market IDs.
    """

    MATCH_RESULT = "MATCH_RESULT"
    TOTAL_GOALS = "TOTAL_GOALS"
    BTTS = "BTTS"
    CORNERS = "CORNERS"
    TEAM_TOTALS = "TEAM_TOTALS"
    PLAYER_GOAL = "PLAYER_GOAL"
    PLAYER_ASSIST = "PLAYER_ASSIST"
    PLAYER_GOALS_PLUS_ASSISTS = "PLAYER_GOALS_PLUS_ASSISTS"
    PLAYER_SHOTS = "PLAYER_SHOTS"
    CARDS = "CARDS"
    OFFSIDES = "OFFSIDES"
    FOULS = "FOULS"
    PENALTIES = "PENALTIES"
    SUBSTITUTIONS = "SUBSTITUTIONS"
    HALFTIME_RESULT = "HALFTIME_RESULT"
    SECOND_HALF_RESULT = "SECOND_HALF_RESULT"
    EXACT_SCORE = "EXACT_SCORE"
    FIRST_TO_SCORE = "FIRST_TO_SCORE"
    TEAM_TO_ADVANCE = "TEAM_TO_ADVANCE"
    GOALKEEPER_SAVES = "GOALKEEPER_SAVES"
    FIRST_CORNER = "FIRST_CORNER"
    CORNERS_ODD_EVEN = "CORNERS_ODD_EVEN"
    EXTRA_TIME = "EXTRA_TIME"
    PENALTY_SHOOTOUT = "PENALTY_SHOOTOUT"
    OTHER = "OTHER"
    UNKNOWN = "UNKNOWN"

    @property
    def is_supported(self) -> bool:
        return self in SUPPORTED_MARKET_TYPES

    @property
    def needs_player(self) -> bool:
        return self in {
            MarketType.PLAYER_GOAL,
            MarketType.PLAYER_ASSIST,
            MarketType.PLAYER_GOALS_PLUS_ASSISTS,
            MarketType.PLAYER_SHOTS,
            MarketType.GOALKEEPER_SAVES,
        }


#: Market families with a working model + data path in v1.0.
SUPPORTED_MARKET_TYPES: frozenset[MarketType] = frozenset(
    {
        MarketType.MATCH_RESULT,
        MarketType.TOTAL_GOALS,
        MarketType.BTTS,
        MarketType.CORNERS,
        MarketType.TEAM_TOTALS,
        MarketType.PLAYER_GOAL,
        MarketType.PLAYER_ASSIST,
        MarketType.PLAYER_GOALS_PLUS_ASSISTS,
        MarketType.PLAYER_SHOTS,
    }
)

#: Deliberately NOT traded: no reliable data path yet. The engine returns
#: UNSUPPORTED / INSUFFICIENT_DATA for these instead of guessing.
UNSUPPORTED_MARKET_TYPES: frozenset[MarketType] = frozenset(
    {
        MarketType.CARDS,
        MarketType.OFFSIDES,
        MarketType.FOULS,
        MarketType.PENALTIES,
        MarketType.SUBSTITUTIONS,
        MarketType.HALFTIME_RESULT,
        MarketType.SECOND_HALF_RESULT,
        MarketType.EXACT_SCORE,
        MarketType.FIRST_TO_SCORE,
        MarketType.TEAM_TO_ADVANCE,
        MarketType.GOALKEEPER_SAVES,
        MarketType.FIRST_CORNER,
        MarketType.CORNERS_ODD_EVEN,
        MarketType.EXTRA_TIME,
        MarketType.PENALTY_SHOOTOUT,
        MarketType.OTHER,
        MarketType.UNKNOWN,
    }
)


def _parse_jsonish(value: Any, default: Any) -> Any:
    """Gamma returns JSON *strings* for outcomes / clobTokenIds / prices."""
    if value is None:
        return default
    if isinstance(value, (list, dict)):
        return value
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return default
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return [part.strip() for part in text.split(",")] if default == [] else default
    return default


def _to_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if math.isfinite(out) else None


@dataclass
class MarketSnapshot:
    """A point-in-time view of one tradeable token."""

    ts: float
    price: float | None = None
    bid: float | None = None
    ask: float | None = None
    mid: float | None = None
    spread: float | None = None
    liquidity: float | None = None
    volume_24h: float | None = None
    best_bid_size: float | None = None
    best_ask_size: float | None = None
    source: str = "gamma"
    stale: bool = False

    def __post_init__(self) -> None:
        if self.spread is None and self.bid is not None and self.ask is not None:
            self.spread = round(max(0.0, self.ask - self.bid), 6)
        if self.mid is None and self.bid is not None and self.ask is not None:
            self.mid = round((self.bid + self.ask) / 2, 6)

    @property
    def executable_buy_price(self) -> float | None:
        """What you actually pay to open a YES position (crossing the spread)."""
        if self.ask is not None:
            return self.ask
        if self.price is not None:
            return self.price
        if self.mid is not None:
            return self.mid
        return None

    @property
    def executable_sell_price(self) -> float | None:
        if self.bid is not None:
            return self.bid
        if self.price is not None:
            return self.price
        if self.mid is not None:
            return self.mid
        return None

    def as_dict(self) -> dict:
        return {
            "ts": self.ts,
            "price": self.price,
            "bid": self.bid,
            "ask": self.ask,
            "mid": self.mid,
            "spread": self.spread,
            "liquidity": self.liquidity,
            "volume_24h": self.volume_24h,
            "source": self.source,
        }


@dataclass
class Market:
    """Normalized Polymarket market - the single input contract for the engine."""

    market_id: str
    question: str = ""
    slug: str = ""
    event_id: str = ""
    event_title: str = ""
    league: str = ""
    home_team: str = ""
    away_team: str = ""
    player: str = ""
    market_type: MarketType = MarketType.UNKNOWN
    market_type_raw: str = ""
    classification_confidence: float = 0.0
    selection: str = ""
    selection_kind: str = ""          # TEAM | PLAYER | OVER | UNDER | OTHER
    line: float | None = None          # O/U line, handicap, etc.
    period: str = "FULL"              # FULL | FIRST_HALF | SECOND_HALF
    yes_token_id: str = ""
    no_token_id: str = ""
    tick_size: float | None = None
    min_order_size: float | None = None
    neg_risk: bool = False
    start_time: str = ""
    end_time: str = ""
    resolution_source: str = ""
    resolution_rules: str = ""
    snapshot: MarketSnapshot | None = None
    raw: dict = field(default_factory=dict)

    # ---------------------------------------------------------------- helpers
    @property
    def label(self) -> str:
        parts = [f"{self.home_team} vs {self.away_team}"] if self.home_team else []
        if self.player:
            parts.append(self.player)
        if self.selection:
            parts.append(self.selection)
        return " | ".join(parts) or self.question

    @property
    def match_key(self) -> str:
        if not (self.home_team and self.away_team):
            return ""
        return make_match_key(self.home_team, self.away_team, self.start_time or self.end_time)

    @property
    def tradeable(self) -> bool:
        return bool(
            self.yes_token_id
            and self.market_type.is_supported
            and self.classification_confidence >= 0.5
        )

    def freshness(self, now: float, max_age_seconds: float) -> tuple[bool, float]:
        if self.snapshot is None:
            return False, float("inf")
        age = max(0.0, now - self.snapshot.ts)
        return age <= max_age_seconds, age

    def to_row(self) -> dict:
        return {
            "market_id": self.market_id,
            "event_id": self.event_id,
            "slug": self.slug,
            "question": self.question,
            "league": self.league,
            "home_team": self.home_team,
            "away_team": self.away_team,
            "player": self.player,
            "market_type": self.market_type.value,
            "classification_confidence": self.classification_confidence,
            "selection": self.selection,
            "yes_token_id": self.yes_token_id,
            "no_token_id": self.no_token_id,
            "start_time": self.start_time,
            "end_time": self.end_time,
            "tick_size": self.tick_size,
            "min_order_size": self.min_order_size,
            "neg_risk": self.neg_risk,
            "resolution_source": self.resolution_source,
            "resolution_rules": self.resolution_rules,
            "raw": self.raw,
        }

    def to_snapshot_row(self) -> dict:
        row = (self.snapshot or MarketSnapshot(ts=0.0)).as_dict()
        row.update({"market_id": self.market_id, "token_id": self.yes_token_id})
        return row


_TEAM_SUFFIX_RE = re.compile(
    r"\b(fc|cf|sc|ac|afc|sk|bk|if|ff|cd|ca|sv|vfl|vfb|tsg|as|ssc|rc|us|ud|club|city|united|"
    r"rovers|wanderers|athletic|town|county|albion|north|south|east|west)\b",
    re.IGNORECASE,
)


def normalize_team_name(name: str) -> str:
    """Aggressive but conservative team-name normalisation for cross-provider joins.

    'Arsenal FC' / 'Arsenal' -> 'arsenal';  'Man City' stays distinct from
    'Manchester United'.
    """
    if not name:
        return ""
    text = name.strip().lower()
    text = text.replace("&", " and ")
    text = re.sub(r"[^\w\s\-\.']", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    # Drop a trailing club-type suffix only (never inline, that would break names).
    text = re.sub(r"\s+(fc|cf|sc|ac|afc|sk|bk|if|ff|cd|ca|as|ssc|rc|us|ud)$", "", text)
    return text.strip()


def normalize_player_name(name: str) -> str:
    """Aggressively normalize a player name for cross-provider matching.

    Providers disagree on diacritics ('Mbappé' vs 'Mbappe'), so names are
    ASCII-folded and punctuation-stripped before comparison.
    """
    if not name:
        return ""
    text = name.strip().lower()
    decomposed = unicodedata.normalize("NFKD", text)
    text = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    text = text.replace("ø", "o").replace("đ", "d").replace("ł", "l").replace("ß", "ss")
    text = re.sub(r"[^\w\s\-']", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def make_match_key(home_team: str, away_team: str, date_hint: str = "") -> str:
    """Stable key for cross-provider joins.

    Date is included so reverse fixtures (A vs B in September, B vs A in March)
    never collide. Only the calendar day is used, because providers disagree on
    kick-off *times* far more often than on days.
    """
    day = ""
    if date_hint:
        match = re.search(r"(\d{4})-(\d{2})-(\d{2})", date_hint)
        if match:
            day = f"{match.group(1)}{match.group(2)}{match.group(3)}"
        else:
            match = re.search(r"(\d{2})/(\d{2})/(\d{4})", date_hint)
            if match:
                day = f"{match.group(3)}{match.group(2)}{match.group(1)}"
    parts = [normalize_team_name(home_team), normalize_team_name(away_team)]
    if day:
        parts.append(day)
    return "|".join(parts)
