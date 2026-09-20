"""football-data.co.uk provider.

Free historical results with shots, shots on target, corners, cards and closing
bookmaker odds. Downloads are plain CSV files - no API key, no scraping of
terms-restricted HTML.

Source: https://www.football-data.co.uk/notes.txt  (public research data)
Layout: https://www.football-data.co.uk/mmz4281/<season>/<league>.csv
"""
from __future__ import annotations

import csv
import io
from dataclasses import dataclass

from src.data.providers.base import Provider, ProviderError
from src.markets.schema import make_match_key, normalize_team_name
from src.monitoring.health import Health

LEAGUE_NAMES = {
    "E0": "Premier League",
    "E1": "Championship",
    "E2": "League One",
    "E3": "League Two",
    "EC": "National League",
    "D1": "Bundesliga",
    "D2": "2. Bundesliga",
    "I1": "Serie A",
    "I2": "Serie B",
    "SP1": "La Liga",
    "SP2": "Segunda Division",
    "F1": "Ligue 1",
    "F2": "Ligue 2",
    "N1": "Eredivisie",
    "P1": "Primeira Liga",
    "B1": "Belgian First Division A",
    "T1": "Super Lig",
    "G1": "Greek Super League",
    "SC0": "Scottish Premiership",
}


def _float(value: str | None) -> float | None:
    if value is None:
        return None
    text = value.strip()
    if text in ("", "-", "NA", "N/A", "null"):
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _int(value: str | None) -> int | None:
    number = _float(value)
    return None if number is None else int(number)


@dataclass
class FootballDataUKProvider(Provider):
    name: str = "football_data_uk"
    kind: str = "historical_results"
    base_url: str = "https://www.football-data.co.uk"
    season: str = "2526"
    leagues: tuple[str, ...] = ("E0", "E1", "D1", "I1", "SP1", "F1", "N1", "P1")

    def csv_url(self, league: str, season: str | None = None) -> str:
        return f"{self.base_url}/mmz4281/{season or self.season}/{league}.csv"

    def fetch_league_csv(self, league: str, season: str | None = None) -> str:
        text = self._fetch_text(self.csv_url(league, season))
        if not text or not text.strip():
            raise ProviderError(
                f"{self.name}: empty response for league {league} (season {season or self.season}) "
                "- the provider is unavailable or the season code is wrong"
            )
        return text

    def parse_league_csv(self, text: str, league: str, season: str | None = None) -> list[dict]:
        """Parse a season CSV into normalized match dicts (finished matches only)."""
        # An empty *or whitespace-only* body is a provider outage, not an empty
        # league: raise so the caller sees a real failure instead of a silent zero.
        if text is None or not text.strip():
            raise ProviderError(f"{self.name}: empty CSV for league {league}")
        reader = csv.DictReader(io.StringIO(text.strip().lstrip("\ufeff")))
        season_label = season or self.season
        out: list[dict] = []
        for row in reader:
            home_raw = (row.get("HomeTeam") or "").strip()
            away_raw = (row.get("AwayTeam") or "").strip()
            if not home_raw or not away_raw:
                continue
            home_goals = _int(row.get("FTHG"))
            away_goals = _int(row.get("FTAG"))
            if home_goals is None or away_goals is None:
                continue  # fixture without a result yet
            date_raw = (row.get("Date") or "").strip()
            match_date = self._normalize_date(date_raw)
            out.append(
                {
                    "match_key": make_match_key(home_raw, away_raw, match_date),
                    "league": LEAGUE_NAMES.get(league, league),
                    "league_code": league,
                    "season": season_label,
                    "match_date": match_date,
                    "home_team": home_raw,
                    "away_team": away_raw,
                    "home_team_norm": normalize_team_name(home_raw),
                    "away_team_norm": normalize_team_name(away_raw),
                    "home_goals": home_goals,
                    "away_goals": away_goals,
                    "home_shots": _int(row.get("HS")),
                    "away_shots": _int(row.get("AS")),
                    "home_shots_on_target": _int(row.get("HST")),
                    "away_shots_on_target": _int(row.get("AST")),
                    "home_corners": _int(row.get("HC")),
                    "away_corners": _int(row.get("AC")),
                    "home_yellows": _int(row.get("HY")),
                    "away_yellows": _int(row.get("AY")),
                    "home_reds": _int(row.get("HR")),
                    "away_reds": _int(row.get("AR")),
                    "home_fouls": _int(row.get("HF")),
                    "away_fouls": _int(row.get("AF")),
                    "odds": self._extract_odds(row),
                    "source": self.name,
                }
            )
        return out

    @staticmethod
    def _extract_odds(row: dict) -> dict:
        """Closing 1X2 + O/U 2.5 odds from a preferred bookmaker (Pinnacle/B365)."""
        out: dict = {}
        for book, prefix in (("pinnacle", "PS"), ("bet365", "B365"), ("avg", "Avg")):
            home = _float(row.get(f"{prefix}H"))
            draw = _float(row.get(f"{prefix}D"))
            away = _float(row.get(f"{prefix}A"))
            if home and draw and away:
                out[book] = {"home": home, "draw": draw, "away": away}
                over = _float(row.get("P>2.5")) if book == "pinnacle" else _float(row.get("B365>2.5"))
                under = _float(row.get("P<2.5")) if book == "pinnacle" else _float(row.get("B365<2.5"))
                if over and under:
                    out[book]["over25"] = over
                    out[book]["under25"] = under
        return out

    @staticmethod
    def _normalize_date(raw: str) -> str:
        """CSV dates come as DD/MM/YYYY (older files) or DD/MM/YY."""
        text = (raw or "").strip()
        parts = text.split("/")
        if len(parts) != 3:
            return text
        day, month, year = parts
        if len(year) == 2:
            year = f"20{year}"
        try:
            return f"{year}-{int(month):02d}-{int(day):02d}"
        except ValueError:
            return text

    def fetch_all(self, season: str | None = None) -> list[dict]:
        matches: list[dict] = []
        for league in self.leagues:
            try:
                text = self.fetch_league_csv(league, season)
            except ProviderError:
                continue  # one missing league must not kill the whole load
            try:
                matches.extend(self.parse_league_csv(text, league, season))
            except ProviderError:
                continue
        if not matches:
            raise ProviderError(f"{self.name}: no matches parsed for any league")
        return matches

    def fetch_fixtures(self) -> str:
        """Upcoming fixture CSV (no results yet)."""
        return self._fetch_text(f"{self.base_url}/fixtures.csv")

    def health_check(self) -> Health:
        try:
            text = self.fetch_league_csv(self.leagues[0] if self.leagues else "E0")
        except ProviderError as exc:
            self._mark_health(Health.CRITICAL, f"football-data.co.uk unreachable: {exc}")
            return Health.CRITICAL
        if "HomeTeam" not in text[:2000]:
            self._mark_health(Health.CRITICAL, "unexpected CSV header")
            return Health.CRITICAL
        self._mark_health(Health.HEALTHY, "CSV feed ok")
        return Health.HEALTHY
