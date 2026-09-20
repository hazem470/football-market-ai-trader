"""Shared Poisson / bivariate-Poisson machinery.

Implements a Dixon-Coles style team-strength model:

  lambda_home = attack_home * defence_away * home_advantage * league_mean
  lambda_away = attack_away * defence_home * league_mean

fitted by weighted iterative proportional fitting on finished matches, with an
optional low-score correction (rho) for the classic 0-0 / 1-0 / 0-1 / 1-1
dependency that a plain independent-Poisson model gets wrong.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

from src.data.normalization.canonical import MatchRecord
from src.models.base import InsufficientModelData

MAX_GOALS_DEFAULT = 10


def poisson_pmf(k: int, lam: float) -> float:
    if lam <= 0:
        return 1.0 if k == 0 else 0.0
    return math.exp(-lam) * (lam ** k) / math.factorial(k)


def poisson_cdf(k: int, lam: float) -> float:
    return sum(poisson_pmf(i, lam) for i in range(0, max(0, k) + 1))


@dataclass
class ScoreMatrix:
    """Joint distribution over scorelines."""

    home_lambda: float
    away_lambda: float
    max_goals: int = MAX_GOALS_DEFAULT
    matrix: list[list[float]] = field(default_factory=list)

    def __post_init__(self) -> None:
        if not self.matrix:
            self.matrix = [
                [poisson_pmf(h, self.home_lambda) * poisson_pmf(a, self.away_lambda)
                 for a in range(self.max_goals + 1)]
                for h in range(self.max_goals + 1)
            ]
            # Renormalise: the truncated grid is not exactly a probability mass.
            self.normalize()

    def normalize(self) -> ScoreMatrix:
        total = sum(sum(row) for row in self.matrix)
        if total > 0:
            self.matrix = [[cell / total for cell in row] for row in self.matrix]
        return self

    def apply_dixon_coles(self, rho: float) -> ScoreMatrix:
        """Low-score dependency correction (Dixon & Coles, 1997)."""
        if rho == 0:
            return self
        adjustments = {(0, 0): 1 - self.home_lambda * self.away_lambda * rho,
                       (0, 1): 1 + self.home_lambda * rho,
                       (1, 0): 1 + self.away_lambda * rho,
                       (1, 1): 1 - rho}
        for (h, a), factor in adjustments.items():
            if h <= self.max_goals and a <= self.max_goals:
                self.matrix[h][a] = max(0.0, self.matrix[h][a] * factor)
        return self.normalize()

    # -------------------------------------------------------------- markets
    def probability_home_win(self) -> float:
        return sum(self.matrix[h][a] for h in range(self.max_goals + 1)
                   for a in range(self.max_goals + 1) if h > a)

    def probability_draw(self) -> float:
        return sum(self.matrix[h][a] for h in range(self.max_goals + 1)
                   for a in range(self.max_goals + 1) if h == a)

    def probability_away_win(self) -> float:
        return sum(self.matrix[h][a] for h in range(self.max_goals + 1)
                   for a in range(self.max_goals + 1) if h < a)

    def probability_total_over(self, line: float) -> float:
        threshold = math.floor(line)  # over 2.5 means >= 3 goals
        return sum(self.matrix[h][a] for h in range(self.max_goals + 1)
                   for a in range(self.max_goals + 1) if (h + a) > threshold)

    def probability_total_under(self, line: float) -> float:
        return 1.0 - self.probability_total_over(line)

    def probability_btts(self) -> float:
        return sum(self.matrix[h][a] for h in range(1, self.max_goals + 1)
                   for a in range(1, self.max_goals + 1))

    def probability_team_over(self, side: str, line: float) -> float:
        threshold = math.floor(line)
        if side.lower().startswith("h"):
            return sum(self.matrix[h][a] for h in range(self.max_goals + 1)
                       for a in range(self.max_goals + 1) if h > threshold)
        return sum(self.matrix[h][a] for h in range(self.max_goals + 1)
                   for a in range(self.max_goals + 1) if a > threshold)

    def expected_total_goals(self) -> float:
        return sum((h + a) * self.matrix[h][a] for h in range(self.max_goals + 1)
                   for a in range(self.max_goals + 1))

    def most_likely_score(self) -> tuple[int, int]:
        best = (0, 0)
        best_p = -1.0
        for h in range(self.max_goals + 1):
            for a in range(self.max_goals + 1):
                if self.matrix[h][a] > best_p:
                    best_p = self.matrix[h][a]
                    best = (h, a)
        return best


@dataclass
class TeamStrengthModel:
    """Weighted Dixon-Coles fit over one league's finished matches."""

    max_goals: int = MAX_GOALS_DEFAULT
    rho: float = -0.05
    half_life_days: float = 180.0
    iterations: int = 25
    min_matches: int = 60

    attack: dict[str, float] = field(default_factory=dict)
    defence: dict[str, float] = field(default_factory=dict)
    home_advantage: float = 1.15
    league_mean: float = 1.35
    fitted_matches: int = 0
    fitted_teams: int = 0
    history_span_days: float = 0.0
    _reference_team: str = ""

    # -------------------------------------------------------------------- fit
    def fit(self, matches: list[MatchRecord]) -> TeamStrengthModel:
        usable = [
            m for m in matches
            if m.finished and m.home_goals is not None and m.away_goals is not None
            and m.home_team_norm and m.away_team_norm and m.home_team_norm != m.away_team_norm
        ]
        if len(usable) < self.min_matches:
            raise InsufficientModelData(
                f"need >= {self.min_matches} finished matches, have {len(usable)}"
            )
        usable.sort(key=lambda m: m.match_date or "")
        self.fitted_matches = len(usable)

        weights = [self._weight(m, usable[-1]) for m in usable]
        total_goals = sum(int(m.home_goals or 0) + int(m.away_goals or 0) for m in usable)
        self.league_mean = max(0.2, total_goals / (2 * len(usable)))

        teams = sorted({m.home_team_norm for m in usable} | {m.away_team_norm for m in usable})
        self.fitted_teams = len(teams)
        attack = {team: 1.0 for team in teams}
        defence = {team: 1.0 for team in teams}

        home_goals_total = sum(int(m.home_goals or 0) * w for m, w in zip(usable, weights, strict=False))
        away_goals_total = sum(int(m.away_goals or 0) * w for m, w in zip(usable, weights, strict=False))
        if away_goals_total > 0:
            self.home_advantage = max(1.0, min(1.6, home_goals_total / away_goals_total))
        else:
            self.home_advantage = 1.15

        for _ in range(self.iterations):
            # attack update
            for team in teams:
                scored = sum(
                    int(m.home_goals if m.home_team_norm == team else m.away_goals or 0) * w
                    for m, w in zip(usable, weights, strict=False)
                    if team in (m.home_team_norm, m.away_team_norm)
                )
                denom = 0.0
                for m, w in zip(usable, weights, strict=False):
                    if m.home_team_norm == team:
                        denom += w * defence[m.away_team_norm] * self.home_advantage * self.league_mean
                    elif m.away_team_norm == team:
                        denom += w * defence[m.home_team_norm] * self.league_mean
                if denom > 0:
                    attack[team] = max(0.2, min(3.0, scored / denom))
            # defence update
            for team in teams:
                conceded = sum(
                    int(m.away_goals if m.home_team_norm == team else m.home_goals or 0) * w
                    for m, w in zip(usable, weights, strict=False)
                    if team in (m.home_team_norm, m.away_team_norm)
                )
                denom = 0.0
                for m, w in zip(usable, weights, strict=False):
                    if m.home_team_norm == team:
                        denom += w * attack[m.away_team_norm] * self.home_advantage * self.league_mean
                    elif m.away_team_norm == team:
                        denom += w * attack[m.home_team_norm] * self.league_mean
                if denom > 0:
                    defence[team] = max(0.2, min(3.0, conceded / denom))
            # identifiability: keep the geometric mean of attack at 1
            self._renormalise(attack, defence)

        self.attack = attack
        self.defence = defence
        self.history_span_days = self._span_days(usable)
        self._reference_team = teams[0]
        return self

    # ---------------------------------------------------------------- predict
    def lambdas(self, home_team_norm: str, away_team_norm: str) -> tuple[float, float] | None:
        home_attack = self.attack.get(home_team_norm)
        away_attack = self.attack.get(away_team_norm)
        home_defence = self.defence.get(home_team_norm)
        away_defence = self.defence.get(away_team_norm)
        if None in (home_attack, away_attack, home_defence, away_defence):
            return None
        lam_home = home_attack * away_defence * self.home_advantage * self.league_mean
        lam_away = away_attack * home_defence * self.league_mean
        return max(0.05, min(6.0, lam_home)), max(0.05, min(6.0, lam_away))

    def score_matrix(self, home_team_norm: str, away_team_norm: str,
                     apply_dixon_coles: bool = True) -> ScoreMatrix | None:
        lambdas = self.lambdas(home_team_norm, away_team_norm)
        if lambdas is None:
            return None
        matrix = ScoreMatrix(home_lambda=lambdas[0], away_lambda=lambdas[1], max_goals=self.max_goals)
        # Truncating the Poisson at `max_goals` drops a small amount of tail mass,
        # so every probability read off the matrix is renormalised to sum to 1.
        matrix.normalize()
        if apply_dixon_coles and self.rho:
            matrix.apply_dixon_coles(self.rho)
        return matrix.normalize()

    def knows(self, team_norm: str) -> bool:
        return team_norm in self.attack

    # --------------------------------------------------------------- internal
    @staticmethod
    def _renormalise(attack: dict[str, float], defence: dict[str, float]) -> None:
        att_mean = math.exp(sum(math.log(max(1e-6, v)) for v in attack.values()) / max(1, len(attack)))
        def_mean = math.exp(sum(math.log(max(1e-6, v)) for v in defence.values()) / max(1, len(defence)))
        if att_mean > 0:
            for team in attack:
                attack[team] /= att_mean
        if def_mean > 0:
            for team in defence:
                defence[team] /= def_mean

    def _weight(self, match: MatchRecord, latest: MatchRecord) -> float:
        if not self.half_life_days or not match.match_date or not latest.match_date:
            return 1.0
        days = _days_between(match.match_date, latest.match_date)
        if days is None:
            return 1.0
        return 0.5 ** (days / self.half_life_days)

    @staticmethod
    def _span_days(matches: list[MatchRecord]) -> float:
        if len(matches) < 2:
            return 0.0
        span = _days_between(matches[0].match_date, matches[-1].match_date)
        return float(span or 0)


