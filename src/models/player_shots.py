"""Player shots model (negative binomial).

IMPORTANT DATA LIMITATION: the free FPL API does not publish per-player shots.
Without a per-player shots feed the features are None and this model returns
INSUFFICIENT_DATA -> NO TRADE, exactly as the spec requires. Configure a paid
provider (or the optional shots adapter) to make this family tradeable.
"""
from __future__ import annotations

from dataclasses import dataclass

from src.features.engine import FeatureSet
from src.markets.schema import MarketType
from src.models.base import Model, Prediction
from src.models.poisson_core import RateDistribution


@dataclass
class PlayerShotsModel(Model):
    name: str = "negative_binomial_player_shots"
    version: str = "1.0.0"
    market_types: tuple[str, ...] = (MarketType.PLAYER_SHOTS.value,)
    dispersion: float = 0.35

    def fit(self, *_args, **_kwargs) -> PlayerShotsModel:
        return self

    def predict(self, features: FeatureSet) -> Prediction:
        base = dict(
            model_name=self.name, market_type=MarketType.PLAYER_SHOTS.value, model_version=self.version,
            feature_version=features.feature_version, data_timestamp=features.data_timestamp,
            selection=features.context.get("selection", ""), market_id=features.market_id,
            match_key=features.match_key,
        )
        values = features.values
        rate = values.get("player_shot_rate_90")
        if rate is None:
            return Prediction.insufficient(
                self.name, MarketType.PLAYER_SHOTS.value,
                "no per-player shots feed configured (FPL does not publish player shots)",
                **base,
            )
        start = values.get("starting_probability")
        if start is None:
            return Prediction.insufficient(
                self.name, MarketType.PLAYER_SHOTS.value, "starting probability unknown", **base
            )
        expected_minutes = values.get("expected_minutes")
        minutes = float(expected_minutes) if expected_minutes else 90.0 * float(start)
        mean = max(0.0, float(rate)) * (minutes / 90.0)
        line = features.context.get("line")
        if line is None:
            return Prediction.insufficient(
                *(), **{**base, "reason": "shots market without an O/U line"}
            )
        side = str(features.context.get("selection_kind") or "OVER").upper()
        distribution = RateDistribution(mean=mean, dispersion=self.dispersion)
        probability = distribution.probability_under(float(line)) if side == "UNDER" else distribution.probability_over(float(line))
        return Prediction(
            probability=round(min(0.99, max(0.01, probability)), 6),
            confidence=0.6,
            diagnostics={"mean_shots": round(mean, 4), "line": float(line), "side": side,
                         "dispersion": self.dispersion, "expected_minutes": round(minutes, 1)},
            **base,
        )
