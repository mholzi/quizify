"""Server-level regression tests for the freeze LOCKOUT (#300).

Markus' decision: the freeze power-up is a *lockout*, not a pause+refund.
While frozen, the target (a) CANNOT submit an answer — the server rejects it,
(b) their round clock keeps running (no extension), and (c) gets NO speed-bonus
refund (frozen time counts as elapsed). This is the real ~5s penalty matching
the "penalize an opponent" intent. Previously freeze HELPED the target.

These tests exercise the full state path (submit_answer rejection) on top of
the QuestionTimer-level tests in test_timer_freeze_254.py.
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT))

from custom_components.quizify.const import ERR_FROZEN  # noqa: E402
from custom_components.quizify.game.state import (  # noqa: E402
    GamePhase,
    QuizifyGameState,
)
from custom_components.quizify.game.state import AnswerResult  # noqa: E402


class _FakeRuntime:
    def __init__(self, tmp_path: Path) -> None:
        self.data_dir = tmp_path


def _fake_ws() -> MagicMock:
    ws = MagicMock()
    ws.closed = False
    return ws


class _FakeClock:
    """Deterministic monotonic clock for the timer module."""

    def __init__(self) -> None:
        self.now = 5000.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


@pytest.fixture
def state(tmp_path: Path) -> QuizifyGameState:
    return QuizifyGameState(runtime=_FakeRuntime(tmp_path), entry_id="test")


@pytest.fixture
def clock(monkeypatch) -> _FakeClock:
    """Drive QuestionTimer's monotonic clock so freezes are deterministic."""
    c = _FakeClock()
    monkeypatch.setattr("custom_components.quizify.game.timer.time.monotonic", c)
    return c


def _start_active_round(state: QuizifyGameState) -> None:
    state.add_player("Alice", _fake_ws())
    state.add_player("Bob", _fake_ws())
    state.start_game(language="de", num_rounds=3, timer_duration=30)
    state.start_next_question()
    assert state.phase == GamePhase.QUESTION_ACTIVE


def test_frozen_player_submit_is_rejected(state: QuizifyGameState, clock: _FakeClock) -> None:
    """A frozen player's submit_answer is rejected with ERR_FROZEN during the
    freeze window."""
    _start_active_round(state)
    state._timers["Bob"].freeze(5.0)

    result = state.submit_answer("Bob", 0)

    assert result == ERR_FROZEN
    assert state._player_registry.get_player("Bob").submitted is False


def test_submit_accepted_after_freeze_expires(state: QuizifyGameState, clock: _FakeClock) -> None:
    """Once the freeze window passes, the same player may submit normally."""
    _start_active_round(state)
    state._timers["Bob"].freeze(5.0)
    assert state.submit_answer("Bob", 0) == ERR_FROZEN

    clock.advance(5.1)  # freeze window elapsed (timer not expired: only ~5s)

    result = state.submit_answer("Bob", 0)
    assert isinstance(result, AnswerResult)
    assert state._player_registry.get_player("Bob").submitted is True


def test_freeze_does_not_extend_target_clock(state: QuizifyGameState, clock: _FakeClock) -> None:
    """The freeze must NOT pause/extend the target's round clock (#300)."""
    _start_active_round(state)
    timer = state._timers["Bob"]
    clock.advance(2.0)
    remaining_before = timer.get_remaining()  # 28.0
    timer.freeze(5.0)
    clock.advance(5.0)

    # Clock kept running through the freeze: 7s gone → 23s left, not 28s.
    assert timer.get_remaining() == pytest.approx(23.0)
    assert remaining_before == pytest.approx(28.0)


def test_frozen_then_late_answer_gets_no_speed_refund(
    state: QuizifyGameState, clock: _FakeClock
) -> None:
    """A player frozen then answering after the window gets NO speed-bonus
    refund: their elapsed includes the frozen time (#300)."""
    _start_active_round(state)
    timer = state._timers["Bob"]
    clock.advance(1.0)
    timer.freeze(5.0)
    assert state.submit_answer("Bob", 0) == ERR_FROZEN
    clock.advance(5.0)  # total 6s elapsed, freeze just ended

    state.submit_answer("Bob", 0)
    bob = state._player_registry.get_player("Bob")

    # last_elapsed reflects full wall-clock (6s), not a freeze-discounted value.
    assert bob.last_elapsed == pytest.approx(6.0)
