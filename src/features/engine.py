"""Feature engine.

Generates ONLY the features the discovered market requires (see
`requirements.py`). Every feature bundle is snapshotted so a prediction can be
reproduced exactly later.

Missing data produces ``None`` - never an imputed value that the model would
then treat as real.
"""
from __future__ import annotations

import hashlib
import json
import time
from dataclasses import asdict, dataclass, field
from typing import Any

from src.data.normalization.canonical import PlayerRecord, TeamHistory
from src.features.requirements import DataPlan, build_plan
from src.markets.schema import Market, MarketType, normalize_player_name, normalize_team_name

FEATURE_VERSION = "1.0.0"

#: Reference goals-per-team-per-match for a typical top-division league. Used to
#: scale expected goals around the observed league environment; it is a modelling
#: constant, not a claim about any specific competition.
_LEAGUE_GOALS_REFERENCE = 1.4


@dataclass
class FeatureSet:
    """A reproducible feature bundle for one (market, fixture) pair."""

    market_id: str = ""
    market_type: str = ""
    match_key: str = ""
    created_at: float = field(default_factory=time.time)
    data_timestamp: float = 0.0
    feature_version: str = FEATURE_VERSION
    values: dict[str, Any] = field(default_factory=dict)
    context: dict[str, Any] = field(default_factory=dict)
    provenance: dict[str, str] = field(default_factory=dict)
    missing: list[str] = field(default_factory=list)
    plan: dict[str, Any] = field(default_factory=dict)
    validation: dict[str, Any] = field(default_factory=dict)

    @property
    def complete(self) -> bool:
        return not self.missing

    @property
    def feature_key(self) -> str:
        payload = json.dumps(
            {
                "market_id": self.market_id,
                "type": self.market_type,
                "match_key": self.match_key,
                "v": self.feature_version,
                "data_ts": round(self.data_timestamp, 0),
            },
            sort_keys=True,
        )
        # Content address for a feature snapshot: an identity, not a security
        # primitive. SHA-256 is used regardless so the construct is unambiguous
        # to both readers and static analysers.
        digest = hashlib.sha256(payload.encode()).hexdigest()
        return f"FEAT-{digest[:16]}"

    def get(self, name: str, default: Any = None) -> Any:
        value = self.values.get(name, default)
        return default if value is None else value

    def as_dict(self) -> dict:
        return asdict(self)

    def to_db_record(self, feature_version: str | None = None) -> dict:
        return {
            "feature_key": self.feature_key,
            "match_key": self.match_key,
            "market_id": self.market_id,
            "feature_version": feature_version or self.feature_version,
            "data_timestamp": self.data_timestamp,
            "payload": {
                "values": self.values,
                "context": self.context,
                "provenance": self.provenance,
                "missing": self.missing,
                "plan": self.plan,
                "validation": self.validation,
            },
        }


