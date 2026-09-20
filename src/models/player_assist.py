"""Player assist model (same rate structure as goals, weaker signal)."""
from __future__ import annotations

import math
from dataclasses import dataclass

from src.features.engine import FeatureSet
from src.markets.schema import MarketType
from src.models.base import Model, Prediction

TEAM_ATTACK_ANCHOR = 1.4
ASSIST_UNCERTAINTY = 0.15  # assists are noisier than goals -> lower confidence


@dataclass
class PlayerAssistModel(Model):
    name: str = "player_assist_rate_model"
    version: str = "1.0.0"
    market_types: tuple[str, ...] = (MarketType.PLAYER_ASSIST.value,)
    league_average_goals_per_team_match: float = TEAM_ATTACK_ANCHOR
    min_minutes: float = 180.0

    def fit(self, *_args, **_kwargs) -> PlayerAssistModel:
        return self

    def predict(self, features: FeatureSet) -> Prediction:
        base = dict(
            model_name=self.name, market_type=MarketType.PLAYER_ASSIST.value, model_version=self.version,
            feature_version=features.feature_version, data_timestamp=features.data_timestamp,
            selection=features.context.get("selection", ""), market_id=features.market_id,
            match_key=features.match_key,
        )
        values = features.values
        rate = values.get("player_assist_rate_90")
        start = values.get("starting_probability")
        availability = values.get("player_availability")
        team_attack = values.get("team_attack_rate")
        opponent_defence = values.get("opponent_defence_rate")
        missing = [
            name for name, value in (
                ("player_assist_rate_90", rate), ("starting_probability", start),
                ("player_availability", availability), ("team_attack_rate", team_attack),
                ("opponent_defence_rate", opponent_defence),
            ) if value is None
        ]
        if missing:
            return Prediction.insufficient(
                self.name, MarketType.PLAYER_ASSIST.value,
                f"missing required player features: {', '.join(missing)}", **base,
            )
        if float(availability) <= 0.05 or float(start) <= 0.05:
            return Prediction.insufficient(
                self.name, MarketType.PLAYER_ASSIST.value, "player unlikely to play", **base
            )
        expected_minutes = values.get("expected_minutes")
        minutes = float(expected_minutes) if expected_minutes else 90.0 * float(start)
        attack_factor = float(team_attack) / self.league_average_goals_per_team_match
        lam = float(rate) * (minutes / 90.0) * max(0.4, min(2.0, attack_factor))
        probability = min(0.9, max(0.0, 1.0 - math.exp(-max(0.0, lam))))
        return Prediction(
            probability=round(probability, 6),
            confidence=round(max(0.3, min(0.8, 0.7 - ASSIST_UNCERTAINTY)), 4),
            diagnostics={
                "assist_lambda": round(lam, 5), "assist_rate_90": round(float(rate), 5),
                "expected_minutes": round(minutes, 1),
                "note": "assist rates are materially noisier than goal rates",
            },
            **base,
        )
