"""Probability calibration + scoring.

Raw model probabilities are not trustworthy out of the box. This module fits
Platt scaling / isotonic regression on out-of-sample predictions and tracks
Brier score, log loss and calibration error.

Nothing here is fitted on in-sample predictions: `Calibrator.fit` is meant to be
called from backtest walk-forward output.
"""
from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from pathlib import Path

EPS = 1e-6


def _logit(p: float) -> float:
    p = min(1 - EPS, max(EPS, p))
    return math.log(p / (1 - p))


def _sigmoid(x: float) -> float:
    if x >= 0:
        z = math.exp(-x)
        return 1 / (1 + z)
    z = math.exp(x)
    return z / (1 + z)


# ------------------------------------------------------------------- scoring
def brier_score(predictions: list[float], outcomes: list[int | float]) -> float:
    if not predictions or len(predictions) != len(outcomes):
        raise ValueError("predictions and outcomes must be equal-length non-empty lists")
    return sum((p - o) ** 2 for p, o in zip(predictions, outcomes, strict=False)) / len(predictions)


def log_loss(predictions: list[float], outcomes: list[int | float]) -> float:
    if not predictions or len(predictions) != len(outcomes):
        raise ValueError("predictions and outcomes must be equal-length non-empty lists")
    total = 0.0
    for p, o in zip(predictions, outcomes, strict=False):
        p = min(1 - EPS, max(EPS, p))
        total += -(o * math.log(p) + (1 - o) * math.log(1 - p))
    return total / len(predictions)


def brier_skill_score(predictions: list[float], outcomes: list[int | float], base_rate: float | None = None) -> float:
    """How much better than a constant base-rate forecast (0 = no better)."""
    if base_rate is None:
        base_rate = sum(outcomes) / len(outcomes)
    reference = brier_score([base_rate] * len(outcomes), outcomes)
    if reference <= 0:
        return 0.0
    return 1.0 - (brier_score(predictions, outcomes) / reference)


@dataclass
class CalibrationCurve:
    """Reliability table: predicted bucket vs observed frequency."""

    bins: list[tuple[float, float, int, float]] = field(default_factory=list)  # lo, hi, n, observed

    @property
    def expected_calibration_error(self) -> float:
        total = sum(item[2] for item in self.bins)
        if total == 0:
            return 0.0
        value = 0.0
        for lo, hi, count, observed in self.bins:
            midpoint = (lo + hi) / 2
            value += (count / total) * abs(observed - midpoint)
        return round(value, 6)

    @property
    def max_calibration_error(self) -> float:
        worst = 0.0
        for lo, hi, _count, observed in self.bins:
            midpoint = (lo + hi) / 2
            worst = max(worst, abs(observed - midpoint))
        return round(worst, 6)

    def as_dict(self) -> dict:
        return {
            "bins": [
                {"lo": lo, "hi": hi, "n": n, "observed": round(obs, 4)}
                for lo, hi, n, obs in self.bins
            ],
            "ece": self.expected_calibration_error,
            "mce": self.max_calibration_error,
        }


def reliability_curve(predictions: list[float], outcomes: list[int | float], n_bins: int = 10) -> CalibrationCurve:
    curve = CalibrationCurve()
    if not predictions:
        return curve
    width = 1.0 / n_bins
    for index in range(n_bins):
        lo = index * width
        hi = lo + width
        bucket = [
            (p, o) for p, o in zip(predictions, outcomes, strict=False)
            if (lo <= p < hi) or (index == n_bins - 1 and p == 1.0)
        ]
        if not bucket:
            continue
        observed = sum(o for _p, o in bucket) / len(bucket)
        curve.bins.append((round(lo, 3), round(hi, 3), len(bucket), observed))
    return curve


# --------------------------------------------------------------- calibrators
class Calibrator:
    name = "none"
    version = "1.0.0"
    fitted = False

    def fit(self, predictions: list[float], outcomes: list[int | float]) -> Calibrator:
        raise NotImplementedError

    def calibrate(self, probability: float) -> float:
        raise NotImplementedError

    def state(self) -> dict:
        return {"name": self.name, "version": self.version, "fitted": self.fitted}


