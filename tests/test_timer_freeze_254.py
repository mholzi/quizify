"""Regression tests for QuestionTimer freeze-credit accounting (#254).

The freeze power-up adds a 5s pause credit to the target's timer. The
speed-bonus calculation (get_elapsed) subtracts that credit so a frozen
player isn't penalised. The bug: the FULL credit was subtracted up front,
so a frozen player who answered immediately harvested up to 5s of free
speed bonus. The fix credits only the portion of the freeze that has
actually elapsed in wall-clock time.
"""

from __future__ import annotations

import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT))

from custom_components.quizify.game.timer import QuestionTimer  # noqa: E402


class _FakeClock:
    """Deterministic monotonic clock so the tests don't sleep."""

    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def _timer_with_clock(monkeypatch, duration: float = 30.0) -> tuple[QuestionTimer, _FakeClock]:
    clock = _FakeClock()
    monkeypatch.setattr("custom_components.quizify.game.timer.time.monotonic", clock)
    timer = QuestionTimer(duration=duration)
    return timer, clock


def test_freeze_credit_counts_only_elapsed_not_full(monkeypatch) -> None:
    """A player frozen for 5s who answers 1s later must only get ~1s of
    freeze credit on their speed bonus, not the full 5s (#254)."""
    timer, clock = _timer_with_clock(monkeypatch)
    timer.start()
    clock.advance(2.0)  # 2s real elapsed before the freeze
    timer.pause_for_player(5.0)  # 5s freeze applied
    clock.advance(1.0)  # player answers 1s into the freeze

    # Real elapsed = 3.0s; only 1.0s of the 5s freeze has truly passed.
    # Effective elapsed for speed bonus = 3.0 - 1.0 = 2.0s.
    assert timer.get_elapsed() == 2.0


def test_freeze_credit_caps_at_full_duration(monkeypatch) -> None:
    """Once the entire freeze window has elapsed, the full credit applies —
    never more (no negative elapsed)."""
    timer, clock = _timer_with_clock(monkeypatch)
    timer.start()
    clock.advance(1.0)
    timer.pause_for_player(5.0)
    clock.advance(10.0)  # well past the 5s freeze window

    # Real elapsed = 11.0s; freeze credit capped at 5.0s.
    assert timer.get_elapsed() == 6.0


def test_no_free_speed_bonus_for_immediate_answer(monkeypatch) -> None:
    """The exploit: answer the instant the freeze lands. Effective elapsed
    must equal real elapsed (zero freeze credit yet)."""
    timer, clock = _timer_with_clock(monkeypatch)
    timer.start()
    clock.advance(4.0)
    timer.pause_for_player(5.0)
    # No time advances — answer is instantaneous.

    assert timer.get_elapsed() == 4.0


def test_get_elapsed_without_freeze_is_plain_elapsed(monkeypatch) -> None:
    """Sanity: with no freeze, elapsed is just wall-clock since start."""
    timer, clock = _timer_with_clock(monkeypatch)
    timer.start()
    clock.advance(7.0)
    assert timer.get_elapsed() == 7.0


def test_reset_clears_freeze_credit(monkeypatch) -> None:
    """Reset must wipe pause bookkeeping so a new round starts clean."""
    timer, clock = _timer_with_clock(monkeypatch)
    timer.start()
    clock.advance(1.0)
    timer.pause_for_player(5.0)
    timer.reset()
    timer.start()
    clock.advance(3.0)
    assert timer.get_elapsed() == 3.0
