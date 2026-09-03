"""Regression tests for issue #702.

``add_late_joiner_timer`` only acts in QUESTION_ACTIVE, so a guest who joined
while the game was paused was in none of the pause snapshots. ``resume()``
then gave every such name a fresh ``QuestionTimer(full).start()``: paused with
14s left, the existing player resumed with 14.0s and the newcomer got 20.0s.

The tick loop ends only once every connected timer has expired and
``all_submitted()`` ignores late joiners, so the newcomer held the round open —
every phone and the television sat at 0:00 for up to a full round.

Joining mid-question *without* a pause already handed out the round's shared
remaining time, so this was an inconsistency between two paths. Both now use
the same clock.
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

ROUND = 30.0


def _paused_mid_round(shift: float, names: list[str]) -> PhaseController:
    """A controller paused ``shift`` seconds into a round, with ``names`` playing.

    Backdates the shared round wall-clock and every timer so the round looks
    ``shift`` seconds old without sleeping, then pauses.
    """
    pc = PhaseController(players_fn=lambda: list(names))
    pc.begin_round(ROUND)
    pc.enter_question_active()
    pc.round_start_time -= shift  # type: ignore[operator]
    for timer in pc.timers.values():
        timer._start_time -= shift  # noqa: SLF001
    assert pc.pause("admin_disconnected") is True
    assert pc.phase == GamePhase.PAUSED
    return pc


class TestGuestJoiningDuringPause:
    def test_newcomer_gets_the_rounds_remaining_not_a_full_clock(self) -> None:
        names = ["ontime"]
        pc = _paused_mid_round(16.0, names)  # 14s left on a 30s round
        names.append("newcomer")  # joins while paused

        assert pc.resume() is True

        ontime_left = pc.timers["ontime"].get_remaining()
        newcomer_left = pc.timers["newcomer"].get_remaining()
        assert ontime_left == pytest.approx(14.0, abs=0.2)
        # The bug: 20.0 here (the full round), 6s past everybody else.
        assert newcomer_left == pytest.approx(ontime_left, abs=0.2)

    def test_the_round_no_longer_waits_for_the_newcomer(self) -> None:
        """Every connected timer expires together, so the tick loop can end."""
        names = ["ontime"]
        pc = _paused_mid_round(29.6, names)  # 0.4s left
        names.append("newcomer")
        assert pc.resume() is True

        # Both clocks are within the same fraction of a second of zero; before
        # the fix the newcomer still had the full 30s.
        assert pc.timers["newcomer"].get_remaining() <= 1.0

    def test_newcomer_speed_bonus_matches_an_on_time_player(self) -> None:
        """elapsed is seeded from the shared wall-clock, not from the join."""
        names = ["ontime"]
        pc = _paused_mid_round(20.0, names)
        names.append("newcomer")
        assert pc.resume() is True

        assert pc.timers["newcomer"].get_elapsed() == pytest.approx(
            pc.timers["ontime"].get_elapsed(), abs=0.3
        )

    def test_newcomer_can_still_answer_the_in_flight_question(self) -> None:
        """The 0.5s late-join grace applies here too."""
        names = ["ontime"]
        pc = _paused_mid_round(29.99, names)
        names.append("newcomer")
        assert pc.resume() is True

        assert pc.timers["newcomer"].get_remaining() >= 0.49
        assert not pc.timers["newcomer"].is_expired()

    def test_players_present_at_pause_still_resume_untouched(self) -> None:
        """#295/#254 behaviour is unchanged for everyone in the snapshot."""
        names = ["a", "b"]
        pc = _paused_mid_round(10.0, names)
        assert pc.resume() is True

        for name in names:
            assert pc.timers[name].get_remaining() == pytest.approx(20.0, abs=0.2)
            assert pc.timers[name].get_elapsed() == pytest.approx(10.0, abs=0.2)

    def test_only_question_active_is_pausable_at_all(self) -> None:
        """The path this fix touches is the only one resume() can reach.

        pause() is a no-op outside QUESTION_ACTIVE, so ``paused_from`` is
        always QUESTION_ACTIVE and there is no second phase to special-case.
        """
        names = ["ontime"]
        pc = PhaseController(players_fn=lambda: list(names))
        pc.begin_round(ROUND)
        pc.enter_question_active()
        pc.phase = GamePhase.ANSWER_REVEAL
        assert pc.pause("admin_disconnected") is False


class TestLateJoinerTimerHelper:
    def test_helper_returns_none_without_a_round(self) -> None:
        pc = PhaseController(players_fn=list)
        assert pc._late_joiner_timer() is None

    def test_both_join_paths_use_the_same_clock(self) -> None:
        """Mid-question join and post-pause join must agree."""
        names = ["ontime"]
        pc = PhaseController(players_fn=lambda: list(names))
        pc.begin_round(ROUND)
        pc.enter_question_active()
        pc.round_start_time -= 12.0  # type: ignore[operator]
        pc.timers["ontime"]._start_time -= 12.0  # noqa: SLF001
        pc.add_late_joiner_timer("mid_question")

        paused = _paused_mid_round(12.0, ["other"])
        helper_timer = paused._late_joiner_timer()
        assert helper_timer is not None
        assert helper_timer.get_remaining() == pytest.approx(
            pc.timers["mid_question"].get_remaining(), abs=0.3
        )
