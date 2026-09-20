"""Calibration: scoring functions, fit quality, refusal to overfit tiny samples."""
from __future__ import annotations

import math
import random

import pytest

from src.calibration.calibrators import (
    CalibrationService,
    IdentityCalibrator,
    IsotonicCalibrator,
    PlattCalibrator,
    brier_score,
    brier_skill_score,
    log_loss,
    reliability_curve,
)


def test_brier_score_perfect_is_zero():
    assert brier_score([1.0, 0.0], [1, 0]) == pytest.approx(0.0)


def test_brier_score_worst_is_one():
    assert brier_score([0.0, 1.0], [1, 0]) == pytest.approx(1.0)


def test_brier_score_constant_half():
    assert brier_score([0.5, 0.5], [1, 0]) == pytest.approx(0.25)


def test_log_loss_reference_value():
    assert log_loss([0.5], [1]) == pytest.approx(-math.log(0.5))


def test_log_loss_is_clipped_not_infinite():
    assert math.isfinite(log_loss([0.0, 1.0], [1, 0]))


def test_log_loss_rejects_mismatched_lengths():
    with pytest.raises(ValueError):
        log_loss([0.5], [1, 0])


def test_brier_skill_score_reference_is_zero():
    predictions = [0.5] * 10
    outcomes = [1, 0] * 5
    assert brier_skill_score(predictions, outcomes) == pytest.approx(0.0, abs=1e-6)


def test_brier_skill_score_positive_for_good_forecasts():
    predictions = [0.9, 0.1] * 10
    outcomes = ([1, 0] * 10)
    assert brier_skill_score(predictions, outcomes) > 0


def test_reliability_curve_bins_and_error():
    predictions = [0.05] * 5 + [0.95] * 5
    outcomes = [0] * 5 + [1] * 5
    curve = reliability_curve(predictions, outcomes, n_bins=10)
    assert curve.expected_calibration_error < 0.1
    assert curve.bins


def test_identity_calibrator_passthrough():
    calibrator = IdentityCalibrator()
    assert calibrator.calibrate(0.37) == pytest.approx(0.37)
    assert calibrator.calibrate(1.5) == 1.0


def test_platt_learns_to_shrink_overconfident_predictions():
    rng = random.Random(7)
    predictions = [0.85] * 200
    outcomes = [1 if rng.random() < 0.65 else 0 for _ in range(200)]
    calibrator = PlattCalibrator().fit(predictions, outcomes)
    calibrated = calibrator.calibrate(0.85)
    assert 0.5 < calibrated < 0.85


def test_platt_preserves_ordering():
    predictions = [0.1 * i for i in range(1, 10)]
    outcomes = [1 if p > 0.5 else 0 for p in predictions]
    calibrator = PlattCalibrator().fit(predictions, outcomes)
    values = [calibrator.calibrate(p) for p in predictions]
    assert values == sorted(values)


def test_platt_unfitted_is_identity():
    assert PlattCalibrator().calibrate(0.42) == pytest.approx(0.42)


def test_isotonic_is_monotone():
    predictions = [0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9]
    outcomes = [0, 0, 0, 1, 1, 1, 1, 1, 1]
    calibrator = IsotonicCalibrator().fit(predictions, outcomes)
    values = [calibrator.calibrate(p) for p in predictions]
    assert values == sorted(values)


def test_isotonic_interpolates_between_knots():
    calibrator = IsotonicCalibrator().fit([0.2, 0.8], [0, 1])
    mid = calibrator.calibrate(0.5)
    assert 0 < mid < 1


def test_isotonic_extrapolates_to_knot_bounds():
    calibrator = IsotonicCalibrator().fit([0.2, 0.8], [0, 1])
    assert calibrator.calibrate(0.01) == pytest.approx(0.0)
    assert calibrator.calibrate(0.99) == pytest.approx(1.0)


def test_calibration_service_skips_tiny_samples(tmp_path):
    service = CalibrationService(method="platt", min_samples=30, artifact_path=tmp_path / "cal.json")
    metrics = service.fit([0.5] * 5, [1, 0, 1, 0, 1])
    assert "skipped" in metrics
    assert service.calibrator.name == "identity"


def test_calibration_service_fits_and_writes_artifact(tmp_path):
    rng = random.Random(3)
    predictions = [rng.uniform(0.05, 0.95) for _ in range(200)]
    outcomes = [1 if rng.random() < p * 0.8 else 0 for p in predictions]
    service = CalibrationService(method="platt", min_samples=30, artifact_path=tmp_path / "cal.json")
    metrics = service.fit(predictions, outcomes)
    assert metrics["n_samples"] == 200
    assert metrics["log_loss_calibrated"] <= metrics["log_loss_raw"] + 1e-6
    assert (tmp_path / "cal.json").exists()
    assert service.calibrator_version().startswith("platt")


def test_calibration_service_disabled_is_identity(tmp_path):
    service = CalibrationService(method="platt", enabled=False, artifact_path=tmp_path / "c.json")
    assert service.calibrate(0.33) == pytest.approx(0.33)
    assert service.calibrator_version() == "none"
