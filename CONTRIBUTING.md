# Contributing

Thanks for helping. This project trades (optionally) real money, so the bar for
safety-related changes is deliberately high.

## Ground rules

1. **Fail closed.** If data is missing, stale or unsupported, the answer is
   `INSUFFICIENT_DATA` / `UNSUPPORTED` / `NO TRADE`. Never impute, average or
   guess a value to keep a market tradeable.
2. **No secrets, ever.** Never commit a key, token or seed phrase. The project
   refuses seed phrases by design and CI runs `scripts/scan_secrets.py`.
3. **Live gating is sacred.** No change may make it possible to place a real
   order without `POLYMARKET_ALLOW_LIVE_TRADING=true`, the `--confirm` phrase and
   a passing pre-flight.
4. **The LLM never decides.** The AI layer extracts structured facts only. It
   must never be able to set `decision`, `side`, `size`, `price` or `order`.
5. **Separate model and trading performance.** They are reported apart for a
   reason: a well-calibrated model can still lose money after costs.

## Development setup

```bash
git clone <YOUR_FORK_URL>
cd football-market-ai-trader
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt -r requirements-dev.txt
cp .env.example .env             # add YOUR OWN credentials (never commit it)
python -m app.cli lint-config
pytest -q
ruff check .
python scripts/scan_secrets.py
```

Network-touching tests are marked and skipped by default:

```bash
pytest -q -m network             # hits live public APIs
```

## Code style

* `ruff check .` must pass; line length 110.
* Type hints on all public functions.
* Dataclasses over loose dicts where a shape is reused.
* New modules go under `src/<area>/` mirroring the architecture in
  `docs/ARCHITECTURE.md`.

## Adding a market family

Adding a family is a four-part change and all four parts are required:

1. `src/markets/schema.py` - add the `MarketType`, and only add it to
   `SUPPORTED_MARKET_TYPES` once steps 2-4 are done.
2. `src/features/requirements.py` - declare the data requirements and features.
3. `src/models/` - implement the model (with an explicit `INSUFFICIENT_DATA`
   path) and register it in `src/models/registry.py`.
4. `tests/` - unit tests for the model plus a test proving the refusal path
   works when the data is absent.

## Adding a data provider

Implement `src/data/providers/base.py::Provider`, register it in
`registry.py`, and add:
* `health_check()` with honest states,
* a documented licence/terms comment,
* a `FakeHttpClient` fixture in tests so CI needs no network.

Scraping a site whose terms forbid it is not acceptable, regardless of technical
feasibility.

## Commits and PRs

* Conventional, meaningful messages: `feat:`, `fix:`, `docs:`, `test:`, `chore:`.
* Keep commits reviewable; avoid one giant mixed change.
* Fill in the PR template, especially the safety checklist.
* Reference the affected market family and mode in the PR description.

## Reporting security issues

Do **not** open a public issue. See `SECURITY.md`.
