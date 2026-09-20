# Architecture

## Module map

```
football-market-ai-trader/
├── main.py                        # safe entry point (defaults to `check`)
├── app/
│   ├── cli/main.py                # the CLI: init check markets signals backtest
│   │                              # calibrate shadow paper live status stop test
│   └── dashboard/streamlit_app.py # 15-tab read-only dashboard
├── configs/
│   ├── config.yaml                # all non-secret configuration
│   └── requirements.json          # extend the data-requirements engine
├── src/
│   ├── config/settings.py         # precedence, validation, redacted summary
│   ├── core/
│   │   ├── http.py                # HttpClient protocol + HttpxClient + FakeHttpClient
│   │   ├── preflight.py           # the 15 pre-flight checks + repository secret scan
│   │   └── runtime.py             # the only place the engine is assembled
│   ├── markets/
│   │   ├── discovery.py           # Gamma paging -> canonical Market objects
│   │   ├── classifier.py          # sportsMarketType map + ordered text rules
│   │   ├── schema.py              # Market, MarketSnapshot, MarketType, name normalisation
│   │   ├── filters.py             # hard tradeability filters
│   │   └── orderbook.py           # book analysis + book-walking fill simulation
│   ├── data/
│   │   ├── providers/             # base, polymarket, football_data_uk, fpl,
│   │   │                          # openfootball, injuries, lineups, news, registry
│   │   ├── normalization/         # canonical MatchRecord / PlayerRecord / TeamHistory
│   │   └── validation/            # freshness, completeness, impossible-value checks
│   ├── features/
│   │   ├── requirements.py        # MARKET TYPE -> REQUIRED DATA -> FEATURES -> MODEL
│   │   └── engine.py              # builds FeatureSet, records provenance + missing
│   ├── models/
│   │   ├── poisson_core.py        # Dixon-Coles team strengths, ScoreMatrix, RateDistribution
│   │   ├── match_result.py goals.py corners.py
│   │   ├── player_goal.py player_assist.py player_shots.py cards.py
│   │   └── registry.py            # (market_type, league) -> fitted model, lazily cached
│   ├── calibration/calibrators.py # Platt, isotonic, Brier/log-loss/ECE/reliability
│   ├── strategy/
│   │   ├── edge.py                # raw edge -> realistic edge, with the full decomposition
│   │   ├── signals.py             # Signal + SignalState (BUY/SELL/NO_TRADE/…)
│   │   └── strategy.py            # the decision ladder
│   ├── risk/
│   │   ├── limits.py sizing.py exposure.py correlation.py
│   │   ├── circuit_breaker.py     # kill switch, triggers, half-open recovery
│   │   └── engine.py              # the single gate every order passes
│   ├── execution/
│   │   ├── base.py                # ExecutionVenue / OrderRequest / OrderResult
│   │   ├── order_manager.py       # revalidation, duplicate protection, submission
│   │   ├── position_manager.py    # documented exit rules
│   │   └── polymarket.py          # live CLOB execution (gated)
│   ├── wallet/keystore.py         # refuses seed phrases; .env / OS-keyring only
│   ├── paper/simulator.py         # Portfolio + PaperVenue (real book, virtual capital)
│   ├── backtest/engine.py         # walk-forward backtest, separate model/trading reports
│   ├── analytics/performance.py   # model vs trading performance reporting
│   ├── notifications/             # telegram.py + events.py (thresholds, event routing)
│   ├── monitoring/
│   │   ├── logging_setup.py       # structured logging + mandatory redaction
│   │   └── health.py              # HEALTHY / WARNING / CRITICAL, blocks trading
│   ├── ai/                        # optional fact extraction (never decides)
│   └── storage/database.py        # SQLite schema + typed accessors (Postgres-ready)
├── tests/                         # 420 tests: unit, integration, failure, security
├── scripts/                       # run_all.py, scan_secrets.py
└── docs/
```

## The decision ladder

