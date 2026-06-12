"""Regression tests for QuestionTimer freeze LOCKOUT semantics (#300).

Freeze is now a *lockout*, not a pause+refund (Markus' decision, #300). While
the freeze window is active the target cannot submit, but their round clock
keeps running and the frozen seconds count as elapsed — so there is NO
speed-bonus refund. This reverses the earlier pause + refund model (#254),
which made freeze HELP the target (paused clock + full speed-bonus refund).

These tests assert the lockout invariants directly on QuestionTimer:
* the clock is NOT extended by a freeze,
* elapsed (speed-bonus input) is NOT reduced by a freeze,
* is_frozen() reports the lockout window correctly,
* reset() clears the freeze.
The submit-rejection side (server rejects a frozen player's submit_answer)
lives in test_freeze_lockout_300.py.
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


def test_freeze_does_not_extend_the_clock(monkeypatch) -> None:
    """A freeze must NOT pause/extend get_remaining — the clock keeps running
    underneath, so the lockout costs the target real answer time (#300)."""
    timer, clock = _timer_with_clock(monkeypatch, duration=30.0)
    timer.start()
    clock.advance(2.0)
    remaining_before = timer.get_remaining()  # 28.0
    timer.freeze(5.0)
    clock.advance(5.0)  # the whole freeze window elapses

    # Without lockout extension, 7s have passed → 23s remain. (Were this still
    # a pause it would report 28s — the bug being fixed.)
    assert timer.get_remaining() == 23.0
    assert remaining_before == 28.0


def test_freeze_gives_no_speed_bonus_refund(monkeypatch) -> None:
    """Frozen seconds count as elapsed — get_elapsed (the speed-bonus input)
    is NOT reduced by a freeze (#300, reverses the #254 refund)."""
    timer, clock = _timer_with_clock(monkeypatch)
    timer.start()
    clock.advance(2.0)
    timer.freeze(5.0)
    clock.advance(5.0)  # answer right after the freeze window

    # 7s of real wall-clock elapsed, no freeze credit subtracted.
    assert timer.get_elapsed() == 7.0


def test_is_frozen_reports_lockout_window(monkeypatch) -> None:
    """is_frozen() is true during the window and false once it passes."""
    timer, clock = _timer_with_clock(monkeypatch)
    timer.start()
    assert timer.is_frozen() is False
    timer.freeze(5.0)
    assert timer.is_frozen() is True
    clock.advance(4.9)
    assert timer.is_frozen() is True
    clock.advance(0.2)  # past the 5s window
    assert timer.is_frozen() is False


def test_frozen_remaining_counts_down(monkeypatch) -> None:
    """frozen_remaining() reports the seconds left on the lockout, 0 when over."""
    timer, clock = _timer_with_clock(monkeypatch)
    timer.start()
    assert timer.frozen_remaining() == 0.0
    timer.freeze(5.0)
    assert timer.frozen_remaining() == 5.0
    clock.advance(3.0)
    assert timer.frozen_remaining() == 2.0
    clock.advance(5.0)
    assert timer.frozen_remaining() == 0.0


def test_refreeze_extends_to_later_end_only(monkeypatch) -> None:
    """Re-freezing extends the lockout to the later end-time; a shorter
    re-freeze never shortens an active one."""
    timer, clock = _timer_with_clock(monkeypatch)
    timer.start()
    timer.freeze(5.0)
    clock.advance(1.0)
    # A 1s re-freeze ends at now+1 = 4s left on the original → keep the longer.
    timer.freeze(1.0)
    assert timer.frozen_remaining() == 4.0
    # A 10s re-freeze extends the window.
    timer.freeze(10.0)
    assert timer.frozen_remaining() == 10.0


def test_get_elapsed_without_freeze_is_plain_elapsed(monkeypatch) -> None:
    """Sanity: with no freeze, elapsed is just wall-clock since start."""
    timer, clock = _timer_with_clock(monkeypatch)
    timer.start()
    clock.advance(7.0)
    assert timer.get_elapsed() == 7.0


def test_reset_clears_freeze(monkeypatch) -> None:
    """Reset must wipe the freeze so a new round starts clean."""
    timer, clock = _timer_with_clock(monkeypatch)
    timer.start()
    clock.advance(1.0)
    timer.freeze(5.0)
    timer.reset()
    timer.start()
    assert timer.is_frozen() is False
    clock.advance(3.0)
    assert timer.get_elapsed() == 3.0
