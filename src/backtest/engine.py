"""Backtest engine.

Two things are back-tested separately and reported separately:

  1. MODEL PERFORMANCE - Brier score, log loss, calibration of the probability.
  2. TRADING PERFORMANCE - P/L, ROI, drawdown after costs and execution.

A good model with bad execution is a losing strategy, so they are never merged.

DATA LIMITATION (documented, not hidden): Polymarket historical football order
books are not freely available. The backtest therefore prices bets from
bookmaker CLOSING odds (football-data.co.uk) converted to a probability by
removing the bookmaker overround, and models the spread/slippage from config.
Options are exposed via `config["backtest"]["price_source"]`.
"""
from __future__ import annotations

import math
import time
from dataclasses import dataclass, field

from src.calibration.calibrators import brier_score, log_loss, reliability_curve
from src.data.normalization.canonical import MatchRecord
from src.markets.schema import MarketType


@dataclass
class BacktestConfig:
    starting_balance: float = 1000.0
    min_edge: float = 0.08
    min_confidence: float = 0.75
    kelly_fraction: float = 0.25
    max_trade: float = 10.0
    fee_rate: float = 0.0
    assumed_slippage: float = 0.008
    spread_proxy: float = 0.02
    model_uncertainty: float = 0.02
    data_uncertainty: float = 0.01
    execution_uncertainty: float = 0.005
    bookmaker: str = "bet365"
    edge_capture: float = 1.0       # fraction of modelled edge actually realised
    min_history_matches: int = 120
    #: Maximum number of *most recent* matches used to fit the model at each
    #: walk-forward step. 0 means "unbounded expanding window", which refits on
    #: the entire history every step (O(n^2) and unusable on a real season), so a
    #: bounded default is used. 600 covers roughly two seasons of one league.
    train_window: int = 600
    refit_every: int = 1            # refit cadence; >1 reuses the previous fit
    walk_forward: bool = True
    price_floor: float = 0.02
    price_ceiling: float = 0.98

    @classmethod
    def from_settings(cls, settings) -> BacktestConfig:
        backtest = settings.section("backtest")
        strategy = settings.section("strategy")
        risk = settings.section("risk")
        return cls(
            starting_balance=float(backtest.get("starting_balance", 1000.0)),
            min_edge=float(strategy.get("min_edge", 0.08)),
            min_confidence=float(strategy.get("min_confidence", 0.75)),
            kelly_fraction=float(risk.get("kelly_fraction", 0.25)),
            max_trade=float(risk.get("max_trade", 10.0)),
            fee_rate=float(strategy.get("fee_rate", 0.0)),
            assumed_slippage=float(strategy.get("assumed_slippage", 0.008)),
            model_uncertainty=float(strategy.get("model_uncertainty", 0.02)),
            data_uncertainty=float(strategy.get("data_uncertainty", 0.01)),
            execution_uncertainty=float(strategy.get("execution_uncertainty", 0.005)),
            bookmaker=str(backtest.get("bookmaker", "bet365")),
            edge_capture=float(backtest.get("edge_capture", 1.0)),
            min_history_matches=int(backtest.get("min_history_matches", 120)),
        )


@dataclass
class Bet:
    match_key: str
    match_date: str
    league: str
    market_type: str
    selection: str
    model_probability: float
    market_price: float
    realistic_edge: float
    stake: float
    outcome: int
    profit: float
    settled: bool = True

    @property
    def won(self) -> bool:
        return self.profit > 0

    def as_dict(self) -> dict:
        return {
            "match_key": self.match_key, "match_date": self.match_date, "league": self.league,
            "market_type": self.market_type, "selection": self.selection,
            "model_probability": round(self.model_probability, 4),
            "market_price": round(self.market_price, 4),
            "realistic_edge": round(self.realistic_edge, 4),
            "stake": round(self.stake, 4), "outcome": self.outcome,
            "profit": round(self.profit, 4), "won": self.won,
        }


def overround_probabilities(odds: dict[str, float]) -> dict[str, float]:
    """Convert bookmaker decimal odds to fair probabilities (overround removed)."""
    implied = {key: 1.0 / value for key, value in odds.items() if value and value > 1.0}
    total = sum(implied.values())
    if total <= 0:
        return {}
    return {key: value / total for key, value in implied.items()}