`src/strategy/strategy.py::evaluate_market` is the whole strategy, in order:

1. **Unsupported family?** -> `UNSUPPORTED`. No validation, no price lookups, no wasted work.
2. **Can the market even be priced?** (`validate_market`) -> `INSUFFICIENT_DATA` on failure:
   missing token, unclassifiable type, stale snapshot, out-of-range bid/ask, no resolution time,
   missing participants.
3. **Build features** from the data-requirements plan. No plan -> `UNSUPPORTED`.
   Zero features -> `INSUFFICIENT_DATA`.
4. **Model probability.** Any `insufficient_data` from the model -> `INSUFFICIENT_DATA`.
   **Any required feature still missing -> `INSUFFICIENT_DATA`**, even if the model returned a
   number: such a number would be extrapolated from imputed inputs.
5. **Calibrate** (identity when no calibrator is fitted).
6. **Edge** against the real executable price. Incomplete -> `NO_TRADE`.
7. **Decide**: edge below threshold -> `NO_TRADE` (with the number in the explanation);
   confidence below threshold -> `NO_TRADE`; otherwise `BUY`.

Every branch writes a `Signal` carrying the feature key, model version, calibration version,
feature snapshot, plan and the full edge breakdown — so any decision can be replayed later.

## Edge decomposition

`src/strategy/edge.py` turns a probability and a real order book into the edge a taker can actually
get. For a YES buy, with the model at 63% and a market quoting 0.475 / 0.485:

| Item | Value |
|---|---|
| Model probability | 63.0% |
| Executable market price (ask) | 48.5% |
| **Raw edge** | **+14.5%** |
| Spread share (half of 1.0c) | −0.5% |
| Assumed slippage | −0.8% |
| Fees | −0.5% × price |
| Model uncertainty | −2.0% |
| Data uncertainty | −1.0% |
| Execution uncertainty | −0.5% |
| **Realistic edge** | **≈ +9.7%** |

Only the realistic edge is compared against `strategy.min_edge`. Every term is configurable.

## Risk engine

`src/risk/engine.py` evaluates, short-circuiting on the first denial:

| # | Check | Failure |
|---|---|---|
| 1 | Circuit breaker / kill switch | `OPEN` |
| 2 | Price age vs `max_price_age_seconds` | `STALE` |
| 3 | Realistic edge vs `min_edge`, confidence vs `min_confidence` | `LOW` |
| 4 | Liquidity and spread | `LOW` / `WIDE` |
| 5 | Fractional-Kelly sizing (hard-capped) | `ZERO` |
| 6 | Open-position count, daily loss, daily exposure | `AT_LIMIT` / `BREACHED` |
| 7 | **Correlated** exposure per match/team/player bucket | `BREACHED` |
| 8 | Hard max-trade and available balance | `BREACHED` / `INSUFFICIENT` |

### Correlation

Positions that move together share one budget. `group_key_for()` buckets by match, and
`effective_exposure()` weights each existing position by an empirically-set overlap factor:

| Family pair | Overlap |
|---|---|
| Match result ↔ Team totals | 0.75 |
| Total goals ↔ BTTS | 0.70 |
| Total goals ↔ Team totals | 0.55 |
| Match result ↔ Player goal | 0.45 |
| Player goal ↔ Team totals | 0.45 |
| Player goal ↔ Total goals | 0.40 |
| Match result ↔ Total goals | 0.35 |
| Player goal ↔ BTTS | 0.35 |
| Match result ↔ BTTS | 0.30 |
| Corners ↔ Total goals | 0.25 |
| any other pair | 0.20 |
| same family | 1.00 |

So "Arsenal win + Arsenal over 1.5 + an Arsenal player to score + Arsenal corners" is one bucket,
not four independent bets.

### Position sizing

Kelly for a binary contract at price `p` with true probability `q`:

```
b  = (1 - p) / p
f* = (q * b - (1 - q)) / b  =  (q - p) / (1 - p)
```

