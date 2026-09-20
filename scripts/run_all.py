"""Convenience runner: check -> calibrate -> backtest.

    python scripts/run_all.py

Useful as a first-run smoke test after configuring .env.
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from app.cli.main import main as cli_main  # noqa: E402


def run(argv: list[str]) -> int:
    print(f"\n$ python -m app.cli {' '.join(argv)}")
    return cli_main(argv)


if __name__ == "__main__":
    code = run(["check"])
    if code == 0:
        run(["backtest", "--market-type", "MATCH_RESULT", "--league", "E0"])
        run(["calibrate", "--market-type", "MATCH_RESULT", "--league", "E0"])
        run(["shadow", "--limit", "10"])
    raise SystemExit(code)
