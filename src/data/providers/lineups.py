"""Lineup / starting-probability adapter.

Polymarket does not publish lineups and no free provider gives confirmed XIs
far enough ahead. v1.0 therefore derives a *statistical* starting probability
from minutes played share in the official FPL API, and reports it as an
estimate - never as confirmed news.

If neither minutes nor availability data exists, the adapter returns UNKNOWN
and the player markets become INSUFFICIENT_DATA.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

from src.data.providers.fpl import FplProvider

#: Minutes above which we treat a player as a nailed-on starter.
STARTER_MINUTES = 80.0
#: Team appearances denominator when estimating share of a season.
DEFAULT_TEAM_GAMES = 5.0


@runtime_checkable
class LineupProvider(Protocol):
    def starting_probability(self, player_name: str, team: str = "") -> dict: ...


@dataclass
class MinutesBasedLineupProvider:
    """Estimate P(start) from recent minutes and availability."""

    fpl: FplProvider
    _index: dict[str, dict] = field(default_factory=dict, init=False)
    _loaded: bool = field(default=False, init=False)

    def load(self) -> None:
        if self._loaded:
            return
        players = self.fpl.players()
        for player in players:
            key = f"{player['team_norm']}|{player['norm_name']}"
            self._index[key] = player
            self._index.setdefault(player["norm_name"], player)
        self._loaded = True

    def starting_probability(self, player_name: str, team: str = "") -> dict:
        from src.markets.schema import normalize_player_name, normalize_team_name

        self.load()
        norm_player = normalize_player_name(player_name)
        norm_team = normalize_team_name(team)
        record = self._index.get(f"{norm_team}|{norm_player}") if norm_team else None
        if record is None:
            record = self._index.get(norm_player)
        if record is None:
            return {"starting_probability": None, "source": "none", "is_unknown": True,
                    "rationale": "player not found in the lineup provider"}

        games = max(record["games"], 1.0)
        minutes = record["minutes"]
        per_game_minutes = minutes / games if games else 0.0
        raw = per_game_minutes / 90.0
        if per_game_minutes >= STARTER_MINUTES:
            raw = max(raw, 0.85)
        estimate = max(0.0, min(1.0, raw))
        estimate *= record["availability"]
        return {
            "starting_probability": estimate,
            "minutes_per_game": round(per_game_minutes, 1),
            "expected_minutes": round(per_game_minutes, 1),
            "availability": record["availability"],
            "status": record["status"],
            "source": "fpl_minutes_model",
            "is_unknown": False,
            "rationale": (
                f"{per_game_minutes:.0f} min/game over {games:.0f} app(s), "
                f"availability {record['availability']:.2f}"
            ),
        }
