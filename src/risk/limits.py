"""Risk limits - the last word before any order.

The risk engine ALWAYS overrides model sizing. If a limit cannot be evaluated
because data is missing, the answer is NO TRADE, never "assume it is fine".
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum


class RiskVerdict(str, Enum):
    ALLOW = "ALLOW"
    DENY = "DENY"


@dataclass
class RiskLimits:
    capital: float = 500.0
    max_trade: float = 10.0
    max_daily_exposure: float = 50.0
    max_daily_loss: float = 20.0
    max_open_positions: int = 6
    min_liquidity: float = 100.0
    max_slippage: float = 0.02
    max_spread: float = 0.04
    max_correlated_exposure: float = 25.0
    min_edge: float = 0.08
    min_confidence: float = 0.75
    kelly_fraction: float = 0.25
    max_price_age_seconds: float = 60.0

    @classmethod
    def from_config(cls, config: dict, strategy: dict | None = None) -> RiskLimits:
        strategy = strategy or {}
        return cls(
            capital=float(config.get("capital", 500.0)),
            max_trade=float(config.get("max_trade", 10.0)),
            max_daily_exposure=float(config.get("max_daily_exposure", 50.0)),
            max_daily_loss=float(config.get("max_daily_loss", 20.0)),
            max_open_positions=int(config.get("max_open_positions", 6)),
            min_liquidity=float(config.get("min_liquidity", 100.0)),
            max_slippage=float(config.get("max_slippage", 0.02)),
            max_spread=float(config.get("max_spread", 0.04)),
            max_correlated_exposure=float(config.get("max_correlated_exposure", 25.0)),
            min_edge=float(strategy.get("min_edge", 0.08)),
            min_confidence=float(strategy.get("min_confidence", 0.75)),
            kelly_fraction=float(config.get("kelly_fraction", 0.25)),
        )

    def validate(self) -> list[str]:
        problems: list[str] = []
        if self.capital <= 0:
            problems.append("capital must be > 0")
        if self.max_trade <= 0:
            problems.append("max_trade must be > 0")
        if self.max_trade > self.capital:
            problems.append("max_trade cannot exceed capital")
        if self.max_daily_exposure > self.capital:
            problems.append("max_daily_exposure cannot exceed capital")
        if self.max_daily_loss > self.capital:
            problems.append("max_daily_loss cannot exceed capital")
        if not (0 < self.kelly_fraction <= 1):
            problems.append("kelly_fraction must be in (0, 1]")
        if not (0 <= self.min_confidence <= 1):
            problems.append("min_confidence must be in [0, 1]")
        return problems


@dataclass
class RiskDecision:
    verdict: RiskVerdict = RiskVerdict.DENY
    size: float = 0.0
    reasons: list[str] = field(default_factory=list)
    checks: dict[str, str] = field(default_factory=dict)
    evaluated_at: float = field(default_factory=time.time)

    @property
    def allowed(self) -> bool:
        return self.verdict is RiskVerdict.ALLOW and self.size > 0

    def deny(self, reason: str) -> RiskDecision:
        self.verdict = RiskVerdict.DENY
        self.size = 0.0
        self.reasons.append(reason)
        return self

    def as_dict(self) -> dict:
        return {
            "verdict": self.verdict.value,
            "size": round(self.size, 4),
            "reasons": self.reasons,
            "checks": self.checks,
        }
