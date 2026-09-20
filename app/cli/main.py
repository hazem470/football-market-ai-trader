"""Command-line interface.

Implemented commands: init, check, config, markets, signals, backtest, calibrate,
shadow, paper, live, status, stop, test, version.

Every mode command runs pre-flight first. LIVE additionally requires
POLYMARKET_ALLOW_LIVE_TRADING=true AND the exact confirmation phrase.
"""
from __future__ import annotations

import argparse
import json
import signal as signal_module
import sys
import time
from pathlib import Path

from src.config.settings import LIVE_CONFIRMATION_DEFAULT, VALID_MODES, ConfigError, load_settings
from src.core.runtime import Runtime
from src.monitoring.logging_setup import setup_logging
from src.wallet.keystore import wallet_setup_instructions


def _print_json(payload) -> None:
    print(json.dumps(payload, indent=2, default=str, ensure_ascii=False))


def _build_settings(args) -> object:
    overrides: dict = {}
    if getattr(args, "mode", None):
        overrides.setdefault("trading", {})["mode"] = args.mode
    return load_settings(
        config_path=getattr(args, "config", None),
        env_file=getattr(args, "env", None),
        overrides=overrides or None,
    )


# --------------------------------------------------------------------- commands
def cmd_version(_args) -> int:
    from src import __version__

    print(f"football-market-ai-trader {__version__}")
    print("Supported modes: " + ", ".join(VALID_MODES))
    print("Live trading: disabled by default (POLYMARKET_ALLOW_LIVE_TRADING=false)")
    return 0


def cmd_init(args) -> int:
    """Create directories, the database and a starter .env if missing."""
    settings = _build_settings(args)
    settings.ensure_dirs()
    from src.storage.database import Database

    database = Database(settings.database_path).connect()
    tables = database.table_names()
    database.close()
    print(f"data dir      : {settings.abs_path(settings.data_dir)}")
    print(f"database      : {settings.abs_path(settings.database_path)} ({len(tables)} tables)")
    env_path = settings.repo_root / ".env"
    example = settings.repo_root / ".env.example"
    if not env_path.exists() and example.exists():
        env_path.write_text(example.read_text(encoding="utf-8"), encoding="utf-8")
        print(f".env          : created from .env.example at {env_path}")
        print("                -> now open it and add YOUR OWN credentials.")
    elif env_path.exists():
        print(f".env          : already present at {env_path}")
    else:
        print(".env          : no .env.example found; create .env manually")
    print()
    print(wallet_setup_instructions())
    return 0


def cmd_check(args) -> int:
    """Full system health report. Use before any mode."""
    settings = _build_settings(args)
    runtime = Runtime.build(settings, load_data=not args.skip_data, build_venue=True)
    try:
        report = runtime.preflight(require_live=(settings.mode == "live"))
        print(report.render())
        if args.json:
            _print_json(report.as_dict())
        if not report.ok:
            print("\nFix the FAIL lines above before running a mode.", file=sys.stderr)
            return 2
        return 0
    finally:
        runtime.close()


def cmd_config(args) -> int:
    settings = _build_settings(args)
    payload = settings.redacted_summary()
    problems = settings.validate()
    payload["configuration_problems"] = problems or "none"
    payload["env_file"] = str(settings.env_file) if settings.env_file else "not found"
    if args.json:
        _print_json(payload)
    else:
        print("EFFECTIVE CONFIGURATION (secrets shown only as set/missing)")
        print("-" * 68)
        for key, value in payload.items():
            if isinstance(value, dict):
                print(f"{key}:")
                for sub, sub_value in value.items():
                    print(f"    {sub}: {sub_value}")
            else:
                print(f"{key}: {value}")
    return 0 if not problems else 2


