"""Streamlit dashboard.

Run with:  streamlit run app/dashboard/streamlit_app.py

Read-only: the dashboard never places an order. It shows state and lets you
trigger SAFE actions (a discovery scan, a backtest run) via the same engine the
CLI uses. Live trading is started from the CLI only - deliberately.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import streamlit as st

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.config.settings import ConfigError, load_settings  # noqa: E402
from src.monitoring.logging_setup import setup_logging  # noqa: E402
from src.storage.database import Database  # noqa: E402

st.set_page_config(page_title="Football Market AI Trader", page_icon="⚽", layout="wide")


@st.cache_resource
def get_settings():
    return load_settings()


@st.cache_resource
def get_database(path: str):
    return Database(path).connect()


def safe_json(value) -> str:
    try:
        return json.dumps(value, indent=2, default=str)
    except (TypeError, ValueError):
        return str(value)


def main() -> None:
    try:
        settings = get_settings()
    except ConfigError as exc:
        st.error(f"Configuration error: {exc}")
        st.stop()

    settings.ensure_dirs()
    setup_logging(settings.log_level, settings.log_dir, settings.log_json, "dashboard")
    database = get_database(settings.database_path)

    mode = settings.mode
    live_enabled = settings.live_trading
    st.title("⚽ Football Market AI Trader")
    badge = "🔴 LIVE" if mode == "live" and live_enabled else f"🟢 {mode.upper()}"
    st.caption(
        f"{badge}  |  config: {settings.config_path.name}  |  "
        f"database: {settings.database_path}  |  "
        f"live trading {'ENABLED' if live_enabled else 'DISABLED (default)'}"
    )
    if mode == "live" and live_enabled:
        st.warning(
            "LIVE MODE IS CONFIGURED. This dashboard is read-only - live orders are only "
            "submitted by `python -m app.cli live --confirm ...`."
        )

    tabs = st.tabs([
        "Overview", "Markets", "Signals", "Positions", "Orders", "Performance",
        "Backtest", "Paper", "Risk", "Data", "AI", "Telegram", "Wallet", "Logs", "Settings",
    ])

    signals = database.list_signals(limit=500)
    positions = database.list_positions(status=None, limit=500)
    orders = database.list_orders(limit=300)
    trades = database.list_trades(limit=500)
    pnl = database.latest_pnl()
    risk_events = database.list_risk_events(limit=100)

    # ------------------------------------------------------------------ Overview
    with tabs[0]:
        st.subheader("System overview")
        col1, col2, col3, col4 = st.columns(4)
        col1.metric("Mode", mode)
        col2.metric("Signals recorded", len(signals))
        col3.metric("Open positions", len([p for p in positions if p.get("status") == "OPEN"]))
        col4.metric("Trades", len(trades))
        if pnl:
            col1, col2, col3, col4 = st.columns(4)
            col1.metric("Balance", f"{pnl.get('balance') or 0:.2f}")
            col2.metric("Realized P/L", f"{pnl.get('realized_pnl') or 0:+.2f}")
            col3.metric("Exposure", f"{pnl.get('exposure') or 0:.2f}")
            col4.metric("Open positions", pnl.get("open_positions") or 0)
        else:
            st.info("No P/L snapshots yet. Run `python -m app.cli paper` to generate some.")
        st.markdown("#### Risk limits")
        st.json(settings.section("risk"))
        if risk_events:
            st.markdown("#### Recent risk events")
            st.dataframe(risk_events[:20], use_container_width=True)

    # ------------------------------------------------------------------- Markets
    with tabs[1]:
        st.subheader("Discovered Polymarket football markets")
        st.caption(
            "Markets are discovered live from Polymarket. Nothing here is hardcoded. "
            "A market that cannot be classified becomes UNKNOWN and is never traded."
        )
        if st.button("Run discovery scan (read-only)"):
            with st.spinner("Discovering live football markets..."):
                try:
                    from src.core.runtime import Runtime

                    runtime = Runtime.build(settings, load_data=False, build_venue=False)
                    try:
                        result, discovery = runtime.discover_markets()
                        rows = [
                            {
                                "market_id": m.market_id,
                                "type": m.market_type.value if hasattr(m.market_type, "value") else m.market_type,
                                "confidence": m.classification_confidence,
                                "label": m.label,
                                "ask": (m.snapshot.executable_buy_price if m.snapshot else None),
                                "bid": (m.snapshot.bid if m.snapshot else None),
                                "spread": (m.snapshot.spread if m.snapshot else None),
                                "liquidity": (m.snapshot.liquidity if m.snapshot else None),
                                "league": m.league,
                            }
                            for m in result.accepted
                        ]
                        st.session_state["discovery"] = rows
                        st.session_state["discovery_stats"] = discovery.stats.as_dict()
                        st.session_state["discovery_rejections"] = result.rejection_reasons()
                    finally:
                        runtime.close()
                except Exception as exc:
                    st.error(f"Discovery failed: {exc}")
        rows = st.session_state.get("discovery")
        if rows:
            st.write(st.session_state.get("discovery_stats"))
            st.write("rejections:", st.session_state.get("discovery_rejections"))
            st.dataframe(rows, use_container_width=True)
        else:
            st.info("Click the button to scan. Stored markets:")
            st.dataframe(database.list_markets(limit=200), use_container_width=True)

    # ------------------------------------------------------------------- Signals
    with tabs[2]:
        st.subheader("Signals")
        if signals:
            states = sorted({s.get("state") for s in signals})
            chosen = st.multiselect("Filter by state", states, default=states)
            filtered = [s for s in signals if s.get("state") in chosen]
            st.dataframe(filtered[:200], use_container_width=True)
            payload = st.selectbox(
                "Inspect a signal",
                options=range(len(filtered[:100])),
                format_func=lambda i: f"{filtered[i].get('signal_id')} {filtered[i].get('state')} "
                                      f"{filtered[i].get('market_type')}",
            ) if filtered else None
            if payload is not None:
                st.json(filtered[payload])
                try:
                    st.text(json.dumps(json.loads(filtered[payload]["trace_json"]), indent=2)[:8000])
                except (KeyError, TypeError, ValueError):
                    pass
        else:
            st.info("No signals yet. Run `python -m app.cli signals` or shadow mode.")

    # ----------------------------------------------------------------- Positions
    with tabs[3]:
        st.subheader("Positions")
        st.dataframe(positions, use_container_width=True)

    # -------------------------------------------------------------------- Orders
    with tabs[4]:
        st.subheader("Orders")
        st.dataframe(orders, use_container_width=True)

    # --------------------------------------------------------------- Performance
    with tabs[5]:
        st.subheader("Performance (model vs trading, reported apart)")
        from src.analytics.performance import PerformanceReport

        report = PerformanceReport(trades=[
            {"pnl": t.get("pnl"), "stake": (t.get("price") or 0) * (t.get("size") or 0),
             "price": t.get("price"), "slippage": t.get("slippage")}
            for t in trades
        ])
        st.markdown("##### Trading performance")
        st.json(report.trading_metrics())
        st.markdown("##### Model performance")
        st.info(
            "Model calibration is measured in the backtest tab / `python -m app.cli calibrate`, "
            "because it needs labelled outcomes rather than trade records."
        )

    # ---------------------------------------------------------------- Backtest
    with tabs[6]:
        st.subheader("Backtest")
        st.caption(
            "Historical prices are proxied from bookmaker closing odds (overround removed) because "
            "Polymarket historical football order books are not freely available. See DATA LIMITATIONS "
            "in the report."
        )
        col1, col2 = st.columns(2)
        market_type = col1.selectbox("Market type", ["MATCH_RESULT", "TOTAL_GOALS", "BTTS"])
        league = col2.text_input("League code (blank = all loaded)", "")
        if st.button("Run backtest"):
            with st.spinner("Loading history and running walk-forward backtest..."):
                try:
                    from src.core.runtime import Runtime

                    runtime = Runtime.build(settings, load_data=True, build_venue=False)
                    try:
                        result = runtime.run_backtest(market_type=market_type, league=league)
                    finally:
                        runtime.close()
                    if "error" in result:
                        st.error(result["error"])
                    else:
                        st.session_state["backtest"] = result
                except Exception as exc:
                    st.error(f"Backtest failed: {exc}")
        if st.session_state.get("backtest"):
            result = st.session_state["backtest"]
            col1, col2 = st.columns(2)
            col1.markdown("##### Model performance")
            col1.json(result.get("MODEL_PERFORMANCE", {}))
            col2.markdown("##### Trading performance")
            col2.json(result.get("TRADING_PERFORMANCE", {}))
            st.markdown("##### Data limitations")
            for item in result.get("data_limitations", []):
                st.write(f"- {item}")

    # -------------------------------------------------------------------- Paper
    with tabs[7]:
        st.subheader("Paper trading")
        st.caption("Paper mode is started from the CLI: `python -m app.cli paper`. "
                   "The dashboard shows the resulting state.")
        st.json({"starting_balance": settings.get("paper.starting_balance"),
                 "rejection_rate": settings.get("paper.rejection_rate"),
                 "slippage_model": settings.get("paper.slippage_model")})

    # --------------------------------------------------------------------- Risk
    with tabs[8]:
        st.subheader("Risk")
        from src.risk.limits import RiskLimits

        limits = RiskLimits.from_config(settings.section("risk"), settings.section("strategy"))
        st.json(limits.__dict__)
        problems = limits.validate()
        if problems:
            st.error("Risk configuration problems: " + "; ".join(problems))
        else:
            st.success("Risk configuration is internally consistent.")
        st.markdown("##### Circuit breaker")
        st.json(settings.get("risk.circuit_breaker", {}))

    # --------------------------------------------------------------------- Data
    with tabs[9]:
        st.subheader("Data providers")
        st.json(settings.get("data.providers", {}))
        st.markdown("##### Provider health (last run)")
        st.dataframe(database.list_providers(), use_container_width=True)
        st.markdown("##### Match history in database")
        col1, col2 = st.columns(2)
        col1.metric("Matches", database.match_count())
        col2.metric("Markets stored", len(database.list_markets(limit=10000)))

    # ----------------------------------------------------------------------- AI
    with tabs[10]:
        st.subheader("AI layer (optional)")
        ai = settings.section("ai")
        st.json(ai)
        st.info(
            "The AI layer may only extract structured facts from unstructured text. It can never "
            "emit a trading decision, size or price, and it cannot bypass the risk engine."
        )
        st.write("API key:", "set" if settings.secrets.has_ai() else "missing")

    # ----------------------------------------------------------------- Telegram
    with tabs[11]:
        st.subheader("Telegram notifications")
        st.json(settings.section("notifications"))
        st.write("Bot token:", "set" if settings.secrets.telegram_bot_token else "missing")
        st.write("Chat id:", "set" if settings.secrets.telegram_chat_id else "missing")
        st.caption("Create your OWN bot with @BotFather. This project never logs into a personal account.")
        st.dataframe(database.query("SELECT * FROM notifications ORDER BY ts DESC LIMIT 50"),
                     use_container_width=True)

    # ------------------------------------------------------------------- Wallet
    with tabs[12]:
        st.subheader("Wallet / signing")
        st.json({
            "signer": "set" if settings.secrets.polymarket_private_key else "missing",
            "api_credentials": "set" if settings.secrets.has_polymarket_api_creds() else "missing",
            "funder_address": "set" if settings.secrets.polymarket_funder_address else "missing",
            "chain_id": settings.chain_id,
            "signature_type": settings.signature_type,
            "live_trading_allowed": settings.live_trading,
        })
        st.warning(
            "Use a DEDICATED trading wallet with only the funds you can afford to lose. "
            "Never enter a seed phrase anywhere. This project will refuse one."
        )

    # --------------------------------------------------------------------- Logs
    with tabs[13]:
        st.subheader("Logs")
        st.dataframe(database.list_logs(limit=200), use_container_width=True)
        log_file = settings.abs_path(settings.log_dir) / "football_trader.log"
        if log_file.exists():
            st.markdown("##### Tail of the log file (secrets are redacted)")
            lines = log_file.read_text(encoding="utf-8", errors="ignore").splitlines()[-120:]
            st.code("\n".join(lines))

    # ----------------------------------------------------------------- Settings
    with tabs[14]:
        st.subheader("Effective configuration (redacted)")
        st.json(settings.redacted_summary())
        problems = settings.validate()
        if problems:
            st.error("Configuration problems: " + "; ".join(problems))
        else:
            st.success("Configuration validated.")


main()
