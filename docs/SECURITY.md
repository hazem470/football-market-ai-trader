# Security Guide

This document is about **your** safety. Read all of it before you put money anywhere near this bot.

---

## The absolute rules

1. **Never share your seed phrase. With anyone. Ever.** Not with this project, not with a
   maintainer, not in a GitHub issue, not over Telegram or Discord, not in the dashboard, not in a
   cloud database. A seed phrase controls your entire wallet, and exposure is irreversible.
2. **Never commit a private key.** `.env` is gitignored; keep it that way. CI fails the build if a
   credential-shaped string is committed.
3. **Never use your main wallet for automated trading.** Use a **dedicated trading wallet** funded
   with only what you can afford to lose.
4. **Never send secrets to Telegram.** The notification layer physically cannot echo them: every log
   record passes a redaction filter first.
5. **Live trading is off by default and needs three separate actions to enable.** Keep it that way
   until you have run shadow and paper mode for a meaningful period.

---

## What this project will refuse to do

* Accept a seed phrase / mnemonic. `src/wallet/keystore.py::assert_not_a_mnemonic` detects anything
  mnemonic-shaped and raises `SeedPhraseRejected` with a blunt explanation. This applies to `.env`,
  the OS keyring and any programmatic input.
* Store a private key inside the repository. `assert_file_not_committable` refuses to read a wallet
  secret from a path inside the repo tree.
* Print a secret. `WalletConfig` and `Secrets` override `__repr__` with `repr=False` on the
  dataclass, so a private key cannot appear in a traceback, a log line or a `print()`.
* Send a secret anywhere. The redaction filter runs on **every** log record before it reaches any
  sink (stdout, the rotating file, or JSON output).

---

## Where credentials live

| Credential | Where | How it is protected |
|---|---|---|
| Dedicated trading wallet private key | `.env` or the OS keyring | gitignored; refused if it looks like a mnemonic; redacted in logs; never echoed by the CLI |
| Polymarket API key / secret / passphrase | `.env` | identical protection; derivable at runtime so you can leave them blank |
| AI provider key | `.env` | optional; never required |
| Telegram bot token | `.env` | optional; your own bot |
| Dashboard password | `.env` | only if you expose the dashboard |

Using the OS keyring instead of `.env`:

```bash
python -c "import keyring; keyring.set_password('football-market-ai-trader','polymarket_private_key','0x...')"
```

Leave `POLYMARKET_PRIVATE_KEY` empty in `.env` and the loader will find it in the keyring
(`load_signer()` reports its source as `env`, `keyring` or `none`).

---

## Wallet setup — do it this way

```
YOU
  -> DEDICATED TRADING WALLET   (fresh; not your main wallet)
  -> OFFICIAL POLYMARKET AUTH   (the SDK signs; the key never leaves your machine)
  -> POLYMARKET
```

1. Create a **new** wallet used only for this bot.
2. Fund it with USDC on Polygon — the minimum you need, not your savings.
3. Connect it to Polymarket and set the required allowances.
4. Export only that wallet's private key into your local `.env` (or the keyring).
5. Never reuse the key anywhere else — not in another bot, another site, another machine.

### Signature type

| Your login | `POLYMARKET_SIGNATURE_TYPE` | Notes |
|---|---|---|
| Direct EOA wallet | `0` | trading straight from the wallet |
| Polymarket email / Magic login | `1` | needs `POLYMARKET_FUNDER_ADDRESS` (the proxy) |
| MetaMask / Rabby / Coinbase Wallet | `2` | needs `POLYMARKET_FUNDER_ADDRESS` (the proxy) |

Get this wrong and you will see "wallet/funder mismatch" or a balance of zero. See
[TROUBLESHOOTING.md](TROUBLESHOOTING.md).

---

## Redaction

`src/monitoring/logging_setup.py` sanitises every record with these patterns before emission:

| Pattern | Example caught |
|---|---|
| 64-hex string | a raw private key |
| 12+ word mnemonic | a seed phrase |
| `<digits>:<token>` | a Telegram bot token |
| `eyJ…` triple-segment | a JWT |
| `Bearer …` / `api_key=…` | an auth header or key pair |

Sensitive **keys** are also dropped from structured payloads: `private_key`, `api_secret`, `api_key`,
`passphrase`, `bot_token`, `seed`, `mnemonic`, `authorization`, `password`, `token`. You can extend
this with `logging.redact_keys` in `configs/config.yaml`.