def cmd_markets(args) -> int:
    """Discover and classify the football markets that ACTUALLY exist right now."""
    settings = _build_settings(args)
    runtime = Runtime.build(settings, load_data=False, build_venue=False)
    try:
        result, discovery = runtime.discover_markets(args.tag)
        markets = result.accepted if args.tradeable_only else (
            result.accepted + [m for m in _all_discovered(discovery) if m.market_id not in
                               {a.market_id for a in result.accepted}]
        )
        print(f"Discovery: scanned {discovery.stats.events_scanned} events, "
              f"{discovery.stats.soccer_events} soccer, {discovery.stats.markets_scanned} markets")
        print(f"           price-enriched {discovery.stats.price_enriched}, "
              f"failed {discovery.stats.price_failed}, "
              f"classified {discovery.stats.classified}, unknown {discovery.stats.unknown}")
        print(f"Tradeable: {result.accepted_count} / rejected: {result.rejected_count} "
              f"({result.rejection_reasons()})")
        print("-" * 100)
        for market in sorted(markets, key=lambda m: (m.market_type.value, m.home_team)):
            snapshot = market.snapshot
            spread = f"{snapshot.spread:.3f}" if snapshot and snapshot.spread is not None else "n/a"
            liquidity = f"{snapshot.liquidity:.0f}" if snapshot and snapshot.liquidity is not None else "n/a"
            print(
                f"{market.market_type.value:<26} conf={market.classification_confidence:.2f} "
                f"| {market.label[:58]:<58} | ask="
                f"{(snapshot.executable_buy_price if snapshot else None) or 0:.3f} "
                f"| spread={spread} | liq={liquidity}"
            )
        if args.json:
            _print_json([
                {**m.to_row(), "snapshot": m.snapshot.as_dict() if m.snapshot else None} for m in markets
            ])
        if runtime.database is not None and markets:
            runtime.database.upsert_markets([m.to_row() for m in markets])
        return 0
    finally:
        runtime.close()


def _all_discovered(discovery_result) -> list:
    return []


def cmd_signals(args) -> int:
    """Discover, model, calibrate and print signals WITHOUT placing anything."""
    settings = _build_settings(args)
    runtime = Runtime.build(settings, load_data=True, build_venue=False)
    try:
        result, discovery = runtime.discover_markets(args.tag)
        markets = result.accepted[: args.limit] if args.limit else result.accepted
        if not markets:
            print("No tradeable football markets matched the filters.")
            print(f"rejections: {result.rejection_reasons()}")
            return 0
        signals = runtime.scan(markets)
        actionable = [s for s in signals if s.actionable]
        print(f"Evaluated {len(signals)} markets -> {len(actionable)} actionable signal(s)")
        print("=" * 88)
        for signal in sorted(signals, key=lambda s: -s.edge):
            print(signal.render())
            print("-" * 88)
        if args.json:
            _print_json([s.to_db_row() for s in signals])
        return 0
    finally:
        runtime.close()


def cmd_backtest(args) -> int:
    settings = _build_settings(args)
    runtime = Runtime.build(settings, load_data=True, build_venue=False)
    try:
        if not runtime.history or not runtime.history.matches:
            print("No historical matches loaded. Check your football data providers "
                  "(enabled in configs/config.yaml, or set FOOTBALL_DATA_UK_ENABLED=true).",
                  file=sys.stderr)
            return 2
        print(f"Loaded {len(runtime.history.matches)} finished matches")
        leagues = sorted({(m.league_code or m.league) for m in runtime.history.matches})
        print(f"Leagues: {', '.join(leagues)}")
        report = runtime.run_backtest(market_type=args.market_type, league=args.league)
        if "error" in report:
            print(f"Backtest error: {report['error']}", file=sys.stderr)
            return 2
        print(json.dumps(report, indent=2, default=str))
        return 0
    finally:
        runtime.close()


def cmd_calibrate(args) -> int:
    settings = _build_settings(args)
    setup_logging(settings.log_level, settings.log_dir, settings.log_json, "calibrate")
    runtime = Runtime.build(settings, load_data=True, build_venue=False)
    try:
        result = runtime.calibrate_from_backtest(market_type=args.market_type, league=args.league)
        if "error" in result:
            print(f"Calibration failed: {result['error']}", file=sys.stderr)
            return 2
        _print_json(result)
        print(f"\nCalibrator artifact written to "
              f"{settings.abs_path(settings.get('calibration.artifact_path', 'artifacts/calibration.json'))}")
        return 0
    finally:
        runtime.close()


def cmd_shadow(args) -> int:
    """Rank-order safety: real data, real decisions, NO orders."""
    settings = _build_settings(args)
    # Shadow must never touch live, even if the env says live.
    settings.config["trading"]["mode"] = "shadow"
    settings.mode = "shadow"
    runtime = Runtime.build(settings, load_data=True, build_venue=True)
    try:
        report = runtime.preflight(require_live=False)
        print(report.render())
        if not report.ok:
            print("\nPre-flight failed - not running.", file=sys.stderr)
            return 2
        payload = runtime.run_shadow(limit=args.limit)
        _print_json(payload["summary"] if not args.json else payload)
        return 0
    finally:
        runtime.close()


