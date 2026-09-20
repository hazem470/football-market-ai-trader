"""Strategy pipeline: the decision ladder and every refusal path."""
from __future__ import annotations

import pytest

from src.calibration.calibrators import CalibrationService
from src.features.engine import FeatureEngine
from src.markets.schema import MarketType
from src.models.registry import ModelRegistry
from src.strategy.signals import Signal, SignalState, new_signal_id
from src.strategy.strategy import StrategyConfig, StrategyEngine


@pytest.fixture
def strategy(synthetic_history, synthetic_players):
    engine = FeatureEngine(history=synthetic_history, players=synthetic_players, form_window=10)
    registry = ModelRegistry(model_config={
        "match_result": {"min_history_matches": 100},
        "total_goals": {"min_history_matches": 100},
        "btts": {"min_history_matches": 100},
        "corners": {"min_history_matches": 50},
        "player_goal": {"min_history_matches": 6},
        "player_assist": {"min_history_matches": 6},
    })
    calibration = CalibrationService(method="none", min_samples=1000, artifact_path="unused.json",
                                     enabled=False)
    return StrategyEngine(
        feature_engine=engine, models=registry, calibration=calibration,
        config=StrategyConfig(min_edge=0.02, min_confidence=0.5), history=synthetic_history,
    )


def test_signal_id_format():
    assert new_signal_id().startswith("SIG-")


def test_unsupported_market_yields_unsupported_state(strategy, market_factory):
    signal = strategy.evaluate_market(market_factory(market_type=MarketType.CARDS))
    assert signal.state is SignalState.UNSUPPORTED
    assert "no v1.0 data path" in signal.explanation


def test_unknown_market_type_yields_unsupported(strategy, market_factory):
    """A market we cannot classify is refused as UNSUPPORTED, symmetrically with
    every other family we have no data path for."""
    market = market_factory(market_type=MarketType.UNKNOWN)
    signal = strategy.evaluate_market(market)
    assert signal.state is SignalState.UNSUPPORTED
    assert "classif" in signal.explanation


def test_unpriceable_market_yields_insufficient_data(strategy, market_factory):
    """A *classified* market whose data cannot be sourced is INSUFFICIENT_DATA."""
    market = market_factory(home="Nobody FC", away="Ghost United",
                            market_type=MarketType.MATCH_RESULT)
    signal = strategy.evaluate_market(market)
    assert signal.state is SignalState.INSUFFICIENT_DATA
    assert "no features" in signal.explanation.lower() or "missing" in signal.explanation.lower()


def test_unknown_teams_yield_insufficient_data(strategy, market_factory):
    market = market_factory(home="Nobody FC", away="Ghost United")
    signal = strategy.evaluate_market(market)
    assert signal.state is SignalState.INSUFFICIENT_DATA


def test_player_market_without_player_identity_is_refused(strategy, market_factory):
    market = market_factory(market_type=MarketType.PLAYER_GOAL, player="", selection="Someone")
    signal = strategy.evaluate_market(market)
    assert signal.state is SignalState.INSUFFICIENT_DATA


def test_player_market_with_unknown_player_stays_insufficient(strategy, market_factory):
    market = market_factory(market_type=MarketType.PLAYER_GOAL, player="Nobody At All",
                            home="Team A", away="Team B")
    signal = strategy.evaluate_market(market)
    assert signal.state is SignalState.INSUFFICIENT_DATA


def test_player_market_with_known_player_produces_a_probability(strategy, market_factory):
    market = market_factory(market_type=MarketType.PLAYER_GOAL, player="Striker Team A",
                            selection="Striker Team A", home="Team A", away="Team B")
    signal = strategy.evaluate_market(market)
    assert signal.state in (SignalState.BUY, SignalState.NO_TRADE)
    assert signal.model_probability > 0
    if signal.trace.get("prediction"):
        assert not signal.trace["prediction"]["insufficient_data"]


def test_stale_price_is_refused_before_modeling(strategy, market_factory):
    import time

    market = market_factory(ts=time.time() - 900)
    signal = strategy.evaluate_market(market)
    assert signal.state is SignalState.INSUFFICIENT_DATA


