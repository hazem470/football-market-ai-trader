"""Backtest: model metrics and trading metrics are separate and honest."""
from __future__ import annotations

import pytest

from src.backtest.engine import BacktestConfig, Backtester, overround_probabilities
from src.models.goals import BttsModel, TotalGoalsModel
from src.models.match_result import MatchResultModel


def test_overround_removal_normalises_to_one():
    fair = overround_probabilities({"home": 1.8, "draw": 3.6, "away": 4.5})
    assert sum(fair.values()) == pytest.approx(1.0)
    assert fair["home"] > fair["away"]


def test_overround_handles_empty_input():
    assert overround_probabilities({}) == {}


def test_backtest_produces_separate_reports(synthetic_history):
    config = BacktestConfig(starting_balance=1000, min_edge=0.02, min_history_matches=100,
                            max_trade=10.0)
    result = Backtester(model_factory=lambda: MatchResultModel(min_matches=100),
                        config=config).run(synthetic_history.matches, "MATCH_RESULT", league="E0")
    report = result.report()
    assert "MODEL_PERFORMANCE" in report and "TRADING_PERFORMANCE" in report
    assert report["MODEL_PERFORMANCE"]["n_predictions"] > 50
    assert "brier_score" in report["MODEL_PERFORMANCE"]
    assert "log_loss" in report["MODEL_PERFORMANCE"]


def test_backtest_documents_its_data_limitations(synthetic_history):
    result = Backtester(model_factory=lambda: MatchResultModel(min_matches=100),
                        config=BacktestConfig(min_history_matches=100)).run(
        synthetic_history.matches, "MATCH_RESULT", league="E0")
    assert result.data_limitations
    assert any("bookmaker" in item for item in result.data_limitations)


def test_backtest_refuses_insufficient_history():
    from tests.conftest import make_match

    result = Backtester(model_factory=lambda: MatchResultModel(min_matches=100),
                        config=BacktestConfig(min_history_matches=100)).run(
        [make_match("A", "B", 1, 0)], "MATCH_RESULT")
    assert result.bets == []
    assert any("only" in item for item in result.data_limitations)


def test_backtest_stakes_respect_max_trade(synthetic_history):
    config = BacktestConfig(min_edge=0.0, min_history_matches=100, max_trade=5.0, kelly_fraction=1.0)
    result = Backtester(model_factory=lambda: MatchResultModel(min_matches=100),
                        config=config).run(synthetic_history.matches, "MATCH_RESULT", league="E0")
    assert all(bet.stake <= 5.0 + 1e-9 for bet in result.bets)


def test_backtest_high_edge_threshold_yields_no_trades(synthetic_history):
    config = BacktestConfig(min_edge=0.95, min_history_matches=100)
    result = Backtester(model_factory=lambda: MatchResultModel(min_matches=100),
                        config=config).run(synthetic_history.matches, "MATCH_RESULT", league="E0")
    assert result.bets == []
    assert "no trades" in result.trading_metrics()["note"]


def test_trading_metrics_are_internally_consistent(synthetic_history):
    result = Backtester(model_factory=lambda: MatchResultModel(min_matches=100),
                        config=BacktestConfig(min_edge=0.0, min_history_matches=100)).run(
        synthetic_history.matches, "MATCH_RESULT", league="E0")
    metrics = result.trading_metrics()
    if metrics.get("n_trades"):
        assert metrics["n_wins"] + metrics["n_losses"] == metrics["n_trades"]
        staked = metrics["total_staked"]
        assert metrics["roi"] == pytest.approx(metrics["net_pnl"] / staked, abs=1e-3)
        assert 0 <= metrics["max_drawdown"] <= 1


def test_total_goals_backtest_uses_the_over_under_line(synthetic_history):
    result = Backtester(model_factory=lambda: TotalGoalsModel(min_matches=100),
                        config=BacktestConfig(min_edge=0.0, min_history_matches=100)).run(
        synthetic_history.matches, "TOTAL_GOALS", league="E0")
    assert result.model_predictions
    assert all(0 <= p <= 1 for p in result.model_predictions)


def test_btts_backtest_runs(synthetic_history):
    result = Backtester(model_factory=lambda: BttsModel(min_matches=100),
                        config=BacktestConfig(min_edge=0.0, min_history_matches=100)).run(
        synthetic_history.matches, "BTTS", league="E0")
    assert result.model_predictions
    assert all(o in (0, 1) for o in result.model_outcomes)


def test_report_renders_without_crashing(synthetic_history):
    result = Backtester(model_factory=lambda: MatchResultModel(min_matches=100),
                        config=BacktestConfig(min_edge=0.0, min_history_matches=100)).run(
        synthetic_history.matches, "MATCH_RESULT", league="E0")
    rendered = result.render()
    assert "MODEL PERFORMANCE" in rendered
    assert "TRADING PERFORMANCE" in rendered


def test_sharpe_is_suppressed_with_too_few_periods(synthetic_history):
    result = Backtester(model_factory=lambda: MatchResultModel(min_matches=100),
                        config=BacktestConfig(min_edge=0.0, min_history_matches=100)).run(
        synthetic_history.matches, "MATCH_RESULT", league="E0")
    metrics = result.trading_metrics()
    if metrics.get("n_trades") and metrics["n_trades"] < 30:
        assert metrics["sharpe_ratio"] is None


def test_train_window_is_bounded_by_default():
    """An unbounded expanding window refits on the whole history at every step,
    which is O(n^2) and hangs on a real season."""
    config = BacktestConfig()
    assert config.train_window > 0
    assert config.train_window <= 2000


def test_bounded_window_still_produces_predictions(synthetic_history):
    config = BacktestConfig(min_edge=0.0, min_history_matches=100, train_window=150)
    result = Backtester(model_factory=lambda: MatchResultModel(min_matches=100),
                        config=config).run(synthetic_history.matches, "MATCH_RESULT", league="E0")
    assert result.model_predictions


def test_refit_every_reuses_the_model_but_still_predicts(synthetic_history):
    config = BacktestConfig(min_edge=0.0, min_history_matches=100, train_window=150, refit_every=5)
    result = Backtester(model_factory=lambda: MatchResultModel(min_matches=100),
                        config=config).run(synthetic_history.matches, "MATCH_RESULT", league="E0")
    assert len(result.model_predictions) > 50
