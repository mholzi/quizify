"""Tests for group-level adaptive difficulty calibration (#40).

Covers:
* GroupCalibrator: no-op without signal, bounded single-rung steps, smoothing
  over the window, hold in the comfortable band, clamping at easy/hard,
  ignoring zero-participant rounds.
* QuestionBank.get_next_question_at_difficulty: exact-target serving, nearest
  fallback, no-double-serve, exhaustion.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT))

from custom_components.quizify.game.calibration import GroupCalibrator  # noqa: E402
from custom_components.quizify.game.questions import (  # noqa: E402
    Answer,
    Question,
    QuestionBank,
)
from custom_components.quizify.game.types import Difficulty  # noqa: E402


def _q(qid: str, difficulty: str) -> Question:
    return Question(
        id=qid,
        question=f"q-{qid}",
        answers=[
            Answer(text="a", correct=True),
            Answer(text="b", correct=False),
            Answer(text="c", correct=False),
        ],
        difficulty=difficulty,
    )


# ----------------------------------------------------------------------
# GroupCalibrator
# ----------------------------------------------------------------------


class TestCalibratorNoSignal:
    def test_starts_at_configured_start(self) -> None:
        cal = GroupCalibrator(start=Difficulty.MEDIUM)
        assert cal.current_target == Difficulty.MEDIUM

    def test_invalid_start_falls_back_to_medium(self) -> None:
        # Passing something not on the ladder is coerced to medium.
        cal = GroupCalibrator(start="bogus")  # type: ignore[arg-type]
        assert cal.current_target == Difficulty.MEDIUM

    def test_no_target_move_before_min_rounds(self) -> None:
        cal = GroupCalibrator(start=Difficulty.MEDIUM, min_rounds=2)
        # One perfect round is not enough signal -> still medium.
        cal.record_round(correct=4, total=4)
        assert cal.next_target() == Difficulty.MEDIUM

    def test_zero_participant_rounds_carry_no_signal(self) -> None:
        cal = GroupCalibrator(start=Difficulty.MEDIUM, min_rounds=1)
        cal.record_round(correct=0, total=0)
        assert cal.observed_rounds == 0
        assert cal.average_rate() is None
        assert cal.next_target() == Difficulty.MEDIUM


class TestCalibratorStepUp:
    def test_sustained_high_rate_steps_harder(self) -> None:
        cal = GroupCalibrator(
            start=Difficulty.MEDIUM, window=3, min_rounds=2, step_up_threshold=0.8
        )
        cal.record_round(correct=4, total=4)  # 1.0
        cal.record_round(correct=4, total=4)  # 1.0
        assert cal.next_target() == Difficulty.HARD

    def test_step_is_bounded_to_one_rung(self) -> None:
        # Even with crushing performance, easy -> medium (not easy -> hard).
        cal = GroupCalibrator(
            start=Difficulty.EASY, window=3, min_rounds=2, step_up_threshold=0.8
        )
        cal.record_round(correct=5, total=5)
        cal.record_round(correct=5, total=5)
        assert cal.next_target() == Difficulty.MEDIUM

    def test_clamps_at_hard(self) -> None:
        cal = GroupCalibrator(
            start=Difficulty.HARD, window=2, min_rounds=2, step_up_threshold=0.8
        )
        cal.record_round(correct=4, total=4)
        cal.record_round(correct=4, total=4)
        assert cal.next_target() == Difficulty.HARD


class TestCalibratorStepDown:
    def test_sustained_low_rate_steps_easier(self) -> None:
        cal = GroupCalibrator(
            start=Difficulty.MEDIUM, window=3, min_rounds=2, step_down_threshold=0.4
        )
        cal.record_round(correct=0, total=4)
        cal.record_round(correct=1, total=4)  # avg = 0.125
        assert cal.next_target() == Difficulty.EASY

    def test_clamps_at_easy(self) -> None:
        cal = GroupCalibrator(
            start=Difficulty.EASY, window=2, min_rounds=2, step_down_threshold=0.4
        )
        cal.record_round(correct=0, total=4)
        cal.record_round(correct=0, total=4)
        assert cal.next_target() == Difficulty.EASY


class TestCalibratorSmoothing:
    def test_comfortable_band_holds(self) -> None:
        cal = GroupCalibrator(
            start=Difficulty.MEDIUM,
            window=3,
            min_rounds=2,
            step_up_threshold=0.8,
            step_down_threshold=0.4,
        )
        cal.record_round(correct=2, total=4)  # 0.5
        cal.record_round(correct=3, total=4)  # 0.75 ; avg 0.625
        assert cal.next_target() == Difficulty.MEDIUM

    def test_single_lucky_round_does_not_swing_within_window(self) -> None:
        # A struggling group (lots of misses) with one perfect round mixed in
        # should NOT be bumped up: the windowed average stays low.
        cal = GroupCalibrator(
            start=Difficulty.MEDIUM,
            window=3,
            min_rounds=2,
            step_up_threshold=0.8,
        )
        cal.record_round(correct=0, total=4)  # 0.0
        cal.record_round(correct=0, total=4)  # 0.0
        cal.record_round(correct=4, total=4)  # 1.0 ; avg over window = 0.33
        # avg 0.33 < step_up 0.8 -> not harder. (It IS below step_down 0.4,
        # so it steps easier, which is the correct read of a struggling group.)
        assert cal.next_target() == Difficulty.EASY

    def test_window_drops_oldest_rounds(self) -> None:
        cal = GroupCalibrator(
            start=Difficulty.MEDIUM,
            window=2,
            min_rounds=2,
            step_up_threshold=0.8,
        )
        cal.record_round(correct=0, total=4)  # 0.0  (will be dropped)
        cal.record_round(correct=4, total=4)  # 1.0
        cal.record_round(correct=4, total=4)  # 1.0 ; window now [1.0, 1.0]
        assert cal.average_rate() == pytest.approx(1.0)
        assert cal.next_target() == Difficulty.HARD


# ----------------------------------------------------------------------
# QuestionBank.get_next_question_at_difficulty
# ----------------------------------------------------------------------


def _bank_with(questions: list[Question]) -> QuestionBank:
    qb = QuestionBank()
    qb._categories = {"test": questions}
    # Build the mixed (all-difficulty) queue, as auto mode does.
    qb.reset(category="test", difficulty=None)
    return qb


class TestBankDifficultyTargeting:
    def test_serves_exact_target_difficulty(self) -> None:
        qb = _bank_with(
            [_q("e1", "easy"), _q("m1", "medium"), _q("h1", "hard")]
        )
        q = qb.get_next_question_at_difficulty("hard")
        assert q is not None
        assert q.difficulty == "hard"

    def test_does_not_serve_same_question_twice(self) -> None:
        qb = _bank_with(
            [_q("m1", "medium"), _q("m2", "medium")]
        )
        first = qb.get_next_question_at_difficulty("medium")
        second = qb.get_next_question_at_difficulty("medium")
        assert first is not None and second is not None
        assert first.id != second.id

    def test_falls_back_to_nearest_when_target_missing(self) -> None:
        # No "hard" questions: targeting hard should fall back to medium
        # (the nearest rung), not easy.
        qb = _bank_with(
            [_q("e1", "easy"), _q("m1", "medium")]
        )
        q = qb.get_next_question_at_difficulty("hard")
        assert q is not None
        assert q.difficulty == "medium"

    def test_falls_back_outward_to_easy_when_only_easy_left(self) -> None:
        qb = _bank_with([_q("e1", "easy")])
        q = qb.get_next_question_at_difficulty("hard")
        assert q is not None
        assert q.difficulty == "easy"

    def test_returns_none_when_exhausted(self) -> None:
        qb = _bank_with([_q("m1", "medium")])
        assert qb.get_next_question_at_difficulty("medium") is not None
        assert qb.get_next_question_at_difficulty("medium") is None


# ----------------------------------------------------------------------
# GameState integration: auto mode end-to-end vs. fixed difficulty
# ----------------------------------------------------------------------

from unittest.mock import MagicMock  # noqa: E402

from custom_components.quizify.game.state import (  # noqa: E402
    GamePhase,
    QuizifyGameState,
)


class _FakeRuntime:
    def __init__(self, tmp_path: Path) -> None:
        self.data_dir = tmp_path


def _fake_ws() -> MagicMock:
    ws = MagicMock()
    ws.closed = False
    return ws


def _inject_bank(state: QuizifyGameState) -> None:
    """Replace the loaded packs with a controlled, difficulty-rich set.

    Enough questions at each rung that auto mode can serve several rounds.
    """
    questions = []
    for i in range(6):
        questions.append(_q(f"e{i}", "easy"))
        questions.append(_q(f"m{i}", "medium"))
        questions.append(_q(f"h{i}", "hard"))
    bank = state._question_bank
    bank._categories = {"test": questions}
    bank._loaded = True  # make load_all_categories a cache hit


def _correct_idx(state: QuizifyGameState) -> int:
    return next(
        i for i, a in enumerate(state._current_question.answers) if a.correct
    )


def _wrong_idx(state: QuizifyGameState) -> int:
    return next(
        i for i, a in enumerate(state._current_question.answers) if not a.correct
    )


class TestAutoModeIntegration:
    def test_fixed_difficulty_creates_no_calibrator(self, tmp_path: Path) -> None:
        state = QuizifyGameState(runtime=_FakeRuntime(tmp_path), entry_id="t")
        _inject_bank(state)
        state.add_player("Alice", _fake_ws())
        state.start_game(language="de", num_rounds=5, difficulty="medium")
        assert state._calibrator is None
        # Fixed mode keeps serving the pinned difficulty.
        state.start_next_question()
        assert state._current_question.difficulty == "medium"

    def test_auto_mode_creates_calibrator_starting_medium(
        self, tmp_path: Path
    ) -> None:
        state = QuizifyGameState(runtime=_FakeRuntime(tmp_path), entry_id="t")
        _inject_bank(state)
        state.add_player("Alice", _fake_ws())
        state.start_game(language="de", num_rounds=8, difficulty="auto")
        assert state._calibrator is not None
        assert state._calibrator.current_target == Difficulty.MEDIUM
        # First served question (no signal yet) is the medium start.
        state.start_next_question()
        assert state._current_question.difficulty == "medium"

    def test_auto_mode_ramps_harder_on_strong_group(
        self, tmp_path: Path
    ) -> None:
        state = QuizifyGameState(runtime=_FakeRuntime(tmp_path), entry_id="t")
        _inject_bank(state)
        state.add_player("Alice", _fake_ws())
        state.start_game(language="de", num_rounds=8, difficulty="auto")

        served = []
        for _ in range(5):
            q = state.start_next_question()
            if q is None:
                break
            served.append(q.difficulty)
            state.submit_answer("Alice", _correct_idx(state))
            state.evaluate_round()

        # Started medium; an all-correct group should be served hard by the end.
        assert served[0] == "medium"
        assert "hard" in served
        assert state._calibrator.current_target == Difficulty.HARD

    def test_auto_mode_eases_on_struggling_group(self, tmp_path: Path) -> None:
        state = QuizifyGameState(runtime=_FakeRuntime(tmp_path), entry_id="t")
        _inject_bank(state)
        state.add_player("Alice", _fake_ws())
        state.start_game(language="de", num_rounds=8, difficulty="auto")

        served = []
        for _ in range(5):
            q = state.start_next_question()
            if q is None:
                break
            served.append(q.difficulty)
            state.submit_answer("Alice", _wrong_idx(state))
            state.evaluate_round()

        assert served[0] == "medium"
        assert "easy" in served
        assert state._calibrator.current_target == Difficulty.EASY

    def test_reset_to_lobby_clears_calibrator(self, tmp_path: Path) -> None:
        state = QuizifyGameState(runtime=_FakeRuntime(tmp_path), entry_id="t")
        _inject_bank(state)
        state.add_player("Alice", _fake_ws())
        state.start_game(language="de", num_rounds=5, difficulty="auto")
        assert state._calibrator is not None
        state.reset_to_lobby()
        assert state._calibrator is None