class FeatureEngine:
    """Builds feature bundles for discovered markets from canonical data."""

    def __init__(
        self,
        history: TeamHistory | None = None,
        players: list[PlayerRecord] | None = None,
        form_window: int = 10,
        requirements_config: dict | None = None,
    ) -> None:
        self.history = history or TeamHistory([])
        self.players = players or []
        self.form_window = form_window
        self.requirements_config = requirements_config or {}
        self._player_index: dict[str, PlayerRecord] = {}
        for player in self.players:
            name = player.norm_name or normalize_player_name(player.display_name)
            if not name:
                continue
            # Keyed by "team|player". `player_key` already carries a team prefix,
            # so it must NOT be used here - doing so produced keys like
            # "arsenal|arsenal|saka" and every lookup silently missed.
            self._player_index[f"{player.team_norm}|{name}"] = player
            self._player_index.setdefault(name, player)

    # ------------------------------------------------------------------ public
    def build(self, market: Market) -> FeatureSet:
        plan = build_plan(market, self.requirements_config)
        bundle = FeatureSet(
            market_id=market.market_id,
            market_type=market.market_type.value,
            match_key=market.match_key,
            data_timestamp=time.time(),
            plan=plan.as_dict(),
        )

        if not plan.requirements:
            bundle.missing = ["UNSUPPORTED_MARKET"]
            bundle.context = {"reason": "no data plan for this market family"}
            return bundle

        home_norm = normalize_team_name(market.home_team)
        away_norm = normalize_team_name(market.away_team)
        home_form = self.history.form(home_norm, last_n=self.form_window)
        away_form = self.history.form(away_norm, last_n=self.form_window)

        bundle.context = {
            "home_team": market.home_team,
            "away_team": market.away_team,
            "home_norm": home_norm,
            "away_norm": away_norm,
            "home_form_matches": home_form.matches,
            "away_form_matches": away_form.matches,
            "league": market.league,
        }
        bundle.provenance["history"] = f"{len(self.history.matches)} finished matches"

        if home_form.matches == 0 or away_form.matches == 0:
            bundle.missing.append("team_history")
            bundle.context["reason"] = (
                "no finished-match history for "
                + (", ".join(t for t, f in ((market.home_team, home_form), (market.away_team, away_form)) if f.matches == 0))
            )
            return bundle

        if market.market_type in _GOAL_FAMILIES:
            self._goal_features(bundle, home_form, away_form, market)
        if market.market_type is MarketType.CORNERS:
            self._corner_features(bundle, home_form, away_form, market)
        if market.market_type.needs_player:
            self._player_features(bundle, market, home_norm, away_norm)

        self._finalise(bundle, plan)
        return bundle

    # ------------------------------------------------------------------ families
    def _goal_features(self, bundle: FeatureSet, home_form, away_form, market: Market) -> None:
        league_env = self._league_goal_environment(market)
        home_attack = home_form.goals_for / max(1, home_form.matches)
        away_attack = away_form.goals_for / max(1, away_form.matches)
        home_defence = home_form.goals_against / max(1, home_form.matches)
        away_defence = away_form.goals_against / max(1, away_form.matches)
        # Goals-per-team-match in a typical top division; used to express the
        # fixture's scoring environment as a multiplier around the league mean.
        home_adv_factor = 1.0 + (self._home_advantage(market) - 0.5)

        values = bundle.values
        values["home_attack_rate"] = round(home_attack, 4)
        values["away_attack_rate"] = round(away_attack, 4)
        values["home_defence_rate"] = round(home_defence, 4)
        values["away_defence_rate"] = round(away_defence, 4)
        values["league_goal_environment"] = round(league_env, 4)
        values["home_advantage"] = self._home_advantage(market)
        values["form_delta"] = round(
            (home_form.points / max(1, home_form.matches)) - (away_form.points / max(1, away_form.matches)), 4
        )
        values["rest_days_delta"] = self._rest_days_delta(bundle.match_key)
        values["home_shots_rate"] = round(home_form.shots / max(1, home_form.matches), 3)
        values["away_shots_rate"] = round(away_form.shots / max(1, away_form.matches), 3)
        values["home_clean_sheet_rate"] = round(home_form.clean_sheets / max(1, home_form.matches), 4)
        values["away_clean_sheet_rate"] = round(away_form.clean_sheets / max(1, away_form.matches), 4)
        values["btts_base_rate"] = self._btts_base_rate(market)
        values["attack_strength"] = self._strength_ratio(home_attack, away_attack)
        values["defence_strength"] = self._strength_ratio(home_defence, away_defence)
        # Expected goals for the fixture: attack rate blended with the opponent's
        # concession rate, scaled by the league scoring environment. These are the
        # inputs the goal-family models and the BTTS model read, so they must be
        # produced here rather than recomputed inconsistently downstream.
        values["expected_home_goals"] = round(
            ((home_attack + away_defence) / 2) * (league_env / _LEAGUE_GOALS_REFERENCE) * home_adv_factor, 4
        )
        values["expected_away_goals"] = round(
            ((away_attack + home_defence) / 2) * (league_env / _LEAGUE_GOALS_REFERENCE) * (2 - home_adv_factor), 4
        )
        values["total_expected_goals"] = round(
            values["expected_home_goals"] + values["expected_away_goals"], 4
        )
        values["clean_sheet_rates"] = round(
            (values["home_clean_sheet_rate"] + values["away_clean_sheet_rate"]) / 2, 4
        )
        bundle.provenance["rates"] = "football_data_uk rolling window"

    def _corner_features(self, bundle: FeatureSet, home_form, away_form, market: Market) -> None:
        home_cf = home_form.corners_for / max(1, home_form.matches)
        home_ca = home_form.corners_against / max(1, home_form.matches)
        away_cf = away_form.corners_for / max(1, away_form.matches)
        away_ca = away_form.corners_against / max(1, away_form.matches)
        values = bundle.values
        if home_form.corners_for == 0 and away_form.corners_for == 0:
            bundle.missing.append("team_corners_for")
            return
        values["home_corners_for_pm"] = round(home_cf, 3)
        values["home_corners_against_pm"] = round(home_ca, 3)
        values["away_corners_for_pm"] = round(away_cf, 3)
        values["away_corners_against_pm"] = round(away_ca, 3)
        values["expected_total_corners"] = round(((home_cf + home_ca) / 2) + ((away_cf + away_ca) / 2), 3)
        values["home_away_corner_split"] = round(home_cf - away_cf, 3)
        values["shots_proxy"] = round(
            (home_form.shots + away_form.shots) / max(1, home_form.matches + away_form.matches), 3
        )
        bundle.provenance["corners"] = "football_data_uk HC/AC columns"

    def _player_features(self, bundle: FeatureSet, market: Market, home_norm: str, away_norm: str) -> None:
        player, side = self._find_player(market, home_norm, away_norm)
        if player is None:
            bundle.missing.append("player_identity")
            bundle.context["reason"] = f"player '{market.player}' not found in any provider"
            return

        values = bundle.values
        battle = bundle.values
        team_form = self.history.form(player.team_norm, last_n=self.form_window)
        opponent_norm = away_norm if side == "home" else home_norm
        opponent_form = self.history.form(opponent_norm, last_n=self.form_window)

        values["starting_probability"] = (
            round(player.starting_probability, 4) if player.starting_probability is not None else None
        )
        values["expected_minutes"] = player.expected_minutes
        values["player_availability"] = round(player.availability, 4)
        values["player_goal_rate_90"] = round(max(player.xg_per_90, player.goals_per_90), 4)
        values["player_assist_rate_90"] = round(max(player.xa_per_90, player.assists_per_90), 4)
        values["player_player_index"] = (
            f"{player.team_norm}|{normalize_player_name(player.display_name)}"
        )
        values["team_attack_rate"] = round(team_form.goals_for / max(1, team_form.matches), 4)
        values["opponent_defence_rate"] = round(opponent_form.goals_against / max(1, opponent_form.matches), 4)
        values["league_goal_environment"] = round(self._league_goal_environment(market), 4)
        values["penalty_duty"] = self._penalty_duty(player)
        values["player_shots"] = player.shots_per_90
        values["player_shot_rate_90"] = player.shots_per_90
        values["player_received_cards_per_90"] = player.received_cards_per_90
        battle["player_side"] = side
        bundle.provenance["player"] = f"{player.source} ({player.status})"

        if market.market_type is MarketType.PLAYER_SHOTS and player.shots_per_90 is None:
            bundle.missing.append("player_shots_rate")

    # ------------------------------------------------------------------ helpers
    def _finalise(self, bundle: FeatureSet, plan: DataPlan) -> None:
        """Record which planned features are still absent."""
        for name in plan.features:
            if bundle.values.get(name) is None and name not in bundle.missing:
                bundle.missing.append(name)
        bundle.missing = sorted(set(bundle.missing))

    def _find_player(self, market: Market, home_norm: str, away_norm: str):
        """Resolve the market's player to a provider record, or refuse.

        Polymarket questions use full names ("Bukayo Saka") while the FPL feed
        publishes short names ("Saka"), so matching is done on several
        normalised forms. An ambiguous match is deliberately NOT resolved: a
        50/50 guess between two players is exactly the invented data this project
        refuses to trade on.
        """
        if not market.player:
            return None, ""
        key = normalize_player_name(market.player)
        if not key:
            return None, ""

        def side_for(record: PlayerRecord) -> str:
            return "home" if record.team_norm == home_norm else "away"

        # Candidate name forms, most specific first.
        tokens = key.split()
        forms = [key]
        if len(tokens) > 1:
            forms.append(tokens[-1])                       # surname
            forms.append(" ".join(tokens[-2:]))            # last two tokens

        # 1) explicit team hint: the question names one of the two teams.
        question = market.question.lower()
        team_hint = ""
        for team_label, norm in ((market.home_team, home_norm), (market.away_team, away_norm)):
            if team_label and team_label.lower()[:5] and team_label.lower()[:5] in question:
                team_hint = norm
                break
        if team_hint:
            for form in forms:
                found = self._player_index.get(f"{team_hint}|{form}")
                if found is not None:
                    return found, ("home" if team_hint == home_norm else "away")

        # 2) name match restricted to the two participating teams.
        for form in forms:
            pool = [
                p for p in self.players
                if p.team_norm in (home_norm, away_norm)
                and (p.norm_name == form or form in p.norm_name or p.norm_name in form)
            ]
            unique = {p.player_key: p for p in pool}
            if len(unique) == 1:
                record = next(iter(unique.values()))
                return record, side_for(record)

        # 3) global unique match (kept conservative: uniqueness is required).
        for form in forms:
            pool = [p for p in self.players if p.norm_name == form]
            unique = {p.player_key: p for p in pool}
            if len(unique) == 1:
                record = next(iter(unique.values()))
                return record, side_for(record)
        return None, ""

    def _league_goal_environment(self, market: Market) -> float:
        league_matches = [
            m for m in self.history.matches
            if market.league and market.league.lower()[:6] in (m.league or "").lower()
        ] or self.history.matches
        if not league_matches:
            return 2.6  # documented fallback: long-run top-division average
        totals = [m.total_goals for m in league_matches if m.total_goals is not None]
        if not totals:
            return 2.6
        return sum(totals) / len(totals)

    def _home_advantage(self, market: Market) -> float:
        """Empirical home-goal share in the league (documented, not guessed)."""
        league_matches = [
            m for m in self.history.matches
            if market.league and market.league.lower()[:6] in (m.league or "").lower()
        ] or self.history.matches
        home = sum(int(m.home_goals or 0) for m in league_matches)
        away = sum(int(m.away_goals or 0) for m in league_matches)
        if home + away == 0:
            return 0.54
        return round(home / (home + away), 4)

    def _rest_days_delta(self, match_key: str) -> float | None:
        parts = match_key.split("|")
        if len(parts) < 3:
            return None
        return None  # requires fixture-level scheduling data we do not have

    def _btts_base_rate(self, market: Market) -> float | None:
        league_matches = [
            m for m in self.history.matches
            if market.league and market.league.lower()[:6] in (m.league or "").lower()
        ] or self.history.matches
        flags = [m.btts for m in league_matches if m.btts is not None]
        if not flags:
            return None
        return round(sum(1 for f in flags if f) / len(flags), 4)

    @staticmethod
    def _strength_ratio(a: float, b: float) -> float:
        total = a + b
        if total <= 0:
            return 0.5
        return round(a / total, 4)

    @staticmethod
    def _penalty_duty(player: PlayerRecord) -> float | None:
        """Penalty-taking weight, or None when the provider says nothing at all.

        A provider that reports ``penalties_order = None`` is telling us the
        player is NOT a designated penalty taker (weight 0.0). Only a completely
        absent key means "unknown" (None -> the market is refused).
        """
        attributes = player.attributes or {}
        if "penalty_order" not in attributes:
            return None
        order = attributes.get("penalty_order")
        if order in (None, "", 0, "0"):
            return 0.0
        try:
            order_int = int(order)
        except (TypeError, ValueError):
            return None
        return 1.0 if order_int == 1 else (0.35 if order_int <= 3 else 0.1)


_GOAL_FAMILIES = frozenset(
    {
        MarketType.MATCH_RESULT,
        MarketType.TOTAL_GOALS,
        MarketType.BTTS,
        MarketType.TEAM_TOTALS,
    }
)
