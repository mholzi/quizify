"""Round timer management for Quizify."""

from __future__ import annotations

import time


class QuestionTimer:
    """Per-player countdown timer for a question round.

    Supports a freeze LOCKOUT (freeze power-up) and extension (time_boost
    power-up).

    Freeze is a *lockout*, not a pause (#300, Markus' decision): while the
    freeze window is active the target cannot submit an answer, but their
    round clock keeps running and the frozen seconds count as elapsed (so
    there is no speed-bonus refund). The net effect is a real ~5s penalty —
    the target loses answer time — matching the "penalize an opponent" intent.
    This replaces the earlier pause + refund model (which made freeze a *help*
    to the target: paused clock + fully refunded speed bonus, #254).
    """

    def __init__(self, duration: float = 30.0) -> None:
        """Initialize the timer with a duration in seconds."""
        self._duration = duration
        self._start_time: float | None = None
        self._bonus_time: float = 0.0
        self._frozen_until: float | None = None

    def start(self) -> None:
        """Start the countdown timer."""
        self._start_time = time.monotonic()
        self._bonus_time = 0.0
        self._frozen_until = None

    def get_remaining(self) -> float:
        """Get remaining time in seconds.

        Freeze does NOT extend this — the clock keeps running while frozen
        (#300), so a frozen target genuinely loses answer time.
        """
        if self._start_time is None:
            return self._duration
        elapsed = time.monotonic() - self._start_time
        effective_duration = self._duration + self._bonus_time
        remaining = effective_duration - elapsed
        return max(0.0, remaining)

    def is_expired(self) -> bool:
        """Check if the timer has expired."""
        return self._start_time is not None and self.get_remaining() <= 0

    def freeze(self, seconds: float) -> None:
        """Lock the player out of submitting for ``seconds`` (freeze power-up).

        Sets a ``frozen_until`` timestamp. While ``is_frozen()`` is true the
        server rejects the target's ``submit_answer`` (#300). The round clock
        is NOT paused and the frozen time is NOT refunded from the speed bonus —
        the lockout costs the target real answer time. Re-freezing extends the
        lockout to whichever end-time is later (it never shortens an active one).
        """
        new_until = time.monotonic() + max(0.0, seconds)
        if self._frozen_until is None or new_until > self._frozen_until:
            self._frozen_until = new_until

    def is_frozen(self) -> bool:
        """True while the freeze lockout window is still active."""
        return self._frozen_until is not None and time.monotonic() < self._frozen_until

    def frozen_remaining(self) -> float:
        """Seconds left on the freeze lockout (0.0 if not frozen)."""
        if self._frozen_until is None:
            return 0.0
        return max(0.0, self._frozen_until - time.monotonic())

    def add_time(self, seconds: float) -> None:
        """Add bonus time to the timer.

        Used by the time_boost power-up.
        """
        self._bonus_time += seconds

    def get_elapsed(self) -> float:
        """Get effective elapsed time since start — used for speed bonus calculation.

        Plain wall-clock elapsed since start. Freeze does NOT subtract a credit
        (#300): frozen seconds count as elapsed, so a frozen player gets NO
        speed-bonus refund. This is the deliberate penalty — it reverses the
        earlier refund model (#254) that made freeze help the target.
        """
        if self._start_time is None:
            return 0.0
        return max(0.0, time.monotonic() - self._start_time)

    @classmethod
    def resumed(cls, remaining: float, elapsed: float) -> "QuestionTimer":
        """Build a running timer that reports a known *remaining* AND *elapsed*.

        Used by the pause/resume path (#295). A naive ``QuestionTimer(remaining)``
        re-``start()``ed at resume time would report ``get_elapsed() ≈ 0`` —
        inflating the speed bonus, because scoring credits speed off elapsed.
        This reconstructs the internal clock so that, immediately after resume:

        * ``get_remaining()`` == *remaining* (the time frozen at pause), and
        * ``get_elapsed()``  == *elapsed* (the time already spent pre-pause),

        and both then advance normally as wall-clock runs. With no bonus,
        ``get_remaining() = _duration - (now - _start_time)`` and
        ``get_elapsed() = now - _start_time``; setting ``_start_time = now -
        elapsed`` and ``_duration = remaining + elapsed`` satisfies both.
        """
        timer = cls(duration=max(0.0, remaining) + max(0.0, elapsed))
        timer._start_time = time.monotonic() - max(0.0, elapsed)
        timer._bonus_time = 0.0
        timer._frozen_until = None
        return timer

    def reset(self, duration: float | None = None) -> None:
        """Reset the timer, optionally with a new duration."""
        if duration is not None:
            self._duration = duration
        self._start_time = None
        self._bonus_time = 0.0
        self._frozen_until = None