def _days_between(start: str, end: str) -> float | None:
    from datetime import date

    try:
        a = date.fromisoformat(start[:10])
        b = date.fromisoformat(end[:10])
    except (ValueError, TypeError):
        return None
    return abs((b - a).days)


@dataclass
class RateDistribution:
    """Poisson or negative-binomial count model for a per-90 rate."""

    mean: float
    dispersion: float = 0.0        # 0 -> Poisson; >0 -> negative binomial

    def pmf(self, k: int) -> float:
        if self.dispersion <= 0:
            return poisson_pmf(k, self.mean)
        # Negative binomial with mean m and variance m + m^2/r
        r = max(1e-3, self.mean / self.dispersion)
        p = r / (r + self.mean)
        from math import comb, log

        try:
            return math.exp(
                log(comb(k + int(r) - 1, k)) + int(r) * log(p) + k * log(1 - p)
            ) if r >= 1 else poisson_pmf(k, self.mean)
        except (ValueError, OverflowError):
            return poisson_pmf(k, self.mean)

    def probability_at_least(self, threshold: int, max_k: int = 25) -> float:
        if threshold <= 0:
            return 1.0
        return sum(self.pmf(k) for k in range(threshold, max_k + 1))

    def probability_equals(self, k: int) -> float:
        return self.pmf(k)

    def probability_over(self, line: float) -> float:
        return self.probability_at_least(math.floor(line) + 1)

    def probability_under(self, line: float) -> float:
        return max(0.0, 1.0 - self.probability_over(line))