The engine uses `kelly_fraction * f*` (default **0.25**) and then clamps by `max_trade`. Full Kelly
is never used by default. Risk limits always override model sizing.

### Circuit breaker

Trips on: consecutive errors, repeated order rejections, stale critical data, abnormal price
movement, daily-loss breach, or a manual `stop`. When open, no new orders are submitted; monitoring
continues; Telegram is notified; the event is recorded in `risk_events`.

## Model maths

### Dixon-Coles team strengths (`src/models/poisson_core.py`)

```
lambda_home = attack_home * defence_away * home_advantage * league_mean
lambda_away = attack_away * defence_home * league_mean
```

Fitted by weighted iterative proportional fitting with a **recency half-life** (default 180 days),
renormalising each team's attack/defence so the model is identifiable. A Dixon-Coles `rho`
correction adjusts the 0-0 / 1-0 / 0-1 / 1-1 cells for the low-score dependency that independent
Poisson gets wrong. The truncated score matrix is renormalised so probabilities sum to 1.

Market probabilities are then read directly off the joint distribution: `P(home)`, `P(draw)`,
`P(away)`, `P(total > line)`, `P(BTTS)`, `P(team X > line)`.

### Count models

Corners and player shots use a `RateDistribution`: Poisson when `dispersion = 0`, negative binomial
when `dispersion > 0` (corners and shots are mildly over-dispersed in practice).

Player props use `P(at least one) = 1 - exp(-lambda)` with

```
lambda = rate_per_90 * (expected_minutes / 90) * attack_factor * defence_factor
```

where `attack_factor` and `defence_factor` are clamped to a sane band (0.4–2.0) so a small sample
cannot produce an absurd lambda.

### Calibration

Platt scaling (`sigmoid(a * logit(p) + b)`, gradient descent with L2 shrinkage toward the identity)
and isotonic regression (pool-adjacent-violators with linear interpolation). **Both are fitted only
on out-of-sample walk-forward predictions** — never in-sample. Quality is reported as Brier score,
log loss, Brier skill score, a reliability table, ECE and MCE.

## Data flow and storage

Every stage persists an auditable record in SQLite (PostgreSQL-ready: only
`src/storage/database.py` touches SQL):

`matches` · `players` · `markets` · `market_snapshots` · `features` · `predictions` · `signals` ·
`orders` · `positions` · `trades` · `pnl` · `risk_events` · `notifications` · `backtests` ·
`system_logs` · `providers` · `users`

Feature bundles are content-addressed (`FEAT-<sha1>`), so a prediction can be reproduced from its
feature key plus the recorded model, feature and calibration versions.

## Data limitations (documented, not hidden)

1. **No free historical Polymarket order books for football.** The backtest prices bets from
   bookmaker **closing odds** with the overround removed, and applies a configurable spread proxy
   and slippage. This measures the model, not Polymarket's microstructure. `docs/DEVELOPMENT_STATUS.md`
   tracks this.
2. **No per-player shots** in the free FPL API, so the `PLAYER_SHOTS` family returns
   `INSUFFICIENT_DATA` by default. It becomes tradeable with a licensed shots feed.
3. **No confirmed lineups** ahead of kick-off from free sources; starting probability is estimated
   from minutes played and availability, and is labelled as an estimate everywhere.
4. **No player-level card data**, so `CARDS` is deliberately `UNSUPPORTED` — converting team-level
   card rates into a player probability would be invented data.
5. **No fixture-congestion data.** `rest_days_delta` was removed from the feature plan rather than
   fabricated.
6. **Team-name joins are heuristic.** Cross-provider aliases are normalised but not exhaustive; a
   failed join produces `INSUFFICIENT_DATA`, never a wrong-team guess.
7. **Live execution is untested against real funds** in this repository. The code path exists,
   imports the official client, and is triple-gated, but no live order has been placed by the
   authors. Treat v1.0 live mode as **untested**.
