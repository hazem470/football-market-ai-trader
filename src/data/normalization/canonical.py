"""Cross-provider canonical data model.

`MatchRecord` and `PlayerRecord` are the ONLY things the models consume.
Provider quirks are resolved here and nowhere else.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from src.markets.schema import make_match_key, normalize_player_name, normalize_team_name


@dataclass
class TeamForm:
    """Rolling form snapshot for one team, computed from canonical history."""

    team: str
    matches: int = 0
    goals_for: float = 0.0
    goals_against: float = 0.0
    shots: float = 0.0
    shots_on_target: float = 0.0
    corners_for: float = 0.0
    corners_against: float = 0.0
    cards_for: float = 0.0
    cards_against: float = 0.0
    clean_sheets: int = 0
    points: float = 0.0

    def rates(self, n: int) -> dict[str, float]:
        n = max(1, n)
        return {
            "goals_for_pm": self.goals_for / n,
            "goals_against_pm": self.goals_against / n,
            "shots_pm": self.shots / n,
            "shots_on_target_pm": self.shots_on_target / n,
            "corners_for_pm": self.corners_for / n,
            "corners_against_pm": self.corners_against / n,
            "cards_for_pm": self.cards_for / n,
            "cards_against_pm": self.cards_against / n,
            "clean_sheet_rate": self.clean_sheets / n,
            "points_pm": self.points / n,
        }


@dataclass
class MatchRecord:
    """One finished football match, normalized."""

    match_key: str
    league: str = ""
    league_code: str = ""
    season: str = ""
    match_date: str = ""
    home_team: str = ""
    away_team: str = ""
    home_team_norm: str = ""
    away_team_norm: str = ""
    home_goals: int | None = None
    away_goals: int | None = None
    home_shots: int | None = None
    away_shots: int | None = None
    home_shots_on_target: int | None = None
    away_shots_on_target: int | None = None
    home_corners: int | None = None
    away_corners: int | None = None
    home_yellows: int | None = None
    away_yellows: int | None = None
    home_reds: int | None = None
    away_reds: int | None = None
    home_fouls: int | None = None
    away_fouls: int | None = None
    odds: dict = field(default_factory=dict)
    source: str = ""
    issues: list[str] = field(default_factory=list)

    @property
    def finished(self) -> bool:
        return self.home_goals is not None and self.away_goals is not None

    @property
    def total_goals(self) -> int | None:
        if not self.finished:
            return None
        return int(self.home_goals or 0) + int(self.away_goals or 0)

    @property
    def btts(self) -> bool | None:
        if not self.finished:
            return None
        return bool((self.home_goals or 0) > 0 and (self.away_goals or 0) > 0)

    def field_coverage(self) -> float:
        fields = [
            self.home_goals, self.away_goals, self.home_shots, self.away_shots,
            self.home_shots_on_target, self.away_shots_on_target,
            self.home_corners, self.away_corners, self.home_yellows, self.away_yellows,
        ]
        present = sum(1 for value in fields if value is not None)
        return present / len(fields)

    def to_db_row(self) -> dict:
        data = asdict(self)
        data.pop("issues", None)
        data.pop("odds", None)
        return data


@dataclass
class PlayerRecord:
    """One player, normalized for player-prop modelling."""

    player_key: str
    display_name: str = ""
    norm_name: str = ""
    team: str = ""
    team_norm: str = ""
    position: str = ""
    minutes: float = 0.0
    starts: float = 0.0
    games: float = 0.0
    goals: float = 0.0
    assists: float = 0.0
    xg: float = 0.0
    xa: float = 0.0
    goals_per_90: float = 0.0
    assists_per_90: float = 0.0
    xg_per_90: float = 0.0
    xa_per_90: float = 0.0
    availability: float = 0.0
    starting_probability: float | None = None
    expected_minutes: float | None = None
    status: str = "UNKNOWN"
    news: str = ""
    source: str = ""
    shots_per_90: float | None = None       # often unavailable - never invented
    shots_on_target_per_90: float | None = None
    received_cards_per_90: float | None = None
    attributes: dict[str, Any] = field(default_factory=dict)
    issues: list[str] = field(default_factory=list)

    @property
    def is_available(self) -> bool:
        return self.availability > 0.05 and self.status not in ("i", "u", "n", "s")


class Normalizer:
    """Provider payload -> canonical records."""

    @staticmethod
    def match_from_raw(raw: dict) -> MatchRecord:
        home = str(raw.get("home_team", "")).strip()
        away = str(raw.get("away_team", "")).strip()
        date_raw = str(raw.get("match_date", ""))
        return MatchRecord(
            match_key=raw.get("match_key") or make_match_key(home, away, date_raw),
            league=str(raw.get("league", "")),
            league_code=str(raw.get("league_code", raw.get("league", ""))),
            season=str(raw.get("season", "")),
            match_date=date_raw,
            home_team=home,
            away_team=away,
            home_team_norm=raw.get("home_team_norm") or normalize_team_name(home),
            away_team_norm=raw.get("away_team_norm") or normalize_team_name(away),
            home_goals=_opt_int(raw.get("home_goals")),
            away_goals=_opt_int(raw.get("away_goals")),
            home_shots=_opt_int(raw.get("home_shots")),
            away_shots=_opt_int(raw.get("away_shots")),
            home_shots_on_target=_opt_int(raw.get("home_shots_on_target")),
            away_shots_on_target=_opt_int(raw.get("away_shots_on_target")),
            home_corners=_opt_int(raw.get("home_corners")),
            away_corners=_opt_int(raw.get("away_corners")),
            home_yellows=_opt_int(raw.get("home_yellows")),
            away_yellows=_opt_int(raw.get("away_yellows")),
            home_reds=_opt_int(raw.get("home_reds")),
            away_reds=_opt_int(raw.get("away_reds")),
            home_fouls=_opt_int(raw.get("home_fouls")),
            away_fouls=_opt_int(raw.get("away_fouls")),
            odds=dict(raw.get("odds") or {}),
            source=str(raw.get("source", "")),
        )

    @staticmethod
    def player_from_raw(raw: dict, lineup: dict | None = None) -> PlayerRecord:
        lineup = lineup or {}
        shots_per_90 = raw.get("shots_per_90")
        return PlayerRecord(
            player_key=str(raw.get("player_key", "")),
            display_name=str(raw.get("display_name", "")),
            norm_name=raw.get("norm_name") or normalize_player_name(str(raw.get("display_name", ""))),
            team=str(raw.get("team", "")),
            team_norm=raw.get("team_norm") or normalize_team_name(str(raw.get("team", ""))),
            position=str(raw.get("position", "")),
            minutes=float(raw.get("minutes") or 0.0),
            starts=float(raw.get("starts") or 0.0),
            games=float(raw.get("games") or 0.0),
            goals=float(raw.get("goals") or 0.0),
            assists=float(raw.get("assists") or 0.0),
            xg=float(raw.get("xg") or 0.0),
            xa=float(raw.get("xa") or 0.0),
            goals_per_90=float(raw.get("goals_per_90") or 0.0),
            assists_per_90=float(raw.get("assists_per_90") or 0.0),
            xg_per_90=float(raw.get("xg_per_90") or 0.0),
            xa_per_90=float(raw.get("xa_per_90") or 0.0),
            availability=float(raw.get("availability") or 0.0),
            starting_probability=lineup.get("starting_probability"),
            expected_minutes=lineup.get("expected_minutes"),
            status=str(raw.get("status", "UNKNOWN")),
            news=str(raw.get("news", "")),
            source=str(raw.get("source", "")),
            shots_per_90=float(shots_per_90) if shots_per_90 is not None else None,
            shots_on_target_per_90=raw.get("shots_on_target_per_90"),
            received_cards_per_90=raw.get("received_cards_per_90"),
            attributes=dict(raw.get("attributes") or {}),
        )


def _opt_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


class TeamHistory:
    """Canonical match history with fast per-team lookups."""

    def __init__(self, matches: list[MatchRecord]) -> None:
        self.matches = [m for m in matches if m.finished]
        self.by_league: dict[str, list[MatchRecord]] = {}
        self.by_team: dict[str, list[MatchRecord]] = {}
        for match in self.matches:
            self.by_league.setdefault(match.league_code or match.league, []).append(match)
            self.by_team.setdefault(match.home_team_norm, []).append(match)
            self.by_team.setdefault(match.away_team_norm, []).append(match)

    def teams(self, league_code: str | None = None) -> list[str]:
        pool = self.by_league.get(league_code, self.matches) if league_code else self.matches
        names: set[str] = set()
        for match in pool:
            names.add(match.home_team_norm)
            names.add(match.away_team_norm)
        return sorted(names)

    def form(self, team_norm: str, last_n: int = 10) -> TeamForm:
        records = self.by_team.get(team_norm, [])
        records = sorted(records, key=lambda m: m.match_date)[-last_n:]
        form = TeamForm(team=team_norm, matches=len(records))
        for match in records:
            home = match.home_team_norm == team_norm
            gf = match.home_goals if home else match.away_goals
            ga = match.away_goals if home else match.home_goals
            gf = int(gf or 0)
            ga = int(ga or 0)
            form.goals_for += gf
            form.goals_against += ga
            form.points += 3 if gf > ga else (1 if gf == ga else 0)
            form.clean_sheets += 1 if ga == 0 else 0
            shots = match.home_shots if home else match.away_shots
            shots_against = match.away_shots if home else match.home_shots
            sot = match.home_shots_on_target if home else match.away_shots_on_target
            corners = match.home_corners if home else match.away_corners
            corners_against = match.away_corners if home else match.home_corners
            cards = _sum_or_none(
                match.home_yellows if home else match.away_yellows,
                match.home_reds if home else match.away_reds,
            )
            cards_against = _sum_or_none(
                match.away_yellows if home else match.home_yellows,
                match.away_reds if home else match.home_reds,
            )
            form.shots += float(shots or 0)
            if shots_against is not None:
                form.shots_against = getattr(form, "shots_against", 0.0) + float(shots_against)
            form.shots_on_target += float(sot or 0)
            form.corners_for += float(corners or 0)
            form.corners_against += float(corners_against or 0)
            form.cards_for += float(cards or 0)
            form.cards_against += float(cards_against or 0)
        return form

    def head_to_head(self, home_norm: str, away_norm: str, last_n: int = 6) -> list[MatchRecord]:
        pairs = [
            m for m in self.matches
            if (m.home_team_norm == home_norm and m.away_team_norm == away_norm)
            or (m.home_team_norm == away_norm and m.away_team_norm == home_norm)
        ]
        return sorted(pairs, key=lambda m: m.match_date)[-last_n:]


def _sum_or_none(*values: Any) -> int | None:
    present = [v for v in values if v is not None]
    if not present:
        return None
    return int(sum(present))
