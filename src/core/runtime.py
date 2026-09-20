"""Runtime wiring: builds the whole engine for a given mode and runs it.

This is the only place that assembles providers, models, risk and venues, so
each CLI mode gets an identically-configured engine.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path

from src.ai.factory import build_ai_provider
from src.backtest.engine import BacktestConfig, Backtester
from src.calibration.calibrators import CalibrationService, build_calibrator
from src.config.settings import Settings
from src.core.http import HttpxClient
from src.core.preflight import PreflightReport, run_preflight
from src.data.normalization.canonical import (
    MatchRecord,
    Normalizer,
    PlayerRecord,
    TeamHistory,
)
from src.data.providers.injuries import FplAvailabilityProvider
from src.data.providers.lineups import MinutesBasedLineupProvider
from src.data.providers.registry import ProviderRegistry
from src.execution.base import ExecutionVenue
from src.execution.order_manager import OrderManager
from src.execution.polymarket import PolymarketExecutor
from src.execution.position_manager import ExitRules, ManagedPosition, PositionManager
from src.features.engine import FeatureEngine
from src.markets.discovery import MarketDiscovery
from src.markets.schema import Market, MarketType
from src.models.registry import ModelRegistry
from src.monitoring.health import Health
from src.monitoring.logging_setup import log_event, setup_logging
from src.notifications.events import NotificationCenter
from src.notifications.telegram import TelegramNotifier
from src.paper.simulator import PaperConfig, PaperVenue, Portfolio
from src.risk.circuit_breaker import BreakerConfig, CircuitBreaker
from src.risk.engine import RiskContext, RiskEngine
from src.risk.exposure import ExposureTracker
from src.risk.limits import RiskLimits
from src.storage.database import Database
from src.strategy.signals import Signal
from src.strategy.strategy import StrategyConfig, StrategyEngine
from src.wallet.keystore import WalletConfig, load_signer


@dataclass
class Runtime:
    """Everything needed to run a mode, built once and reused."""

    settings: Settings
    log: object | None = None
    http: object | None = None
    database: Database | None = None
    providers: ProviderRegistry | None = None
    history: TeamHistory | None = None
    players: list[PlayerRecord] = field(default_factory=list)
    feature_engine: FeatureEngine | None = None
    models: ModelRegistry | None = None
    calibration: CalibrationService | None = None
    strategy: StrategyEngine | None = None
    risk: RiskEngine | None = None
    venue: ExecutionVenue | None = None
    order_manager: OrderManager | None = None
    positions: PositionManager | None = None
    notifications: NotificationCenter | None = None
    ai: object | None = None
    discovery: MarketDiscovery | None = None
    last_preflight: PreflightReport | None = None

    # ------------------------------------------------------------------ build
    @classmethod
    def build(cls, settings: Settings, load_data: bool = True, build_venue: bool = True) -> Runtime:
        settings.ensure_dirs()
        log = setup_logging(settings.log_level, settings.log_dir, settings.log_json, "runtime")
        http = HttpxClient(timeout_seconds=float(settings.get("data.providers.polymarket_gamma.timeout_seconds", 20)))
        database = Database(settings.database_path).connect()
        providers = ProviderRegistry(settings=settings, client=http)
        runtime = cls(settings=settings, log=log, http=http, database=database, providers=providers)

        runtime.models = ModelRegistry(model_config=settings.get("models", {}) or {})
        runtime._load_calibration()
        runtime.notifications = runtime._build_notifications()
        runtime.ai = build_ai_provider(settings, http)
        runtime.discovery = MarketDiscovery(
            gamma=providers.gamma, clob=providers.clob, settings=settings,
            enrich_prices=True,
            max_price_enrichment=int(settings.get("markets.page_limit", 100)) + 40,
        )
        if load_data:
            runtime.load_football_data()
        else:
            runtime.history = TeamHistory([])
            runtime._build_engines()
        if build_venue:
            runtime.build_venue()
        return runtime

    def load_football_data(self) -> None:
        """Load historical results + players from the configured providers."""
        assert self.providers is not None
        matches_raw: dict[str, dict] = {}
        sources: list[str] = []

        if self.providers.football_data_uk.enabled:
            try:
                for row in self.providers.football_data_uk.fetch_all():
                    matches_raw[row["match_key"]] = row
                sources.append("football_data_uk")
                self.providers.health.record("football_data_uk", Health.HEALTHY,
                                             f"{len(matches_raw)} matches")
            except Exception as exc:
                self.providers.health.record("football_data_uk", Health.CRITICAL, str(exc))
                log_event(self.log, "provider_failure", "football-data.co.uk load failed", error=str(exc))

        if not matches_raw and self.providers.openfootball.enabled:
            try:
                for row in self.providers.openfootball.fetch_all():
                    if row.get("finished"):
                        matches_raw[row["match_key"]] = row
                sources.append("openfootball")
            except Exception as exc:
                log_event(self.log, "provider_failure", "openfootball load failed", error=str(exc))

        records = [Normalizer.match_from_raw(row) for row in matches_raw.values()]
        self.history = TeamHistory(records)
        if self.database is not None:
            try:
                self.database.upsert_matches([r.to_db_row() for r in records])
            except Exception as exc:
                log_event(self.log, "db_error", "match persistence failed", error=str(exc))

        # players (optional: only used by player-prop markets)
        lineup_provider = None
        if self.providers.fpl.enabled:
            try:
                players_raw = self.providers.fpl.players()
                availability = FplAvailabilityProvider(fpl=self.providers.fpl)
                lineup_provider = MinutesBasedLineupProvider(fpl=self.providers.fpl)
                for raw in players_raw:
                    lineup = lineup_provider.starting_probability(raw["display_name"], raw["team"])
                    record = Normalizer.player_from_raw(raw, lineup)
                    self.players.append(record)
                    if self.database is not None:
                        self.database.upsert_player({
                            "player_key": record.player_key, "team": record.team,
                            "display_name": record.display_name, "position": record.position,
                            "provider": record.source, "attributes": record.attributes,
                        })
                _ = availability  # retained for future news-driven availability overrides
            except Exception as exc:
                self.providers.health.record("fpl", Health.CRITICAL, str(exc))
                log_event(self.log, "provider_failure", "FPL load failed", error=str(exc))

        log_event(self.log, "data_loaded", "football data loaded",
                  matches=len(records), players=len(self.players), sources=sources)
        self._build_engines()

    def _build_engines(self) -> None:
        self.feature_engine = FeatureEngine(
            history=self.history, players=self.players,
            form_window=int(self.settings.get("models.form_window", 10)),
        )
        self.strategy = StrategyEngine(
            feature_engine=self.feature_engine,
            models=self.models,
            calibration=self.calibration,
            config=StrategyConfig.from_settings(self.settings),
            history=self.history,
        )
        limits = RiskLimits.from_config(self.settings.section("risk"), self.settings.section("strategy"))
        breaker_cfg = self.settings.get("risk.circuit_breaker", {}) or {}
        tracker = ExposureTracker(limits, database=self.database)
        self.risk = RiskEngine(
            limits=limits,
            tracker=tracker,
            breaker=CircuitBreaker(config=BreakerConfig(
                enable=bool(breaker_cfg.get("enable", True)),
                max_consecutive_errors=int(breaker_cfg.get("max_consecutive_errors", 5)),
                max_stale_data_minutes=float(breaker_cfg.get("max_stale_data_minutes", 180)),
                abnormal_price_move=float(breaker_cfg.get("abnormal_price_move", 0.25)),
            )),
            mode=self.settings.mode,
        )
        exit_cfg = self.settings.get("exit", {}) or {}
        self.positions = PositionManager(
            rules=ExitRules(
                exit_min_edge=float(exit_cfg.get("min_edge", 0.0)),
                probability_reversal_threshold=float(exit_cfg.get("probability_reversal", 0.12)),
                stop_loss_fraction=float(exit_cfg.get("stop_loss_fraction", 0.5)),
                take_profit_fraction=float(exit_cfg.get("take_profit_fraction", 0.8)),
                flatten_on_circuit_breaker=bool(exit_cfg.get("flatten_on_circuit_breaker", False)),
            ),
            database=self.database,
        )

    def _load_calibration(self) -> None:
        section = self.settings.section("calibration")
        service = CalibrationService(
            method=str(section.get("method", "platt")),
            min_samples=int(section.get("min_samples", 30)),
            artifact_path=str(section.get("artifact_path", "artifacts/calibration.json")),
            enabled=bool(section.get("enabled", True)),
        )
        artifact = service.artifact_path
        if isinstance(artifact, str):
            path = self.settings.abs_path(artifact)
        else:
            path = Path(artifact)
        if path.exists():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                calibrator = build_calibrator(str(data.get("method", "platt")))
                state = data.get("state") or {}
                for key, value in state.items():
                    if hasattr(calibrator, key):
                        setattr(calibrator, key, value)
                calibrator.fitted = bool(state.get("fitted", True))
                service.calibrator = calibrator
                service.metrics = data.get("metrics", {})
                service.version = str(data.get("version", "1.0.0"))
                log_event(self.log, "calibration_loaded", "calibrator restored from artifact",
                          method=data.get("method"), samples=data.get("n_samples"))
            except Exception as exc:
                log_event(self.log, "calibration_error", "could not load calibrator", error=str(exc))
        self.calibration = service

    def _build_notifications(self) -> NotificationCenter:
        section = self.settings.section("notifications")
        telegram_cfg = section.get("telegram", {}) or {}
        notifier = TelegramNotifier(
            client=self.http,
            bot_token=self.settings.secrets.telegram_bot_token,
            chat_id=self.settings.secrets.telegram_chat_id,
            enabled=bool(telegram_cfg.get("enabled", False)),
            database=self.database,
        )
        return NotificationCenter(
            notifier=notifier,
            console=bool(section.get("console", True)),
            min_edge_to_notify=float(telegram_cfg.get("min_edge_to_notify", 0.08)),
            notify_on=tuple(telegram_cfg.get("notify_on", ()) or _all_events()),
            database=self.database,
        )

    def build_venue(self) -> ExecutionVenue:
        mode = self.settings.mode
        if mode in ("backtest", "shadow"):
            self.venue = _NullVenue(mode=mode)
        elif mode == "paper":
            self.venue = PaperVenue(
                config=PaperConfig(
                    starting_balance=float(self.settings.get("paper.starting_balance", 100.0)),
                    rejection_rate=float(self.settings.get("paper.rejection_rate", 0.02)),
                    min_fill_ratio=float(self.settings.get("paper.min_fill_ratio", 0.25)),
                    fixed_slippage=float(self.settings.get("paper.fixed_slippage", 0.005)),
                    slippage_model=str(self.settings.get("paper.slippage_model", "spread")),
                    fee_rate=float(self.settings.get("strategy.fee_rate", 0.0)),
                ),
                clob=self.providers.clob if self.providers else None,
            )
        else:
            private_key, source = load_signer()
            wallet = WalletConfig(
                private_key=private_key,
                api_key=self.settings.secrets.polymarket_api_key,
                api_secret=self.settings.secrets.polymarket_api_secret,
                api_passphrase=self.settings.secrets.polymarket_api_passphrase,
                funder_address=self.settings.secrets.polymarket_funder_address,
                chain_id=self.settings.chain_id,
                signature_type=self.settings.signature_type,
            )
            log_event(self.log, "wallet_loaded", "signer loaded", source=source)
            self.venue = PolymarketExecutor(
                wallet=wallet, clob_host=self.settings.clob_host,
                gamma_host=self.settings.gamma_host, allow_live=bool(self.settings.live_trading),
            )
        self.order_manager = OrderManager(
            venue=self.venue,
            database=self.database,
            clob=self.providers.clob if self.providers else None,
            max_slippage=float(self.settings.get("risk.max_slippage", 0.02)),
        )
        return self.venue

    # ------------------------------------------------------------- preflight
    def preflight(self, require_live: bool | None = None, check_secrets: bool = True) -> PreflightReport:
        report = run_preflight(
            self.settings, providers=self.providers, database=self.database,
            venue=self.venue, require_live=require_live, check_secrets=check_secrets,
        )
        self.last_preflight = report
        return report

    # ------------------------------------------------------------------ modes
    def discover_markets(self, tag_id: str | int | None = None):
        assert self.discovery is not None
        return self.discovery.discover_tradeable(tag_id)

    def scan(self, markets: list[Market]) -> list[Signal]:
        assert self.strategy is not None
        signals = self.strategy.evaluate_many(markets)
        if self.database is not None:
            for signal in signals:
                try:
                    self.database.insert_signal(signal.to_db_row())
                except Exception as exc:
                    log_event(self.log, "db_error", "signal persistence failed", error=str(exc))
        return signals

    def run_shadow(self, limit: int | None = None) -> dict:
        """Observe real markets, make decisions, place NO orders, record everything."""
        result, _discovery = self.discover_markets()
        markets = result.accepted[:limit] if limit else result.accepted
        signals = self.scan(markets)
        actionable = [s for s in signals if s.actionable]
        for signal in actionable:
            if self.notifications:
                self.notifications.opportunity(signal)
        summary = {
            "mode": "shadow",
            "markets_discovered": result.accepted_count + result.rejected_count,
            "markets_tradeable": result.accepted_count,
            "rejection_reasons": result.rejection_reasons(),
            "signals": len(signals),
            "actionable": len(actionable),
            "orders_placed": 0,
            "states": _count_states(signals),
        }
        log_event(self.log, "shadow_run", "shadow scan complete", **summary)
        return {"summary": summary, "signals": [s.to_db_row() for s in signals]}

    def run_paper(self, limit: int | None = None, iterations: int = 1,
                  poll_seconds: float | None = None) -> dict:
        """Live data, virtual capital, real risk rules, simulated fills."""
        poll_seconds = poll_seconds or float(self.settings.get("trading.poll_interval_seconds", 120))
        totals = {"cycles": 0, "orders": 0, "filled": 0, "rejected": 0, "signals": 0}
        for cycle in range(iterations):
            cycle_summary = self._paper_cycle(limit)
            totals["cycles"] += 1
            for key in ("orders", "filled", "rejected", "signals"):
                totals[key] += cycle_summary.get(key, 0)
            if cycle < iterations - 1:
                time.sleep(poll_seconds)
        assert self.venue is not None
        portfolio = getattr(self.venue, "portfolio", None)
        summary = {
            "mode": "paper",
            **totals,
            "portfolio": portfolio.as_dict() if portfolio else {},
            "positions": self.positions.summary() if self.positions else {},
            "breaker": self.risk.breaker.as_dict() if self.risk else {},
        }
        log_event(self.log, "paper_run", "paper trading cycle(s) complete", **totals)
        if self.notifications:
            self.notifications.daily_summary(summary.get("portfolio", {}))
        return summary

    def _paper_cycle(self, limit: int | None) -> dict:
        result, _discovery = self.discover_markets()
        markets = result.accepted[:limit] if limit else result.accepted
        signals = self.scan(markets)
        counts = {"orders": 0, "filled": 0, "rejected": 0, "signals": len(signals)}
        assert self.order_manager is not None and self.risk is not None
        assert self.venue is not None

        for signal in signals:
            if not signal.actionable:
                continue
            market = next((m for m in markets if m.market_id == signal.market_id), None)
            if market is None:
                continue
            snapshot = market.snapshot
            price_age = (time.time() - snapshot.ts) if snapshot else 1e9
            decision = self.risk.evaluate(
                market, _edge_from_signal(signal),
                RiskContext(
                    balance=_paper_balance(self.venue),
                    price_age_seconds=price_age,
                    confidence=signal.confidence,
                    exposure_group=_exposure_group(market),
                ),
            )
            if not decision.allowed:
                if self.notifications and any("exceeds" in r or "below" in r for r in decision.reasons):
                    self.notifications.risk_limit(decision.reasons[0] if decision.reasons else "risk deny")
                continue
            signal.recommended_size = decision.size
            order = self.order_manager.execute_signal(signal, market, decision.size, mode="paper")
            counts["orders"] += 1
            if order.status.value in ("FILLED", "PARTIAL"):
                counts["filled"] += 1
                self.positions.register(ManagedPosition(  # type: ignore[union-attr]
                    position_id=order.order_id,
                    market_id=market.market_id,
                    token_id=market.yes_token_id,
                    entry_price=order.avg_price,
                    size=order.filled_size,
                    opened_at=time.time(),
                    match_key=market.match_key,
                    market_type=market.market_type.value,
                    selection=market.selection,
                    model_probability=signal.calibrated_probability,
                    exposure_group=_exposure_group(market),
                    mode="paper",
                ))
                if self.database is not None:
                    self.database.insert_position({
                        "position_id": order.order_id, "market_id": market.market_id,
                        "token_id": market.yes_token_id, "match_key": market.match_key,
                        "selection": market.selection, "market_type": market.market_type.value,
                        "side": "BUY", "size": order.filled_size, "entry_price": order.avg_price,
                        "current_price": order.avg_price, "status": "OPEN", "mode": "paper",
                        "signal_id": signal.signal_id,
                        "model_probability": signal.calibrated_probability,
                        "payload": {"exposure_group": _exposure_group(market)},
                    })
                if self.notifications:
                    self.notifications.order_filled(order.as_dict())
            else:
                counts["rejected"] += 1
                self.risk.on_rejection(order.message)
                if self.notifications:
                    self.notifications.execution_failure(order.message)

        self._manage_open_positions()
        self._persist_pnl()
        return counts

    def _manage_open_positions(self) -> None:
        if not self.positions or not self.providers:
            return
        for position in self.positions.open_positions():
            try:
                snapshot = self.providers.clob.snapshot(position.token_id)
            except Exception as exc:
                log_event(self.log, "provider_failure", "position mark failed", error=str(exc))
                continue
            price = snapshot.executable_sell_price
            self.positions.refresh(position.market_id, price)
            breaker_open = bool(self.risk and self.risk.breaker.is_open)
            for managed, decision in self.positions.sweep(position.market_id, current_edge=None,
                                                          breaker_open=breaker_open):
                exit_price = managed.current_price or managed.entry_price
                from src.execution.position_manager import apply_exit

                apply_exit(managed, exit_price, decision.reason)
                self.positions.record_exit(managed, decision)
                if isinstance(self.venue, PaperVenue):
                    paper_position = next(
                        (p for p in self.venue.portfolio.open_positions if p.position_id == managed.position_id),
                        None,
                    )
                    if paper_position is not None:
                        self.venue.portfolio.settle(paper_position, exit_price)
                if self.database is not None:
                    self.database.insert_trade({
                        "position_id": managed.position_id, "market_id": managed.market_id,
                        "action": "CLOSE", "price": exit_price, "size": managed.size,
                        "pnl": managed.realized_pnl, "mode": managed.mode,
                    })
                if self.notifications:
                    self.notifications.position_closed(managed.as_dict())

    def _persist_pnl(self) -> None:
        if self.database is None or self.venue is None:
            return
        from src.risk.exposure import start_of_day_utc

        portfolio = getattr(self.venue, "portfolio", None)
        if portfolio is None:
            return
        exposure = self.database.open_exposure(mode=self.settings.mode)
        self.database.record_pnl({
            "mode": self.settings.mode,
            "balance": portfolio.equity,
            "realized_pnl": self.database.realized_pnl_since(start_of_day_utc()),
            "unrealized_pnl": portfolio.unrealized_pnl({}),
            "exposure": exposure,
            "open_positions": len(portfolio.open_positions),
            "note": "paper cycle",
        })

    def run_backtest(self, market_type: str = MarketType.MATCH_RESULT.value,
                     league: str = "", min_matches: int | None = None) -> dict:
        """Walk-forward backtest. Model metrics and trading metrics stay separate."""
        if self.history is None or not self.history.matches:
            return {"error": "no historical matches loaded; enable football_data_uk or openfootball"}
        from src.models.goals import BttsModel, TotalGoalsModel
        from src.models.match_result import MatchResultModel

        factories = {
            MarketType.MATCH_RESULT.value: MatchResultModel,
            MarketType.TOTAL_GOALS.value: TotalGoalsModel,
            MarketType.BTTS.value: BttsModel,
        }
        factory = factories.get(market_type)
        if factory is None:
            return {
                "error": f"{market_type} cannot be back-tested from the free historical feed "
                         "(no bookmaker line for that market in football-data.co.uk)"
            }
        config = BacktestConfig.from_settings(self.settings)
        if min_matches:
            config.min_history_matches = min_matches
        pool = self._resolve_pool(league)
        if league and not pool:
            return {
                "error": f"no finished matches for league {league!r}; loaded leagues: "
                         + ", ".join(sorted(self.history.by_league)) or "(none)"
            }
        backtester = Backtester(model_factory=factory, config=config)
        result = backtester.run(pool, market_type, league=league)
        report = result.report()
        if self.database is not None:
            try:
                self.database.save_backtest(result.run_id, config.__dict__, report)
            except Exception as exc:
                log_event(self.log, "db_error", "backtest persistence failed", error=str(exc))
        return report

    def _resolve_pool(self, league: str) -> list[MatchRecord]:
        """Select the match pool for a league code.

        `by_league` is keyed by whichever code the provider supplied (usually the
        upper-case football-data.co.uk code such as ``E0``), so the lookup must be
        case-insensitive. A silent miss here would fall back to EVERY loaded
        league and make the report meaningless, so a miss returns an empty list
        and the caller reports the loaded leagues instead of guessing.
        """
        if self.history is None:
            return []
        if not league:
            return self.history.matches
        wanted = league.strip().lower()
        for key, pool in self.history.by_league.items():
            if key.strip().lower() == wanted:
                return pool
        # Also accept a league NAME ("Premier League") as a convenience.
        for match in self.history.matches:
            if (match.league or "").strip().lower() == wanted:
                return [m for m in self.history.matches
                        if (m.league or "").strip().lower() == wanted]
        return []

    def calibrate_from_backtest(self, market_type: str = MarketType.MATCH_RESULT.value,
                                league: str = "") -> dict:
        """Fit the calibrator from walk-forward backtest predictions (out of sample)."""
        result = self.run_backtest(market_type=market_type, league=league)
        if "error" in result:
            return result
        model = result.get("MODEL_PERFORMANCE", {})
        if not model.get("n_predictions"):
            return {"error": "no predictions produced; cannot calibrate"}
        # Re-run to grab the raw prediction/outcome arrays.
        from src.models.goals import BttsModel, TotalGoalsModel
        from src.models.match_result import MatchResultModel

        factories = {
            MarketType.MATCH_RESULT.value: MatchResultModel,
            MarketType.TOTAL_GOALS.value: TotalGoalsModel,
            MarketType.BTTS.value: BttsModel,
        }
        config = BacktestConfig.from_settings(self.settings)
        pool = self._resolve_pool(league)
        backtester = Backtester(model_factory=factories[market_type], config=config)
        raw = backtester.run(pool, market_type, league=league)
        assert self.calibration is not None
        metrics = self.calibration.fit(raw.model_predictions, raw.model_outcomes)
        log_event(self.log, "calibration_fitted", "calibrator fitted from walk-forward output",
                  market_type=market_type, samples=len(raw.model_predictions))
        return {"metrics": metrics, "method": self.calibration.method,
                "calibrator": self.calibration.calibrator.state()}

    def status(self) -> dict:
        assert self.risk is not None
        portfolio = getattr(self.venue, "portfolio", None) if self.venue else None
        return {
            "mode": self.settings.mode,
            "live_trading_enabled": self.settings.live_trading,
            "health": self.providers.health.as_dict() if self.providers else {},
            "risk": self.risk.as_dict(),
            "portfolio": portfolio.as_dict() if portfolio else {},
            "positions": self.positions.summary() if self.positions else {},
            "history_matches": len(self.history.matches) if self.history else 0,
            "players": len(self.players),
            "calibration": self.calibration.as_dict() if self.calibration else {},
            "model_fit_errors": dict(self.models.fit_errors) if self.models else {},
        }

    def stop(self, reason: str = "operator stop") -> None:
        if self.risk is not None:
            self.risk.stop(reason)
        if self.order_manager is not None:
            self.order_manager.cancel_all()
        if self.notifications:
            self.notifications.stopped(reason)
        if self.database is not None:
            self.database.record_risk_event({
                "kind": "manual_stop", "severity": "WARNING", "detail": reason,
            })

    def close(self) -> None:
        if self.providers is not None:
            self.providers.close()
        if self.database is not None:
            self.database.close()


# --------------------------------------------------------------------- helpers
class _NullVenue(ExecutionVenue):
    """Used by backtest/shadow: it exists so the order path is uniform, but it
    can never fill anything."""

    name: str = "none"
    is_live: bool = False

    def __init__(self, mode: str = "shadow") -> None:
        self.mode = mode

    def submit(self, request):
        from src.execution.base import OrderResult, OrderStatus

        return OrderResult(order_id="", status=OrderStatus.REJECTED,
                           message=f"{self.mode} mode places no orders (observation only)")

    def cancel(self, order_id: str) -> bool:
        return True

    def open_orders(self, market_id: str | None = None) -> list[dict]:
        return []

    def describe(self) -> dict:
        return {"name": self.name, "live": False, "mode": self.mode}


def _all_events() -> list[str]:
    from src.notifications.events import NotifyEvent

    return list(NotifyEvent.ALL)


def _count_states(signals: list[Signal]) -> dict:
    counts: dict[str, int] = {}
    for signal in signals:
        counts[signal.state.value] = counts.get(signal.state.value, 0) + 1
    return counts


def _paper_balance(venue) -> float:
    portfolio = getattr(venue, "portfolio", None)
    if isinstance(portfolio, Portfolio):
        return portfolio.cash
    return 0.0


def _edge_from_signal(signal: Signal):
    from src.strategy.edge import EdgeBreakdown

    breakdown = signal.edge_breakdown or {}
    return EdgeBreakdown(
        model_probability=breakdown.get("model_probability", signal.model_probability),
        calibrated_probability=breakdown.get("calibrated_probability", signal.calibrated_probability),
        market_price=breakdown.get("market_price", signal.market_price),
        raw_edge=breakdown.get("raw_edge", 0.0),
        spread_cost=breakdown.get("spread_cost", 0.0),
        slippage_cost=breakdown.get("slippage_cost", 0.0),
        fee_cost=breakdown.get("fee_cost", 0.0),
        model_uncertainty=breakdown.get("model_uncertainty", 0.0),
        data_uncertainty=breakdown.get("data_uncertainty", 0.0),
        execution_uncertainty=breakdown.get("execution_uncertainty", 0.0),
        realistic_edge=breakdown.get("realistic_edge", signal.edge),
        side=breakdown.get("side", "BUY"),
        price_source=breakdown.get("price_source", ""),
        incomplete_reason="" if breakdown.get("complete", True) else "edge incomplete",
    )


def _exposure_group(market: Market) -> str:
    from src.risk.correlation import group_key_for

    return group_key_for(market)