def cmd_paper(args) -> int:
    """Live data, virtual capital, simulated fills, real risk limits."""
    settings = _build_settings(args)
    settings.config["trading"]["mode"] = "paper"
    settings.mode = "paper"
    runtime = Runtime.build(settings, load_data=True, build_venue=True)
    try:
        report = runtime.preflight(require_live=False)
        print(report.render())
        if not report.ok:
            print("\nPre-flight failed - not running.", file=sys.stderr)
            return 2
        summary = runtime.run_paper(limit=args.limit, iterations=args.iterations,
                                    poll_seconds=args.poll)
        _print_json(summary)
        print("\nREMINDER: paper results are simulated. They are not evidence of live profitability.")
        return 0
    finally:
        runtime.close()


def cmd_live(args) -> int:
    """REAL MONEY. Requires every gate."""
    settings = _build_settings(args)
    settings.config["trading"]["mode"] = "live"
    settings.mode = "live"

    expected = settings.live_confirmation_phrase or LIVE_CONFIRMATION_DEFAULT
    print("=" * 78)
    print("LIVE TRADING - REAL MONEY")
    print("=" * 78)
    print("This will place REAL orders on your OWN Polymarket account using YOUR OWN funds.")
    print("There is no profit guarantee. You can lose everything you deposit.")
    print("=" * 78)

    if not args.i_understand_risk:
        print("\nRefusing to start: pass --i-understand-risk together with --confirm.", file=sys.stderr)
        return 3
    if args.confirm != expected:
        print(f"\nRefusing to start: --confirm must be exactly:\n  {expected!r}", file=sys.stderr)
        return 3
    if not settings.live_trading:
        print("\nRefusing to start: POLYMARKET_ALLOW_LIVE_TRADING is not true in .env.", file=sys.stderr)
        return 3

    runtime = Runtime.build(settings, load_data=True, build_venue=True)
    try:
        report = runtime.preflight(require_live=True)
        print(report.render())
        if not report.ok:
            print("\nPRE-FLIGHT FAILED - NO TRADE. Fix the FAIL lines above.", file=sys.stderr)
            return 2

        balance = getattr(runtime.venue, "balance", lambda: None)()
        print(f"\nExchange balance: {balance if balance is not None else 'not reported'}")
        if balance is not None and balance <= 0:
            print("Zero balance reported - refusing to trade.", file=sys.stderr)
            return 2

        print(f"\nLIVE loop starting. poll={args.poll}s iterations={args.iterations}")
        print("Ctrl-C to stop (the kill switch closes cleanly).")

        def _handle_stop(_signum, _frame):
            print("\nStop signal received - shutting down cleanly...", file=sys.stderr)
            runtime.stop("SIGINT")
            runtime.close()
            sys.exit(130)

        signal_module.signal(signal_module.SIGINT, _handle_stop)

        # Reuse the paper cycle shape but against the live venue: risk -> order -> position.
        settings.mode = "live"
        if runtime.risk is not None:
            runtime.risk.mode = "live"
        if isinstance(runtime.venue, object) and runtime.venue is not None:
            from src.paper.simulator import PaperVenue

            if isinstance(runtime.venue, PaperVenue):  # never expected in live
                print("Internal error: paper venue constructed in live mode - aborting.", file=sys.stderr)
                return 2
        for cycle in range(max(1, args.iterations)):
            summary = _live_cycle(runtime, args.limit)
            _print_json(summary)
            if cycle < args.iterations - 1:
                time.sleep(args.poll)
        return 0
    finally:
        runtime.close()


