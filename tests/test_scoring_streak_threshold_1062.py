"""The streak multiplier only applies from a streak of 3 (#1062)."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from custom_components.quizify.game.scoring import (  # noqa: E402
    STREAK_MILESTONES,
    calculate_round_score,
    get_streak_milestone_bonus,
    get_streak_multiplier,
)
from custom_components.quizify.game.scoring_engine import ScoringEngine  # noqa: E402
from custom_components.quizify.game.types import Difficulty  # noqa: E402


def _score(streak: int, double: bool = False):
    return ScoringEngine().score_submission(
        correct=True,
        elapsed=0.0,
        round_duration=15.0,
        difficulty=Difficulty.MEDIUM,
        streak=streak,
        double_points_active=double,
        is_final_round=False,
        wager=None,
        score_before_wager=0,
    )


@pytest.mark.parametrize("streak", [0, 1, 2])
def test_below_three_there_is_no_multiplier(streak: int) -> None:
    assert get_streak_multiplier(streak) == 1.0
    c = _score(streak)
    assert c.streak_bonus == 0
    assert c.points == _score(0).points == 22
    assert c.milestone_bonus == 0
    assert calculate_round_score(True, 0.0, 15.0, Difficulty.MEDIUM, streak) == 22


@pytest.mark.parametrize(
    ("streak", "mult", "points"),
    [(3, 1.3, 29), (4, 1.4, 31), (5, 1.5, 33), (6, 1.5, 33), (12, 1.5, 33)],
)
def test_from_three_the_multiplier_climbs_to_the_cap(
    streak: int, mult: float, points: int
) -> None:
    assert get_streak_multiplier(streak) == pytest.approx(mult)
    # The engine adds the one-off milestone spike on top (unchanged by #1062).
    c = _score(streak)
    assert c.points - c.milestone_bonus == points
    assert calculate_round_score(True, 0.0, 15.0, Difficulty.MEDIUM, streak) == points


@pytest.mark.parametrize("streak", range(0, 8))
@pytest.mark.parametrize("double", [False, True])
def test_breakdown_parts_add_up_to_the_score(streak: int, double: bool) -> None:
    c = _score(streak, double)
    assert (
        10
        + c.speed_bonus
        + c.difficulty_bonus
        + c.streak_bonus
        + c.double_bonus
        + c.milestone_bonus
        == c.points
    )


def test_milestone_bonuses_are_unchanged() -> None:
    assert STREAK_MILESTONES == {3: 20, 5: 50, 10: 100, 15: 150, 20: 250, 25: 400}
    assert get_streak_milestone_bonus(1) == 0
    assert get_streak_milestone_bonus(3) == 20
