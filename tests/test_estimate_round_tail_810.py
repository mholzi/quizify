"""One round-closing tail, and estimate rounds feed auto-difficulty (#810).

``_do_evaluate_round`` and ``_evaluate_estimate_round`` each carried their own
copy of the round-closing sequence — build the ``AnswerResult``s, take the
leaderboard, construct the ``RoundSummary``, flip to ANSWER_REVEAL, record the
question stats, clear ``joined_late``, log, broadcast. The estimate copy's
docstring claimed it mirrored the MC one and did not: the ``GroupCalibrator``
feed (#40/#302) existed only in the MC branch. Since #566 every pack carries
estimate questions, so an auto-mode room contributed no signal on those rounds
and the target never advanced for them.

The tail is now ``_close_round``, called by both. The property worth pinning is
not "the two agree today" but "there is only one of them".

**The correctness reading, decided here:** an exact hit stays what the reveal
and the per-question stats mean by correct, and the calibrator gets a different
reading — whether the guess landed inside
``ESTIMATE_CALIBRATION_BAND`` of the question's slider span. Distance zero on a
slider is close to unattainable, so feeding ``exact`` to the calibrator would
have scored every estimate round 0/N and walked an auto-mode game down to EASY
and held it there. That would be a worse bug than the missing feed, not a fix
for it.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import MagicMock

from custom_components.quizify.game.scoring import (
    ESTIMATE_CALIBRATION_BAND,
    estimate_within_calibration_band,
)
from custom_components.quizify.game.state import GamePhase, QuizifyGameState


class _Runtime:
    def __init__(self, tmp_path: Path) -> None:
        self.data_dir = tmp_path

    async def run_in_executor(self, func, *args):  # noqa: ANN001, ANN002
        return func(*args)

    def create_task(self, coro):  # noqa: ANN001
        return asyncio.ensure_future(coro)


def _ws() -> MagicMock:
    ws = MagicMock()
    ws.closed = False
    return ws


def _auto_game(tmp_path: Path, category: str) -> QuizifyGameState:
    """An "auto"-difficulty game, so ``_calibrator`` is live."""
    gs = QuizifyGameState(runtime=_Runtime(tmp_path), entry_id="t")
    for name in ("Anna", "Mira"):
        gs.add_player(name, _ws())
    gs.start_game(
        category=category,
        difficulty="auto",
        num_rounds=5,
        language="en",
        lightning_enabled=False,
        hot_seat_enabled=False,
    )
    assert gs._calibrator is not None, "auto mode did not arm the calibrator"
    gs.start_next_question()
    return gs


class _SpyCalibrator:
    """Records what it was fed, then delegates nothing — the target holds."""

    def __init__(self, real) -> None:  # noqa: ANN001
        self._real = real
        self.rounds: list[tuple[int, int]] = []

    def record_round(self, correct: int, total: int) -> None:
        self.rounds.append((correct, total))
        self._real.record_round(correct=correct, total=total)

    def next_target(self):  # noqa: ANN202
        return self._real.next_target()

    def average_rate(self):  # noqa: ANN202
        return self._real.average_rate()

    @property
    def current_target(self):  # noqa: ANN202
        return self._real.current_target


# --------------------------------------------------------------------------
# The bug: an auto-mode estimate round produced no signal at all
# --------------------------------------------------------------------------


def test_an_auto_mode_estimate_round_feeds_the_calibrator(
    tmp_path: Path,
) -> None:
    game = _auto_game(tmp_path, "estimation-en")
    spy = _SpyCalibrator(game._calibrator)
    game._calibrator = spy

    question = game.get_current_question()
    assert question.is_estimate
    game.submit_guess("Anna", question.estimate_answer)
    game.submit_guess("Mira", question.estimate_answer)
    game.evaluate_round()

    assert spy.rounds, "the estimate round fed the calibrator nothing"
    assert spy.rounds == [(2, 2)]


def test_a_room_missing_by_a_mile_is_a_signal_too(tmp_path: Path) -> None:
    """Not just "record_round was called" — the number has to mean something."""
    game = _auto_game(tmp_path, "estimation-en")
    spy = _SpyCalibrator(game._calibrator)
    game._calibrator = spy

    question = game.get_current_question()
    span = question.estimate_max - question.estimate_min
    game.submit_guess("Anna", question.estimate_min)
    game.submit_guess("Mira", question.estimate_max)
    game.evaluate_round()

    # At least one of the two extremes is more than a tenth of the span away
    # from any answer inside the range, so the room cannot have scored 2/2.
    assert spy.rounds and spy.rounds[0][1] == 2
    assert spy.rounds[0][0] < 2, (
        f"a guess {span:.0f} wide counted as correct: {spy.rounds}"
    )


def test_the_reveal_still_calls_only_an_exact_hit_correct(
    tmp_path: Path,
) -> None:
    """The calibration band is for the calibrator alone; the reveal is unchanged."""
    game = _auto_game(tmp_path, "estimation-en")
    question = game.get_current_question()
    span = question.estimate_max - question.estimate_min

    # Inside the band, but not the value: correct for calibration, wrong on
    # the number line.
    close = question.estimate_answer + span * ESTIMATE_CALIBRATION_BAND / 2
    close = min(close, question.estimate_max)
    game.submit_guess("Anna", close)
    game.submit_guess("Mira", question.estimate_min)
    game.evaluate_round()  # a no-op when the last guess already triggered it

    summary = game.get_round_summary()
    anna = next(r for r in summary.results if r.player_id == "Anna")
    assert anna.correct is False
    assert game.get_player("Anna").round_history[-1] == "wrong"


def test_the_calibration_band_is_relative_to_the_slider_span() -> None:
    assert estimate_within_calibration_band(9.0, 100.0) is True
    assert estimate_within_calibration_band(11.0, 100.0) is False
    # No usable span → nothing to judge closeness against, so exact only.
    assert estimate_within_calibration_band(0.0, None) is True
    assert estimate_within_calibration_band(1.0, None) is False
    assert estimate_within_calibration_band(1.0, 0.0) is False


# --------------------------------------------------------------------------
# The structural half: one tail, two callers
# --------------------------------------------------------------------------


def test_both_evaluators_close_the_round_through_the_same_helper(
    tmp_path: Path,
) -> None:
    """Two blocks documented as mirrors drifted anyway. Now there is one."""
    calls: list[str] = []
    original = QuizifyGameState._close_round

    for category in ("picture-round-en", "estimation-en"):
        game = _auto_game(tmp_path, category)

        def _spy(self, *a, _cat=category, **kw):  # noqa: ANN001, ANN002, ANN003
            calls.append(_cat)
            return original(self, *a, **kw)

        game._close_round = _spy.__get__(game)
        before = len(calls)

        question = game.get_current_question()
        if question.is_estimate:
            game.submit_guess("Anna", question.estimate_answer)
        else:
            correct = next(i for i, a in enumerate(question.answers) if a.correct)
            game.submit_answer("Anna", correct)
        game.evaluate_round()

        assert len(calls) == before + 1, f"{category} did not use the helper"


def test_the_estimate_round_still_transitions_and_broadcasts(
    tmp_path: Path,
) -> None:
    """The rest of the tail the estimate path already had must survive."""
    fired: list[str] = []
    game = _auto_game(tmp_path, "estimation-en")
    game._fire_broadcast = lambda event, **kw: fired.append(event)  # noqa: ARG005

    question = game.get_current_question()
    game.get_player("Anna").joined_late = True
    game.submit_guess("Anna", question.estimate_answer)
    game.evaluate_round()
    summary = game.get_round_summary()

    assert game.phase == GamePhase.ANSWER_REVEAL
    assert "round_evaluated" in fired
    assert summary.estimate is not None, "the number-line block went missing"
    assert summary.correct_answer.text
    assert game.get_player("Anna").joined_late is False
    assert {r.player_id for r in summary.results} == {"Anna", "Mira"}
