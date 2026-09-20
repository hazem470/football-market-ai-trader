# Configuration

Two places, and only two:

* **`.env`** — secrets and deployment-level switches. Gitignored. Copied from `.env.example`.
* **`configs/config.yaml`** — all non-secret behaviour. Safe to commit.

**Precedence (highest wins):** process environment → `.env` → `configs/config.yaml` → built-in
defaults.

**You never edit Python to configure this bot.**

Check what is actually in effect at any time:

```bash
python -m app.cli config          # human readable
python -m app.cli config --json   # machine readable
```

Secrets are always reported as `set` / `missing` — never printed.

---

## `.env` reference

### Trading mode

| Variable | Required | Purpose | Example |
|---|---|---|---|
| `TRADING_MODE` | yes | active mode | `backtest` \| `shadow` \| `paper` \| `live` |
| `POLYMARKET_ALLOW_LIVE_TRADING` | yes (live) | the live-trading master switch | `false` |
| `LIVE_CONFIRMATION_PHRASE` | no | phrase required by `live --confirm` | `I UNDERSTAND THE RISK` |

### Polymarket

Market data is public and needs **no** credentials. These are only for live order placement.

| Variable | Required | Purpose | Example |
|---|---|---|---|
| `POLYMARKET_GAMMA_HOST` | no | market metadata API | `https://gamma-api.polymarket.com` |
| `POLYMARKET_CLOB_HOST` | no | order book / execution API | `https://clob.polymarket.com` |
| `POLYMARKET_CHAIN_ID` | no | Polygon (137) or Amoy testnet (80002) | `137` |
| `POLYMARKET_PRIVATE_KEY` | live | **dedicated trading wallet** key | `0x…` (never commit) |
| `POLYMARKET_API_KEY` | no | L2 API credentials; derived at runtime if blank | — |
| `POLYMARKET_API_SECRET` | no | as above | — |
| `POLYMARKET_API_PASSPHRASE` | no | as above | — |
| `POLYMARKET_FUNDER_ADDRESS` | proxy | address holding your USDC on Polymarket | `0x…` |
| `POLYMARKET_SIGNATURE_TYPE` | proxy | `0` EOA, `1` Polymarket proxy (email), `2` browser wallet | `1` |

### Football data

| Variable | Required | Purpose | Example |
|---|---|---|---|
| `FOOTBALL_DATA_UK_ENABLED` | no | historical results/odds provider | `true` |
| `FOOTBALL_DATA_UK_BASE_URL` | no | provider base URL | `https://www.football-data.co.uk` |
| `FOOTBALL_DATA_UK_SEASON` | no | season code | `2526` (2025/26) |
| `FOOTBALL_DATA_UK_LEAGUES` | no | comma-separated **public** league codes | `E0,E1,D1,I1,SP1,F1,N1,P1` |
| `FPL_ENABLED` | no | official Fantasy Premier League API | `true` |
| `OPENFOOTBALL_ENABLED` | no | fixture/result fallback | `true` |

### AI (optional)

| Variable | Required | Purpose | Example |
|---|---|---|---|
| `AI_ENABLED` | no | enable fact extraction | `false` |
| `AI_PROVIDER` | no | `openrouter` \| `openai` \| `anthropic` | `openrouter` |
| `AI_BASE_URL` | no | provider base URL | `https://openrouter.ai/api/v1` |
| `AI_API_KEY` | no | your key | `sk-…` |
| `AI_MODEL` | no | model id | `openai/gpt-4o-mini` |
| `AI_TIMEOUT_SECONDS` | no | request timeout | `45` |

### Telegram (optional)

| Variable | Required | Purpose | Example |
|---|---|---|---|
| `TELEGRAM_ENABLED` | no | enable notifications | `false` |
| `TELEGRAM_BOT_TOKEN` | no | **your own** bot token from @BotFather | `123:ABC…` |
| `TELEGRAM_CHAT_ID` | no | destination chat | `123456789` |
| `TELEGRAM_MIN_EDGE_TO_NOTIFY` | no | only notify above this edge | `0.08` |

### Storage

| Variable | Required | Purpose | Example |
|---|---|---|---|
| `DATABASE_PATH` | no | SQLite file | `data/football_trader.db` |
| `DATA_DIR` | no | data directory | `data` |
| `CACHE_DIR` | no | provider cache | `data/cache` |
| `LOG_DIR` | no | log directory | `logs` |
| `DASHBOARD_PASSWORD` | no | only if you expose the dashboard | *(empty)* |

### Risk limits

