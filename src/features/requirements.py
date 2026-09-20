"""Dynamic data-requirements engine.

MARKET TYPE -> REQUIRED DATA -> FEATURES -> MODEL

This is the module that decides what the bot is allowed to *ask for*. Nothing
downstream may add an implicit requirement; a market whose requirements cannot
be met is reported as INSUFFICIENT_DATA and never traded.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from src.markets.schema import Market, MarketType

#: Feature-pack configuration (extensible via configs/requirements.json).
DEFAULT_REQUIREMENTS_PATH = Path(__file__).resolve().parents[2] / "configs" / "requirements.json"


@dataclass(frozen=True)
class DataRequirement:
    key: str
    description: str
    critical: bool = True
    provider_hint: str = ""

    def as_dict(self) -> dict:
        return {
            "key": self.key,
            "description": self.description,
            "critical": self.critical,
            "provider_hint": self.provider_hint,
        }


@dataclass
class DataPlan:
    """The concrete data request derived from a discovered market."""

    market_type: MarketType
    requirements: list[DataRequirement] = field(default_factory=list)
    features: list[str] = field(default_factory=list)
    model_name: str = ""
    notes: list[str] = field(default_factory=list)

    @property
    def critical_keys(self) -> list[str]:
        return [r.key for r in self.requirements if r.critical]

    def as_dict(self) -> dict:
        return {
            "market_type": self.market_type.value,
            "model_name": self.model_name,
            "features": self.features,
            "requirements": [r.as_dict() for r in self.requirements],
            "notes": self.notes,
        }


# --------------------------------------------------------------------- catalog
_BASE_MATCH = [
    DataRequirement("league_history", "finished matches for the competition", True, "football_data_uk/openfootball"),
    DataRequirement("team_identity", "both teams resolvable across providers", True, "all"),
    DataRequirement("match_date", "kick-off date for fixture alignment", True, "polymarket/gamma"),
]

_GOAL_RATES = [
    DataRequirement("team_goals_for", "goals scored per team", True, "football_data_uk"),
    DataRequirement("team_goals_against", "goals conceded per team", True, "football_data_uk"),
    DataRequirement("home_away_split", "home/away form split for advantage", False, "football_data_uk"),
    DataRequirement("recent_form", "rolling form window", True, "football_data_uk"),
]

ATTACK_STRENGTH = DataRequirement(
    "attack_strength", "per-team attacking strength rating", True, "football_data_uk/fpl"
)
DEFENCE_STRENGTH = DataRequirement(
    "defence_strength", "per-team defensive strength rating", True, "football_data_uk/fpl"
)

_CORNERS = [
    DataRequirement("team_corners_for", "corners won per team", True, "football_data_uk"),
    DataRequirement("team_corners_against", "corners conceded per team", True, "football_data_uk"),
    DataRequirement("shots_proxy", "shots as attacking-pressure proxy", False, "football_data_uk"),
]

_PLAYER_CORE = [
    DataRequirement("player_identity", "player resolvable to a stats record", True, "fpl"),
    DataRequirement("player_minutes", "minutes played (rate denominator)", True, "fpl"),
    DataRequirement("player_availability", "injury/suspension status", True, "fpl"),
    DataRequirement("player_goal_rate", "goals or xG per 90", True, "fpl"),
    DataRequirement("team_attack_strength", "team attacking context", True, "football_data_uk/fpl"),
    DataRequirement("opponent_defence_strength", "opponent defensive context", True, "football_data_uk/fpl"),
    DataRequirement("expected_minutes", "projected minutes", False, "fpl (minutes model)"),
]

_MARKET_REQUIREMENTS: dict[MarketType, tuple[list[DataRequirement], list[str], str]] = {
    MarketType.MATCH_RESULT: (
        _BASE_MATCH + _GOAL_RATES + [ATTACK_STRENGTH, DEFENCE_STRENGTH],
        # NOTE: fixture-congestion features (rest days, travel) are intentionally
        # absent: the free historical feeds do not carry a reliable schedule, and
        # a fabricated congestion number would silently distort every price.
        ["home_attack_rate", "home_defence_rate", "away_attack_rate", "away_defence_rate",
         "home_advantage", "form_delta", "league_goal_environment"],
        "dixon_coles_match_result",
    ),
    MarketType.TOTAL_GOALS: (
        _BASE_MATCH + _GOAL_RATES + [ATTACK_STRENGTH, DEFENCE_STRENGTH],
        ["expected_home_goals", "expected_away_goals", "total_expected_goals",
         "league_goal_environment", "form_delta"],
        "poisson_total_goals",
    ),
    MarketType.BTTS: (
        _BASE_MATCH + _GOAL_RATES + [ATTACK_STRENGTH, DEFENCE_STRENGTH],
        ["expected_home_goals", "expected_away_goals", "clean_sheet_rates",
         "btts_base_rate", "form_delta"],
        "poisson_btts",
    ),
    MarketType.TEAM_TOTALS: (
        _BASE_MATCH + _GOAL_RATES + [ATTACK_STRENGTH, DEFENCE_STRENGTH],
        ["expected_home_goals", "expected_away_goals", "league_goal_environment", "form_delta"],
        "poisson_team_totals",
    ),
    MarketType.CORNERS: (
        _BASE_MATCH + _CORNERS,
        ["home_corners_for_pm", "home_corners_against_pm", "away_corners_for_pm",
         "away_corners_against_pm", "expected_total_corners", "home_away_corner_split"],
        "poisson_corners",
    ),
    MarketType.PLAYER_GOAL: (
        _BASE_MATCH + _PLAYER_CORE,
        ["player_goal_rate_90", "starting_probability", "expected_minutes", "team_attack_rate",
         "opponent_defence_rate", "league_goal_environment", "penalty_duty"],
        "player_goal_rate_model",
    ),
    MarketType.PLAYER_ASSIST: (
        _BASE_MATCH + _PLAYER_CORE
        + [DataRequirement("player_assist_rate", "assists or xA per 90", True, "fpl")],
        ["player_assist_rate_90", "starting_probability", "expected_minutes", "team_attack_rate",
         "opponent_defence_rate"],
        "player_assist_rate_model",
    ),
    MarketType.PLAYER_GOALS_PLUS_ASSISTS: (
        _BASE_MATCH + _PLAYER_CORE
        + [DataRequirement("player_assist_rate", "assists or xA per 90", True, "fpl")],
        ["player_goal_rate_90", "player_assist_rate_90", "starting_probability", "expected_minutes",
         "team_attack_rate", "opponent_defence_rate"],
        "player_goal_involvement_model",
    ),
    MarketType.PLAYER_SHOTS: (
        _BASE_MATCH + _PLAYER_CORE
        + [DataRequirement("player_shots_rate", "shots or shots-on-target per 90", True,
                           "requires a per-player shots feed (not in FPL)")],
        ["player_shot_rate_90", "starting_probability", "expected_minutes", "team_attack_rate"],
        "negative_binomial_player_shots",
    ),
}


def _load_overrides(path: Path | None = None) -> dict:
    path = path or DEFAULT_REQUIREMENTS_PATH
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def requirements_for(market_type: MarketType) -> list[DataRequirement]:
    entry = _MARKET_REQUIREMENTS.get(market_type)
    return list(entry[0]) if entry else []


def build_plan(market: Market, config: dict | None = None) -> DataPlan:
    """Derive the data plan for a discovered market.

    Unsupported families get an empty requirement set and a plan note, which the
    strategy layer turns into UNSUPPORTED.
    """
    entry = _MARKET_REQUIREMENTS.get(market.market_type)
    if entry is None:
        return DataPlan(
            market_type=market.market_type,
            requirements=[],
            features=[],
            model_name="",
            notes=[
                f"{market.market_type.value} has no v1.0 data path. "
                "Result: UNSUPPORTED / INSUFFICIENT DATA -> NO TRADE."
            ],
        )

    requirements, features, model_name = entry
    plan = DataPlan(
        market_type=market.market_type,
        requirements=list(requirements),
        features=list(features),
        model_name=model_name,
    )

    if market.period != "FULL":
        plan.notes.append(
            f"market is a {market.period} market; only FULL-period models are calibrated in v1.0"
        )

    overrides = _load_overrides(Path((config or {}).get("path", DEFAULT_REQUIREMENTS_PATH)))
    extra = (overrides.get(market.market_type.value) or {}).get("extra_features") or []
    if isinstance(extra, list):
        plan.features.extend(str(item) for item in extra)

    if market.market_type.needs_player and not market.player:
        plan.notes.append("player prop without a resolvable player name")
    return plan