def test_high_edge_produces_buy(strategy, market_factory):
    market = market_factory(market_type=MarketType.MATCH_RESULT, home="Team A", away="Team B",
                            bid=0.06, ask=0.08)
    market.selection = "Team A"
    signal = strategy.evaluate_market(market)
    assert signal.state in (SignalState.BUY, SignalState.NO_TRADE)
    if signal.state is SignalState.BUY:
        assert signal.edge >= strategy.config.min_edge


def test_no_trade_when_edge_is_below_threshold(synthetic_history, synthetic_players, market_factory):
    """Priced at ~50/50 with a tight spread, the model's edge cannot clear the
    threshold, so the signal must be an explicit NO_TRADE (not a silent skip)."""
    engine = FeatureEngine(history=synthetic_history, players=synthetic_players)
    registry = ModelRegistry(model_config={"match_result": {"min_history_matches": 100}})
    strict = StrategyEngine(
        feature_engine=engine, models=registry,
        calibration=CalibrationService(enabled=False, artifact_path="x.json"),
        config=StrategyConfig(min_edge=0.25, min_confidence=0.0), history=synthetic_history,
    )
    market = market_factory(market_type=MarketType.MATCH_RESULT, home="Team A", away="Team B",
                            bid=0.49, ask=0.51)
    signal = strict.evaluate_market(market)
    assert signal.state is SignalState.NO_TRADE
    assert signal.edge < 0.25
    assert "edge" in signal.explanation


def test_no_trade_when_confidence_below_threshold(synthetic_history, synthetic_players, market_factory):
    engine = FeatureEngine(history=synthetic_history, players=synthetic_players)
    registry = ModelRegistry(model_config={"match_result": {"min_history_matches": 100}})
    strict = StrategyEngine(
        feature_engine=engine, models=registry,
        calibration=CalibrationService(enabled=False, artifact_path="x.json"),
        config=StrategyConfig(min_edge=0.0, min_confidence=0.999), history=synthetic_history,
    )
    market = market_factory(market_type=MarketType.MATCH_RESULT, home="Team A", away="Team B",
                            bid=0.05, ask=0.06)
    signal = strict.evaluate_market(market)
    assert signal.state is SignalState.NO_TRADE
    assert "confidence" in signal.explanation


def test_signal_trace_is_auditable(strategy, market_factory):
    market = market_factory(market_type=MarketType.MATCH_RESULT, home="Team A", away="Team B")
    signal = strategy.evaluate_market(market)
    assert signal.market_id == market.market_id
    if signal.trace:
        assert "feature_key" in signal.trace
        assert signal.trace["feature_key"].startswith("FEAT-")


def test_evaluate_many_survives_a_broken_market(strategy, market_factory):
    class Exploding:
        market_id = "boom"

        @property
        def market_type(self):
            raise RuntimeError("kaboom")

    signals = strategy.evaluate_many([Exploding()])  # type: ignore[list-item]
    assert len(signals) == 1
    assert signals[0].state is SignalState.NO_TRADE


def test_blocked_signal_helper():
    signal = Signal.blocked(SignalState.NO_TRADE, "why not", market_id="M")
    assert signal.state is SignalState.NO_TRADE
    assert signal.reasons == ["why not"]
    assert not signal.actionable


def test_signal_render_contains_the_key_fields(strategy, market_factory):
    market = market_factory(market_type=MarketType.MATCH_RESULT, home="Team A", away="Team B")
    rendered = strategy.evaluate_market(market).render()
    for needle in ("Signal ID", "Decision", "Realistic edge", "Calibrated prob"):
        assert needle in rendered


def test_signal_db_row_shape(strategy, market_factory):
    row = strategy.evaluate_market(market_factory()).to_db_row()
    assert {"signal_id", "state", "market_id", "edge", "trace"} <= set(row)


def test_stats_are_tracked(strategy, market_factory):
    strategy.evaluate_market(market_factory(market_type=MarketType.CARDS))
    strategy.evaluate_market(market_factory(market_id="x"))
    assert strategy.stats.get("unsupported") == 1
    assert strategy.stats.get("evaluated") == 2