def _live_cycle(runtime: Runtime, limit: int | None) -> dict:
    """One live iteration: discover -> model -> risk -> order -> manage."""
    from src.core.runtime import _edge_from_signal, _exposure_group
    from src.execution.base import OrderStatus
    from src.execution.position_manager import ManagedPosition
    from src.risk.engine import RiskContext

    assert runtime.risk is not None and runtime.order_manager is not None and runtime.positions is not None
    result, _discovery = runtime.discover_markets()
    markets = result.accepted[:limit] if limit else result.accepted
    signals = runtime.scan(markets)
    counts = {"signals": len(signals), "orders": 0, "filled": 0, "rejected": 0, "skipped": 0}

    balance = getattr(runtime.venue, "balance", lambda: None)() or 0.0
    for signal in signals:
        if not signal.actionable:
            continue
        market = next((m for m in markets if m.market_id == signal.market_id), None)
        if market is None:
            continue
        snapshot = market.snapshot
        price_age = (time.time() - snapshot.ts) if snapshot else 1e9
        decision = runtime.risk.evaluate(
            market, _edge_from_signal(signal),
            RiskContext(balance=balance, price_age_seconds=price_age,
                        confidence=signal.confidence, exposure_group=_exposure_group(market)),
        )
        if not decision.allowed:
            counts["skipped"] += 1
            if runtime.notifications:
                runtime.notifications.risk_limit(
                    decision.reasons[0] if decision.reasons else "risk deny",
                    f"market {market.market_id}",
                )
            continue
        order = runtime.order_manager.execute_signal(signal, market, decision.size, mode="live")
        counts["orders"] += 1
        if order.status in (OrderStatus.FILLED, OrderStatus.PARTIAL):
            counts["filled"] += 1
            runtime.positions.register(ManagedPosition(
                position_id=order.order_id, market_id=market.market_id, token_id=market.yes_token_id,
                entry_price=order.avg_price, size=order.filled_size, opened_at=time.time(),
                match_key=market.match_key, market_type=market.market_type.value,
                selection=market.selection, model_probability=signal.calibrated_probability,
                exposure_group=_exposure_group(market), mode="live",
            ))
            if runtime.database is not None:
                runtime.database.insert_position({
                    "position_id": order.order_id, "market_id": market.market_id,
                    "token_id": market.yes_token_id, "match_key": market.match_key,
                    "selection": market.selection, "market_type": market.market_type.value,
                    "side": "BUY", "size": order.filled_size, "entry_price": order.avg_price,
                    "current_price": order.avg_price, "status": "OPEN", "mode": "live",
                    "signal_id": signal.signal_id,
                    "model_probability": signal.calibrated_probability,
                    "payload": {"exposure_group": _exposure_group(market)},
                })
            if runtime.notifications:
                runtime.notifications.order_filled(order.as_dict())
        else:
            counts["rejected"] += 1
            runtime.risk.on_rejection(order.message)
            if runtime.notifications:
                runtime.notifications.execution_failure(order.message)
    runtime._manage_open_positions()
    return counts


def cmd_status(args) -> int:
    settings = _build_settings(args)
    setup_logging(settings.log_level, settings.log_dir, settings.log_json, "status")
    runtime = Runtime.build(settings, load_data=False, build_venue=False)
    try:
        payload = runtime.status()
        if runtime.database is not None:
            payload["recent_signals"] = runtime.database.list_signals(limit=10)
            payload["open_positions_db"] = runtime.database.list_positions(status="OPEN")[:10]
            payload["latest_pnl"] = runtime.database.latest_pnl()
            payload["risk_events"] = runtime.database.list_risk_events(limit=10)
        _print_json(payload)
        return 0
    finally:
        runtime.close()


def cmd_stop(args) -> int:
    """Kill switch. Marks the breaker tripped and cancels resting orders."""
    settings = _build_settings(args)
    setup_logging(settings.log_level, settings.log_dir, settings.log_json, "stop")
    runtime = Runtime.build(settings, load_data=False, build_venue=True)
    try:
        runtime.stop(args.reason)
        if runtime.database is not None:
            runtime.database.log("WARNING", "cli", "manual_stop", args.reason)
        print("Kill switch engaged.")
        print("  * new orders are blocked until the breaker is reset")
        print("  * resting orders were asked to cancel")
        print("  * no live position is force-closed by this command")
        return 0
    finally:
        runtime.close()


def cmd_test(args) -> int:
    """Run the test suite (delegates to pytest)."""
    import subprocess

    cmd = [sys.executable, "-m", "pytest", "-q"]
    if args.network:
        cmd.append("--run-network")
    if args.paths:
        cmd.extend(args.paths)
    print("$ " + " ".join(cmd))
    return subprocess.call(cmd, cwd=str(Path(__file__).resolve().parents[2]))


def _name(value: str) -> str:
    return value.strip().lower()


def cmd_lint_config(_args) -> int:
    """Fail fast if configs/config.yaml is not loadable."""
    try:
        settings = load_settings()
    except ConfigError as exc:
        print(f"CONFIG ERROR: {exc}", file=sys.stderr)
        return 2
    problems = settings.validate()
    if problems:
        print("CONFIG PROBLEMS:")
        for problem in problems:
            print(f"  - {problem}")
        return 2
    print("Configuration is valid.")
    return 0