class IdentityCalibrator(Calibrator):
    name = "identity"

    def __init__(self) -> None:
        self.fitted = True

    def fit(self, predictions: list[float], outcomes: list[int | float]) -> IdentityCalibrator:
        return self

    def calibrate(self, probability: float) -> float:
        return min(1.0, max(0.0, probability))


class PlattCalibrator(Calibrator):
    """Platt scaling: p' = sigmoid(a * logit(p) + b), fitted by gradient descent.

    No scikit-learn dependency; plain gradient descent on log loss with L2
    shrinkage toward the identity map so tiny samples cannot overfit wildly.
    """

    name = "platt"
    version = "1.0.0"

    def __init__(self, a: float = 1.0, b: float = 0.0, learning_rate: float = 0.35,
                 iterations: int = 400, l2: float = 1e-3) -> None:
        self.a = a
        self.b = b
        self.learning_rate = learning_rate
        self.iterations = iterations
        self.l2 = l2
        self.fitted = False
        self.n_samples = 0

    def fit(self, predictions: list[float], outcomes: list[int | float]) -> PlattCalibrator:
        if len(predictions) != len(outcomes) or not predictions:
            raise ValueError("need equal-length non-empty arrays")
        xs = [_logit(p) for p in predictions]
        ys = [float(o) for o in outcomes]
        n = len(xs)
        for _ in range(self.iterations):
            grad_a = 0.0
            grad_b = 0.0
            for x, y in zip(xs, ys, strict=False):
                p = _sigmoid(self.a * x + self.b)
                error = p - y
                grad_a += error * x
                grad_b += error
            grad_a = grad_a / n + self.l2 * (self.a - 1.0)
            grad_b = grad_b / n + self.l2 * self.b
            self.a -= self.learning_rate * grad_a
            self.b -= self.learning_rate * grad_b
        self.fitted = True
        self.n_samples = n
        return self

    def calibrate(self, probability: float) -> float:
        if not self.fitted:
            return min(1.0, max(0.0, probability))
        return min(1.0, max(0.0, _sigmoid(self.a * _logit(probability) + self.b)))

    def state(self) -> dict:
        return {"name": self.name, "version": self.version, "fitted": self.fitted,
                "a": round(self.a, 6), "b": round(self.b, 6), "n_samples": self.n_samples}


class IsotonicCalibrator(Calibrator):
    """Isotonic regression via pool-adjacent-violators, with linear interpolation."""

    name = "isotonic"
    version = "1.0.0"

    def __init__(self) -> None:
        self.x: list[float] = []
        self.y: list[float] = []
        self.fitted = False
        self.n_samples = 0

    def fit(self, predictions: list[float], outcomes: list[int | float]) -> IsotonicCalibrator:
        if len(predictions) != len(outcomes) or not predictions:
            raise ValueError("need equal-length non-empty arrays")
        pairs = sorted(zip(predictions, (float(o) for o in outcomes), strict=False), key=lambda pair: pair[0])
        xs = [p for p, _ in pairs]
        ys = [o for _, o in pairs]
        weights = [1.0] * len(ys)
        # pool adjacent violators
        i = 0
        while i < len(ys) - 1:
            if ys[i] > ys[i + 1]:
                total_weight = weights[i] + weights[i + 1]
                merged = (ys[i] * weights[i] + ys[i + 1] * weights[i + 1]) / total_weight
                ys[i] = merged
                weights[i] = total_weight
                del ys[i + 1]
                del xs[i + 1]
                del weights[i + 1]
                i = max(0, i - 1)
            else:
                i += 1
        self.x = xs
        self.y = ys
        self.fitted = True
        self.n_samples = len(predictions)
        return self

    def calibrate(self, probability: float) -> float:
        if not self.fitted or not self.x:
            return min(1.0, max(0.0, probability))
        if probability <= self.x[0]:
            return min(1.0, max(0.0, self.y[0]))
        if probability >= self.x[-1]:
            return min(1.0, max(0.0, self.y[-1]))
        for index in range(len(self.x) - 1):
            if self.x[index] <= probability <= self.x[index + 1]:
                span = self.x[index + 1] - self.x[index]
                if span <= 0:
                    return min(1.0, max(0.0, self.y[index]))
                ratio = (probability - self.x[index]) / span
                value = self.y[index] + ratio * (self.y[index + 1] - self.y[index])
                return min(1.0, max(0.0, value))
        return min(1.0, max(0.0, probability))

    def state(self) -> dict:
        return {"name": self.name, "version": self.version, "fitted": self.fitted,
                "knots": len(self.x), "n_samples": self.n_samples}


