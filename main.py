#!/usr/bin/env python
"""Entry point.

    python main.py                 # runs `check` (safe by default)
    python main.py --mode paper    # run a mode
    python -m app.cli <command>    # full CLI

The default action is deliberately harmless: a system check. Nothing trades
unless you ask for a mode explicitly.
"""
from __future__ import annotations

import sys

from app.cli.main import main as cli_main


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv:
        argv = ["check"]
    elif argv[0].startswith("-"):
        # `python main.py --mode paper` -> `python -m app.cli --mode paper paper`
        argv = [*argv, "check"]
    return cli_main(argv)


if __name__ == "__main__":
    raise SystemExit(main())
