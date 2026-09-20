"""Position sizing: fractional Kelly, always capped by hard limits.

Kelly for a binary contract bought at price ``p`` with true probability ``q``:

    b = (1 - p) / p            # net odds
    f* = (q * b - (1 - q)) / b = (q - p) / (1 - p)

We use ``fraction * f*`` (default 0.25) and then clamp by ``max_trade``. A
negative edge produces size 0 - never a short by accident.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class SizingResult:
    kelly_fraction: float
    full_kelly: float
    fraction: float
    raw_size: float
    capped_size: float
    binding_constraint: str = ""
    notes: list[str] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.notes is None:
            self.notes = []

    def as_dict(self) -> dict:
        return {
            "kelly_fraction": round(self.kelly_fraction, 4),
            "full_kelly": round(self.full_kelly, 6),
            "fraction": round(self.fraction, 4),
            "raw_size": round(self.raw_size, 4),
            "capped_size": round(self.capped_size, 4),
            "binding_constraint": self.binding_constraint,
            "notes": self.notes,
        }


def kelly_fraction_for_binary(probability: float, price: float) -> float:
    """Full-Kelly stake as a fraction of bankroll for a YES contract."""
    if not (0 < price < 1):
        return 0.0
    if probability <= price:
        return 0.0
    return (probability - price) / (1.0 - price)


def compute_size(
    probability: float,
    price: float,
    bankroll: float,
    fraction: float = 0.25,
    max_trade: float = 10.0,
    min_trade: float = 1.0,
) -> SizingResult:
    full = kelly_fraction_for_binary(probability, price)
    raw = max(0.0, full * fraction * bankroll)
    capped = min(raw, max_trade)
    binding = "max_trade" if raw > max_trade else ""
    notes: list[str] = []
    if raw < min_trade and raw > 0:
        notes.append(f"raw Kelly size {raw:.2f} below minimum trade {min_trade:.2f} - skipped")
        capped = 0.0
        binding = "min_trade"
    if full == 0.0:
        notes.append("no positive Kelly edge at the current executable price")
    if fraction > 0.5:
        notes.append("fraction above 0.5 is aggressive for a prediction-market edge estimate")
    return SizingResult(
        kelly_fraction=full, full_kelly=full, fraction=fraction,
        raw_size=raw, capped_size=max(0.0, capped), binding_constraint=binding, notes=notes,
    )
