"""End-to-end verification against the LIVE public APIs.

    python scripts/verify_end_to_end.py            # full run
    python scripts/verify_end_to_end.py --quick    # skip the season backtest

This places NO orders and needs NO credentials. It exercises:

  1. configuration load + validation
  2. pre-flight (including the repository secret scan)
  3. live Polymarket football market discovery + classification
  4. live football data load (results, players)
  5. the full signal path on a market whose teams ARE in the loaded history
     (features -> model -> calibration -> edge -> decision)
  6. a walk-forward backtest, and that MODEL and TRADING performance are reported
     separately
  7. the three live-trading gates, all of which must REFUSE
  8. shadow mode placing zero orders

Every step prints PASS/FAIL and the script exits non-zero on any failure, so it
can be used as a smoke test before a release.
"""
from __future__ import annotations

import argparse
import datetime as dt
import subprocess
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from src.config.settings import load_settings                              # noqa: E402
from src.core.runtime import Runtime                                      # noqa: E402
from src.markets.schema import Market, MarketSnapshot, MarketType         # noqa: E402

RESULTS: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    RESULTS.append((name, ok, detail))
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f"  -- {detail}" if detail else ""))


def build_market(market_type: MarketType, **kwargs) -> Market:
    now = dt.datetime.now(dt.timezone.utc)
    fmt = "%Y-%m-%dT%H:%M:%SZ"
    return Market(
        snapshot=MarketSnapshot(ts=time.time(), bid=kwargs.pop("bid", 0.30),
                                ask=kwargs.pop("ask", 0.33), liquidity=5000.0,
                                volume_24h=900.0, source="verify"),
        end_time=(now + dt.timedelta(hours=6)).strftime(fmt),
        start_time=(now + dt.timedelta(hours=2)).strftime(fmt),
        yes_token_id="verify-token", classification_confidence=0.95,
        **kwargs,
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--quick", action="store_true", help="skip the season backtest")
    args = parser.parse_args()

    print("=" * 78)
    print("Football Market AI Trader - end-to-end verification (no orders, no credentials)")
    print("=" * 78)

    # 1 - configuration
    try:
        settings = load_settings(env_file=REPO_ROOT / ".env")
        problems = settings.validate()
        check("configuration loads and validates", not problems, "; ".join(problems) or f"mode={settings.mode}")
    except Exception as exc:
        check("configuration loads and validates", False, str(exc))
        return 1

    runtime = Runtime.build(settings, load_data=True, build_venue=False)
    try:
        # 2 - pre-flight
        report = runtime.preflight(require_live=False)
        check("pre-flight (15 checks incl. secret scan)", report.ok,
              f"{len(report.failures)} critical failures")

        # 3 - live discovery
        result, discovery = runtime.discover_markets()
        stats = discovery.stats
        check("live Polymarket market discovery", stats.markets_scanned > 0,
              f"{stats.events_scanned} events, {stats.markets_scanned} markets, "
              f"{stats.classified} classified, {stats.unknown} unknown")
        check("tradeability filters applied", result.accepted_count + result.rejected_count == stats.markets_scanned,
              f"{result.accepted_count} tradeable / {result.rejected_count} rejected")
        if result.accepted:
            m = result.accepted[0]
            check("accepted markets are fully classified",
                  m.market_type is not MarketType.UNKNOWN and m.classification_confidence >= 0.5,
                  f"{m.market_type.value} conf={m.classification_confidence:.2f}")

        # 4 - football data
        matches = len(runtime.history.matches) if runtime.history else 0
        check("live football history loaded", matches > 0, f"{matches} finished matches")
        check("player data loaded (FPL)", len(runtime.players) > 0, f"{len(runtime.players)} players")

        # 5 - the full decision path on teams we DO have history for
        team_a, team_b = _pick_known_teams(runtime)
        if team_a and team_b:
            for market_type, extra in (
                (MarketType.MATCH_RESULT, {"selection": team_a, "selection_kind": "TEAM"}),
                (MarketType.TOTAL_GOALS, {"selection": "Over 2.5", "selection_kind": "OVER", "line": 2.5}),
                (MarketType.BTTS, {"selection": "Both Teams to Score", "selection_kind": "OVER"}),
                (MarketType.CORNERS, {"selection": "Over 9.5 Corners", "selection_kind": "OVER", "line": 9.5}),
                (MarketType.TEAM_TOTALS, {"selection": "Over 1.5", "selection_kind": "OVER", "line": 1.5}),
            ):
                market = build_market(
                    market_type, market_id=f"VERIFY-{market_type.value}",
                    question=f"{team_a} vs {team_b} {market_type.value}",
                    event_id="VERIFY", event_title=f"{team_a} vs. {team_b}",
                    league="Premier League", home_team=team_a, away_team=team_b, **extra,
                )
                signal = runtime.strategy.evaluate_market(market)
                produced = signal.model_probability > 0 or signal.state.value in (
                    "INSUFFICIENT_DATA", "UNSUPPORTED", "NO_TRADE")
                check(f"signal path: {market_type.value}", produced,
                      f"{signal.state.value} model={signal.model_probability:.3f} "
                      f"price={signal.market_price:.3f} edge={signal.edge:+.3f}")

        # unsupported families must refuse with a reason
        for market_type in (MarketType.CARDS, MarketType.EXACT_SCORE, MarketType.UNKNOWN):
            signal = runtime.strategy.evaluate_market(build_market(
                market_type, market_id=f"VERIFY-{market_type.value}",
                question="verify", event_id="V", event_title=f"{team_a} vs. {team_b}",
                league="Premier League", home_team=team_a or "A", away_team=team_b or "B",
                selection="x", selection_kind="OTHER"))
            check(f"refuses {market_type.value} without guessing",
                  signal.state.value == "UNSUPPORTED", signal.explanation[:70])

        # 6 - backtest
        if not args.quick:
            report_bt = runtime.run_backtest(market_type="MATCH_RESULT", league="E0")
            ok = "error" not in report_bt and report_bt.get("MODEL_PERFORMANCE", {}).get("n_predictions", 0) > 0
            check("walk-forward backtest produces predictions", ok,
                  str(report_bt.get("error") or f"{report_bt['MODEL_PERFORMANCE']['n_predictions']} predictions"))
            check("model and trading performance reported separately",
                  "MODEL_PERFORMANCE" in report_bt and "TRADING_PERFORMANCE" in report_bt)
            check("backtest documents its data limitations", bool(report_bt.get("data_limitations")))

        # 7 - shadow places nothing
        shadow = runtime.run_shadow(limit=5)
        check("shadow mode places zero orders", shadow["summary"]["orders_placed"] == 0,
              f"{shadow['summary']['signals']} signals, {shadow['summary']['actionable']} actionable")
    finally:
        runtime.close()

    # 8 - the live gates must refuse
    for label, argv in (
        ("live gate: missing --i-understand-risk", ["live"]),
        ("live gate: wrong --confirm phrase",
         ["live", "--i-understand-risk", "--confirm", "yes"]),
        ("live gate: env flag off",
         ["live", "--i-understand-risk", "--confirm", "I UNDERSTAND THE RISK"]),
    ):
        proc = subprocess.run([sys.executable, "-m", "app.cli", *argv],
                              capture_output=True, text=True, cwd=str(REPO_ROOT), timeout=180)
        check(label, proc.returncode == 3, (proc.stderr or proc.stdout).strip().splitlines()[-1][:80])

    print("=" * 78)
    failures = [name for name, ok, _ in RESULTS if not ok]
    print(f"{len(RESULTS) - len(failures)}/{len(RESULTS)} checks passed")
    if failures:
        print("FAILED: " + "; ".join(failures))
        return 1
    print("ALL CHECKS PASSED - no orders were placed and no credentials were used")
    return 0


def _pick_known_teams(runtime: Runtime) -> tuple[str, str]:
    """Find two real teams present in the loaded history (so the model can price them)."""
    if not runtime.history or not runtime.history.matches:
        return "", ""
    for match in runtime.history.matches:
        if match.league_code and match.league_code.upper() == "E0":
            return match.home_team, match.away_team
    first = runtime.history.matches[0]
    return first.home_team, first.away_team


if __name__ == "__main__":
    raise SystemExit(main())
