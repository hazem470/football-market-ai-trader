"""Prediction model interfaces.

Rule: an LLM is NEVER the primary numerical probability model. Every model here
is a deterministic statistical estimator that can be fitted, versioned,
calibrated and back-tested.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field

from src.features.engine import FeatureSet


class ModelNotFitted(RuntimeError):
    """Raised when a model is asked to predict before it has been fitted."""


class InsufficientModelData(RuntimeError):
    """Raised when there is not enough history to fit a defensible model."""


@dataclass
class Prediction:
    """A model output: probability plus everything needed to audit it."""

    model_name: str
    market_type: str
    probability: float
    confidence: float = 0.5
    model_version: str = "1.0.0"
    feature_version: str = "1.0.0"
    calibration_version: str = "none"
    raw_probability: float | None = None
    calibrated_probability: float | None = None
    data_timestamp: float = 0.0
    selection: str = ""
    market_id: str = ""
    match_key: str = ""
    diagnostics: dict = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)
    insufficient_data: bool = False

    @property
    def effective_probability(self) -> float:
        if self.calibrated_probability is not None:
            return self.calibrated_probability
        return self.probability

    def as_dict(self) -> dict:
        return {
            "model_name": self.model_name,
            "market_type": self.market_type,
            "probability": self.probability,
            "calibrated_probability": self.calibrated_probability,
            "confidence": self.confidence,
            "model_version": self.model_version,
            "feature_version": self.feature_version,
            "calibration_version": self.calibration_version,
            "data_timestamp": self.data_timestamp,
            "selection": self.selection,
            "market_id": self.market_id,
            "match_key": self.match_key,
            "diagnostics": self.diagnostics,
            "notes": self.notes,
            "insufficient_data": self.insufficient_data,
        }

    @classmethod
    def insufficient(cls, *args, **kwargs) -> Prediction:
        """Build an explicit INSUFFICIENT_DATA prediction.

        Accepts either ``insufficient(model_name, market_type, reason, **fields)``
        or ``insufficient(model_name=..., market_type=..., reason=..., **fields)``.
        Positional args are used so that callers can safely splat a ``base`` dict
        that also carries ``model_name`` / ``market_type`` without a keyword clash.
        """
        model_name = args[0] if len(args) > 0 else kwargs.pop("model_name", "")
        market_type = args[1] if len(args) > 1 else kwargs.pop("market_type", "")
        reason = args[2] if len(args) > 2 else kwargs.pop("reason", "insufficient data")
        # Never let a duplicate slip through from a splatted base dict.
        kwargs.pop("model_name", None)
        kwargs.pop("market_type", None)
        return cls(
            model_name=model_name,
            market_type=market_type,
            probability=0.0,
            confidence=0.0,
            insufficient_data=True,
            notes=[reason],
            diagnostics={"reason": reason},
            **kwargs,
        )


class Model(ABC):
    """A market-family model."""

    name: str = "model"
    version: str = "1.0.0"
    market_types: tuple[str, ...] = ()

    @abstractmethod
    def fit(self, *args, **kwargs) -> Model:
        ...

    @abstractmethod
    def predict(self, features: FeatureSet) -> Prediction:
        ...

    def supports(self, market_type: str) -> bool:
        return market_type in self.market_types

    def describe(self) -> dict:
        return {"name": self.name, "version": self.version, "market_types": list(self.market_types)}