It also runs a **repository-wide secret scan** in pre-flight and in CI
(`scripts/scan_secrets.py`), covering AWS keys, GitHub tokens, Slack tokens, Google API keys,
OpenAI-style keys, Telegram tokens and bare private keys.

---

## Threat model

### In scope

1. **Credential leakage** via logs, exceptions, the database or a committed file.
2. **Unauthorised order placement** — any path that submits an order without all live gates and the
   risk engine.
3. **Risk-limit bypass** — sizing, exposure, loss or correlation limits being skippable by a crafted
   market or signal.
4. **Duplicate orders** — a retry creating a second fill for one signal.
5. **Silent trading on bad data** — a missing or stale input becoming an imputed value instead of a
   refusal.

### Out of scope

* Compromise of your host machine or your browser wallet.
* Polymarket's own infrastructure, terms or uptime.
* Regulatory compliance in your jurisdiction — that is yours to determine.
* Losses from a strategy that is implemented correctly but is simply unprofitable.

### How the in-scope risks are mitigated

| Risk | Mitigation |
|---|---|
| Credential leakage | redaction filter on every record; `repr=False` dataclasses; repo secret scan in pre-flight and CI; gitignored `.env` |
| Unauthorised orders | triple gate (env flag + phrase + pre-flight) asserted inside `PolymarketExecutor._require_gates`; the paper/shadow venues physically cannot submit |
| Risk-limit bypass | `RiskEngine.evaluate` is the only path to `OrderManager`; sizing is clamped by `max_trade` after Kelly; an incomplete edge **denies** rather than passes |
| Duplicate orders | client order id derived from the signal id; checks against open positions, open orders and recent submissions; financial orders are **never** blind-retried |
| Silent trading on bad data | validation + the explicit `INSUFFICIENT_DATA` branch in the strategy; the model's number is discarded when a required feature is missing |

---

## Operating recommendations

* Run `python -m app.cli check` before every session; it includes the secret scan.
* Keep `RISK_MAX_TRADE` small (default 10 on a 500 bankroll) and set `RISK_MAX_DAILY_LOSS`.
* Leave the circuit breaker enabled; test it with `python -m app.cli stop` and confirm new orders are
  blocked.
* Run shadow, then paper, for long enough to see refusals you believe and no spurious trips.
* Run live only on a machine you control. Never on a shared host.
* Keep the machine patched; the key sits in a local file.

---

## If a credential is exposed

Treat it as compromised the moment it leaves your machine.

1. **Move the funds** out of the dedicated trading wallet to a safe address.
2. **Revoke the APIs**: Polymarket → API keys; your AI provider → rotate the key; Telegram →
   `/revoke` with @BotFather to issue a new token.
3. **Delete the secret** from `.env` (or delete the keyring entry) and re-create the wallet if the
   private key leaked.
4. If a secret reached a Git commit: rewrite history (`git filter-repo`), force-push, and **assume it
   leaked anyway** — rotate first, clean second.
5. Check your other services for reuse of the same key.

---

## Removing credentials from your local setup

```bash
# 1. stop anything running
python -m app.cli stop --reason "credential rotation"

# 2. clear the file (keep the structure, empty the values)
cp .env.example .env

# 3. or remove it entirely
rm .env

# 4. if you used the keyring
python -c "import keyring; keyring.delete_password('football-market-ai-trader','polymarket_private_key')"

# 5. confirm nothing sensitive is left behind, then re-run the checks
python scripts/scan_secrets.py
python -m app.cli config      # every secret should read 'missing'
```

---

## How to stop the bot / disable live trading

**Stop the running bot:** Ctrl-C in the live loop (clean shutdown), or from another terminal:

```bash
python -m app.cli stop --reason "manual stop"
```

The kill switch blocks new orders and asks resting orders to cancel. It does **not** force-close
open positions — closing them is your decision, and the exit rules in
`src/execution/position_manager.py` are documented rather than arbitrary.

**Disable live trading permanently:** set `POLYMARKET_ALLOW_LIVE_TRADING=false` and
`TRADING_MODE=backtest` in `.env`. Live mode then becomes unreachable regardless of CLI flags, and
`preflight()` will report the gate as closed.
