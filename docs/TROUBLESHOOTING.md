# Troubleshooting

Run this first — it catches most problems and includes the repository secret scan:

```bash
python -m app.cli check
python -m app.cli config
```

---

## Polymarket authentication

### `LIVE TRADING IS DISABLED`

```
LiveExecutionDisabled: LIVE TRADING IS DISABLED. Set POLYMARKET_ALLOW_LIVE_TRADING=true and run
`python -m app.cli live --confirm "I UNDERSTAND THE RISK"`.
```

This is the intended default. To enable live you need **all three**: the env flag, the matching
`--confirm` phrase, and `--i-understand-risk`.

### `py-clob-client is not installed`

```
LiveExecutionUnavailable: py-clob-client is not installed. Live trading needs it:
`pip install -r requirements-live.txt`.
```

```bash
pip install -r requirements-live.txt
```

Before you rely on it, check the current official docs at
[docs.polymarket.com](https://docs.polymarket.com) — Polymarket has been migrating from
`py-clob-client` / `py-clob-client-v2` toward a unified `polymarket-client` SDK, and
`src/execution/polymarket.py` tells you which surface it expects.

### `no dedicated trading-wallet key configured`

`POLYMARKET_PRIVATE_KEY` is empty. Export the key of your **dedicated** trading wallet
([reveal.polymarket.com](https://reveal.polymarket.com) for email/Magic accounts) into `.env` or the
OS keyring.

### `A seed phrase / mnemonic was supplied`

You pasted a seed phrase where a private key belongs. This project will never accept one — that is a
deliberate refusal, not a bug. Create a dedicated trading wallet and use **its** private key.

### `private key must be a 32-byte hex string`

The value is not a 64-hex-character string. Check for whitespace, quotes or a `0x` inside a quoted
value. Correct: `POLYMARKET_PRIVATE_KEY=0xabc...` (64 hex chars after the prefix).

### `wallet/funder mismatch`, or a zero balance on a funded account

Almost always `POLYMARKET_SIGNATURE_TYPE` / `POLYMARKET_FUNDER_ADDRESS`:

| Your login | `SIGNATURE_TYPE` | `FUNDER_ADDRESS` |
|---|---|---|
| Direct EOA wallet | `0` | *(leave empty)* |
| Polymarket email / Magic | `1` | your Polymarket proxy address |
| MetaMask / Rabby / Coinbase | `2` | your Polymarket proxy address |

The **funder** is the address that holds your USDC on Polymarket — not necessarily the address you
exported the key from. Find it in the Polymarket UI under your wallet/deposit address.

### `insufficient balance` / `allowance problem`

1. Confirm USDC (not another token) on **Polygon** (chain id 137) in the funder address.
2. Place one small trade manually through the Polymarket UI first — that sets the USDC allowances for
   the exchange contracts.
3. If you migrated or lost the key to the original wallet, the allowances must be set again.

### `order submission failed: …`

`src/execution/polymarket.py` deliberately does **not** retry financial orders automatically, because
a retry can duplicate a fill. On a timeout, reconcile instead of re-running:

```python
from src.execution.polymarket import PolymarketExecutor   # or use the reconciler in the client
executor.reconcile(client_order_id="fmt-…")
```

### `circuit breaker OPEN` / new orders blocked

Something tripped the breaker: consecutive errors, repeated rejections, stale data, an abnormal price
move, or a daily-loss breach. Inspect and reset:

```bash
python -m app.cli status
```

```python
runtime.risk.breaker.render()
runtime.risk.breaker.reset("operator name")
```

Fix the underlying cause first; resetting alone will trip it again.

---

## API keys

### `AI provider error: 401`

Wrong or expired AI key. Set `AI_API_KEY` (or `AI_ENABLED=false` — the AI layer is optional and
nothing else depends on it).

### `Telegram` notifications silently do nothing

The layer fails quietly by design so a notification outage cannot break trading. Check:

1. `TELEGRAM_ENABLED=true` in `.env`
2. Bot token **and** chat id both set (`python -m app.cli config` shows `set`/`missing`)
3. You have sent at least one message to your bot, so a chat exists
4. `TELEGRAM_MIN_EDGE_TO_NOTIFY` is not filtering out everything
5. The error status in the log: `[telegram] …` lines, or the `notifications` table

---

## Football API

### `football_data_uk: empty response for league E0`

Either the provider is down, or the season code is wrong. Season codes are `2526` for 2025/26,
`2425` for 2024/25. Verify:
[football-data.co.uk/matches.php](https://www.football-data.co.uk/matches.php).

### `data_loaded … matches=0`

No provider returned rows. Check `python -m app.cli check` — the `football_data` line shows each
provider's health. If your machine sits behind a proxy or restrictive DNS, set
`FOOTBALL_DATA_UK_ENABLED` / `FPL_ENABLED` / `OPENFOOTBALL_ENABLED` according to what you can reach.

### Every signal is `INSUFFICIENT_DATA` with "no features could be built"

The live market is for teams your loaded history does not cover. This is correct behaviour — the bot
will not invent a team rating. Either add the league to `FOOTBALL_DATA_UK_LEAGUES`, or accept that
this market is out of scope.

```bash
python -m app.cli markets     # see which leagues exist right now
```

### `PLAYER_SHOTS` always `INSUFFICIENT_DATA`

Expected. The free FPL API does not publish per-player shots. Configure a licensed shots feed to make
that family tradeable; until then the bot refuses it rather than approximating.

---

## Stale data

### `stale_price` / `price is … old (limit 60s)`

Market snapshot is too old to trade on. Raise `trading.max_price_age_seconds` in `config.yaml` only
if you understand the trade-off — a stale price is how you pay the wrong amount.

### `CRITICAL data health` in pre-flight

One or more providers returned `CRITICAL`. The bot refuses to trade. Check the `football_data` /
`polymarket_api` lines for which one failed, then retry. Transient provider outages are normal; the
circuit breaker and health states exist to make them non-fatal.

---

## Database

### `sqlite3.OperationalError: unable to open database file`

`DATABASE_PATH` points at a directory, or a path you cannot write to.

```bash
python -m app.cli init        # recreates directories + schema
python -m app.cli status
```

### `database is locked`

Another process holds a write transaction. SQLite is single-writer: stop the second instance of the
bot, or the dashboard if it is writing. WAL mode is enabled, so reads are unaffected.

---

## Model

### The backtest reports zero trades

Either the edge never cleared `min_edge` (correct — a real finding), or there is no bookmaker line for
that market, or too little history. Read the `DATA LIMITATIONS` block in the report and the
`note` field in `TRADING_PERFORMANCE`.

### `InsufficientModelData: need >= 120 finished matches`

Lower `models.<family>.min_history_matches` **if** you accept a weaker model, or load more seasons by
adjusting `FOOTBALL_DATA_UK_SEASON`.

### Calibration metrics look identical before/after

With fewer than `calibration.min_samples` (default 30) the service uses the identity calibrator and
records `skipped` in the metrics. Run `python -m app.cli calibrate` on a longer backtest window.

---

## Dashboard

### `streamlit: command not found`

```bash
pip install -r requirements.txt          # streamlit is a declared dependency
streamlit run app/dashboard/streamlit_app.py
```

### Dashboard shows no data

It reads the SQLite file directly. Confirm it is the same `DATABASE_PATH`, and that you have run
something (`python -m app.cli paper` or `shadow`) to populate it. The Markets tab has a button to run
a read-only discovery scan.

### Port already in use

```bash
streamlit run app/dashboard/streamlit_app.py --server.port 8502
```

---

## Order rejection

`OrderManager.execute_signal` returns a **rejected result** (it does not raise) with a reason. The
common ones:

| Message | Meaning / fix |
|---|---|
| `duplicate protection: an open position already exists` | one position per market by design |
| `duplicate protection: an open order already exists` | a resting order exists; cancel or wait |
| `price moved … refusing to chase` | the book moved beyond `max_price_drift`; take the new price or skip |
| `insufficient book depth: … unfilled` | not enough liquidity for your size; reduce size or skip |
| `estimated slippage … above max` | the book is too thin at your size |
| `no order book available for the price recheck` | the venue could not report a book; refused on purpose |
| `insufficient simulated cash` (paper) | paper portfolio is out of virtual capital |
| `simulated exchange rejection` (paper) | the simulated rejection rate fired; expected |

---

## Network failure

Transport errors are retried with exponential backoff for **read-only GETs** (4 attempts, 1–20 s).
Financial order submission is **not** auto-retried. If the network is down:

* `check` reports the provider as `CRITICAL` and trading is blocked;
* the circuit breaker trips on repeated errors;
* backlogged work resumes when connectivity returns.

Adjust the HTTP timeout with `data.providers.*.timeout_seconds` in `config.yaml`.

---

## Configuration

### `CONFIG ERROR: TRADING_MODE must be one of …`

Typo in `TRADING_MODE`. Valid: `backtest`, `shadow`, `paper`, `live`.

### `TRADING_MODE=live requires POLYMARKET_ALLOW_LIVE_TRADING=true`

Intentional. Both are required.

### `risk.max_trade cannot exceed risk.capital`

Your limits are internally inconsistent — a single trade could exceed your bankroll.

```bash
python -m app.cli lint-config
python -m app.cli config
```

---

## Tests

### `pytest` fails on a fresh clone

```bash
pip install -r requirements.txt -r requirements-dev.txt
pytest -q
```

Default tests use fakes and need **no** network and **no** credentials. Network tests are opt-in:

```bash
pytest -q -m network
```

### `scripts/scan_secrets.py` flags a file

It found 64-hex, Telegram-token, AWS, GitHub, Slack, Google or OpenAI-key-shaped strings in a tracked
file. If it is a genuine false positive, add the path to `ALLOWLIST` in that script — never weaken a
pattern to hide a real credential.
