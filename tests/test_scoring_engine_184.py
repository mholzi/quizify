"""Unit tests for the extracted ScoringEngine (#184).

These pin the *pure* scoring arithmetic that was lifted out of
QuizifyGameState.submit_answer so the refactor is provably behaviour-preserving.
"""

from __future__ import annotations

import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT))

from custom_components.quizify.game.scoring import (  # noqa: E402
    MAX_SPEED_BONUS,
    calculate_round_score,
)
from custom_components.quizify.game.scoring_engine import (  # noqa: E402
    ScoreComputation,
    ScoringEngine,
)
from custom_components.quizify.game.types import Difficulty  # noqa: E402

ENGINE = ScoringEngine()


def _score(**kw):
    base = dict(
        correct=True,
        elapsed=0.0,
        round_duration=30.0,
        difficulty=Difficulty.EASY,
        streak=1,
        double_points_active=False,
        is_final_round=False,
        wager=None,
        score_before_wager=100,
    )
    base.update(kw)
    return ENGINE.score_submission(**base)


class TestScoringEngineMatchesLegacy:
    def test_wrong_answer_scores_zero(self):
        c = _score(correct=False, streak=0)
        assert c.points == 0
        assert c.speed_bonus == 0
        assert c.streak_bonus == 0
        assert c.milestone_bonus == 0

    def test_correct_matches_calculate_round_score(self):
        # The non-wager, non-milestone path must equal the underlying helper.
        for elapsed in (0.0, 5.0, 15.0, 29.0):
            for diff in (Difficulty.EASY, Difficulty.MEDIUM, Difficulty.HARD):
                for streak in (1, 2, 4):
                    c = _score(
                        elapsed=elapsed, difficulty=diff, streak=streak
                    )
                    expected = calculate_round_score(
                        correct=True,
                        elapsed=elapsed,
                        time_limit=30.0,
                        difficulty=diff,
                        streak=streak,
                        double_points_active=False,
                    )
                    assert c.points == expected

    def test_speed_bonus_full_at_zero_elapsed(self):
        c = _score(elapsed=0.0)
        assert c.speed_bonus == MAX_SPEED_BONUS

    def test_double_points_matches_helper(self):
        double = _score(double_points_active=True)
        expected = calculate_round_score(
            correct=True,
            elapsed=0.0,
            time_limit=30.0,
            difficulty=Difficulty.EASY,
            streak=1,
            double_points_active=True,
        )
        assert double.points == expected
        assert double.double_points is True

    def test_milestone_spike_folded_into_points(self):
        # streak 3 → +20 milestone on top of the regular score.
        regular = calculate_round_score(
            correct=True,
            elapsed=0.0,
            time_limit=30.0,
            difficulty=Difficulty.EASY,
            streak=3,
            double_points_active=False,
        )
        c = _score(streak=3)
        assert c.milestone_bonus == 20
        assert c.points == regular + 20

    def test_breakdown_dict_shape(self):
        c = _score(streak=3)
        bd = c.breakdown
        assert set(bd) == {
            "speed_bonus",
            "streak_bonus",
            "difficulty_multiplier",
            "double_points",
            "wager",
            "milestone_bonus",
        }
        assert bd["milestone_bonus"] == 20


class TestWagerOverride:
    def test_wager_correct_replaces_score(self):
        c = _score(
            is_final_round=True, wager=50, score_before_wager=200, streak=2
        )
        # 50% of 200 = 100, replaces normal scoring.
        assert c.points == 100
        assert c.wager_used == 100
        assert c.speed_bonus == 0
        assert c.streak_bonus == 0

    def test_wager_wrong_subtracts_but_not_below_bank(self):
        c = _score(
            correct=False,
            is_final_round=True,
            wager=50,
            score_before_wager=200,
            streak=0,
        )
        assert c.points == -100
        assert c.wager_used == 100

    def test_wager_skips_milestone(self):
        c = _score(
            is_final_round=True, wager=10, score_before_wager=100, streak=3
        )
        assert c.milestone_bonus == 0

    def test_no_wager_on_non_final_round(self):
        c = _score(is_final_round=False, wager=50, score_before_wager=200)
        assert c.wager_used is None


class TestScoreComputation:
    def test_is_dataclass_result(self):
        c = _score()
        assert isinstance(c, ScoreComputation)
