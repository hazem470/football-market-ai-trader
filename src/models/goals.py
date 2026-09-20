"""Goal-total models: total goals O/U, team totals, BTTS."""
from __future__ import annotations

from dataclasses import dataclass

from src.data.normalization.canonical import MatchRecord
from src.features.engine import FeatureSet
from src.markets.schema import MarketType
from src.models.base import Model, Prediction
from src.models.poisson_core import TeamStrengthModel

DEFAULT_LINE = 2.5


@dataclass
class TotalGoalsModel(Model):
    name: str = "poisson_total_goals"
    version: str = "1.0.0"
    market_types: tuple[str, ...] = (MarketType.TOTAL_GOALS.value,)
    min_matches: int = 120
    max_goals: int = 12
    rho: float = -0.04
    half_life_days: float = 180.0
    strengths: TeamStrengthModel | None = None

    def fit(self, matches: list[MatchRecord], league_label: str = "") -> TotalGoalsModel:
        self.strengths = TeamStrengthModel(
            max_goals=self.max_goals, rho=self.rho,
            half_life_days=self.half_life_days, min_matches=self.min_matches,
        ).fit(matches)
        return self

    def score_matrix(self, home_team_norm: str, away_team_norm: str,
                     apply_dixon_coles: bool = True):
        if self.strengths is None:
            return None
        return self.strengths.score_matrix(home_team_norm, away_team_norm,
                                           apply_dixon_coles=apply_dixon_coles)

    def predict(self, features: FeatureSet) -> Prediction:
        base = dict(
            model_name=self.name, market_type=MarketType.TOTAL_GOALS.value, model_version=self.version,
            feature_version=features.feature_version, data_timestamp=features.data_timestamp,
            selection=features.context.get("selection", ""), market_id=features.market_id,
            match_key=features.match_key,
        )
        if self.strengths is None:
            return Prediction.insufficient(self.name, MarketType.TOTAL_GOALS.value, "model not fitted", **base)
        matrix = self.strengths.score_matrix(
            features.context.get("home_norm", ""), features.context.get("away_norm", "")
        )
        if matrix is None:
            return Prediction.insufficient(self.name, MarketType.TOTAL_GOALS.value,
                                           "teams not in fitted history", **base)

        line = float(features.context.get("line") or DEFAULT_LINE)
        side = str(features.context.get("selection_kind") or "OVER").upper()
        p_over = matrix.probability_total_over(line)
        probability = p_over if side == "OVER" else (1.0 - p_over)
        return Prediction(
            probability=round(probability, 6),
            confidence=0.7,
            diagnostics={
                "line": line, "side": side,
                "p_over": round(p_over, 6),
                "expected_total_goals": round(matrix.expected_total_goals(), 4),
                "lambda_home": round(matrix.home_lambda, 4),
                "lambda_away": round(matrix.away_lambda, 4),
                "league_mean": round(self.strengths.league_mean, 4),
            },
            **base,
        )


@dataclass
class TeamTotalsModel(Model):
    name: str = "poisson_team_totals"
    version: str = "1.0.0"
    market_types: tuple[str, ...] = (MarketType.TEAM_TOTALS.value,)
    min_matches: int = 120
    max_goals: int = 10
    rho: float = -0.04
    half_life_days: float = 180.0
    strengths: TeamStrengthModel | None = None

    def fit(self, matches: list[MatchRecord], league_label: str = "") -> TeamTotalsModel:
        self.strengths = TeamStrengthModel(
            max_goals=self.max_goals, rho=self.rho,
            half_life_days=self.half_life_days, min_matches=self.min_matches,
        ).fit(matches)
        return self

    def predict(self, features: FeatureSet) -> Prediction:
        base = dict(
            model_name=self.name, market_type=MarketType.TEAM_TOTALS.value, model_version=self.version,
            feature_version=features.feature_version, data_timestamp=features.data_timestamp,
            selection=features.context.get("selection", ""), market_id=features.market_id,
            match_key=features.match_key,
        )
        if self.strengths is None:
            return Prediction.insufficient(self.name, MarketType.TEAM_TOTALS.value, "model not fitted", **base)
        matrix = self.strengths.score_matrix(
            features.context.get("home_norm", ""), features.context.get("away_norm", "")
        )
        if matrix is None:
            return Prediction.insufficient(self.name, MarketType.TEAM_TOTALS.value,
                                           "teams not in fitted history", **base)
        line = float(features.context.get("line") or 0.5)
        side = str(features.context.get("selection_kind") or "OVER").upper()
        team_side = str(features.context.get("team_side") or "HOME")
        p_over = matrix.probability_team_over(team_side, line)
        probability = p_over if side == "OVER" else (1.0 - p_over)
        return Prediction(
            probability=round(probability, 6),
            confidence=0.65,
            diagnostics={"line": line, "side": side, "team_side": team_side, "p_over": round(p_over, 6)},
            **base,
        )


@dataclass
class BttsModel(Model):
    name: str = "poisson_btts"
    version: str = "1.0.0"
    market_types: tuple[str, ...] = (MarketType.BTTS.value,)
    min_matches: int = 120
    max_goals: int = 10
    rho: float = -0.04
    half_life_days: float = 180.0
    strengths: TeamStrengthModel | None = None

    def fit(self, matches: list[MatchRecord], league_label: str = "") -> BttsModel:
        self.strengths = TeamStrengthModel(
            max_goals=self.max_goals, rho=self.rho,
            half_life_days=self.half_life_days, min_matches=self.min_matches,
        ).fit(matches)
        return self

    def score_matrix(self, home_team_norm: str, away_team_norm: str,
                     apply_dixon_coles: bool = True):
        if self.strengths is None:
            return None
        return self.strengths.score_matrix(home_team_norm, away_team_norm,
                                           apply_dixon_coles=apply_dixon_coles)

    def predict(self, features: FeatureSet) -> Prediction:
        base = dict(
            model_name=self.name, market_type=MarketType.BTTS.value, model_version=self.version,
            feature_version=features.feature_version, data_timestamp=features.data_timestamp,
            selection=features.context.get("selection", ""), market_id=features.market_id,
            match_key=features.match_key,
        )
        if self.strengths is None:
            return Prediction.insufficient(self.name, MarketType.BTTS.value, "model not fitted", **base)
        matrix = self.strengths.score_matrix(
            features.context.get("home_norm", ""), features.context.get("away_norm", "")
        )
        if matrix is None:
            return Prediction.insufficient(self.name, MarketType.BTTS.value,
                                           "teams not in fitted history", **base)
        p_yes = matrix.probability_btts()
        side = str(features.context.get("selection_kind") or "OVER").upper()
        probability = p_yes if side in ("OVER", "YES", "TEAM", "OTHER") else (1.0 - p_yes)
        return Prediction(
            probability=round(probability, 6),
            confidence=0.68,
            diagnostics={
                "p_yes": round(p_yes, 6),
                "p_no": round(1 - p_yes, 6),
                "lambda_home": round(matrix.home_lambda, 4),
                "lambda_away": round(matrix.away_lambda, 4),
                "btts_base_rate": features.values.get("btts_base_rate"),
            },
            **base,
        )
