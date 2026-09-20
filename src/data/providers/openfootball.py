"""openfootball JSON feed provider.

Free, public-domain fixture/result JSON on GitHub raw. Used for fixture lists
and cross-league result history when football-data.co.uk is unavailable.

Source: https://github.com/openfootball/football.json
"""
from __future__ import annotations

from dataclasses import dataclass

from src.data.providers.base import Provider, ProviderError
from src.markets.schema import make_match_key
from src.monitoring.health import Health

DEFAULT_LEAGUES = {
    "en.1": "Premier League",
    "en.2": "Championship",
    "de.1": "Bundesliga",
    "it.1": "Serie A",
    "es.1": "La Liga",
    "fr.1": "Ligue 1",
    "nl.1": "Eredivisie",
    "pt.1": "Primeira Liga",
}


@dataclass
class OpenFootballProvider(Provider):
    name: str = "openfootball"
    kind: str = "fixtures"
    base_url: str = "https://raw.githubusercontent.com/openfootball/football.json/master"
    leagues: tuple[str, ...] = ("en.1", "de.1", "it.1", "es.1", "fr.1")

    def fetch_league(self, league: str, season: str = "2025-26") -> dict:
        url = f"{self.base_url}/{season}/{league}.json"
        data = self._fetch_json(url)
        if not isinstance(data, dict) or "matches" not in data:
            raise ProviderError(f"{self.name}: unexpected payload for {league}")
        return data

    def parse(self, data: dict, league_code: str) -> list[dict]:
        out: list[dict] = []
        for match in data.get("matches", []):
            home = (match.get("team1") or "").strip()
            away = (match.get("team2") or "").strip()
            if not home or not away:
                continue
            score = match.get("score") or {}
            full_time = score.get("ft")
            home_goals = away_goals = None
            if isinstance(full_time, list) and len(full_time) == 2:
                home_goals, away_goals = int(full_time[0]), int(full_time[1])
            date_raw = match.get("date", "")
            out.append(
                {
                    "match_key": make_match_key(home, away, date_raw),
                    "league": DEFAULT_LEAGUES.get(league_code, league_code),
                    "league_code": league_code,
                    "match_date": date_raw,
                    "kickoff": match.get("time"),
                    "home_team": home,
                    "away_team": away,
                    "home_goals": home_goals,
                    "away_goals": away_goals,
                    "finished": home_goals is not None,
                    "round": match.get("round"),
                    "source": self.name,
                }
            )
        return out

    def fetch_all(self, season: str = "2025-26") -> list[dict]:
        matches: list[dict] = []
        for league in self.leagues:
            try:
                matches.extend(self.parse(self.fetch_league(league, season), league))
            except ProviderError:
                continue
        if not matches:
            raise ProviderError(f"{self.name}: no matches parsed")
        return matches

    def health_check(self) -> Health:
        try:
            data = self.fetch_league(self.leagues[0])
        except ProviderError as exc:
            self._mark_health(Health.CRITICAL, f"openfootball unreachable: {exc}")
            return Health.CRITICAL
        self._mark_health(Health.HEALTHY, f"{len(data.get('matches', []))} matches")
        return Health.HEALTHY
