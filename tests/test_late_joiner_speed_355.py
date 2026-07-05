"""Regression tests for the late-joiner speed-bonus exploit (#355).

Before the fix ``PhaseController.add_late_joiner_timer`` built a fresh
``QuestionTimer(remaining).start()``, so the joiner's ``get_elapsed()`` counted
from their JOIN time rather than the round start. ``submit_answer`` feeds that
elapsed together with the FULL ``round_duration`` into
``ScoringEngine.score_submission``, where the speed bonus is
``MAX_SPEED_BONUS * (1 - elapsed / round_duration)``. A player joining with 5s
left on a 30s round and answering in 2s therefore scored ``time_fraction ≈ 0.93``
(near-max speed bonus) while an on-time player answering at the same wall-clock
instant scored ``≈ 0.17`` — late joining systematically beat honest players on
the speed component.

The fix seeds the late-joiner timer against the SHARED round wall-clock (the
pause/resume ``QuestionTimer.resumed`` trick), so ``get_elapsed()`` is identical
to an on-time player's and the speed bonus can never exceed theirs.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT))

from custom_components.quizify.game.phase_controller import (  # noqa: E402
    GamePhase,
    PhaseController,
)
from custom_components.quizify.game.scoring_engine import ScoringEngine  # noqa: E402
from custom_components.quizify.game.types import Difficulty  # noqa: E402

ENGINE = ScoringEngine()
ROUND = 30.0


def _controller_mid_round(shift: float) -> PhaseController:
    """A QUESTION_ACTIVE controller with one on-time player, ``shift`` seconds in.

    Backdates both the shared round wall-clock and the on-time player's timer so
    the round looks like it started ``shift`` seconds ago without sleeping.
    """
    names = ["ontime"]
    pc = PhaseController(players_fn=lambda: list(names))
    pc.begin_round(ROUND)
    pc.enter_question_active()
    assert pc.phase == GamePhase.QUESTION_ACTIVE
    pc.round_start_time -= shift  # type: ignore[operator]
    pc.timers["ontime"]._start_time -= shift  # noqa: SLF001
    return pc


class TestLateJoinerElapsed:
    def test_late_joiner_elapsed_tracks_round_start_not_join(self) -> None:
        # Round is 25s into a 30s round; a player joins now (5s left).
        pc = _controller_mid_round(25.0)
        pc.add_late_joiner_timer("late")
        assert "late" in pc.timers

        ontime_elapsed = pc.timers["ontime"].get_elapsed()
        late_elapsed = pc.timers["late"].get_elapsed()

        # The late joiner's elapsed must reflect the SHARED round wall-clock
        # (~25s), NOT a fresh clock started at join (~0s). Both players answering
        # at this same wall-clock instant see the same elapsed.
        assert late_elapsed == pytest.approx(ontime_elapsed, abs=0.2)
        assert late_elapsed >= 24.0  # not the ~0 the pre-fix fresh timer gave

    def test_late_joiner_keeps_grace_window_to_answer(self) -> None:
        # Even joining with little time left, the 0.5s floor still lets them
        # answer the in-flight question.
        pc = _controller_mid_round(29.9)
        pc.add_late_joiner_timer("late")
        # ~0.5s floor (allow a hair of clock drift since construction).
        assert pc.timers["late"].get_remaining() >= 0.49
        assert not pc.timers["late"].is_expired()


class TestLateJoinerSpeedBonus:
    def test_late_joiner_speed_bonus_not_higher_than_on_time(self) -> None:
        # 25s into a 30s round, a late joiner appears and both submit NOW.
        pc = _controller_mid_round(25.0)
        pc.add_late_joiner_timer("late")

        ontime_elapsed = pc.timers["ontime"].get_elapsed()
        late_elapsed = pc.timers["late"].get_elapsed()

        common = {
            "correct": True,
            "round_duration": ROUND,
            "difficulty": Difficulty.EASY,
            "streak": 1,
            "double_points_active": False,
            "is_final_round": False,
            "wager": None,
            "score_before_wager": 100,
        }
        ontime = ENGINE.score_submission(elapsed=ontime_elapsed, **common)
        late = ENGINE.score_submission(elapsed=late_elapsed, **common)

        # The core invariant: late joining must never beat an honest player on
        # the speed component at the same wall-clock moment.
        assert late.speed_bonus <= ontime.speed_bonus
        # And with a ~25s elapsed both are near the bottom of the speed curve
        # (pre-fix the late joiner scored a near-max bonus from a ~0 elapsed).
        assert late.speed_bonus <= 1