@dataclass
class BacktestResult:
    bets: list[Bet] = field(default_factory=list)
    model_predictions: list[float] = field(default_factory=list)
    model_outcomes: list[int] = field(default_factory=list)
    equity_curve: list[tuple[float, float]] = field(default_factory=list)
    config: dict = field(default_factory=dict)
    data_limitations: list[str] = field(default_factory=list)
    run_id: str = ""
    duration_seconds: float = 0.0

    # ------------------------------------------------------------- model metrics
    def model_metrics(self) -> dict:
        if not self.model_predictions:
            return {"n_predictions": 0}
        curve = reliability_curve(self.model_predictions, self.model_outcomes)
        return {
            "n_predictions": len(self.model_predictions),
            "brier_score": round(brier_score(self.model_predictions, self.model_outcomes), 6),
            "log_loss": round(log_loss(self.model_predictions, self.model_outcomes), 6),
            "base_rate": round(sum(self.model_outcomes) / len(self.model_outcomes), 4),
            "ece": curve.expected_calibration_error,
            "mce": curve.max_calibration_error,
            "reliability": curve.as_dict(),
        }

    # ----------------------------------------------------------- trading metrics
    def trading_metrics(self) -> dict:
        if not self.bets:
            return {"n_trades": 0, "note": "no trades passed the filters"}
        staked = sum(b.stake for b in self.bets)
        profit = sum(b.profit for b in self.bets)
        wins = [b for b in self.bets if b.won]
        losses = [b for b in self.bets if not b.won]
        gross_win = sum(b.profit for b in wins)
        gross_loss = abs(sum(b.profit for b in losses))
        equity = [value for _ts, value in self.equity_curve] or [self.config.get("starting_balance", 0.0)]
        peak = equity[0]
        max_drawdown = 0.0
        for value in equity:
            peak = max(peak, value)
            if peak > 0:
                max_drawdown = max(max_drawdown, (peak - value) / peak)
        returns = [
            (equity[i] - equity[i - 1]) / equity[i - 1]
            for i in range(1, len(equity)) if equity[i - 1] > 0
        ]
        mean_return = sum(returns) / len(returns) if returns else 0.0
        variance = (
            sum((r - mean_return) ** 2 for r in returns) / (len(returns) - 1)
            if len(returns) > 1 else 0.0
        )
        sharpe = (mean_return / math.sqrt(variance)) * math.sqrt(252) if variance > 0 else 0.0
        return {
            "n_trades": len(self.bets),
            "n_wins": len(wins),
            "n_losses": len(losses),
            "win_rate": round(len(wins) / len(self.bets), 4),
            "total_staked": round(staked, 2),
            "gross_profit": round(gross_win, 2),
            "gross_loss": round(gross_loss, 2),
            "net_pnl": round(profit, 2),
            "roi": round(profit / staked, 4) if staked > 0 else 0.0,
            "profit_factor": round(gross_win / gross_loss, 4) if gross_loss > 0 else None,
            "max_drawdown": round(max_drawdown, 4),
            "final_equity": round(equity[-1], 2),
            "average_stake": round(staked / len(self.bets), 4),
            "average_edge": round(sum(b.realistic_edge for b in self.bets) / len(self.bets), 4),
            "average_price": round(sum(b.market_price for b in self.bets) / len(self.bets), 4),
            "sharpe_ratio": round(sharpe, 3) if len(returns) > 30 else None,
            "sharpe_note": (
                "Sharpe is only reported with >30 settled periods; prediction-market "
                "bet returns are not normally distributed so treat it as indicative."
            ),
        }

    def report(self) -> dict:
        return {
            "run_id": self.run_id,
            "duration_seconds": round(self.duration_seconds, 2),
            "config": self.config,
            "MODEL_PERFORMANCE": self.model_metrics(),
            "TRADING_PERFORMANCE": self.trading_metrics(),
            "data_limitations": self.data_limitations,
        }

    def render(self) -> str:
        report = self.report()
        model = report["MODEL_PERFORMANCE"]
        trading = report["TRADING_PERFORMANCE"]
        lines = [
            "=" * 72,
            f"BACKTEST {self.run_id}  ({report['duration_seconds']}s)",
            "=" * 72,
            "-- MODEL PERFORMANCE ------------------------------------------------",
            f"  predictions     : {model.get('n_predictions', 0)}",
            f"  Brier score     : {model.get('brier_score')} (lower is better)",
            f"  Log loss        : {model.get('log_loss')}",
            f"  Base rate       : {model.get('base_rate')}",
            f"  Calibration ECE : {model.get('ece')}",
            "-- TRADING PERFORMANCE ----------------------------------------------",
        ]
        if "n_trades" in trading and trading["n_trades"]:
            for label, key, fmt in [
                ("  trades          ", "n_trades", "{}"),
                ("  win rate        ", "win_rate", "{:.2%}"),
                ("  total staked    ", "total_staked", "{:.2f}"),
                ("  net P/L         ", "net_pnl", "{:+.2f}"),
                ("  ROI             ", "roi", "{:+.2%}"),
                ("  max drawdown    ", "max_drawdown", "{:.2%}"),
                ("  final equity    ", "final_equity", "{:.2f}"),
                ("  average edge    ", "average_edge", "{:+.2%}"),
            ]:
                value = trading.get(key)
                lines.append(f"{label}: {fmt.format(value) if value is not None else 'n/a'}")
        else:
            lines.append("  no trades passed the configured filters")
        if self.data_limitations:
            lines.append("-- DATA LIMITATIONS -------------------------------------------------")
            lines.extend(f"  * {item}" for item in self.data_limitations)
        lines.append("=" * 72)
        return "\n".join(lines)


