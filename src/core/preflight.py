"""Pre-flight checks. Nothing may trade until every critical check passes.

Mirrors the 15 checks required before LIVE mode, and the lighter set used by
`python -m app.cli check`.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

from src.config.settings import Settings


class CheckStatus(str, Enum):
    PASS = "PASS"
    WARN = "WARN"
    FAIL = "FAIL"
    SKIP = "SKIP"


@dataclass
class Check:
    name: str
    status: CheckStatus = CheckStatus.PASS
    detail: str = ""
    critical: bool = True


@dataclass
class PreflightReport:
    mode: str = "backtest"
    checks: list[Check] = field(default_factory=list)
    started_at: float = field(default_factory=time.time)

    def add(self, name: str, status: CheckStatus, detail: str = "", critical: bool = True) -> Check:
        check = Check(name=name, status=status, detail=detail, critical=critical)
        self.checks.append(check)
        return check

    @property
    def failures(self) -> list[Check]:
        return [c for c in self.checks if c.status is CheckStatus.FAIL and c.critical]

    @property
    def warnings(self) -> list[Check]:
        return [c for c in self.checks if c.status is CheckStatus.WARN]

    @property
    def ok(self) -> bool:
        return not self.failures

    def as_dict(self) -> dict:
        return {
            "mode": self.mode,
            "ok": self.ok,
            "checks": [
                {"name": c.name, "status": c.status.value, "detail": c.detail, "critical": c.critical}
                for c in self.checks
            ],
            "duration_seconds": round(time.time() - self.started_at, 2),
        }

    def render(self) -> str:
        icon = {CheckStatus.PASS: "PASS", CheckStatus.WARN: "WARN",
                CheckStatus.FAIL: "FAIL", CheckStatus.SKIP: "SKIP"}
        width = max((len(c.name) for c in self.checks), default=10)
        lines = [f"PRE-FLIGHT ({self.mode})", "-" * (width + 40)]
        for check in self.checks:
            lines.append(f"{icon[check.status]:<5} {check.name:<{width}} {check.detail}")
        lines.append("-" * (width + 40))
        lines.append(
            f"{'READY' if self.ok else 'NOT READY'} - "
            f"{len(self.failures)} critical failure(s), {len(self.warnings)} warning(s)"
        )
        return "\n".join(lines)


def _secret_leak_scan(repo_root: Path) -> tuple[CheckStatus, str]:
    """Scan tracked files for anything that looks like a live credential."""
    import re

    patterns = {
        "private_key": re.compile(r"\b(0x)?[0-9a-fA-F]{64}\b"),
        "telegram_token": re.compile(r"\b\d{6,12}:[A-Za-z0-9_-]{20,}\b"),
        "aws_key": re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
        "openai_key": re.compile(r"\bsk-[A-Za-z0-9]{20,}\b"),
    }
    allowlist_files = {".env.example", "configs/config.yaml"}
    hits: list[str] = []
    for path in repo_root.rglob("*"):
        if not path.is_file():
            continue
        if any(part in {".git", ".venv", "venv", "__pycache__", ".pytest_cache", "node_modules", "logs",
                        "data", "artifacts"} for part in path.parts):
            continue
        if path.suffix.lower() in {".png", ".jpg", ".jpeg", ".gif", ".pdf", ".db", ".sqlite", ".pyc", ".ico"}:
            continue
        if path.name in allowlist_files:
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        for name, pattern in patterns.items():
            if pattern.search(text):
                hits.append(f"{path.relative_to(repo_root)} ({name})")
    if hits:
        return CheckStatus.FAIL, "possible secrets in repository files: " + ", ".join(hits[:6])
    return CheckStatus.PASS, "no credential-shaped strings found in repository files"


def run_preflight(
    settings: Settings,
    providers=None,
    database=None,
    venue=None,
    require_live: bool | None = None,
    check_secrets: bool = True,
) -> PreflightReport:
    """Run every check and return a report. Never raises."""
    report = PreflightReport(mode=settings.mode)
    require_live = settings.mode == "live" if require_live is None else require_live

    # 1 - configuration
    problems = settings.validate()
    if problems:
        report.add("configuration", CheckStatus.FAIL, "; ".join(problems))
    else:
        report.add("configuration", CheckStatus.PASS, f"mode={settings.mode}")

    # 2 - live gate
    if settings.mode == "live":
        if settings.live_trading:
            report.add("live_trading_gate", CheckStatus.PASS,
                       "POLYMARKET_ALLOW_LIVE_TRADING=true (explicit confirmation still required)")
        else:
            report.add("live_trading_gate", CheckStatus.FAIL,
                       "live mode requested but POLYMARKET_ALLOW_LIVE_TRADING is not true")
    else:
        report.add("live_trading_gate", CheckStatus.PASS,
                   f"live trading not requested (mode={settings.mode})", critical=False)

    # 3 - .env present?
    if settings.env_file:
        report.add(".env", CheckStatus.PASS, f"loaded from {settings.env_file}", critical=False)
    else:
        report.add(".env", CheckStatus.WARN,
                   "no .env found; using configs/config.yaml + process environment", critical=False)

    # 4 - directories writable
    try:
        settings.ensure_dirs()
        report.add("storage_paths", CheckStatus.PASS,
                   f"data={settings.data_dir} logs={settings.log_dir}")
    except OSError as exc:
        report.add("storage_paths", CheckStatus.FAIL, f"cannot create directories: {exc}")

    # 5 - database
    if database is not None:
        try:
            tables = database.table_names()
            report.add("database", CheckStatus.PASS, f"{len(tables)} tables at {database.path}")
        except Exception as exc:
            report.add("database", CheckStatus.FAIL, f"database unusable: {exc}")
    else:
        report.add("database", CheckStatus.SKIP, "not initialised in this run", critical=False)

    # 6 - providers
    if providers is not None:
        results = providers.check_all()
        critical_down = [name for name, state in results.items() if state == "CRITICAL"]
        market_feeds = [n for n in results if n.startswith("polymarket_")]
        market_down = [n for n in market_feeds if results[n] == "CRITICAL"]
        if market_down:
            report.add("polymarket_api", CheckStatus.FAIL,
                       f"unreachable: {', '.join(market_down)}")
        elif market_feeds:
            report.add("polymarket_api", CheckStatus.PASS,
                       f"{len(market_feeds)} endpoints responding")
        else:
            report.add("polymarket_api", CheckStatus.FAIL, "no Polymarket provider configured")

        data_feeds = [n for n in results if not n.startswith("polymarket_")]
        data_down = [n for n in data_feeds if results[n] == "CRITICAL"]
        if data_down and len(data_down) == len(data_feeds):
            report.add("football_data", CheckStatus.FAIL,
                       f"all football providers unreachable: {', '.join(data_down)}")
        elif data_down:
            report.add("football_data", CheckStatus.WARN,
                       f"degraded, unavailable: {', '.join(data_down)}")
        else:
            report.add("football_data", CheckStatus.PASS, ", ".join(f"{n}={results[n]}" for n in data_feeds))
        if critical_down:
            report.add("provider_summary", CheckStatus.WARN, f"critical: {', '.join(critical_down)}",
                       critical=False)
    else:
        report.add("providers", CheckStatus.SKIP, "not initialised in this run", critical=False)

    # 7 - credential presence for live
    if require_live:
        if settings.secrets.has_polymarket_credentials():
            report.add("wallet_signer", CheckStatus.PASS, "dedicated trading key present")
        else:
            report.add("wallet_signer", CheckStatus.FAIL,
                       "POLYMARKET_PRIVATE_KEY missing (live needs a dedicated trading wallet)")
        if settings.secrets.polymarket_funder_address or settings.signature_type == 0:
            report.add("funder", CheckStatus.PASS,
                       f"signature_type={settings.signature_type}", critical=False)
        else:
            report.add("funder", CheckStatus.WARN,
                       "signature_type != 0 but no POLYMARKET_FUNDER_ADDRESS set")
    else:
        report.add("wallet_signer", CheckStatus.PASS, "not required in simulation modes", critical=False)

    # 8 - venue / execution readiness
    if venue is not None:
        try:
            ok, issues = venue.preflight()
            report.add("execution_venue", CheckStatus.PASS if ok else CheckStatus.FAIL,
                       "; ".join(issues) if issues else f"{venue.name} ready")
        except Exception as exc:
            report.add("execution_venue", CheckStatus.FAIL, f"{venue.name} preflight raised: {exc}")
    else:
        report.add("execution_venue", CheckStatus.SKIP, "not constructed in this run", critical=False)

    # 9 - risk configuration sanity
    risk = settings.section("risk")
    limits_problems: list[str] = []
    if float(risk.get("max_trade", 0) or 0) > float(risk.get("capital", 0) or 0):
        limits_problems.append("max_trade > capital")
    if float(risk.get("max_daily_loss", 0) or 0) <= 0:
        limits_problems.append("max_daily_loss missing")
    report.add("risk_limits",
               CheckStatus.FAIL if limits_problems else CheckStatus.PASS,
               "; ".join(limits_problems) if limits_problems else json.dumps(risk)[:160])

    # 10 - notifications (optional)
    notifications = settings.section("notifications").get("telegram", {})
    if notifications.get("enabled"):
        if settings.secrets.has_telegram():
            report.add("telegram", CheckStatus.PASS, "bot token and chat id configured", critical=False)
        else:
            report.add("telegram", CheckStatus.WARN,
                       "TELEGRAM_ENABLED=true but token/chat id missing", critical=False)
    else:
        report.add("telegram", CheckStatus.PASS, "disabled", critical=False)

    # 11 - optional AI layer
    ai_cfg = settings.section("ai")
    if ai_cfg.get("enabled"):
        report.add("ai_layer",
                   CheckStatus.PASS if settings.secrets.has_ai() else CheckStatus.WARN,
                   f"provider={ai_cfg.get('provider')} key="
                   f"{'set' if settings.secrets.has_ai() else 'missing'}",
                   critical=False)
    else:
        report.add("ai_layer", CheckStatus.PASS, "disabled (it is optional)", critical=False)

    # 12 - secret leak scan
    if check_secrets:
        status, detail = _secret_leak_scan(settings.repo_root)
        report.add("secret_scan", status, detail)

    # 13 - .gitignore coverage
    gitignore = settings.repo_root / ".gitignore"
    if gitignore.exists():
        text = gitignore.read_text(encoding="utf-8", errors="ignore")
        missing = [pattern for pattern in (".env", "*.db", "logs/") if pattern not in text]
        report.add(".gitignore",
                   CheckStatus.WARN if missing else CheckStatus.PASS,
                   f"missing entries: {missing}" if missing else "covers .env, db files and logs",
                   critical=False)
    else:
        report.add(".gitignore", CheckStatus.WARN, "no .gitignore in the repository", critical=False)

    # 14 - optional live dependency (only relevant for live)
    if require_live:
        try:
            import py_clob_client  # noqa: F401
            report.add("clob_client", CheckStatus.PASS, "py-clob-client importable")
        except ImportError:
            report.add("clob_client", CheckStatus.FAIL,
                       "py-clob-client missing: pip install -r requirements-live.txt")
    else:
        report.add("clob_client", CheckStatus.PASS, "not required in simulation modes", critical=False)

    # 15 - explicit live enablement echoed
    if require_live:
        report.add("explicit_live_confirmation", CheckStatus.WARN,
                   "CLI --confirm phrase is still required at launch", critical=False)

    return report
