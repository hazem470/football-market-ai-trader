# Security Policy

## Reporting a vulnerability

Do not open a public issue for anything security-related.

Email the maintainers listed in the repository profile, or use GitHub's private
"Report a vulnerability" advisory flow. Include:

* what the issue is,
* how to reproduce it,
* the impact (can it leak a credential, place an unintended order, or bypass a risk limit?),
* any suggested fix.

We aim to acknowledge within 72 hours.

## Supported versions

Only the latest release on the default branch is supported.

## What this project will never do

* Ask for, store, log, transmit or accept a **seed phrase / mnemonic**.
* Store a private key anywhere inside the repository.
* Send credentials to Telegram, a dashboard or a third-party service.
* Enable live trading by default, or by a single flag alone.

## Handling of credentials

| Credential | Where it lives | How it is protected |
|---|---|---|
| Dedicated trading wallet private key | `.env` or the OS keyring | gitignored, redacted in logs, never echoed by the CLI |
| Polymarket API key / secret / passphrase | `.env` | derived at runtime if absent; gitignored |
| AI provider key | `.env` | optional; never required |
| Telegram bot token | `.env` | optional; your own bot |

Every log record passes through `src/monitoring/logging_setup.py`, which redacts
64-hex strings, mnemonics, bot tokens, JWTs, bearer headers and
`api_key=`-style pairs before anything reaches a sink.

## Threat model (in scope)

1. **Credential leakage** through logs, exceptions, the database or a committed file.
2. **Unauthorised order placement** - any path that submits an order without passing
   all live gates and the risk engine.
3. **Risk-limit bypass** - sizing, exposure, daily loss or correlation limits being
   skippable by a crafted market or signal.
4. **Duplicate orders** - a retry creating a second fill for one signal.
5. **Data-poisoning that silently trades** - a missing or stale input that becomes
   an imputed value rather than a refusal.

## Threat model (out of scope)

* Compromise of the host machine or the user's browser wallet.
* Polymarket's own infrastructure, API terms or uptime.
* Regulatory compliance in your jurisdiction - that is yours to determine.
* Losses from a strategy that is correct but unprofitable.

## Operational recommendations

* Use a **dedicated trading wallet** funded with only what you can afford to lose.
* Run paper/shadow mode for a meaningful period before going live.
* Keep `RISK_MAX_TRADE` small. The default is 10 on a 500 bankroll.
* Set a hard daily loss limit and leave the circuit breaker enabled.
* Rotate the trading wallet key if it is ever exposed, and move funds first.
* Never run live mode on a machine you do not control.
