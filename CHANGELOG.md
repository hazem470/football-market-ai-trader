# Changelog

All notable changes to this project are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and this project
adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.0.0] - 2026-09-20

First public release. Research/automation software. **Live trading is disabled by default.**

### Added

**Market layer**
- Dynamic Polymarket football market discovery against the live Gamma API. Paging via the keyset
  cursor endpoints; the soccer tag is resolved at runtime from `/tags/slug/soccer`, never hardcoded.
- Market classification from Polymarket's own `sportsMarketType` metadata, with an ordered,
  discounted text fallback. Genuine metadata/text contradictions resolve to `UNKNOWN` and are refused.
- Canonical `Market` / `MarketSnapshot` model with normalised team and player names (diacritic-folding
  so "Mbappé" and "Mbappe" join), stable `match_key` values and freshness tracking.
- Hard tradeability filters: token presence, supported family, classification confidence, price
  freshness, liquidity, volume, spread, time-to-resolution, time-to-start, player identity, teams.
- Order-book analysis and book-walking fill simulation (`analyze_book`, `simulate_fill`).
- 27 recognised football market families; 9 supported with real models.

**Data layer**
- Provider abstraction (`Provider`, `ProviderRegistry`) with honest health states.
- `football_data_uk` provider: results, goals, shots, shots on target, corners, cards, closing odds
  (Pinnacle/Bet365/Avg), multiple leagues and seasons.
- `fpl` provider: official Fantasy Premier League API — xG/90, xA/90, minutes, availability, team
  strength.
- `openfootball` provider: fixture/result JSON fallback.
- `polymarket_gamma` and `polymarket_clob` providers (public, unauthenticated).
- Availability and lineup adapters, with an explicit `UNKNOWN` return instead of a guess.
- Canonical normalisation (`MatchRecord`, `PlayerRecord`, `TeamHistory` with rolling form and H2H).
- Validation with `INFO` / `WARNING` / `CRITICAL` severities; critical issues force `NO TRADE`.

**Modelling**
- Dynamic data-requirements engine: `MARKET TYPE -> REQUIRED DATA -> FEATURES -> MODEL`, extensible
  via `configs/requirements.json`.
- Feature engine producing only the required features, with provenance, missing-feature tracking and
  content-addressed, reproducible feature snapshots.
- Dixon-Coles style bivariate Poisson (weighted IPF with a recency half-life, low-score `rho`
  correction, renormalised score matrix) for match result, total goals, team totals and BTTS.
- Poisson / negative-binomial count models for corners and player shots.
- Player goal, assists, goals+assists and shots models with explicit `INSUFFICIENT_DATA` paths.
- A `CardsModel` that deliberately refuses (team-level data cannot become a player probability).
- Platt scaling and isotonic calibration fitted on out-of-sample walk-forward predictions, with
  Brier score, log loss, Brier skill score, reliability tables, ECE and MCE.
- Model registry caching per `(market_type, league)` and recording fit failures as refusals.

**Strategy, risk, execution**
- Edge engine converting raw edge into realistic edge after spread share, slippage, fees and
  model/data/execution uncertainty, with a printable decomposition.
- Auditable `Signal` objects with `BUY` / `SELL` / `NO_TRADE` / `INSUFFICIENT_DATA` / `UNSUPPORTED`
  states and a machine-readable trace on every decision.
- Risk engine as the single gate: circuit breaker, freshness, edge/confidence, liquidity/spread,
  fractional-Kelly sizing, position count, daily loss, daily exposure, correlation and balance.
- Correlation-bucketed exposure with an explicit overlap table and match-level grouping.
- Circuit breaker / kill switch with triggers, half-open recovery and event logging.
- Order manager with pre-submission price recheck, duplicate-order protection, idempotent client
  order ids, book-depth and slippage previews. Financial orders are never blind-retried.
- Position manager with seven documented exit rules.
- Paper trading venue: real order book, virtual capital, book-walking fills, partial fills, simulated
  rejections, seeded reproducibility, full portfolio accounting.
- Shadow mode: real data and real decisions with a venue that cannot fill anything.
- Live execution via the official Python CLOB client, triple-gated and refusing to initialise without
  every gate.

**Operations**
- SQLite storage (19 tables) with typed accessors; PostgreSQL-ready by design.
- Structured logging with mandatory credential redaction, plus a repository secret scanner.
- Telegram notifications with per-event thresholds (your own bot; no personal account access).
- 15-tab read-only Streamlit dashboard.
- 15-check pre-flight that must pass before any mode runs.
- Full CLI: `init`, `check`, `config`, `markets`, `signals`, `backtest`, `calibrate`, `shadow`,
  `paper`, `live`, `status`, `stop`, `test`, `lint-config`, `version`.
- Walk-forward backtester reporting **MODEL PERFORMANCE** and **TRADING PERFORMANCE** separately,
  with an explicit data-limitations block.
- Dockerfile and docker-compose (no secrets baked in, unprivileged user).
- GitHub Actions CI: ruff, configuration validation, the full test suite and the secret scan on
  Python 3.10/3.11/3.12, plus bandit and pip-audit.
- 438 tests: unit, integration, failure-mode and security suites.
- Documentation: README, user guide, configuration reference, architecture, security, troubleshooting,
  development status, contributing, security policy, PR/issue templates.

### Licensing

- `LICENSE` carries the verbatim MIT text so tooling detects it correctly; the
  research / no-profit-guarantee statement lives in `NOTICE.md` (an additional
  statement of intent, not a modification of the MIT terms). Keeping them apart
  matters: appending prose to `LICENSE` makes GitHub report `NOASSERTION`.

### Security

- Seed phrases are refused everywhere, loudly (`SeedPhraseRejected`).
- Wallet secrets are only read from `.env` or the OS keyring; reading from inside the repo is refused.
- `Secrets` and `WalletConfig` use `repr=False` so a key cannot appear in a traceback or log line.
- Redaction filter applied to every log record (64-hex, mnemonics, Telegram tokens, JWTs, bearer
  headers, `api_key=` pairs) with recursive key dropping in structured payloads.
- Live mode requires the env flag, the exact confirmation phrase and a passing pre-flight.
- `.gitignore` excludes `.env`, databases, logs, `secrets/`, `credentials/` and `private_keys/`.

### Known limitations

See [docs/DEVELOPMENT_STATUS.md](docs/DEVELOPMENT_STATUS.md). The significant ones: Polymarket
historical football order books are unavailable for free (backtest prices are bookmaker proxies);
live execution is untested against real funds; `PLAYER_SHOTS` needs a licensed feed; no free
player-level card data; no confirmed lineups ahead of kick-off.

### NOT included

- No API keys, private keys, seed phrases or credentials of any kind.
- No profit guarantee, no performance claim, no financial advice.
- No developer access to any user's funds, accounts or keys.
