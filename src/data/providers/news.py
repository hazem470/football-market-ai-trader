"""News ingestion (optional AI layer input).

v1.0 does NOT scrape news sites: their terms typically forbid it. Instead this
adapter exposes a hook where a user-supplied, licensed feed can be plugged in.
When nothing is configured it returns an empty list, and the AI layer simply
has nothing to process - it can never invent facts.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class StaticNewsProvider:
    """Manual/structured news items supplied via configuration or the DB.

    ``items`` maps ``"<home_team>|<away_team>"`` (normalized) to a list of
    text snippets the user is responsible for sourcing legally.
    """

    items: dict[str, list[str]] | None = None
    name: str = "news_static"

    def for_match(self, home_team: str, away_team: str) -> list[str]:
        from src.markets.schema import normalize_team_name

        if not self.items:
            return []
        key = f"{normalize_team_name(home_team)}|{normalize_team_name(away_team)}"
        return list(self.items.get(key, []))[:20]
