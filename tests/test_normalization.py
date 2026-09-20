"""Canonical normalization across providers."""
from __future__ import annotations

import pytest

from src.data.normalization.canonical import Normalizer, TeamHistory
from tests.conftest import make_match


def test_match_normalization_from_raw():
    record = Normalizer.match_from_raw({
        "home_team": "Arsenal FC", "away_team": "Chelsea", "home_goals": 2, "away_goals": 1,
        "match_date": "2025-08-15", "league": "Premier League", "league_code": "E0",
        "home_shots": 14, "away_shots": 9, "home_corners": 7, "away_corners": 3,
        "home_yellows": 1, "away_yellows": 2, "source": "test",
    })
    assert record.home_team_norm == "arsenal"
    assert record.away_team_norm == "chelsea"
    assert record.total_goals == 3
    assert record.btts is True
    assert record.finished


def test_match_coverage_ratio():
    full = make_match("A", "B", 1, 1)
    assert full.field_coverage() == pytest.approx(1.0)
    sparse = Normalizer.match_from_raw({
        "home_team": "A", "away_team": "B", "home_goals": 1, "away_goals": 0, "match_date": "2025-08-15",
    })
    assert sparse.field_coverage() < 0.5


def test_unfinished_match_is_not_finished():
    record = Normalizer.match_from_raw({"home_team": "A", "away_team": "B", "match_date": "2025-08-15"})
    assert not record.finished
    assert record.total_goals is None
    assert record.btts is None


def test_team_history_indexes_both_sides(synthetic_history):
    form = synthetic_history.form("team a", last_n=10)
    assert form.matches == 10
    assert form.goals_for > 0


def test_team_history_head_to_head(synthetic_history):
    pairs = synthetic_history.head_to_head("team a", "team b", last_n=5)
    assert len(pairs) <= 5


def test_form_rates_are_per_match(synthetic_history):
    form = synthetic_history.form("team a", last_n=10)
    rates = form.rates(form.matches)
    assert rates["goals_for_pm"] == pytest.approx(form.goals_for / form.matches)
    assert 0 <= rates["clean_sheet_rate"] <= 1


def test_team_history_handles_null_stat_columns():
    history = TeamHistory([make_match("A", "B", 1, 1, home_corners=None, away_corners=None,
                                      home_shots=None, away_shots=None)])
    form = history.form("a")
    assert form.corners_for == 0.0
    assert form.matches == 1


def test_player_normalization_uses_lineup_estimate():
    record = Normalizer.player_from_raw(
        {"player_key": "arsenal|saka", "display_name": "Saka", "team": "Arsenal",
         "minutes": 900.0, "starts": 10.0, "games": 10.0, "xg": 5.0, "availability": 1.0,
         "xg_per_90": 0.5, "status": "a", "source": "test"},
        {"starting_probability": 0.92, "expected_minutes": 85.0},
    )
    assert record.starting_probability == pytest.approx(0.92)
    assert record.expected_minutes == pytest.approx(85.0)
    assert record.is_available


def test_unavailable_player_is_flagged():
    record = Normalizer.player_from_raw({
        "player_key": "x", "display_name": "X", "team": "T", "minutes": 100.0,
        "availability": 0.0, "status": "i", "source": "test",
    })
    assert not record.is_available


def test_shots_per_90_stays_none_when_absent():
    record = Normalizer.player_from_raw({
        "player_key": "x", "display_name": "X", "team": "T", "minutes": 100.0,
        "availability": 1.0, "status": "a", "source": "test",
    })
    assert record.shots_per_90 is None


def test_match_record_db_row_excludes_non_columns():
    row = make_match("A", "B", 1, 0).to_db_row()
    assert "issues" not in row and "odds" not in row
    assert row["home_goals"] == 1
