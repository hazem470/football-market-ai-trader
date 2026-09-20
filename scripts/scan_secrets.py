"""Repository secret scanner used by CI and by `cli check`.

Fails (exit 1) if anything credential-shaped is found in tracked files.
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

PATTERNS = {
    "private_key": re.compile(r"\b(0x)?[0-9a-fA-F]{64}\b"),
    "telegram_bot_token": re.compile(r"\b\d{6,12}:[A-Za-z0-9_-]{20,}\b"),
    "aws_access_key": re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    "openai_style_key": re.compile(r"\bsk-[A-Za-z0-9]{20,}\b"),
    "github_token": re.compile(r"\bgh[pousr]_[A-Za-z0-9]{36,}\b"),
    "slack_token": re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b"),
    "google_api_key": re.compile(r"\bAIza[0-9A-Za-z_-]{35}\b"),
}

ALLOWLIST = {".env.example", "configs/config.yaml", "scripts/scan_secrets.py",
             "src/core/preflight.py", "src/monitoring/logging_setup.py"}
SKIP_DIRS = {".git", ".venv", "venv", "__pycache__", ".pytest_cache", "node_modules",
             "logs", "artifacts", "htmlcov", ".mypy_cache", ".ruff_cache"}
SKIP_SUFFIXES = {".png", ".jpg", ".jpeg", ".gif", ".pdf", ".db", ".sqlite", ".pyc",
                 ".ico", ".woff", ".woff2", ".zip", ".csv"}


def tracked_files() -> list[Path]:
    try:
        out = subprocess.run(
            ["git", "ls-files"], capture_output=True, text=True, check=True,
            cwd=str(Path(__file__).resolve().parents[1]),
        ).stdout
        return [Path(line) for line in out.splitlines() if line.strip()]
    except Exception:
        root = Path(__file__).resolve().parents[1]
        return [p.relative_to(root) for p in root.rglob("*") if p.is_file()]


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    findings: list[str] = []
    checked = 0
    for relative in tracked_files():
        path = root / relative
        if any(part in SKIP_DIRS for part in path.parts):
            continue
        if path.suffix.lower() in SKIP_SUFFIXES:
            continue
        if str(relative).replace("\\", "/") in ALLOWLIST:
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        checked += 1
        for name, pattern in PATTERNS.items():
            for match in pattern.finditer(text):
                line_no = text.count("\n", 0, match.start()) + 1
                findings.append(f"{relative}:{line_no} -> {name}")

    if findings:
        print("SECRET SCAN FAILED - possible credentials in the repository:")
        for finding in findings[:50]:
            print(f"  {finding}")
        print(f"\n{len(findings)} finding(s) across {checked} files.")
        print("Remove them, rotate the credential, and rewrite history if it was committed.")
        return 1
    print(f"Secret scan clean ({checked} files checked).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
