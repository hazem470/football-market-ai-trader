"""Market classification: metadata wins, text is a discounted fallback, else UNKNOWN."""
from __future__ import annotations

import pytest

from src.markets.classifier import (
    classify_by_text,
    classify_market,
    classify_selection_kind,
    detect_period,
    extract_line,
    map_sports_market_type,
)
from src.markets.schema import MarketType


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("moneyline", MarketType.MATCH_RESULT),
        ("total_goals", MarketType.TOTAL_GOALS),
        ("both_teams_to_score", MarketType.BTTS),
        ("soccer_team_total_corners", MarketType.CORNERS),
        ("total_corners", MarketType.CORNERS),
        ("soccer_team_totals", MarketType.TEAM_TOTALS),
        ("soccer_player_goals", MarketType.PLAYER_GOAL),
        ("soccer_anytime_goalscorer", MarketType.PLAYER_GOAL),
        ("soccer_player_assists", MarketType.PLAYER_ASSIST),
        ("soccer_player_shots", MarketType.PLAYER_SHOTS),
        ("soccer_player_goalkeeper_saves", MarketType.GOALKEEPER_SAVES),
        ("soccer_halftime_result", MarketType.HALFTIME_RESULT),
        ("soccer_exact_score", MarketType.EXACT_SCORE),
    ],
)
def test_metadata_mapping(raw, expected):
    family, confidence = map_sports_market_type(raw)
    assert family is expected
    assert confidence >= 0.9


def test_unknown_sports_type_is_not_guessed():
    family, confidence = map_sports_market_type("soccer_some_future_type")
    assert family is MarketType.UNKNOWN
    assert confidence < 0.5


def test_empty_metadata_is_unknown():
    family, confidence = map_sports_market_type("")
    assert family is MarketType.UNKNOWN and confidence == 0.0


def test_text_fallback_classifies_common_questions():
    family, confidence, _reason = classify_by_text("Will both teams score in Arsenal vs Chelsea?")
    assert family is MarketType.BTTS and confidence > 0.5


def test_text_fallback_returns_unknown_for_nonsense():
    family, confidence, _reason = classify_by_text("Will the manager wear a blue tie?")
    assert family is MarketType.UNKNOWN
    assert confidence == 0.0


def test_metadata_beats_text():
    result = classify_market(
        question="Will Arsenal score over 2.5 goals?",
        group_item_title="Over 2.5",
        sports_market_type="soccer_player_goals",
    )
    # metadata says player goals; text says over/under -> conflict is surfaced
    assert result.market_type is MarketType.UNKNOWN
    assert "conflict" in result.source or result.confidence < 0.9


def test_metadata_only_classification():
    result = classify_market(
        question="Will Arsenal win?", group_item_title="Arsenal", sports_market_type="moneyline",
    )
    assert result.market_type is MarketType.MATCH_RESULT
    assert result.source == "metadata"
    assert result.confidence >= 0.9
    assert "moneyline" in result.reason


def test_classification_falls_back_to_text_with_discounted_confidence():
    result = classify_market(question="Will Arsenal vs Chelsea have both teams to score?")
    assert result.market_type is MarketType.BTTS
    assert result.source == "text"
    assert result.confidence <= 0.85


def test_unclassifiable_returns_unknown_with_reason():
    result = classify_market(question="Will it rain in London?")
    assert result.market_type is MarketType.UNKNOWN
    assert result.confidence == 0.0
    assert result.reason


@pytest.mark.parametrize(
    "texts,expected",
    [
        (("Arsenal vs Chelsea - Halftime Result",), "FIRST_HALF"),
        (("Chicago Fire vs Vancouver - Second Half Result",), "SECOND_HALF"),
        (("Arsenal vs Chelsea",), "FULL"),
    ],
)
def test_period_detection(texts, expected):
    assert detect_period(*texts) == expected


@pytest.mark.parametrize(
    "text,expected",
    [("Chelsea O/U 1.5", 1.5), ("Over 2.5", 2.5), ("Arsenal O/U 3.5 Corners", 3.5), ("No line here", None)],
)
def test_line_extraction(text, expected):
    assert extract_line(text) == expected


@pytest.mark.parametrize(
    "group,kind",
    [("Over 2.5", "OVER"), ("Under 1.5", "UNDER"), ("Arsenal", "TEAM"),
     ("Arsenal", "TEAM"), ("Over", "OVER")],
)
def test_selection_kind(group, kind):
    assert classify_selection_kind(MarketType.TOTAL_GOALS, group, "Arsenal", "Chelsea") == kind


def test_selection_kind_player():
    assert classify_selection_kind(MarketType.PLAYER_GOAL, "Bukayo Saka", "Arsenal", "Chelsea") == "PLAYER"


def test_supported_and_unsupported_sets_are_disjoint():
    from src.markets.schema import SUPPORTED_MARKET_TYPES, UNSUPPORTED_MARKET_TYPES

    assert not (SUPPORTED_MARKET_TYPES & UNSUPPORTED_MARKET_TYPES)
    assert MarketType.MATCH_RESULT in SUPPORTED_MARKET_TYPES
    assert MarketType.CARDS in UNSUPPORTED_MARKET_TYPES
    assert MarketType.PLAYER_SHOTS in SUPPORTED_MARKET_TYPES
