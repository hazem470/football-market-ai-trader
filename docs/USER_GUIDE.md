# User Guide

Written for someone who has never run a Python project. Follow it in order. **Do not skip to
live trading** — the whole guide is built to get you to it safely, and the bot itself refuses to
start if you skip a step.

> Reminder: this software can lose money. Nothing here is financial advice and there is no profit
> guarantee. Live trading is disabled until you explicitly enable it.

---

## STEP 1 — Install Python

You need **Python 3.10 or newer** (3.11 recommended).

* **Windows**: download from [python.org/downloads](https://www.python.org/downloads/) and **tick
  "Add Python to PATH"** in the installer.
* **macOS**: `brew install python@3.11` (or the installer from python.org).
* **Linux**: `sudo apt install python3 python3-venv python3-pip`

Verify:

```bash
python --version      # or: python3 --version
```

## STEP 2 — Install Git

* **Windows**: [git-scm.com/download/win](https://git-scm.com/download/win)
* **macOS**: `brew install git`
* **Linux**: `sudo apt install git`

Verify: `git --version`

## STEP 3 — Clone the repository

```bash
git clone https://github.com/<YOUR_GITHUB_USERNAME>/football-market-ai-trader.git
cd football-market-ai-trader
```

## STEP 4 — Install dependencies

Create an isolated environment so nothing pollutes your system Python:

```bash
python -m venv .venv

# Windows
.venv\Scripts\activate

# macOS / Linux
source .venv/bin/activate

pip install -r requirements.txt
```

Your prompt now shows `(.venv)`. Everything below assumes it is active.

## STEP 5 — Create your `.env`

```bash
python -m app.cli init
```

This creates the folders, the database, and copies `.env.example` to `.env`.

**Open `.env` in a text editor now.** Every value is optional except `TRADING_MODE`, which is already
set to the safe default `backtest`.

> **The `.env` file will never be committed to Git.** It is in `.gitignore`. Do not move it
> somewhere tracked, and never paste its contents into a chat, an issue or a Discord message.

## STEP 6 — Create and configure your Polymarket account

Market **discovery and analysis need no credentials at all** — Polymarket's market data is public.
You only need an account for live trading.

1. Go to [polymarket.com](https://polymarket.com) and create an account.
2. **Create a dedicated trading wallet** for the bot — do **not** reuse your main wallet:
   * Easiest: use Polymarket's own email/Magic login, which gives you a proxy wallet.
   * Or connect a fresh browser wallet (MetaMask / Rabby / Coinbase Wallet) created solely for this.
3. Fund it with **USDC on Polygon** — only the amount you are willing to risk.
4. Set the required USDC allowances for the Polymarket exchange contracts (Polymarket's UI does this
   when you place your first trade manually).
5. If you use a **proxy wallet** (email/Magic login or a browser-wallet proxy), find your
   **funder address** — the address that holds your USDC on Polymarket. You need it for step 7.

**Never enter a seed phrase into this project. It will refuse it, and you should never need to.**

## STEP 7 — Configure Polymarket authentication (live mode only)

Skip this entirely for backtest/shadow/paper.

Only once you are ready for live trading:

1. Export the **private key of your dedicated trading wallet** (not your main wallet).
   For a Magic/email account, Polymarket exposes it at
   [reveal.polymarket.com](https://reveal.polymarket.com).
2. Put it in `.env`:

```ini
POLYMARKET_PRIVATE_KEY=0x...
POLYMARKET_CHAIN_ID=137
POLYMARKET_FUNDER_ADDRESS=0x...     # your proxy/funder address, if you use one
POLYMARKET_SIGNATURE_TYPE=1         # 0 = EOA, 1 = Polymarket proxy (email), 2 = browser wallet
POLYMARKET_ALLOW_LIVE_TRADING=false # flip to true ONLY when you are ready (step 14)
```

3. Optionally derive the API credentials ahead of time (saves a round-trip at start-up):

```bash
python -m app.cli init --derive-api-creds
```

`SIGNATURE_TYPE` cheat sheet:

| Your login | `POLYMARKET_SIGNATURE_TYPE` |
|---|---|
| Direct wallet / EOA | `0` |
| Polymarket email or Magic login (proxy) | `1` |
| MetaMask / Rabby / Coinbase Wallet (proxy) | `2` |

## STEP 8 — Configure a football data API

**No key is needed for the defaults.** The project ships with free, legal sources:

| Provider | Cost | What it gives |
|---|---|---|
| `football_data_uk` | free | finished results, goals, shots, shots on target, corners, cards, closing bookmaker odds |
| `fpl` | free | official Fantasy Premier League API: xG/90, xA/90, minutes, availability, team strength |
| `openfootball` | free | fixture/result JSON (fallback history) |

They are on by default in `configs/config.yaml`. Useful knobs in `.env`:

```ini
FOOTBALL_DATA_UK_SEASON=2526
FOOTBALL_DATA_UK_LEAGUES=E0,E1,D1,I1,SP1,F1,N1,P1
```

League codes are public identifiers from
[football-data.co.uk](https://www.football-data.co.uk/notes.txt) (`E0` = Premier League, `D1` =
Bundesliga, `I1` = Serie A, `SP1` = La Liga, `F1` = Ligue 1, `N1` = Eredivisie, `P1` = Primeira Liga).

**Important:** if a live Polymarket football market is for a league your history does not cover,
the bot correctly returns `INSUFFICIENT_DATA` and does not trade it. Add more leagues (or a licensed
provider) to widen coverage.

To add a **paid** provider (Sportradar, API-Football, …), implement the `Provider` interface in
`src/data/providers/` and register it — see [CONTRIBUTING.md](../CONTRIBUTING.md).

## STEP 9 — Optionally configure an AI API

Off by default, and **completely optional**. The AI layer only extracts structured facts (injuries,
availability) from unstructured text. It can **never** place a trade or set a price.

```ini
AI_ENABLED=true
AI_PROVIDER=openrouter          # or openai, anthropic
AI_API_KEY=sk-...
AI_MODEL=openai/gpt-4o-mini
```

## STEP 10 — Optionally configure Telegram

1. In Telegram, message **@BotFather**, send `/newbot`, follow the prompts, copy the token.
2. Start a chat with your new bot, then get your chat id from
   `https://api.telegram.org/bot<TOKEN>/getUpdates`.
3. In `.env`:

```ini
TELEGRAM_ENABLED=true
TELEGRAM_BOT_TOKEN=123456:ABC...
TELEGRAM_CHAT_ID=123456789
TELEGRAM_MIN_EDGE_TO_NOTIFY=0.08
```

This project never logs into your personal Telegram account and never sends credentials to Telegram.

## STEP 11 — Start in BACKTEST

```bash
python -m app.cli check                                  # verify everything first
python -m app.cli backtest --market-type MATCH_RESULT --league E0
python -m app.cli calibrate --market-type MATCH_RESULT --league E0
```

Read the output carefully. It reports **MODEL PERFORMANCE** (Brier score, log loss, calibration) and
**TRADING PERFORMANCE** (P/L, ROI, drawdown) **separately**, because a well-calibrated model does not
imply profitable execution. It also prints the **DATA LIMITATIONS** — read those before drawing any
conclusion.

## STEP 12 — Move to SHADOW

Real markets, real decisions, **zero orders**.

```bash
python -m app.cli shadow --limit 25
```

Check that the signals are sensible and that refusals (`NO_TRADE`, `INSUFFICIENT_DATA`,
`UNSUPPORTED`) have reasons you believe.

## STEP 13 — Move to PAPER

Live data, virtual capital, simulated fills against the real order book, real risk limits.

```ini
TRADING_MODE=paper
```

```bash
python -m app.cli paper --limit 25 --iterations 4 --poll 120
```

Watch for: fills being rejected for good reasons, exposure staying under your limits, and the
circuit breaker never tripping spuriously.

## STEP 14 — Only then enable LIVE

Live mode needs **all three**:

1. `.env`: `POLYMARKET_ALLOW_LIVE_TRADING=true`
2. `.env`: `TRADING_MODE=live`
3. the exact confirmation phrase on the command line

```bash
python -m app.cli live --limit 5 --confirm "I UNDERSTAND THE RISK" --i-understand-risk
```

Before the first order the pre-flight runs all 15 checks. **If any critical check fails, no order is
placed.** Start with a tiny funded balance and small `RISK_MAX_TRADE` values.

### Stopping

Ctrl-C in the live loop shuts down cleanly. From another terminal:

```bash
python -m app.cli stop --reason "manual stop"
```

The kill switch blocks new orders and asks resting orders to cancel; it does not force-close
positions.

### Turning live back off

Set `POLYMARKET_ALLOW_LIVE_TRADING=false` in `.env` (and `TRADING_MODE=backtest`). Live mode is then
unreachable regardless of the CLI flags.

---

## Day-to-day commands

| Command | What it does |
|---|---|
| `python -m app.cli version` | version and mode support |
| `python -m app.cli check` | full pre-flight health check |
| `python -m app.cli config` | effective configuration with secrets shown only as set/missing |
| `python -m app.cli markets` | discover and classify the live football markets right now |
| `python -m app.cli signals --limit 25` | model markets and print signals (places nothing) |
| `python -m app.cli backtest --market-type …` | historical walk-forward test |
| `python -m app.cli calibrate --market-type …` | fit the probability calibrator |
| `python -m app.cli shadow --limit 25` | shadow mode |
| `python -m app.cli paper --limit 25` | paper trading |
| `python -m app.cli live --confirm …` | LIVE (gated) |
| `python -m app.cli status` | health, exposure, recent activity |
| `python -m app.cli stop` | kill switch |
| `python -m app.cli test` | run the test suite |
| `streamlit run app/dashboard/streamlit_app.py` | read-only dashboard |

## Reading a signal

```
Signal ID      : SIG-2026-0920-6B3941
Market         : Arsenal vs Chelsea | Arsenal
Market type    : MATCH_RESULT
Model          : dixon_coles_match_result
Model prob     : 73.61%
Calibrated prob: 73.61%
Market price   : 30.00%      <- the price you can really execute at (the ask)
Realistic edge : +37.81%     <- after spread, slippage, fees and uncertainty
Confidence     : 95.00%
Decision       : BUY
Reason         : estimated probability 73.61% materially exceeds the executable market price ...
```

The **market price** is the executable ask, not a midpoint. The **realistic edge** is what is left
after every cost. The **decision** is refused whenever the data or the price is not trustworthy.

## Understanding the refusal states

| State | Meaning |
|---|---|
| `NO_TRADE` | We have everything we need, and this is not a good bet. |
| `INSUFFICIENT_DATA` | The market is valid but the data behind it is missing, stale or unsourceable. |
| `UNSUPPORTED` | This market family has no v1.0 data path. We will not guess. |
| `BUY` / `SELL` | An actionable signal that has also passed the risk engine. |

A high refusal rate is the system working as designed, not a bug.

## Troubleshooting

Start with [TROUBLESHOOTING.md](TROUBLESHOOTING.md).
