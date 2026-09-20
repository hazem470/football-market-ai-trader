"""Corners model.

Corners are modelled as a count process over the fixture, using each team's
rolling corners-for / corners-against rates with a home/away adjustment.
Supports Poisson or negative-binomial dispersion (corners are mildly
over-dispersed in practice).
"""
from __future__ import annotations

from dataclasses import dataclass

from src.data.normalization.canonical import MatchRecord
from src.features.engine import FeatureSet
from src.markets.schema import MarketType
from src.models.base import InsufficientModelData, Model, Prediction
from src.models.poisson_core import RateDistribution

HOME_CORNER_FACTOR = 1.08
AWAY_CORNER_FACTOR = 0.92


@dataclass
class CornersModel(Model):
    name: str = "poisson_corners"
    version: str = "1.0.0"
    market_types: tuple[str, ...] = (MarketType.CORNERS.value,)
    min_matches: int = 60
    dispersion: float = 0.0
    fitted_matches: int = 0
    league_mean_total_corners: float = 10.4

    def fit(self, matches: list[MatchRecord], league_label: str = "") -> CornersModel:
        usable = [
            m for m in matches
            if m.finished and m.home_corners is not None and m.away_corners is not None
        ]
        if len(usable) < self.min_matches:
            raise InsufficientModelData(
                f"need >= {self.min_matches} matches with corner data, have {len(usable)}"
            )
        self.fitted_matches = len(usable)
        totals = [int(m.home_corners or 0) + int(m.away_corners or 0) for m in usable]
        self.league_mean_total_corners = max(4.0, sum(totals) / len(totals))
        return self

    def predict(self, features: FeatureSet) -> Prediction:
        base = dict(
            model_name=self.name, market_type=MarketType.CORNERS.value, model_version=self.version,
            feature_version=features.feature_version, data_timestamp=features.data_timestamp,
            selection=features.context.get("selection", ""), market_id=features.market_id,
            match_key=features.match_key,
        )
        if self.fitted_matches == 0:
            return Prediction.insufficient(self.name, MarketType.CORNERS.value, "model not fitted", **base)

        values = features.values
        if values.get("home_corners_for_pm") is None or values.get("away_corners_for_pm") is None:
            return Prediction.insufficient(
                self.name, MarketType.CORNERS.value, "corner rates unavailable for one or both teams", **base
            )

        home_rate = (float(values["home_corners_for_pm"]) + float(values["away_corners_against_pm"])) / 2
        away_rate = (float(values["away_corners_for_pm"]) + float(values["home_corners_against_pm"])) / 2
        home_lambda = max(0.5, home_rate * HOME_CORNER_FACTOR)
        away_lambda = max(0.5, away_rate * AWAY_CORNER_FACTOR)
        total_mean = home_lambda + away_lambda

        line = features.context.get("line")
        side = str(features.context.get("selection_kind") or "OVER").upper()
        team_side = str(features.context.get("team_side") or "").upper()

        if team_side.startswith("H") or team_side.startswith("HOME"):
            distribution = RateDistribution(mean=home_lambda, dispersion=self.dispersion)
        elif team_side.startswith("A") or team_side.startswith("AWAY"):
            distribution = RateDistribution(mean=away_lambda, dispersion=self.dispersion)
        else:
            distribution = RateDistribution(mean=total_mean, dispersion=self.dispersion)

        if line is None:
            return Prediction.insufficient(self.name, MarketType.CORNERS.value,
                                           "corner market without an O/U line", **base)
        line = float(line)
        if side == "UNDER":
            probability = distribution.probability_under(line)
        else:
            probability = distribution.probability_over(line)

        return Prediction(
            probability=round(min(0.999, max(0.001, probability)), 6),
            confidence=0.62 if self.fitted_matches >= 150 else 0.55,
            diagnostics={
                "line": line, "side": side, "team_side": team_side or "TOTAL",
                "lambda_home": round(home_lambda, 4), "lambda_away": round(away_lambda, 4),
                "lambda_total": round(total_mean, 4),
                "league_mean_total_corners": round(self.league_mean_total_corners, 3),
                "fitted_matches": self.fitted_matches,
                "dispersion": self.dispersion,
            },
            **base,
        )