# ----------------------------------------------------------------------- parser
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m app.cli",
        description="Football Market AI Trader - Polymarket football market research engine.",
        epilog=(
            "Modes: backtest | shadow | paper | live.  LIVE is disabled by default and requires "
            "POLYMARKET_ALLOW_LIVE_TRADING=true plus the --confirm phrase."
        ),
    )
    parser.add_argument("--config", help="path to configs/config.yaml (default: repository config)")
    parser.add_argument("--env", help="path to a .env file (default: repo root .env)")
    parser.add_argument("--mode", choices=VALID_MODES, help="override TRADING_MODE")
    parser.add_argument("--json", action="store_true", help="machine-readable output")

    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("version", help="print version and mode support").set_defaults(func=cmd_version)

    p_init = sub.add_parser("init", help="create directories, database and a starter .env")
    p_init.set_defaults(func=cmd_init)

    p_check = sub.add_parser("check", help="run the full pre-flight health check")
    p_check.add_argument("--skip-data", action="store_true", help="skip loading football history")
    p_check.set_defaults(func=cmd_check)

    p_config = sub.add_parser("config", help="show the effective, redacted configuration")
    p_config.set_defaults(func=cmd_config)

    p_markets = sub.add_parser("markets", help="discover + classify live Polymarket football markets")
    p_markets.add_argument("--tag", help="Polymarket sports tag id (default: resolved from /tags/slug/soccer)")
    p_markets.add_argument("--all", dest="tradeable_only", action="store_false",
                           help="show every discovered market, not only tradeable ones")
    p_markets.set_defaults(func=cmd_markets, tradeable_only=True)

    p_signals = sub.add_parser("signals", help="evaluate markets and print signals (places nothing)")
    p_signals.add_argument("--tag", help="Polymarket sports tag id")
    p_signals.add_argument("--limit", type=int, default=25, help="max markets to evaluate")
    p_signals.set_defaults(func=cmd_signals)

    p_backtest = sub.add_parser("backtest", help="walk-forward backtest on historical football data")
    p_backtest.add_argument("--market-type", default="MATCH_RESULT",
                            choices=["MATCH_RESULT", "TOTAL_GOALS", "BTTS"])
    p_backtest.add_argument("--league", default="", help="league code, e.g. E0 (default: all loaded)")
    p_backtest.set_defaults(func=cmd_backtest)

    p_cal = sub.add_parser("calibrate", help="fit the probability calibrator from walk-forward output")
    p_cal.add_argument("--market-type", default="MATCH_RESULT",
                       choices=["MATCH_RESULT", "TOTAL_GOALS", "BTTS"])
    p_cal.add_argument("--league", default="")
    p_cal.set_defaults(func=cmd_calibrate)

    p_shadow = sub.add_parser("shadow", help="shadow mode: real data, real decisions, NO orders")
    p_shadow.add_argument("--limit", type=int, default=25)
    p_shadow.set_defaults(func=cmd_shadow)

    p_paper = sub.add_parser("paper", help="paper trading: live data, virtual capital")
    p_paper.add_argument("--limit", type=int, default=25)
    p_paper.add_argument("--iterations", type=int, default=1)
    p_paper.add_argument("--poll", type=float, default=None, help="seconds between cycles")
    p_paper.set_defaults(func=cmd_paper)

    p_live = sub.add_parser("live", help="LIVE trading with real funds (all gates required)")
    p_live.add_argument("--limit", type=int, default=10)
    p_live.add_argument("--iterations", type=int, default=1)
    p_live.add_argument("--poll", type=float, default=120.0)
    p_live.add_argument("--confirm", default="", help="must equal LIVE_CONFIRMATION_PHRASE")
    p_live.add_argument("--i-understand-risk", action="store_true",
                        help="explicit acknowledgement that real money is at risk")
    p_live.set_defaults(func=cmd_live)

    p_status = sub.add_parser("status", help="system health, exposure and recent activity")
    p_status.set_defaults(func=cmd_status)

    p_stop = sub.add_parser("stop", help="kill switch: block new orders and cancel resting ones")
    p_stop.add_argument("--reason", default="operator stop")
    p_stop.set_defaults(func=cmd_stop)

    p_test = sub.add_parser("test", help="run the test suite")
    p_test.add_argument("--network", action="store_true", help="also run network-marked tests")
    p_test.add_argument("paths", nargs="*", help="optional test paths")
    p_test.set_defaults(func=cmd_test)

    p_lint = sub.add_parser("lint-config", help="validate configs/config.yaml")
    p_lint.set_defaults(func=cmd_lint_config)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.func(args))
    except ConfigError as exc:
        print(f"CONFIG ERROR: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("\ninterrupted", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