| Variable | Required | Purpose | Default |
|---|---|---|---|
| `RISK_CAPITAL` | no | bankroll in USD | `500` |
| `RISK_MAX_TRADE` | no | hard per-trade cap | `10` |
| `RISK_MAX_DAILY_EXPOSURE` | no | new exposure allowed per day | `50` |
| `RISK_MAX_DAILY_LOSS` | no | daily loss limit | `20` |
| `RISK_MAX_OPEN_POSITIONS` | no | concurrent positions | `6` |
| `RISK_MIN_EDGE` | no | minimum realistic edge | `0.08` |
| `RISK_MIN_CONFIDENCE` | no | minimum model confidence | `0.75` |
| `RISK_MIN_LIQUIDITY` | no | minimum market liquidity | `100` |
| `RISK_MAX_SPREAD` | no | maximum bid/ask spread | `0.04` |
| `RISK_MAX_SLIPPAGE` | no | maximum estimated slippage | `0.02` |
| `RISK_MAX_CORRELATED_EXPOSURE` | no | per-group correlated exposure cap | `25` |
| `RISK_KELLY_FRACTION` | no | fractional Kelly multiplier | `0.25` |

### Logging

| Variable | Required | Purpose | Default |
|---|---|---|---|
| `LOG_LEVEL` | no | `DEBUG` … `CRITICAL` | `INFO` |
| `LOG_JSON` | no | structured JSON logs | `false` |

---

## `configs/config.yaml` reference

### `app`
`name`, `version`, `timezone`.

### `trading`
| Key | Purpose |
|---|---|
| `mode` | active mode (overridden by `TRADING_MODE`) |
| `live_trading` | live master switch (overridden by `POLYMARKET_ALLOW_LIVE_TRADING`) |
| `min_minutes_to_start` | ignore markets starting sooner than this |
| `max_hours_to_resolution` | ignore markets resolving further out than this |
| `poll_interval_seconds` | loop cadence for shadow/paper/live |
| `max_price_age_seconds` | **hard** price freshness gate before any order |
| `max_feature_age_hours` | football data freshness gate |

### `markets`
`min_liquidity`, `min_volume_24h`, `max_spread`, `min_hours_to_resolution`, `page_limit`,
`max_pages`, `fallback_sports_tag_id`, `exclude_market_types`.

### `strategy`
| Key | Purpose |
|---|---|
| `min_edge` | minimum realistic edge required to trade |
| `min_confidence` | minimum confidence required to trade |
| `fee_rate` | taker fee (0 for most Polymarket markets) |
| `assumed_slippage` | per-share adverse fill estimate |
| `model_uncertainty` / `data_uncertainty` / `execution_uncertainty` | edge haircuts |
| `no_trade_on_unknown_market` / `no_trade_on_stale_data` | safety switches |

### `models`
Per-family fitting parameters (`min_history_matches`, `max_goals`, `rho`, `recency_halflife_days`,
`dispersion`, …) plus `versions.model` / `versions.features` / `versions.calibration`, which are
recorded with **every** prediction for reproducibility.

### `calibration`
`enabled`, `method` (`platt` | `isotonic` | `none`), `min_samples`, `artifact_path`.

### `risk`
The same limits as the `.env` block, plus:

```yaml
risk:
  exposure_groups: [match, team, player, event, family]
  circuit_breaker:
    enable: true
    max_consecutive_errors: 5
    max_stale_data_minutes: 180
    abnormal_price_move: 0.25
```

### `paper`
`starting_balance`, `rejection_rate`, `slippage_model` (`spread` | `fixed`), `fixed_slippage`,
`min_fill_ratio`.

### `backtest`
| Key | Purpose |
|---|---|
| `starting_balance` | virtual bankroll |
| `price_source` | `bookmaker` (default) or `model_market_blend` |
| `bookmaker` | which closing odds column to use (`B365`, `Pinnacle`, `Avg`, …) |
| `edge_capture` | fraction of modelled edge assumed realised |
| `min_history_matches` | warm-up window before the first bet |

### `data.providers`
Per-provider `enabled`, `base_url`, cache TTLs and timeouts. `data.validation` holds
`max_market_price_age_seconds`, `max_match_stats_age_days`, `min_coverage_ratio`.

### `ai`
`enabled`, `provider`, `model`, `base_url`, `timeout_seconds`, `forbidden_output_fields`,
`max_news_items_per_match`. `forbidden_output_fields` is the hard boundary that stops the LLM from
ever emitting a trading decision.

### `notifications`
`console`, plus `telegram.enabled`, `telegram.min_edge_to_notify` and the `notify_on` event list:

`opportunity`, `order_submitted`, `order_filled`, `position_closed`, `daily_summary`,
`risk_limit_reached`, `provider_failure`, `execution_failure`, `stopped`, `circuit_breaker`.

### `dashboard`
`title`, `refresh_seconds`.

### `logging`
`level`, `json`, `dir`, `redact_keys`.

---

## `configs/requirements.json`

Extends the **data-requirements engine** without touching Python:

```json
{
  "CORNERS": { "extra_features": ["my_extra_corner_signal"] }
}
```

Any feature listed here must be produced by the feature engine
(`src/features/engine.py`), otherwise the market stays `NO TRADE` for that feature. This is
intentional: the plan and the engine are contractually kept in sync (there is a test for it).

## Validating your configuration

```bash
python -m app.cli lint-config
python -m app.cli config
```

Validation catches: unknown mode, live mode without the master switch, missing signer in live mode,
shapes of capital vs limits, out-of-range Kelly fraction, out-of-range thresholds, bad chain id.
