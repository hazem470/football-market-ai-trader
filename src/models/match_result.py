"""Match-result (moneyline) model: bivariate Poisson -> win/draw/win."""
from __future__ import annotations

from dataclasses import dataclass

from src.data.normalization.canonical import MatchRecord
from src.features.engine import FeatureSet
from src.markets.schema import MarketType
from src.models.base import Model, Prediction
from src.models.poisson_core import TeamStrengthModel


@dataclass
class MatchResultModel(Model):
    name: str = "dixon_coles_match_result"
    version: str = "1.0.0"
    market_types: tuple[str, ...] = (MarketType.MATCH_RESULT.value,)
    min_matches: int = 120
    max_goals: int = 10
    rho: float = -0.05
    half_life_days: float = 180.0
    strengths: TeamStrengthModel | None = None
    league_label: str = ""

    def fit(self, matches: list[MatchRecord], league_label: str = "") -> MatchResultModel:
        self.league_label = league_label
        self.strengths = TeamStrengthModel(
            max_goals=self.max_goals,
            rho=self.rho,
            half_life_days=self.half_life_days,
            min_matches=self.min_matches,
        ).fit(matches)
        return self

    # --- delegated view of the fitted score matrix (used by the backtest engine)
    def score_matrix(self, home_team_norm: str, away_team_norm: str,
                     apply_dixon_coles: bool = True):
        if self.strengths is None:
            return None
        return self.strengths.score_matrix(home_team_norm, away_team_norm,
                                           apply_dixon_coles=apply_dixon_coles)

    def predict(self, features: FeatureSet) -> Prediction:
        base = dict(
            model_name=self.name,
            market_type=MarketType.MATCH_RESULT.value,
            model_version=self.version,
            feature_version=features.feature_version,
            data_timestamp=features.data_timestamp,
            selection=features.context.get("selection", ""),
            market_id=features.market_id,
            match_key=features.match_key,
        )
        if self.strengths is None:
            return Prediction.insufficient(self.name, MarketType.MATCH_RESULT.value, "model not fitted", **base)

        home = features.context.get("home_norm", "")
        away = features.context.get("away_norm", "")
        matrix = self.strengths.score_matrix(home, away)
        if matrix is None:
            unknown = [t for t in (home, away) if not self.strengths.knows(t)]
            return Prediction.insufficient(
                self.name, MarketType.MATCH_RESULT.value,
                f"no fitted strength for {unknown or 'teams'}", **base,
            )

        p_home, p_draw, p_away = (
            matrix.probability_home_win(), matrix.probability_draw(), matrix.probability_away_win()
        )
        selection = (features.values.get("selection_side") or features.context.get("selection_side") or "HOME")
        side = str(selection).upper()
        probability = p_home if side.startswith("H") else (p_away if side.startswith("A") else p_draw)

        knowledge = min(1.0, (features.context.get("home_form_matches", 0)
                              + features.context.get("away_form_matches", 0)) / 20.0)
        return Prediction(
            probability=round(probability, 6),
            confidence=round(0.55 + 0.4 * knowledge, 4),
            diagnostics={
                "p_home": round(p_home, 6), "p_draw": round(p_draw, 6), "p_away": round(p_away, 6),
                "lambda_home": round(matrix.home_lambda, 4), "lambda_away": round(matrix.away_lambda, 4),
                "most_likely_score": matrix.most_likely_score(),
                "fitted_matches": self.strengths.fitted_matches,
                "fitted_teams": self.strengths.fitted_teams,
                "league_mean": round(self.strengths.league_mean, 4),
                "home_advantage": round(self.strengths.home_advantage, 4),
            },
            **base,
        )
