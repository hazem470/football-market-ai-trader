"""Correlation risk: correlated positions must not be treated as independent.

Example the spec calls out explicitly:
  Arsenal win + Arsenal over 1.5 goals + an Arsenal player to score + Arsenal corners
are highly correlated and must share one exposure budget.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from src.markets.schema import Market, MarketType, normalize_team_name

#: Families that move together within the same match.
GOAL_FAMILY = {
    MarketType.MATCH_RESULT.value,
    MarketType.TOTAL_GOALS.value,
    MarketType.BTTS.value,
    MarketType.TEAM_TOTALS.value,
    MarketType.PLAYER_GOAL.value,
    MarketType.PLAYER_ASSISTS.value if hasattr(MarketType, "PLAYER_ASSISTS") else MarketType.PLAYER_ASSIST.value,
    MarketType.PLAYER_GOALS_PLUS_ASSISTS.value,
}

#: Empirical overlap factors: how much of a position's risk is shared with another
#: family in the same match. 1.0 = fully shared.
OVERLAP: dict[tuple[str, str], float] = {
    (MarketType.MATCH_RESULT.value, MarketType.TOTAL_GOALS.value): 0.35,
    (MarketType.MATCH_RESULT.value, MarketType.TEAM_TOTALS.value): 0.75,
    (MarketType.MATCH_RESULT.value, MarketType.PLAYER_GOAL.value): 0.45,
    (MarketType.MATCH_RESULT.value, MarketType.BTTS.value): 0.30,
    (MarketType.TOTAL_GOALS.value, MarketType.TEAM_TOTALS.value): 0.55,
    (MarketType.TOTAL_GOALS.value, MarketType.BTTS.value): 0.70,
    (MarketType.PLAYER_GOAL.value, MarketType.TOTAL_GOALS.value): 0.40,
    (MarketType.PLAYER_GOAL.value, MarketType.BTTS.value): 0.35,
    (MarketType.PLAYER_GOAL.value, MarketType.TEAM_TOTALS.value): 0.45,
    (MarketType.CORNERS.value, MarketType.TOTAL_GOALS.value): 0.25,
}


@dataclass
class ExposureGroup:
    """A bucket of positions that must share one risk budget."""

    key: str
    match_key: str = ""
    teams: list[str] = field(default_factory=list)
    players: list[str] = field(default_factory=list)
    families: list[str] = field(default_factory=list)
    notional: float = 0.0
    positions: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "key": self.key,
            "match_key": self.match_key,
            "teams": self.teams,
            "players": self.players,
            "families": self.families,
            "notional": round(self.notional, 4),
            "positions": self.positions,
        }


def group_key_for(market: Market) -> str:
    """Primary grouping key: the match. Everything in a match shares a budget."""
    match_key = market.match_key
    if match_key:
        return f"match:{match_key}"
    return f"event:{market.event_id or market.market_id}"


def overlap_factor(family_a: str, family_b: str) -> float:
    if family_a == family_b:
        return 1.0
    return OVERLAP.get((family_a, family_b), OVERLAP.get((family_b, family_a), 0.2))


def effective_exposure(existing: list[dict], new_family: str, new_notional: float) -> dict:
    """Correlation-weighted projected exposure if the new position is added.

    ``existing`` items are dicts with ``family`` and ``notional``.
    """
    total = new_notional
    details: list[dict] = []
    for item in existing:
        factor = overlap_factor(str(item.get("family", "")), new_family)
        contribution = float(item.get("notional", 0.0)) * factor
        total += contribution
        details.append({
            "family": item.get("family"), "notional": round(float(item.get("notional", 0.0)), 4),
            "overlap_factor": factor, "contribution": round(contribution, 4),
        })
    return {"effective_notional": round(total, 4), "components": details}


def teams_in_market(market: Market) -> list[str]:
    return [t for t in (normalize_team_name(market.home_team), normalize_team_name(market.away_team)) if t]
