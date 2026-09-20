"""Official Fantasy Premier League API provider.

Free, unauthenticated, officially published JSON. Gives per-player expected
goals/assists per 90, minutes, availability and per-team strength ratings.

Endpoints:
  https://fantasy.premierleague.com/api/bootstrap-static/
  https://fantasy.premierleague.com/api/fixtures/
"""
from __future__ import annotations

from dataclasses import dataclass

from src.data.providers.base import Provider, ProviderError
from src.markets.schema import normalize_player_name, normalize_team_name
from src.monitoring.health import Health

POSITION_MAP = {1: "GK", 2: "DEF", 3: "MID", 4: "FWD"}


def _float(value) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


@dataclass
class FplProvider(Provider):
    name: str = "fpl"
    kind: str = "player_stats"
    base_url: str = "https://fantasy.premierleague.com/api"

    def bootstrap(self) -> dict:
        data = self._fetch_json(f"{self.base_url}/bootstrap-static/")
        if not isinstance(data, dict) or "elements" not in data:
            raise ProviderError(f"{self.name}: unexpected bootstrap payload")
        return data

    def fixtures(self, event: int | None = None) -> list[dict]:
        params = {"event": event} if event is not None else None
        data = self._fetch_json(f"{self.base_url}/fixtures/", params)
        return data if isinstance(data, list) else []

    def element_summary(self, player_id: int) -> dict:
        data = self._fetch_json(f"{self.base_url}/element-summary/{player_id}/")
        return data if isinstance(data, dict) else {}

    # ------------------------------------------------------------------ model
    def teams(self) -> dict[int, dict]:
        """team_id -> {name, norm, short_name, strength_*}. Strength may be 0 pre-season."""
        data = self.bootstrap()
        out: dict[int, dict] = {}
        for team in data.get("teams", []):
            out[int(team["id"])] = {
                "team_id": int(team["id"]),
                "name": team.get("name", ""),
                "norm": normalize_team_name(team.get("name", "")),
                "short_name": team.get("short_name", ""),
                "strength_overall_home": _float(team.get("strength_overall_home")),
                "strength_overall_away": _float(team.get("strength_overall_away")),
                "strength_attack_home": _float(team.get("strength_attack_home")),
                "strength_attack_away": _float(team.get("strength_attack_away")),
                "strength_defence_home": _float(team.get("strength_defence_home")),
                "strength_defence_away": _float(team.get("strength_defence_away")),
            }
        return out

    def players(self) -> list[dict]:
        """Normalized player records with xG/xA per 90 and availability."""
        data = self.bootstrap()
        teams = self.teams()
        total_players = len(data.get("elements", [])) or 1
        out: list[dict] = []
        for element in data.get("elements", []):
            team = teams.get(int(element.get("team", 0)), {})
            minutes = _float(element.get("minutes")) or 0.0
            starts = _float(element.get("starts")) or 0.0
            games = max(starts, minutes / 90.0) or 0.0
            goals = _float(element.get("goals_scored")) or 0.0
            assists = _float(element.get("assists")) or 0.0
            xg = _float(element.get("expected_goals")) or 0.0
            xa = _float(element.get("expected_assists")) or 0.0
            status = element.get("status", "a")
            chance = element.get("chance_of_playing_next_round")
            chance_f = 1.0 if chance is None else max(0.0, min(1.0, float(chance) / 100.0))
            if status in ("u", "n"):
                availability = 0.0
            elif status in ("i", "s"):
                availability = chance_f * 0.5  # injured/suspended: partial at best
            else:
                availability = chance_f
            out.append(
                {
                    "player_key": f"{team.get('norm', '')}|{normalize_player_name(element.get('web_name', ''))}",
                    "player_id": int(element["id"]),
                    "display_name": element.get("web_name", ""),
                    "full_name": f"{element.get('first_name', '')} {element.get('second_name', '')}".strip(),
                    "norm_name": normalize_player_name(element.get("web_name", "")),
                    "team": team.get("name", ""),
                    "team_norm": team.get("norm", ""),
                    "team_id": team.get("team_id"),
                    "position": POSITION_MAP.get(int(element.get("element_type", 0)), "UNK"),
                    "minutes": minutes,
                    "starts": starts,
                    "games": games,
                    "goals": goals,
                    "assists": assists,
                    "xg": xg,
                    "xa": xa,
                    "goals_per_90": (goals / minutes * 90) if minutes else 0.0,
                    "assists_per_90": (assists / minutes * 90) if minutes else 0.0,
                    "xg_per_90": (xg / minutes * 90) if minutes else (_float(element.get("expected_goals_per_90")) or 0.0),
                    "xa_per_90": (xa / minutes * 90) if minutes else (_float(element.get("expected_assists_per_90")) or 0.0),
                    "shots": _float(element.get("threat")),   # FPL exposes threat, not raw shots
                    "yellow_cards": _float(element.get("yellow_cards")) or 0.0,
                    "red_cards": _float(element.get("red_cards")) or 0.0,
                    "selected_by_percent": _float(element.get("selected_by_percent")) or 0.0,
                    "status": status,
                    "news": element.get("news", ""),
                    "chance_of_playing_next_round": chance,
                    "availability": availability,
                    "penalty_order": element.get("penalties_order"),
                    "price": _float(element.get("now_cost")),
                    "source": self.name,
                    "attributes": {
                        "threat": element.get("threat"),
                        "creativity": element.get("creativity"),
                        "ict_index": element.get("ict_index"),
                        "form": element.get("form"),
                        "team_strength_attack": team.get("strength_attack_home"),
                        "team_strength_defence": team.get("strength_defence_home"),
                        "total_players_share": round(1.0 / total_players, 6),
                    },
                }
            )
        return out

    def team_xg_season(self) -> dict[str, dict]:
        """Aggregate season xG / xGA per team (from player-level xG is too lossy;
        we aggregate goals + xG-conceded from the team summary where available)."""
        data = self.bootstrap()
        teams = self.teams()
        agg: dict[str, dict] = {
            t["norm"]: {"team": t["name"], "xg": 0.0, "xga": 0.0, "goals": 0.0, "conceded": 0.0,
                        "corners": None, "games": 0.0}
            for t in teams.values()
        }
        for element in data.get("elements", []):
            team = teams.get(int(element.get("team", 0)))
            if not team:
                continue
            bucket = agg.get(team["norm"])
            if bucket is None:
                continue
            bucket["xg"] += _float(element.get("expected_goals")) or 0.0
            bucket["goals"] += _float(element.get("goals_scored")) or 0.0
        team_rows = data.get("teams", [])
        for team_row in team_rows:
            norm = normalize_team_name(team_row.get("name", ""))
            if norm not in agg:
                continue
            played = _float(team_row.get("played")) or 0.0
            agg[norm]["games"] = played
            conceded = _float(team_row.get("goals_conceded"))
            if conceded is not None:
                agg[norm]["conceded"] = conceded
        return agg

    def health_check(self) -> Health:
        try:
            data = self.bootstrap()
        except ProviderError as exc:
            self._mark_health(Health.CRITICAL, f"FPL unreachable: {exc}")
            return Health.CRITICAL
        count = len(data.get("elements", []))
        if count == 0:
            self._mark_health(Health.WARNING, "FPL returned no players")
            return Health.WARNING
        self._mark_health(Health.HEALTHY, f"{count} players")
        return Health.HEALTHY
