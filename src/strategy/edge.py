"""Edge engine: model probability vs executable market price, after costs.

Raw edge is a trap. This module converts a model probability and a real order
book into the *realistic* edge actually available to a taker, and refuses to
produce a number when it does not have the inputs to do so honestly.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from src.markets.schema import Market, MarketSnapshot


@dataclass
class CostConfig:
    fee_rate: float = 0.0
    assumed_slippage: float = 0.008
    model_uncertainty: float = 0.02
    data_uncertainty: float = 0.01
    execution_uncertainty: float = 0.005

    def total_uncertainty(self) -> float:
        return self.model_uncertainty + self.data_uncertainty + self.execution_uncertainty


@dataclass
class EdgeBreakdown:
    """Full, auditable decomposition of the edge calculation."""

    model_probability: float
    calibrated_probability: float
    market_price: float
    raw_edge: float
    spread_cost: float
    slippage_cost: float
    fee_cost: float
    model_uncertainty: float
    data_uncertainty: float
    execution_uncertainty: float
    realistic_edge: float
    side: str = "BUY"
    price_source: str = ""
    notes: list[str] = field(default_factory=list)
    incomplete_reason: str = ""

    @property
    def is_complete(self) -> bool:
        return not self.incomplete_reason

    def as_dict(self) -> dict:
        return {
            "model_probability": round(self.model_probability, 6),
            "calibrated_probability": round(self.calibrated_probability, 6),
            "market_price": round(self.market_price, 6),
            "raw_edge": round(self.raw_edge, 6),
            "spread_cost": round(self.spread_cost, 6),
            "slippage_cost": round(self.slippage_cost, 6),
            "fee_cost": round(self.fee_cost, 6),
            "model_uncertainty": round(self.model_uncertainty, 6),
            "data_uncertainty": round(self.data_uncertainty, 6),
            "execution_uncertainty": round(self.execution_uncertainty, 6),
            "realistic_edge": round(self.realistic_edge, 6),
            "side": self.side,
            "price_source": self.price_source,
            "notes": self.notes,
            "complete": self.is_complete,
        }

    def table(self) -> str:
        rows = [
            ("Model probability", f"{self.model_probability:.2%}"),
            ("Calibrated probability", f"{self.calibrated_probability:.2%}"),
            ("Executable market price", f"{self.market_price:.2%}"),
            ("Raw edge", f"{self.raw_edge:+.2%}"),
            ("Spread cost", f"-{self.spread_cost:.2%}"),
            ("Slippage", f"-{self.slippage_cost:.2%}"),
            ("Fees", f"-{self.fee_cost:.2%}"),
            ("Model uncertainty", f"-{self.model_uncertainty:.2%}"),
            ("Data uncertainty", f"-{self.data_uncertainty:.2%}"),
            ("Execution uncertainty", f"-{self.execution_uncertainty:.2%}"),
            ("REALISTIC EDGE", f"{self.realistic_edge:+.2%}"),
        ]
        width = max(len(label) for label, _ in rows)
        return "\n".join(f"{label:<{width}} | {value:>9}" for label, value in rows)


def calculate_edge(
    probability: float,
    market: Market,
    config: CostConfig | None = None,
    side: str = "BUY",
    calibrated: bool = True,
) -> EdgeBreakdown:
    """Compute the realistic edge for the YES side of a market.

    ``probability`` is the model's (already calibrated) probability that the
    YES token resolves true. Everything is expressed in probability points.
    """
    config = config or CostConfig()
    snapshot: MarketSnapshot | None = market.snapshot
    incomplete = ""

    if snapshot is None:
        incomplete = "no order-book snapshot"
        executable = market.snapshot.price if market.snapshot else 0.0
        executable = executable or 0.0
        spread = 0.0
        source = "none"
    else:
        executable = snapshot.executable_buy_price if side.upper() == "BUY" else snapshot.executable_sell_price
        source = snapshot.source
        if executable is None:
            incomplete = "no executable price (no ask/bid/price)"
            executable = 0.0
        spread = snapshot.spread or 0.0

    # Half the spread is the fair share of crossing cost attributable to a taker.
    spread_cost = spread / 2.0
    slippage_cost = config.assumed_slippage
    fee_cost = config.fee_rate * max(0.0, executable)

    raw_edge = (probability - executable) if side.upper() == "BUY" else (executable - probability)
    realistic = (
        raw_edge
        - spread_cost
        - slippage_cost
        - fee_cost
        - config.model_uncertainty
        - config.data_uncertainty
        - config.execution_uncertainty
    )

    breakdown = EdgeBreakdown(
        model_probability=probability,
        calibrated_probability=probability if calibrated else probability,
        market_price=executable,
        raw_edge=raw_edge,
        spread_cost=spread_cost,
        slippage_cost=slippage_cost,
        fee_cost=fee_cost,
        model_uncertainty=config.model_uncertainty,
        data_uncertainty=config.data_uncertainty,
        execution_uncertainty=config.execution_uncertainty,
        realistic_edge=realistic,
        side=side.upper(),
        price_source=source,
        incomplete_reason=incomplete,
    )
    if spread > 0.1:
        breakdown.notes.append(f"very wide spread ({spread:.3f}) - execution risk elevated")
    if snapshot is not None and snapshot.liquidity is not None and snapshot.liquidity < 200:
        breakdown.notes.append(f"thin liquidity ({snapshot.liquidity:.0f})")
    return breakdown


def required_probability_for_edge(market_price: float, min_edge: float,
                                  config: CostConfig, side: str = "BUY") -> float:
    """Inverse question: what model probability is needed to clear `min_edge`?"""
    overhead = (
        config.assumed_slippage + config.fee_rate * market_price + config.model_uncertainty
        + config.data_uncertainty + config.execution_uncertainty
    )
    if side.upper() == "BUY":
        return market_price + overhead + min_edge
    return market_price - overhead - min_edge
