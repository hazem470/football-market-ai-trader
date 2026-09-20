"""Availability / injury adapter.

v1.0 sources availability from the official FPL API (status + news + chance of
playing). It is intentionally a thin adapter: the :class:`AvailabilityProvider`
protocol lets a paid provider (Sportradar, API-Football, ...) be dropped in
without touching the feature engine.

There is NO undocumented scraping here. If a source is unavailable the adapter
returns UNKNOWN availability, which the validation layer turns into
INSUFFICIENT_DATA -> NO TRADE.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

from src.data.providers.fpl import FplProvider


@runtime_checkable
class AvailabilityProvider(Protocol):
    def availability_for(self, player_name: str, team: str = "") -> dict: ...


UNKNOWN_AVAILABILITY = {
    "availability": None,
    "status": "UNKNOWN",
    "news": "",
    "source": "none",
    "is_unknown": True,
}


@dataclass
class FplAvailabilityProvider:
    """Maps FPL player records to a normalized availability record."""

    fpl: FplProvider
    _index: dict[str, dict] = field(default_factory=dict, init=False)
    _loaded: bool = field(default=False, init=False)

    def load(self) -> None:
        if self._loaded:
            return
        for player in self.fpl.players():
            key = f"{player['team_norm']}|{player['norm_name']}"
            self._index[key] = player
            self._index.setdefault(player["norm_name"], player)
        self._loaded = True

    def availability_for(self, player_name: str, team: str = "") -> dict:
        from src.markets.schema import normalize_player_name, normalize_team_name

        self.load()
        norm_player = normalize_player_name(player_name)
        norm_team = normalize_team_name(team)
        record = self._index.get(f"{norm_team}|{norm_player}") if norm_team else None
        if record is None:
            record = self._index.get(norm_player)
        if record is None:
            return dict(UNKNOWN_AVAILABILITY)
        return {
            "availability": record["availability"],
            "status": record["status"],
            "news": record["news"],
            "chance_of_playing_next_round": record["chance_of_playing_next_round"],
            "source": record["source"],
            "is_unknown": False,
            "player_id": record["player_id"],
        }
