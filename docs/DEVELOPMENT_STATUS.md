# Development Status

**Version:** 1.0.0 · **Status:** research/automation release · **Live trading:** disabled by default

## Phase status

| Phase | Scope | Status |
|---|---|---|
| 0 | Environment + requirements audit | ✅ done, recorded below |
| 1 | Project skeleton, configuration, logging, database | ✅ done, tested |
| 2 | Polymarket discovery, football event discovery, classification, prices/order books | ✅ done, tested against the live API |
| 3 | Football data providers, normalization, validation | ✅ done, tested against live providers |
| 4 | Dynamic data requirements, feature engine | ✅ done, tested |
| 5 | Prediction models | ✅ done for 9 families; 15 families refused by design |
| 6 | Probability calibration | ✅ done (Platt + isotonic), fitted from out-of-sample walk-forward output |
| 7 | Edge engine | ✅ done, decomposition unit-tested |
| 8 | Risk engine, correlation, circuit breaker | ✅ done, tested |
| 9 | Backtesting | ✅ done, with documented price-proxy limitations |
| 10 | Shadow mode | ✅ done, tested |
| 11 | Paper trading | ✅ done, tested |
| 12 | Telegram notifications | ✅ done, tested with a fake client. **Not verified against a real bot** (needs your token). |
| 13 | Dashboard | ✅ 15 tabs implemented. **Rendered only in a headless smoke test.** |
| 14 | Polymarket auth / wallet / execution | ⚠️ **Code complete, import-checked, triple-gated — never exercised against real funds.** |
| 15 | Position management / exits | ✅ done, tested; exit rules documented |
| 16 | Security and failure testing | ✅ done: 60+ failure/security tests, repo secret scan, CI |
| 17 | Live trading with a tiny balance | ⛔ **NOT DONE — requires your credentials and your money. Deliberately out of scope for the authors.** |
| 18 | GitHub v1.0 release | ⚠️ repository prepared; push requires your GitHub authentication |

## Verified by execution

Everything below was run and observed (not asserted from reading code):

| What | Evidence |
|---|---|
| Full test suite | `420 passed` |
| Lint | `ruff check .` → `All checks passed!` |
| Secret scan | `python scripts/scan_secrets.py` → `Secret scan clean (125 files checked).` |
| Pre-flight against live APIs | `python -m app.cli check` → `READY - 0 critical failure(s), 0 warning(s)` |
| Live market discovery | `python -m app.cli markets` → 1000 events, 19,374 markets scanned, 17,381 classified |
| Live data providers | `football_data_uk=HEALTHY, fpl=HEALTHY, openfootball=HEALTHY`; 2,916 finished matches, 667 players |
| End-to-end signal generation | Real teams (Arsenal vs Chelsea) → model probability, calibrated probability, executable ask, realistic edge, decision — printed above in this repo's history |
| Backtest | Walk-forward run on E0 history with separate model/trading reports |
| Configuration validation | `python -m app.cli lint-config` → `Configuration is valid.` |
| Database schema | 19 tables created and exercised by tests |

## What is simulated vs real

| Component | Reality |
|---|---|
| Polymarket market discovery | **Real** — live Gamma API |
| Polymarket prices and order books | **Real** — live CLOB API, real bid/ask/depth |
| Football history, xG, availability | **Real** — football-data.co.uk and the official FPL API |
| Model probabilities | **Real** — fitted on the loaded history |
| Backtest prices | **Simulated proxy** — bookmaker closing odds with overround removed, plus a configured spread/slippage. Free historical Polymarket football order books do not exist. |
| Paper fills | **Simulated** — walks the real book for slippage and depth, applies a simulated rejection rate and a seeded RNG. No real money. |
| Shadow decisions | **Real** — but no orders leave the process |
| Live orders | **Never executed by the authors.** The code path exists and is gated. |

## Known limitations

1. **Historical Polymarket football order books are unavailable for free.** The backtest therefore
   measures the *model* well but only *proxies* execution. Do not read backtest ROI as an expected
   live return.
2. **Live execution is untested against real funds.** Treat it as beta. Verify against the current
   [Polymarket docs](https://docs.polymarket.com) before enabling it; the SDK surface has been moving.
3. **`PLAYER_SHOTS` refuses by default** — no free per-player shots feed.
4. **`CARDS`, exact score, half-time, first-to-score, to-advance and others are `UNSUPPORTED`.**
   Refusing beats inventing.
5. **No confirmed lineups.** Starting probability is estimated from minutes and availability, and is
   labelled an estimate everywhere it appears.
6. **League coverage limits which live markets are tradeable.** A live market for a league you have
   not loaded correctly returns `INSUFFICIENT_DATA`.
7. **Team-name joins are heuristic.** Aliases are normalised but not exhaustive; a failed join refuses
   rather than guessing.
8. **Calibration quality depends on backtest volume.** Under `min_samples` it falls back to identity
   and says so.
9. **The dashboard is read-only by design** and was only smoke-tested, not exercised in a browser by
   the authors.
10. **Telegram is unverified against a real bot** (no token available in the build environment).
11. **SQLite only.** The storage layer is Postgres-ready by design (a single class owns all SQL) but
    no Postgres backend is implemented yet.
12. **No `rest_days_delta` / fixture-congestion feature** — removed from the plan rather than faked.

## Next steps (recommended, in order)

1. **Push and verify the repository** (requires your GitHub auth).
2. **Run `python -m app.cli check` on your machine** and confirm every line passes with your `.env`.
3. **Run shadow mode for a few days.** Confirm the refusals make sense and nothing trips spuriously.
4. **Run paper mode** and compare simulated fills with live book snapshots. Watch for partial fills
   and slippage behaving as modelled.
5. **Add a per-player shots provider** to unlock `PLAYER_SHOTS`.
6. **Add more leagues** to widen the set of live markets your history covers.
7. **Fit the calibrator on a longer walk-forward window** and re-check Brier skill score > 0.
8. **Implement the PostgreSQL backend** for multi-process deployments.
9. **Add a Streamlit-authenticated deployment path** if you host the dashboard remotely.
10. **Only then** consider live mode, with `RISK_MAX_TRADE` at the minimum and a dedicated wallet.
