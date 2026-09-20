"""Model registry: market family -> fitted model instance.

Models are fitted lazily per league and cached, so a scan of 200 markets does
not refit the same league 200 times.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from src.data.normalization.canonical import MatchRecord
from src.features.engine import FeatureSet
from src.markets.schema import MarketType
from src.models.base import InsufficientModelData, Model, Prediction
from src.models.cards import CardsModel
from src.models.corners import CornersModel
from src.models.goals import BttsModel, TeamTotalsModel, TotalGoalsModel
from src.models.match_result import MatchResultModel
from src.models.player_assist import PlayerAssistModel
from src.models.player_goal import PlayerGoalInvolvementModel, PlayerGoalModel
from src.models.player_shots import PlayerShotsModel

MODEL_FACTORIES: dict[str, type[Model]] = {
    MarketType.MATCH_RESULT.value: MatchResultModel,
    MarketType.TOTAL_GOALS.value: TotalGoalsModel,
    MarketType.BTTS.value: BttsModel,
    MarketType.TEAM_TOTALS.value: TeamTotalsModel,
    MarketType.CORNERS.value: CornersModel,
    MarketType.PLAYER_GOAL.value: PlayerGoalModel,
    MarketType.PLAYER_ASSIST.value: PlayerAssistModel,
    MarketType.PLAYER_GOALS_PLUS_ASSISTS.value: PlayerGoalInvolvementModel,
    MarketType.PLAYER_SHOTS.value: PlayerShotsModel,
    MarketType.CARDS.value: CardsModel,
}


@dataclass
class ModelRegistry:
    """Holds fitted models keyed by (market_type, league)."""

    model_config: dict = field(default_factory=dict)
    models: dict[tuple[str, str], Model] = field(default_factory=dict)
    fit_errors: dict[tuple[str, str], str] = field(default_factory=dict)

    def _build(self, market_type: str) -> Model:
        factory = MODEL_FACTORIES.get(market_type)
        if factory is None:
            raise KeyError(f"no model registered for {market_type}")
        cfg = (self.model_config or {}).get(market_type.lower(), {}) or {}
        kwargs = {}
        if "min_history_matches" in cfg and "min_matches" in getattr(factory, "__dataclass_fields__", {}):
            kwargs["min_matches"] = int(cfg["min_history_matches"])
        for key in ("rho", "dispersion", "max_goals", "half_life_days", "league_average_goals_per_team_match"):
            if key in cfg and key in getattr(factory, "__dataclass_fields__", {}):
                kwargs[key] = cfg[key]
        try:
            return factory(**kwargs)
        except TypeError:
            return factory()

    def get_or_fit(self, market_type: str, league_key: str,
                   matches: list[MatchRecord], fit: bool = True) -> Model | None:
        key = (market_type, league_key)
        if key in self.models:
            return self.models[key]
        if not fit:
            return None
        try:
            model = self._build(market_type)
        except KeyError:
            return None
        try:
            model.fit(matches, league_label=league_key)  # type: ignore[call-arg]
        except InsufficientModelData as exc:
            self.fit_errors[key] = str(exc)
            return None
        except TypeError:
            try:
                model.fit(matches)
            except InsufficientModelData as exc:
                self.fit_errors[key] = str(exc)
                return None
        except Exception as exc:  # pragma: no cover - defensive
            self.fit_errors[key] = f"fit failed: {exc}"
            return None
        self.models[key] = model
        return model

    def predict(self, market_type: str, league_key: str, features: FeatureSet,
                matches: list[MatchRecord]) -> Prediction:
        """Predict, or return an explicit INSUFFICIENT_DATA prediction."""
        model = self.get_or_fit(market_type, league_key, matches)
        if model is None:
            reason = self.fit_errors.get(
                (market_type, league_key),
                f"no model available for {market_type} in {league_key or 'unknown league'}",
            )
            return Prediction.insufficient(
                MODEL_FACTORIES.get(market_type, type("X", (), {"name": market_type})).__name__,
                market_type, reason,
                market_id=features.market_id, match_key=features.match_key,
                feature_version=features.feature_version,
            )
        return model.predict(features)

    def describe(self) -> list[dict]:
        return [model.describe() for model in self.models.values()]
