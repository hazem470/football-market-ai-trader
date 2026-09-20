"""Performance analytics: model quality vs trading performance, reported apart."""
from __future__ import annotations

from dataclasses import dataclass, field

from src.calibration.calibrators import brier_score, log_loss, reliability_curve


@dataclass
class PerformanceReport:
    """Separates MODEL PERFORMANCE from TRADING PERFORMANCE by design."""

    n_predictions: int = 0
    predictions: list[float] = field(default_factory=list)
    outcomes: list[int] = field(default_factory=list)
    trades: list[dict] = field(default_factory=list)
    equity_curve: list[tuple[float, float]] = field(default_factory=list)

    # ------------------------------------------------------------- model side
    def model_metrics(self) -> dict:
        if not self.predictions:
            return {"n_predictions": 0, "note": "no model observations recorded"}
        curve = reliability_curve(self.predictions, self.outcomes)
        return {
            "n_predictions": len(self.predictions),
            "brier_score": round(brier_score(self.predictions, self.outcomes), 6),
            "log_loss": round(log_loss(self.predictions, self.outcomes), 6),
            "base_rate": round(sum(self.outcomes) / len(self.outcomes), 4),
            "expected_calibration_error": curve.expected_calibration_error,
            "reliability": curve.as_dict(),
        }

    # ----------------------------------------------------------- trading side
    def trading_metrics(self) -> dict:
        if not self.trades:
            return {"n_trades": 0, "note": "no trades recorded"}
        staked = sum(float(t.get("stake") or t.get("notional") or 0) for t in self.trades)
        pnl = sum(float(t.get("pnl") or 0) for t in self.trades)
        wins = [t for t in self.trades if float(t.get("pnl") or 0) > 0]
        losses = [t for t in self.trades if float(t.get("pnl") or 0) <= 0]
        gross_win = sum(float(t.get("pnl") or 0) for t in wins)
        gross_loss = abs(sum(float(t.get("pnl") or 0) for t in losses))
        slippages = [float(t.get("slippage") or 0) for t in self.trades if t.get("slippage") is not None]
        prices = [float(t.get("price") or t.get("avg_price") or 0) for t in self.trades]
        edges = [float(t.get("edge") or 0) for t in self.trades if t.get("edge") is not None]
        drawdown = self._max_drawdown()
        return {
            "n_trades": len(self.trades),
            "wins": len(wins),
            "losses": len(losses),
            "win_rate": round(len(wins) / len(self.trades), 4),
            "gross_pnl": round(gross_win, 2),
            "net_pnl": round(pnl, 2),
            "total_staked": round(staked, 2),
            "roi": round(pnl / staked, 4) if staked > 0 else 0.0,
            "profit_factor": round(gross_win / gross_loss, 4) if gross_loss > 0 else None,
            "max_drawdown": round(drawdown, 4),
            "average_edge": round(sum(edges) / len(edges), 4) if edges else None,
            "average_execution_price": round(sum(prices) / len(prices), 4) if prices else None,
            "average_slippage": round(sum(slippages) / len(slippages), 6) if slippages else None,
        }

    def _max_drawdown(self) -> float:
        if self.equity_curve:
            equity = [value for _ts, value in self.equity_curve]
        else:
            equity = []
            running = 0.0
            for trade in self.trades:
                running += float(trade.get("pnl") or 0)
                equity.append(running)
        peak = 0.0
        worst = 0.0
        for value in equity:
            peak = max(peak, value)
            if peak > 0:
                worst = max(worst, (peak - value) / peak)
        return worst

    def expected_value_per_trade(self) -> float | None:
        if not self.trades:
            return None
        return round(sum(float(t.get("pnl") or 0) for t in self.trades) / len(self.trades), 4)

    def report(self) -> dict:
        return {
            "MODEL_PERFORMANCE": self.model_metrics(),
            "TRADING_PERFORMANCE": self.trading_metrics(),
            "expected_value_per_trade": self.expected_value_per_trade(),
            "note": (
                "Model performance and trading performance are reported separately. "
                "A well-calibrated model does not imply profitable execution."
            ),
        }

    def render(self) -> str:
        report = self.report()
        model = report["MODEL_PERFORMANCE"]
        trading = report["TRADING_PERFORMANCE"]
        lines = [
            "-- MODEL PERFORMANCE ------------------------------------------------",
            f"  predictions              : {model.get('n_predictions')}",
            f"  Brier score              : {model.get('brier_score')}",
            f"  Log loss                 : {model.get('log_loss')}",
            f"  Calibration error (ECE)  : {model.get('expected_calibration_error')}",
            "-- TRADING PERFORMANCE ----------------------------------------------",
            f"  trades                   : {trading.get('n_trades')}",
        ]
        if trading.get("n_trades"):
            lines += [
                f"  win rate                 : {trading.get('win_rate', 0):.2%}",
                f"  net P/L                  : {trading.get('net_pnl', 0):+.2f}",
                f"  ROI                      : {trading.get('roi', 0):+.2%}",
                f"  max drawdown             : {trading.get('max_drawdown', 0):.2%}",
                f"  average edge             : {trading.get('average_edge')}",
                f"  average execution price  : {trading.get('average_execution_price')}",
                f"  average slippage         : {trading.get('average_slippage')}",
            ]
        return "\n".join(lines)


def summarize_signals(signals: list[dict]) -> dict:
    """Aggregate persisted signals for the dashboard."""
    counts: dict[str, int] = {}
    edges: list[float] = []
    for signal in signals:
        state = str(signal.get("state", "UNKNOWN"))
        counts[state] = counts.get(state, 0) + 1
        if signal.get("edge") is not None:
            edges.append(float(signal["edge"]))
    return {
        "total": len(signals),
        "by_state": counts,
        "average_edge": round(sum(edges) / len(edges), 4) if edges else None,
        "max_edge": round(max(edges), 4) if edges else None,
        "actionable": counts.get("BUY", 0) + counts.get("SELL", 0),
        "refusal_rate": round(
            (counts.get("NO_TRADE", 0) + counts.get("INSUFFICIENT_DATA", 0) + counts.get("UNSUPPORTED", 0))
            / len(signals), 4
        ) if signals else 0.0,
    }
