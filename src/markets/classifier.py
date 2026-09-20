"""Dynamic market classification.

Primary signal: Polymarket's own ``sportsMarketType`` metadata.
Fallback: deterministic text parsing of question / title / groupItemTitle.

If neither yields a confident family the market becomes ``UNKNOWN`` and is
never traded - we do not invent a classification.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from src.markets.schema import MarketType

#: Polymarket `sportsMarketType` -> internal family. Keys are lower-cased.
SPORTS_MARKET_TYPE_MAP: dict[str, MarketType] = {
    # moneyline / result
    "moneyline": MarketType.MATCH_RESULT,
    "first_half_moneyline": MarketType.HALFTIME_RESULT,
    "soccer_halftime_result": MarketType.HALFTIME_RESULT,
    "soccer_second_half_result": MarketType.SECOND_HALF_RESULT,
    "soccer_team_to_advance": MarketType.TEAM_TO_ADVANCE,
    # goals
    "total_goals": MarketType.TOTAL_GOALS,
    "first_half_totals": MarketType.TOTAL_GOALS,
    "soccer_exact_score": MarketType.EXACT_SCORE,
    "soccer_first_half_exact_score": MarketType.EXACT_SCORE,
    "soccer_first_to_score": MarketType.FIRST_TO_SCORE,
    "soccer_first_half_first_to_score": MarketType.FIRST_TO_SCORE,
    "soccer_second_half_first_to_score": MarketType.FIRST_TO_SCORE,
    "both_teams_to_score": MarketType.BTTS,
    "both_teams_to_score_first_half": MarketType.BTTS,
    "both_teams_to_score_second_half": MarketType.BTTS,
    "soccer_team_totals": MarketType.TEAM_TOTALS,
    "soccer_home_team_totals": MarketType.TEAM_TOTALS,
    "soccer_away_team_totals": MarketType.TEAM_TOTALS,
    "soccer_first_half_team_totals": MarketType.TEAM_TOTALS,
    "soccer_second_half_team_totals": MarketType.TEAM_TOTALS,
    "game_team_totals": MarketType.TEAM_TOTALS,
    "h2_team_totals": MarketType.TEAM_TOTALS,
    "team_totals": MarketType.TEAM_TOTALS,
    "spreads": MarketType.TEAM_TOTALS,
    "first_half_spreads": MarketType.TEAM_TOTALS,
    # corners
    "total_corners": MarketType.CORNERS,
    "soccer_team_total_corners": MarketType.CORNERS,
    "soccer_first_half_total_corners": MarketType.CORNERS,
    "soccer_second_half_total_corners": MarketType.CORNERS,
    "soccer_first_corner": MarketType.FIRST_CORNER,
    "soccer_game_corners_odd_even": MarketType.CORNERS_ODD_EVEN,
    # player props
    "soccer_player_goals": MarketType.PLAYER_GOAL,
    "soccer_anytime_goalscorer": MarketType.PLAYER_GOAL,
    "soccer_player_assists": MarketType.PLAYER_ASSIST,
    "soccer_player_goals_plus_assists": MarketType.PLAYER_GOALS_PLUS_ASSISTS,
    "soccer_player_shots": MarketType.PLAYER_SHOTS,
    "soccer_player_shots_on_target": MarketType.PLAYER_SHOTS,
    "soccer_player_goalkeeper_saves": MarketType.GOALKEEPER_SAVES,
    "assists": MarketType.PLAYER_ASSIST,
    # match structure
    "soccer_extra_time": MarketType.EXTRA_TIME,
    "soccer_penalty_shootout": MarketType.PENALTY_SHOOTOUT,
    "soccer_player_cards": MarketType.CARDS,
    "cards": MarketType.CARDS,
    "soccer_total_cards": MarketType.CARDS,
    "soccer_offsides": MarketType.OFFSIDES,
    "soccer_fouls": MarketType.FOULS,
    "soccer_penalties": MarketType.PENALTIES,
    "soccer_substitutions": MarketType.SUBSTITUTIONS,
}

#: Ordered (regex, family, weight) rules used when metadata is missing.
#: Rules are evaluated in order; the FIRST match wins. Order matters: a specific
#: team-level rule must be checked before a generic one that could swallow it
#: ("both teams to score" must beat "to score").
TEXT_RULES: tuple[tuple[re.Pattern[str], MarketType, float], ...] = (
    (re.compile(r"\bboth teams to score\b|\bbtts\b|\bboth teams score\b", re.I), MarketType.BTTS, 0.9),
    (re.compile(r"\banytime goalscorer\b|\bto score anytime\b", re.I), MarketType.PLAYER_GOAL, 0.9),
    (re.compile(r"\bgoalkeeper saves?\b", re.I), MarketType.GOALKEEPER_SAVES, 0.9),
    (re.compile(r"\bgoals? \+ assists?\b|\bgoal involvements?\b", re.I), MarketType.PLAYER_GOALS_PLUS_ASSISTS, 0.85),
    (re.compile(r"\b(player|goalscorer)\b.*\b(assists?)\b", re.I), MarketType.PLAYER_ASSIST, 0.85),
    (re.compile(r"\bassists?\b", re.I), MarketType.PLAYER_ASSIST, 0.6),
    (re.compile(r"\bshots? on target\b", re.I), MarketType.PLAYER_SHOTS, 0.85),
    (re.compile(r"\bshots?\b", re.I), MarketType.PLAYER_SHOTS, 0.8),
    # Generic "to score" - only when it is NOT a team-level "both teams" phrasing.
    (re.compile(r"(?<!both teams )\bgoalscorer\b|^(?!.*\bboth teams\b).*\bto score\b", re.I),
     MarketType.PLAYER_GOAL, 0.8),
    (re.compile(r"\bcorners?\b.*\bodd\b|\bcorners?\b.*\beven\b", re.I), MarketType.CORNERS_ODD_EVEN, 0.85),
    (re.compile(r"\bfirst corner\b", re.I), MarketType.FIRST_CORNER, 0.9),
    (re.compile(r"\bcorners?\b", re.I), MarketType.CORNERS, 0.85),
    (re.compile(r"\bexact score\b|\bcorrect score\b", re.I), MarketType.EXACT_SCORE, 0.9),
    (re.compile(r"\bhalftime result\b|\bhalf[- ]time result\b|\bhalf[- ]time winner\b", re.I), MarketType.HALFTIME_RESULT, 0.9),
    (re.compile(r"\bsecond half result\b", re.I), MarketType.SECOND_HALF_RESULT, 0.9),
    (re.compile(r"\bfirst team to score\b|\bfirst to score\b", re.I), MarketType.FIRST_TO_SCORE, 0.85),
    (re.compile(r"\bto advance\b|\bto qualify\b", re.I), MarketType.TEAM_TO_ADVANCE, 0.85),
    (re.compile(r"\bpenalty shootout\b", re.I), MarketType.PENALTY_SHOOTOUT, 0.9),
    (re.compile(r"\bextra time\b", re.I), MarketType.EXTRA_TIME, 0.8),
    (re.compile(r"\bcards?\b|\bbookings?\b", re.I), MarketType.CARDS, 0.8),
    (re.compile(r"\boffsides?\b", re.I), MarketType.OFFSIDES, 0.8),
    (re.compile(r"\bfouls?\b", re.I), MarketType.FOULS, 0.8),
    (re.compile(r"\bsubstitutions?\b", re.I), MarketType.SUBSTITUTIONS, 0.8),
    (re.compile(r"\bo/u\b|\bover/under\b|\bover \d|\bunder \d|\btotal goals?\b|\bgoals?\b.*\bover\b", re.I),
     MarketType.TOTAL_GOALS, 0.7),
)

#: Lines like "Chicago Fire FC O/U 1.5", "Over 2.5", "3.5 Corners", "-1.5"
_LINE_RE = re.compile(r"(?:o/u|over|under|handicap)?\s*([+-]?\d+(?:\.\d+)?)", re.I)
_OVER_RE = re.compile(r"\b(over|o/u|o)\b", re.I)
_UNDER_RE = re.compile(r"\b(under|u)\b", re.I)


@dataclass
class Classification:
    market_type: MarketType
    confidence: float
    source: str
    raw_type: str = ""
    period: str = "FULL"
    line: float | None = None
    selection_kind: str = "OTHER"
    reason: str = ""


def map_sports_market_type(raw: str) -> tuple[MarketType, float]:
    key = (raw or "").strip().lower()
    if not key:
        return MarketType.UNKNOWN, 0.0
    if key in SPORTS_MARKET_TYPE_MAP:
        return SPORTS_MARKET_TYPE_MAP[key], 0.95
    # Unknown *but clearly soccer* types: keep them UNKNOWN rather than guessing.
    if key.startswith("soccer_") or key in ("moneyline", "spreads", "totals"):
        return MarketType.UNKNOWN, 0.2
    return MarketType.UNKNOWN, 0.0


def classify_by_text(*texts: str) -> tuple[MarketType, float, str]:
    haystack = " \n ".join(t for t in texts if t)
    if not haystack:
        return MarketType.UNKNOWN, 0.0, "no text"
    for pattern, family, weight in TEXT_RULES:
        if pattern.search(haystack):
            return family, weight, pattern.pattern
    return MarketType.UNKNOWN, 0.0, "no rule matched"


def detect_period(*texts: str) -> str:
    haystack = " ".join(texts).lower()
    if "first half" in haystack or "1st half" in haystack or "halftime" in haystack:
        return "FIRST_HALF"
    if "second half" in haystack or "2nd half" in haystack:
        return "SECOND_HALF"
    return "FULL"


def extract_line(*texts: str) -> float | None:
    haystack = " ".join(t for t in texts if t)
    if not haystack:
        return None
    match = _LINE_RE.search(haystack)
    if not match:
        return None
    try:
        value = float(match.group(1))
    except ValueError:
        return None
    return value if 0 <= abs(value) <= 30 else None


#: Pairs of families whose text/metadata disagreement is benign (the text rule is
#: simply broader than the metadata label, not contradictory).
_COMPATIBLE_PAIRS: frozenset[frozenset[MarketType]] = frozenset({
    frozenset({MarketType.BTTS, MarketType.TOTAL_GOALS}),
    frozenset({MarketType.CORNERS, MarketType.FIRST_CORNER}),
    frozenset({MarketType.CORNERS, MarketType.CORNERS_ODD_EVEN}),
    frozenset({MarketType.HALFTIME_RESULT, MarketType.MATCH_RESULT}),
    frozenset({MarketType.SECOND_HALF_RESULT, MarketType.MATCH_RESULT}),
    frozenset({MarketType.PLAYER_GOAL, MarketType.PLAYER_GOALS_PLUS_ASSISTS}),
    frozenset({MarketType.FIRST_TO_SCORE, MarketType.MATCH_RESULT}),
})


def _families_are_compatible(a: MarketType, b: MarketType) -> bool:
    return frozenset({a, b}) in _COMPATIBLE_PAIRS


def classify_market(
    question: str = "",
    title: str = "",
    group_item_title: str = "",
    description: str = "",
    sports_market_type: str = "",
) -> Classification:
    """Classify a market. Metadata wins; text is only a fallback."""
    raw = (sports_market_type or "").strip()
    family, confidence = map_sports_market_type(raw)

    period = detect_period(title, question, group_item_title, description, raw)
    line = extract_line(group_item_title, question)

    if family is MarketType.UNKNOWN:
        text_family, text_conf, reason = classify_by_text(title, question, group_item_title, description)
        if text_family is not MarketType.UNKNOWN:
            return Classification(
                market_type=text_family,
                confidence=text_conf * 0.85,  # text-only is deliberately discounted
                source="text",
                raw_type=raw,
                period=period,
                line=line,
                reason=f"text fallback: {reason}",
            )
        return Classification(
            market_type=MarketType.UNKNOWN,
            confidence=0.0,
            source="none",
            raw_type=raw,
            period=period,
            reason=f"unclassified (sportsMarketType={raw or 'missing'})",
        )

    # Metadata is authoritative, but it is not infallible: a genuine
    # contradiction between the declared type and the question text means we
    # cannot tell what the market settles on, so we refuse it outright.
    reason = f"metadata sportsMarketType={raw}"
    text_family, _text_confidence, text_reason = classify_by_text(
        title, question, group_item_title, description
    )
    if text_family is not MarketType.UNKNOWN and text_family is not family:
        if not _families_are_compatible(family, text_family):
            return Classification(
                market_type=MarketType.UNKNOWN,
                confidence=0.3,
                source="conflict",
                raw_type=raw,
                period=period,
                line=line,
                reason=f"metadata={family.value} conflicts with text={text_family.value}",
            )
    if text_family is family:
        reason += f"; confirmed by text ({text_reason})"

    return Classification(
        market_type=family,
        confidence=confidence,
        source="metadata",
        raw_type=raw,
        period=period,
        line=line,
        reason=reason,
    )


def classify_selection_kind(family: MarketType, group_item_title: str, home_team: str, away_team: str) -> str:
    """TEAM | OVER | UNDER | PLAYER | OTHER - drives data requirements."""
    text = (group_item_title or "").strip()
    lowered = text.lower()
    if _OVER_RE.search(lowered):
        return "OVER"
    if _UNDER_RE.search(lowered):
        return "UNDER"
    if family.needs_player:
        return "PLAYER"
    if home_team and text and text.lower().startswith(home_team.lower()[:6]):
        return "TEAM"
    if away_team and text and text.lower().startswith(away_team.lower()[:6]):
        return "TEAM"
    return "OTHER"
