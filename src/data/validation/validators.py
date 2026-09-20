"""Data validation.

Philosophy: a missing or stale critical field is NEVER filled with a guess or a
league average silently. It becomes a validation issue, and any market that
depends on it returns INSUFFICIENT_DATA -> NO TRADE.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum

from src.data.normalization.canonical import MatchRecord, PlayerRecord
from src.markets.schema import Market, MarketType


class Severity(str, Enum):
    INFO = "INFO"
    WARNING = "WARNING"
    CRITICAL = "CRITICAL"


@dataclass
class Issue:
    field: str
    severity: Severity
    detail: str

    def __str__(self) -> str:
        return f"{self.severity.value}:{self.field}:{self.detail}"


@dataclass
class ValidationReport:
    subject: str
    issues: list[Issue] = field(default_factory=list)
    checked_at: float = field(default_factory=time.time)

    @property
    def critical(self) -> list[Issue]:
        return [i for i in self.issues if i.severity is Severity.CRITICAL]

    @property
    def warnings(self) -> list[Issue]:
        return [i for i in self.issues if i.severity is Severity.WARNING]

    @property
    def ok(self) -> bool:
        return not self.critical

    def add(self, field_name: str, severity: Severity, detail: str) -> None:
        self.issues.append(Issue(field_name, severity, detail))

    def summary(self) -> str:
        if self.ok and not self.warnings:
            return "ok"
        parts = [f"critical={len(self.critical)}", f"warnings={len(self.warnings)}"]
        if self.critical:
            parts.append("; ".join(str(i) for i in self.critical[:4]))
        return " ".join(parts)

    def as_dict(self) -> dict:
        return {
            "subject": self.subject,
            "ok": self.ok,
            "issues": [
                {"field": i.field, "severity": i.severity.value, "detail": i.detail} for i in self.issues
            ],
        }


# --------------------------------------------------------------------- matches
def validate_matches(matches: list[MatchRecord], min_matches: int = 30) -> ValidationReport:
    report = ValidationReport(subject="match_history")
    if len(matches) < min_matches:
        report.add("history", Severity.CRITICAL, f"only {len(matches)} finished matches (< {min_matches})")
        return report

    impossible = 0
    missing_goals = 0
    for match in matches:
        for value in (match.home_goals, match.away_goals):
            if value is None:
                missing_goals += 1
            elif value < 0 or value > 20:
                impossible += 1
        if match.home_team_norm and match.home_team_norm == match.away_team_norm:
            report.add("teams", Severity.WARNING, f"{match.match_key} has identical home/away team")

    if impossible:
        report.add("goals", Severity.CRITICAL, f"{impossible} impossible goal values")
    if missing_goals:
        report.add("goals", Severity.WARNING, f"{missing_goals} missing goal values in finished set")

    coverage = sum(m.field_coverage() for m in matches) / len(matches)
    if coverage < 0.6:
        report.add("coverage", Severity.WARNING, f"field coverage {coverage:.2f} below 0.60")
    return report


# ---------------------------------------------------------------------- market
def validate_market(market: Market, now: float | None = None,
                    max_price_age_seconds: float = 60.0) -> ValidationReport:
    now = time.time() if now is None else now
    report = ValidationReport(subject=f"market:{market.market_id}")

    if not market.yes_token_id:
        report.add("yes_token_id", Severity.CRITICAL, "missing CLOB token id")
    if not market.question:
        report.add("question", Severity.CRITICAL, "empty question")
    if market.market_type is MarketType.UNKNOWN:
        report.add("market_type", Severity.CRITICAL, "market could not be classified")
    elif not market.market_type.is_supported:
        report.add("market_type", Severity.CRITICAL, f"{market.market_type.value} has no data path in v1.0")

    snapshot = market.snapshot
    if snapshot is None:
        report.add("snapshot", Severity.CRITICAL, "no price snapshot")
    else:
        age = now - snapshot.ts
        if age > max_price_age_seconds:
            report.add("price_freshness", Severity.CRITICAL, f"price age {age:.0f}s > {max_price_age_seconds:.0f}s")
        if snapshot.bid is None or snapshot.ask is None:
            report.add("order_book", Severity.WARNING, "missing bid or ask")
        if snapshot.bid is not None and snapshot.ask is not None:
            if not (0 <= snapshot.bid <= snapshot.ask <= 1):
                report.add("order_book", Severity.CRITICAL,
                           f"bid/ask out of range: {snapshot.bid}/{snapshot.ask}")
            elif snapshot.ask - snapshot.bid > 0.2:
                report.add("spread", Severity.WARNING, f"very wide spread {snapshot.ask - snapshot.bid:.3f}")

    if not (market.home_team and market.away_team):
        report.add("participants", Severity.CRITICAL, "could not resolve home/away teams")
    if market.market_type.needs_player and not market.player:
        report.add("player", Severity.CRITICAL, "player prop without player identity")
    if not market.end_time:
        report.add("end_time", Severity.CRITICAL, "no resolution time")
    return report


# ---------------------------------------------------------------------- player
def validate_player(player: PlayerRecord, require_shots: bool = False,
                    require_cards: bool = False) -> ValidationReport:
    report = ValidationReport(subject=f"player:{player.display_name}")
    if player.minutes <= 0:
        report.add("minutes", Severity.CRITICAL, "no minutes recorded")
    if player.availability is None:
        report.add("availability", Severity.CRITICAL, "availability unknown")
    elif player.availability <= 0.05:
        report.add("availability", Severity.CRITICAL, f"unavailable ({player.status})")
    elif player.availability < 0.5:
        report.add("availability", Severity.WARNING, f"doubtful ({player.availability:.2f})")
    if player.starting_probability is None:
        report.add("starting_probability", Severity.WARNING,
                   "no lineup source - starting probability estimated from minutes only")
    if player.xg_per_90 <= 0 and player.goals_per_90 <= 0:
        report.add("attack_rate", Severity.CRITICAL, "no xG or goals history")
    if require_shots and player.shots_per_90 is None:
        report.add("shots_per_90", Severity.CRITICAL,
                   "no per-player shots source configured (FPL does not publish player shots)")
    if require_cards and player.received_cards_per_90 is None:
        report.add("cards_per_90", Severity.CRITICAL, "no per-player card source configured")
    return report


def stale_timestamp(ts: float, max_age_seconds: float, label: str = "data") -> Issue | None:
    age = time.time() - ts
    if age > max_age_seconds:
        return Issue(label, Severity.CRITICAL, f"stale by {age - max_age_seconds:.0f}s")
    return None