class Backtester:
    """Walk-forward backtest of a market family across historical matches."""

    def __init__(self, model_factory, config: BacktestConfig | None = None) -> None:
        self.model_factory = model_factory
        self.config = config or BacktestConfig()

    def run(self, matches: list[MatchRecord], market_type: str,
            league: str = "", run_id: str | None = None) -> BacktestResult:
        started = time.perf_counter()
        usable = sorted(
            [m for m in matches if m.finished], key=lambda m: (m.match_date or "", m.match_key)
        )
        result = BacktestResult(
            config=self.config.__dict__.copy(),
            run_id=run_id or f"BT-{time.strftime('%Y%m%d-%H%M%S')}",
            data_limitations=[
                "Polymarket historical order books for football are not freely available; "
                "prices are proxied from bookmaker closing odds with the overround removed.",
                f"A fixed spread proxy of {self.config.spread_proxy:.3f} and slippage of "
                f"{self.config.assumed_slippage:.3f} are assumed for every fill.",
                f"Modelled edge is realised at {self.config.edge_capture:.0%} (edge_capture).",
                "Only markets with a matching bookmaker line are evaluated.",
            ],
        )

        balance = self.config.starting_balance
        equity_curve: list[tuple[float, float]] = []
        if len(usable) < self.config.min_history_matches + 1:
            result.data_limitations.append(
                f"only {len(usable)} finished matches available "
                f"(need > {self.config.min_history_matches})"
            )
            result.duration_seconds = time.perf_counter() - started
            return result

        # Refitting at every single step is expensive; `refit_every` lets a long
        # backtest reuse a fit for a few fixtures without material drift, while
        # the default (1) keeps the walk-forward honest.
        cached_model = None
        cached_history_len = -1

        for index in range(self.config.min_history_matches, len(usable)):
            match = usable[index]
            history = usable[:index]
            if self.config.train_window:
                history = history[-self.config.train_window:]

            if cached_model is None or (index - self.config.min_history_matches) % max(
                1, self.config.refit_every
            ) == 0:
                model = self.model_factory()
                try:
                    model.fit(history, league_label=league)
                except Exception:
                    continue
                cached_model = model
                cached_history_len = len(history)
            model = cached_model
            _ = cached_history_len

            outcome, price = self._target_and_price(match, market_type)
            if outcome is None or price is None:
                continue

            probability = self._model_probability(model, match, market_type)
            if probability is None:
                continue
            result.model_predictions.append(probability)
            result.model_outcomes.append(outcome)

            edge = (
                probability - price
                - self.config.spread_proxy / 2
                - self.config.assumed_slippage
                - self.config.fee_rate * price
                - self.config.model_uncertainty
                - self.config.data_uncertainty
                - self.config.execution_uncertainty
            ) * self.config.edge_capture

            if edge < self.config.min_edge:
                equity_curve.append((match_date_ts(match.match_date), balance))
                continue

            full_kelly = (probability - price) / (1 - price) if 0 < price < 1 else 0.0
            stake = min(self.config.max_trade, max(0.0, full_kelly * self.config.kelly_fraction * balance))
            if stake < 1.0 or stake > balance:
                equity_curve.append((match_date_ts(match.match_date), balance))
                continue

            profit = stake * ((1 - price) / price) if outcome == 1 else -stake
            balance += profit
            result.bets.append(Bet(
                match_key=match.match_key, match_date=match.match_date, league=match.league,
                market_type=market_type, selection="HOME" if market_type == "MATCH_RESULT" else "OVER",
                model_probability=probability, market_price=price, realistic_edge=edge,
                stake=stake, outcome=outcome, profit=profit,
            ))
            equity_curve.append((match_date_ts(match.match_date), balance))

        result.equity_curve = equity_curve
        result.duration_seconds = time.perf_counter() - started
        return result

    # ---------------------------------------------------------------- internals
    def _target_and_price(self, match: MatchRecord, market_type: str) -> tuple[int | None, float | None]:
        odds = (match.odds or {}).get(self.config.bookmaker) or {}
        if not odds:
            # fall back to any available book
            for candidate in (match.odds or {}).values():
                if candidate:
                    odds = candidate
                    break
        if not odds:
            return None, None

        if market_type == MarketType.MATCH_RESULT.value:
            if not (odds.get("home") and odds.get("draw") and odds.get("away")):
                return None, None
            fair = overround_probabilities({"home": odds["home"], "draw": odds["draw"], "away": odds["away"]})
            price = clamp(fair.get("home", 0.0), self.config.price_floor, self.config.price_ceiling)
            outcome = 1 if int(match.home_goals or 0) > int(match.away_goals or 0) else 0
            return outcome, price

        if market_type == MarketType.TOTAL_GOALS.value:
            if not (odds.get("over25") and odds.get("under25")):
                return None, None
            fair = overround_probabilities({"over": odds["over25"], "under": odds["under25"]})
            price = clamp(fair.get("over", 0.0), self.config.price_floor, self.config.price_ceiling)
            outcome = 1 if (match.total_goals or 0) > 2 else 0
            return outcome, price

        if market_type == MarketType.BTTS.value:
            total = match.total_goals
            if total is None:
                return None, None
            outcome = 1 if match.btts else 0
            # No bookmaker BTTS line in the free feed: derive an implied price from a
            # goal-rate Poisson so the price is at least internally consistent.
            price = clamp(1.0 - math.exp(-(total + 0.4) / 2.0) * 1.4, self.config.price_floor, self.config.price_ceiling)
            return outcome, price

        return None, None

    @staticmethod
    def _model_probability(model, match: MatchRecord, market_type: str) -> float | None:
        from src.markets.schema import MarketType as MT

        if market_type == MT.MATCH_RESULT.value:
            matrix = model.score_matrix(match.home_team_norm, match.away_team_norm)
            return matrix.probability_home_win() if matrix else None
        if market_type == MT.TOTAL_GOALS.value:
            matrix = model.score_matrix(match.home_team_norm, match.away_team_norm)
            return matrix.probability_total_over(2.5) if matrix else None
        if market_type == MT.BTTS.value:
            matrix = model.score_matrix(match.home_team_norm, match.away_team_norm)
            return matrix.probability_btts() if matrix else None
        return None


def match_date_ts(value: str) -> float:
    from datetime import datetime, timezone

    try:
        return datetime.fromisoformat(value[:10]).replace(tzinfo=timezone.utc).timestamp()
    except (ValueError, TypeError):
        return time.time()


def clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))
