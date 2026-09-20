"""Cards model - intentionally NOT tradeable in v1.0.

football-data.co.uk gives *team-level* cards only; Polymarket cards markets are
player-level. Turning a team-level rate into a player-level probability would be
invented data, so this model refuses and reports the limitation.
"""
from __future__ import annotations

from dataclasses import dataclass

from src.features.engine import FeatureSet
from src.markets.schema import MarketType
from src.models.base import Model, Prediction


@dataclass
class CardsModel(Model):
    name: str = "cards_unsupported"
    version: str = "1.0.0"
    market_types: tuple[str, ...] = (MarketType.CARDS.value,)

    def fit(self, *_args, **_kwargs) -> CardsModel:
        return self

    def predict(self, features: FeatureSet) -> Prediction:
        return Prediction.insufficient(
            self.name, MarketType.CARDS.value,
            "no player-level card data source configured in v1.0 "
            "(team-level card rates exist, but converting them to a player "
            "probability would be invented data)",
            market_id=features.market_id, match_key=features.match_key,
            feature_version=features.feature_version,
        )
