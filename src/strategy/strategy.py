"""Strategy pipeline: market -> features -> model -> calibration -> edge -> signal.

This function is the heart of the design principle:

  POLYMARKET MARKET -> WHAT IS TRADEABLE -> WHAT DATA IS REQUIRED ->
  CAN WE GET IT RELIABLY -> MODEL PROBABILITY -> IS IT CALIBRATED ->
  REAL EXECUTABLE PRICE -> REALISTIC EDGE -> SIGNAL

It never reverses that order and it never invents data.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from src.calibration.calibrators import CalibrationService
from src.data.normalization.canonical import MatchRecord, TeamHistory
from src.data.validation.validators import validate_market
from src.features.engine import FeatureEngine, FeatureSet
from src.markets.schema import Market, MarketType
from src.models.base import Prediction
from src.models.registry import ModelRegistry
from src.strategy.edge import CostConfig, EdgeBreakdown, calculate_edge
from src.strategy.signals import Signal, SignalState


@dataclass
class StrategyConfig:
    min_edge: float = 0.08
    min_confidence: float = 0.75
    fee_rate: float = 0.0
    assumed_slippage: float = 0.008
    model_uncertainty: float = 0.02
    data_uncertainty: float = 0.01
    execution_uncertainty: float = 0.005
    max_price_age_seconds: float = 60.0
    require_calibration: bool = False
    max_feature_age_hours: float = 72.0

    def cost_config(self) -> CostConfig:
        return CostConfig(
            fee_rate=self.fee_rate,
            assumed_slippage=self.assumed_slippage,
            model_uncertainty=self.model_uncertainty,
            data_uncertainty=self.data_uncertainty,
            execution_uncertainty=self.execution_uncertainty,
        )

    @classmethod
    def from_settings(cls, settings) -> StrategyConfig:
        strategy = settings.section("strategy")
        trading = settings.section("trading")
        return cls(
            min_edge=float(strategy.get("min_edge", 0.08)),
            min_confidence=float(strategy.get("min_confidence", 0.75)),
            fee_rate=float(strategy.get("fee_rate", 0.0)),
            assumed_slippage=float(strategy.get("assumed_slippage", 0.008)),
            model_uncertainty=float(strategy.get("model_uncertainty", 0.02)),
            data_uncertainty=float(strategy.get("data_uncertainty", 0.01)),
            execution_uncertainty=float(strategy.get("execution_uncertainty", 0.005)),
            max_price_age_seconds=float(trading.get("max_price_age_seconds", 60.0)),
            max_feature_age_hours=float(trading.get("max_feature_age_hours", 72.0)),
        )


def _safe_field(market, name: str, default: str = "") -> str:
    """Read an attribute as a string without ever raising.

    A malformed object (or a property that raises) must degrade into an
    uninformative-but-safe string, never crash a market scan.
    """
    try:
        value = getattr(market, name, default)
    except Exception:
        return default
    if isinstance(value, MarketType):
        return value.value
    try:
        return str(value or default)
    except Exception:
        return default


def _safe_label(market) -> str:
    """Best-effort human label that never raises, whatever object arrives."""
    try:
        label = getattr(market, "label", "")
    except Exception:
        label = ""
    return str(label) if label else _safe_field(market, "market_id")


@dataclass
class StrategyEngine:
    """Turns markets into signals. Pure logic - no I/O, no side effects."""

    feature_engine: FeatureEngine
    models: ModelRegistry
    calibration: CalibrationService
    config: StrategyConfig = field(default_factory=StrategyConfig)
    history: TeamHistory | None = None
    stats: dict[str, int] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.history is None:
            self.history = self.feature_engine.history

    # ------------------------------------------------------------------ public
    def evaluate_market(self, market: Market, now: float | None = None) -> Signal:
        now = time.time() if now is None else now
        self.stats["evaluated"] = self.stats.get("evaluated", 0) + 1

        # 0 - unsupported family: refuse explicitly, before anything else.
        #     There is no point validating the price of a market we have no data
        #     path for, and the operator should see UNSUPPORTED, not a validation
        #     complaint about a market we were never going to trade.
        market_type = getattr(market, "market_type", MarketType.UNKNOWN)
        if not isinstance(market_type, MarketType):
            market_type = MarketType.UNKNOWN
        if not market_type.is_supported:
            self.stats["unsupported"] = self.stats.get("unsupported", 0) + 1
            detail = (
                "market could not be classified from its metadata or question text"
                if market_type is MarketType.UNKNOWN
                else f"{market_type.value} has no v1.0 data path"
            )
            return Signal.blocked(
                SignalState.UNSUPPORTED,
                f"{detail} -> NO TRADE",
                market_id=str(getattr(market, "market_id", "")),
                match_key=str(getattr(market, "match_key", "")),
                market_type=market_type.value,
                selection=str(getattr(market, "selection", "")),
                label=_safe_label(market),
            )

        # 1 - does the market even resolve to something we can price?
        validation = validate_market(market, now=now, max_price_age_seconds=self.config.max_price_age_seconds)
        if not validation.ok:
            self.stats["invalid_market"] = self.stats.get("invalid_market", 0) + 1
            return Signal.blocked(
                SignalState.INSUFFICIENT_DATA,
                f"market validation failed: {validation.summary()}",
                market_id=str(getattr(market, "market_id", "")),
                match_key=str(getattr(market, "match_key", "")),
                market_type=market_type.value,
                selection=str(getattr(market, "selection", "")),
                label=_safe_label(market),
                trace={"validation": validation.as_dict()},
            )

        # 2 - features (dynamic, driven by the data-requirements engine)
        features = self.feature_engine.build(market)
        self._attach_context(features, market)

        if not features.values and features.missing:
            self.stats["no_features"] = self.stats.get("no_features", 0) + 1
            return self._blocked_from_features(features, market, SignalState.INSUFFICIENT_DATA,
                                               "no features could be built")

        # 3 - model probability
        league_key = self._league_key(market)
        matches = self._matches_for(league_key)
        prediction = self.models.predict(market.market_type.value, league_key, features, matches)
        if prediction.insufficient_data:
            self.stats["insufficient_data"] = self.stats.get("insufficient_data", 0) + 1
            return self._blocked_from_features(
                features, market, SignalState.INSUFFICIENT_DATA,
                prediction.notes[0] if prediction.notes else "model reported insufficient data",
                prediction=prediction,
            )
        if features.missing:
            # Required features missing: a model that still returned a number
            # would be extrapolating from imputed inputs. Refuse.
            self.stats["missing_features"] = self.stats.get("missing_features", 0) + 1
            return self._blocked_from_features(
                features, market, SignalState.INSUFFICIENT_DATA,
                "required features missing: " + ", ".join(features.missing[:6]),
                prediction=prediction,
            )

        # 4 - calibration
        calibrated = self.calibration.calibrate(prediction.probability)
        prediction.calibrated_probability = calibrated
        prediction.calibration_version = self.calibration.calibrator_version()

        # 5 - edge against the executable price
        edge = calculate_edge(
            probability=calibrated, market=market, config=self.config.cost_config(), side="BUY",
        )

        # 6 - decision
        return self._decide(market, prediction, edge, features, calibration_used=calibrated)

    def evaluate_many(self, markets: list[Market], now: float | None = None) -> list[Signal]:
        signals: list[Signal] = []
        for market in markets:
            try:
                signals.append(self.evaluate_market(market, now=now))
            except Exception as exc:  # defensive: one bad market must not stop a scan
                self.stats["errors"] = self.stats.get("errors", 0) + 1
                signals.append(Signal.blocked(
                    SignalState.NO_TRADE, f"strategy error: {exc}",
                    market_id=_safe_field(market, "market_id"),
                    label=_safe_label(market),
                    market_type=_safe_field(market, "market_type"),
                ))
        return signals

    # ----------------------------------------------------------------- internals
    def _decide(self, market: Market, prediction: Prediction, edge: EdgeBreakdown,
                features: FeatureSet, calibration_used: float) -> Signal:
        base = dict(
            market_id=market.market_id,
            match_key=market.match_key,
            market_type=market.market_type.value,
            selection=market.selection,
            label=market.label,
            model_name=prediction.model_name,
        )
        common_trace = {
            "feature_key": features.feature_key,
            "feature_version": features.feature_version,
            "model_version": prediction.model_version,
            "calibration_version": self.calibration.calibrator_version(),
            "data_timestamp": features.data_timestamp,
            "prediction": prediction.as_dict(),
            "features": features.values,
            "plan": features.plan,
        }

        if not edge.is_complete:
            return Signal.blocked(
                SignalState.NO_TRADE, f"cannot compute executable edge: {edge.incomplete_reason}",
                edge_breakdown=edge.as_dict(), trace=common_trace, **base,
            )

        if edge.realistic_edge < self.config.min_edge:
            signal = Signal.from_edge(edge, state=SignalState.NO_TRADE, trace=common_trace, **base)
            signal.explanation = (
                f"realistic edge {edge.realistic_edge:+.2%} below the {self.config.min_edge:.2%} threshold "
                f"after spread, slippage, fees and uncertainty"
            )
            signal.model_probability = prediction.probability
            signal.calibrated_probability = calibration_used
            signal.confidence = prediction.confidence
            return signal

        if prediction.confidence < self.config.min_confidence:
            signal = Signal.from_edge(edge, state=SignalState.NO_TRADE, trace=common_trace, **base)
            signal.explanation = (
                f"edge {edge.realistic_edge:+.2%} is attractive but confidence "
                f"{prediction.confidence:.2f} is below the {self.config.min_confidence:.2f} requirement"
            )
            signal.model_probability = prediction.probability
            signal.calibrated_probability = calibration_used
            signal.confidence = prediction.confidence
            return signal

        if self.config.require_calibration and prediction.calibration_version == "none":
            return Signal.blocked(
                SignalState.NO_TRADE, "calibration required but no calibrator is fitted",
                edge_breakdown=edge.as_dict(), trace=common_trace, **base,
            )

        signal = Signal.from_edge(edge, state=SignalState.BUY, trace=common_trace, **base)
        signal.model_probability = prediction.probability
        signal.calibrated_probability = calibration_used
        signal.confidence = prediction.confidence
        signal.explanation = (
            f"estimated probability {calibration_used:.2%} materially exceeds the executable market price "
            f"{edge.market_price:.2%}; realistic edge {edge.realistic_edge:+.2%} after uncertainty and "
            f"execution adjustments (confidence {prediction.confidence:.2f})"
        )
        self.stats["buy"] = self.stats.get("buy", 0) + 1
        return signal

    def _blocked_from_features(self, features: FeatureSet, market: Market, state: SignalState,
                               reason: str, prediction: Prediction | None = None) -> Signal:
        trace: dict[str, Any] = {
            "feature_key": features.feature_key,
            "features": features.values,
            "missing": features.missing,
            "plan": features.plan,
            "context": features.context,
        }
        if prediction is not None:
            trace["prediction"] = prediction.as_dict()
        return Signal.blocked(
            state, reason,
            market_id=market.market_id, match_key=market.match_key,
            market_type=market.market_type.value, selection=market.selection,
            label=market.label, trace=trace,
        )

    def _attach_context(self, features: FeatureSet, market: Market) -> None:
        """Pass through market-specific context the models need."""
        context = features.context
        context.setdefault("selection", market.selection)
        context.setdefault("selection_kind", market.selection_kind)
        context.setdefault("market_type", market.market_type.value)
        if market.line is not None:
            context.setdefault("line", market.line)
        if market.period != "FULL":
            context.setdefault("period", market.period)
        # Which team does the selection refer to?
        selection_text = (market.selection or "").lower()
        home = (market.home_team or "").lower()
        away = (market.away_team or "").lower()
        side = ""
        team_side = ""
        if home and selection_text.startswith(home[:6]):
            side, team_side = "HOME", "HOME"
        elif away and selection_text.startswith(away[:6]):
            side, team_side = "AWAY", "AWAY"
        if side:
            context.setdefault("selection_side", side)
            context.setdefault("team_side", team_side)

    def _league_key(self, market: Market) -> str:
        return (market.league or "unknown").strip().lower()

    def _matches_for(self, league_key: str) -> list[MatchRecord]:
        history = self.history or TeamHistory([])
        pool = history.by_league.get(league_key)
        if pool:
            return pool
        # Fall back to the full pool ONLY for player props, where the player's
        # team history matters more than the league label.
        return history.matches