@dataclass
class CalibrationArtifact:
    """Persisted calibrator + its measured quality."""

    method: str = "none"
    version: str = "1.0.0"
    state: dict = field(default_factory=dict)
    metrics: dict = field(default_factory=dict)
    n_samples: int = 0
    fitted_at: float = 0.0

    def save(self, path: str | Path) -> Path:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.as_dict(), indent=2), encoding="utf-8")
        return path

    def as_dict(self) -> dict:
        return {
            "method": self.method, "version": self.version, "state": self.state,
            "metrics": self.metrics, "n_samples": self.n_samples, "fitted_at": self.fitted_at,
        }

    @classmethod
    def load(cls, path: str | Path) -> CalibrationArtifact | None:
        path = Path(path)
        if not path.exists():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        return cls(
            method=data.get("method", "none"), version=data.get("version", "1.0.0"),
            state=data.get("state", {}), metrics=data.get("metrics", {}),
            n_samples=int(data.get("n_samples", 0)), fitted_at=float(data.get("fitted_at", 0.0)),
        )


def build_calibrator(method: str) -> Calibrator:
    return {
        "platt": PlattCalibrator,
        "isotonic": IsotonicCalibrator,
        "none": IdentityCalibrator,
        "identity": IdentityCalibrator,
    }.get((method or "").lower(), IdentityCalibrator)()


@dataclass
class CalibrationService:
    """Wraps a calibrator with quality metrics and an artifact on disk."""

    method: str = "platt"
    min_samples: int = 30
    artifact_path: str | Path = "artifacts/calibration.json"
    enabled: bool = True
    calibrator: Calibrator = field(default_factory=IdentityCalibrator)
    metrics: dict = field(default_factory=dict)
    version: str = "1.0.0"

    def fit(self, predictions: list[float], outcomes: list[int | float]) -> dict:
        if not self.enabled or len(predictions) < self.min_samples:
            self.calibrator = IdentityCalibrator()
            self.metrics = self._metrics(predictions, outcomes, [self.calibrator.calibrate(p) for p in predictions])
            self.metrics["skipped"] = (
                f"only {len(predictions)} samples (< min_samples={self.min_samples}); identity calibration used"
            )
            return self.metrics
        self.calibrator = build_calibrator(self.method)
        self.calibrator.fit(predictions, outcomes)
        calibrated = [self.calibrator.calibrate(p) for p in predictions]
        self.metrics = self._metrics(predictions, outcomes, calibrated)
        CalibrationArtifact(
            method=self.calibrator.name,
            version=self.calibrator.version,
            state=self.calibrator.state(),
            metrics=self.metrics,
            n_samples=len(predictions),
            fitted_at=__import__("time").time(),
        ).save(self.artifact_path)
        return self.metrics

    @staticmethod
    def _metrics(predictions: list[float], outcomes: list[int | float], calibrated: list[float]) -> dict:
        if not predictions:
            return {}
        curve = reliability_curve(calibrated, outcomes)
        return {
            "n_samples": len(predictions),
            "brier_raw": round(brier_score(predictions, outcomes), 6),
            "brier_calibrated": round(brier_score(calibrated, outcomes), 6),
            "log_loss_raw": round(log_loss(predictions, outcomes), 6),
            "log_loss_calibrated": round(log_loss(calibrated, outcomes), 6),
            "brier_skill_score": round(brier_skill_score(calibrated, outcomes), 6),
            "reliability": curve.as_dict(),
        }

    def calibrate(self, probability: float) -> float:
        if not self.enabled:
            return min(1.0, max(0.0, probability))
        return self.calibrator.calibrate(probability)

    def calibrator_version(self) -> str:
        if not self.enabled:
            return "none"
        return f"{self.calibrator.name}-{self.calibrator.version}"

    def as_dict(self) -> dict:
        return {
            "method": self.method, "enabled": self.enabled,
            "calibrator_version": self.calibrator_version(),
            "calibrator": self.calibrator.state(), "metrics": self.metrics,
        }
