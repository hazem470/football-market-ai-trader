"""Player goal / goals+assists model.

P(player scores) ≈ 1 - exp(-lambda_player), where

  lambda_player = per_90_rate * expected_minutes/90 * team_attack_adjustment
                  * opponent_defence_adjustment * starting_probability

Every input must be present and sourced; nothing is imputed.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

from src.features.engine import FeatureSet
from src.markets.schema import MarketType
from src.models.base import Model, Prediction

TEAM_ATTACK_ANCHOR = 1.4        # league-average goals per team per match
MAX_REASONABLE_RATE = 2.2       # goals/90 ceiling for shrinkage safety


@dataclass
class PlayerGoalModel(Model):
    name: str = "player_goal_rate_model"
    version: str = "1.0.0"
    market_types: tuple[str, ...] = (MarketType.PLAYER_GOAL.value,)
    league_average_goals_per_team_match: float = TEAM_ATTACK_ANCHOR
    min_minutes: float = 180.0

    def fit(self, *_args, **_kwargs) -> PlayerGoalModel:
        return self  # rates come from the provider; nothing to fit in v1.0

    def predict(self, features: FeatureSet) -> Prediction:
        base = dict(
            model_name=self.name, market_type=MarketType.PLAYER_GOAL.value, model_version=self.version,
            feature_version=features.feature_version, data_timestamp=features.data_timestamp,
            selection=features.context.get("selection", ""), market_id=features.market_id,
            match_key=features.match_key,
        )
        values = features.values
        rate = values.get("player_goal_rate_90")
        start = values.get("starting_probability")
        availability = values.get("player_availability")
        team_attack = values.get("team_attack_rate")
        opponent_defence = values.get("opponent_defence_rate")

        missing = [
            name for name, value in (
                ("player_goal_rate_90", rate),
                ("starting_probability", start),
                ("player_availability", availability),
                ("team_attack_rate", team_attack),
                ("opponent_defence_rate", opponent_defence),
            ) if value is None
        ]
        if missing:
            return Prediction.insufficient(
                self.name, MarketType.PLAYER_GOAL.value,
                f"missing required player features: {', '.join(missing)}", **base,
            )
        if float(availability) <= 0.05:
            return Prediction.insufficient(
                self.name, MarketType.PLAYER_GOAL.value,
                f"player not available (availability={availability})", **base,
            )
        if float(start) <= 0.05:
            return Prediction.insufficient(
                self.name, MarketType.PLAYER_GOAL.value,
                f"starting probability too low ({start})", **base,
            )

        expected_minutes = values.get("expected_minutes")
        minutes = float(expected_minutes) if expected_minutes else 90.0 * float(start)
        rate = min(MAX_REASONABLE_RATE, max(0.0, float(rate)))
        attack_factor = float(team_attack) / self.league_average_goals_per_team_match
        defence_factor = float(opponent_defence) / self.league_average_goals_per_team_match
        lam = rate * (minutes / 90.0) * max(0.4, min(2.0, attack_factor)) * max(0.4, min(2.0, defence_factor))
        probability = 1.0 - math.exp(-max(0.0, lam))
        probability = min(0.95, max(0.0, probability))

        data_quality = 1.0
        if float(start) < 0.7:
            data_quality -= 0.2   # lineup uncertainty is the dominant risk here
        if values.get("player_goal_rate_90") and rate == 0.0:
            data_quality -= 0.3
        return Prediction(
            probability=round(probability, 6),
            confidence=round(max(0.3, min(0.9, 0.85 * data_quality)), 4),
            diagnostics={
                "player_lambda": round(lam, 5),
                "goal_rate_90": round(rate, 5),
                "expected_minutes": round(minutes, 1),
                "starting_probability": float(start),
                "attack_factor": round(attack_factor, 4),
                "defence_factor": round(defence_factor, 4),
                "player": values.get("player_player_index"),
            },
            **base,
        )


@dataclass
class PlayerGoalInvolvementModel(Model):
    """Goals + assists (goal involvements)."""

    name: str = "player_goal_involvement_model"
    version: str = "1.0.0"
    market_types: tuple[str, ...] = (MarketType.PLAYER_GOALS_PLUS_ASSISTS.value,)
    league_average_goals_per_team_match: float = TEAM_ATTACK_ANCHOR

    def fit(self, *_args, **_kwargs) -> PlayerGoalInvolvementModel:
        return self

    def predict(self, features: FeatureSet) -> Prediction:
        base = dict(
            model_name=self.name, market_type=MarketType.PLAYER_GOALS_PLUS_ASSISTS.value,
            model_version=self.version, feature_version=features.feature_version,
            data_timestamp=features.data_timestamp, selection=features.context.get("selection", ""),
            market_id=features.market_id, match_key=features.match_key,
        )
        values = features.values
        goal_rate = values.get("player_goal_rate_90")
        assist_rate = values.get("player_assist_rate_90")
        start = values.get("starting_probability")
        availability = values.get("player_availability")
        team_attack = values.get("team_attack_rate")
        opponent_defence = values.get("opponent_defence_rate")
        missing = [
            name for name, value in (
                ("player_goal_rate_90", goal_rate), ("player_assist_rate_90", assist_rate),
                ("starting_probability", start), ("player_availability", availability),
                ("team_attack_rate", team_attack), ("opponent_defence_rate", opponent_defence),
            ) if value is None
        ]
        if missing:
            return Prediction.insufficient(
                self.name, MarketType.PLAYER_GOALS_PLUS_ASSISTS.value,
                f"missing required player features: {', '.join(missing)}", **base,
            )
        expected_minutes = values.get("expected_minutes")
        minutes = float(expected_minutes) if expected_minutes else 90.0 * float(start)
        attack_factor = float(team_attack) / self.league_average_goals_per_team_match
        defence_factor = float(opponent_defence) / self.league_average_goals_per_team_match
        combined_rate = min(MAX_REASONABLE_RATE, float(goal_rate) + float(assist_rate))
        lam = combined_rate * (minutes / 90.0) * max(0.4, min(2.0, attack_factor)) * max(0.4, min(2.0, defence_factor))
        probability = min(0.95, max(0.0, 1.0 - math.exp(-max(0.0, lam))))
        return Prediction(
            probability=round(probability, 6),
            confidence=round(max(0.3, min(0.88, 0.85 * (0.8 if float(start) < 0.7 else 1.0))), 4),
            diagnostics={
                "combined_lambda": round(lam, 5),
                "goal_rate_90": round(float(goal_rate), 5),
                "assist_rate_90": round(float(assist_rate), 5),
                "expected_minutes": round(minutes, 1),
            },
            **base,
        )
