## What does this change?

<!-- One or two sentences. -->

## Why?

<!-- The problem it solves. Link the issue. -->

## Safety checklist

- [ ] This change cannot place a live order unless every live gate is satisfied
      (`POLYMARKET_ALLOW_LIVE_TRADING=true` + `--confirm` phrase + passing pre-flight).
- [ ] No credential, key, seed phrase or token was added to a tracked file.
- [ ] New data-source code fails closed (returns INSUFFICIENT_DATA / NO TRADE) rather
      than imputing, guessing or averaging missing inputs.
- [ ] Tests were added or updated for the new behaviour.
- [ ] `pytest -q`, `ruff check .` and `python scripts/scan_secrets.py` all pass locally.

## Risk model impact

<!-- Does this change sizing, limits, correlation grouping or the edge calculation?
     If yes, describe the reasoning and any new configuration. -->
