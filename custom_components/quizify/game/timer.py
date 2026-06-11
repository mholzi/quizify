"""Round timer management for Quizify."""

from __future__ import annotations

import time


class QuestionTimer:
    """Per-player countdown timer for a question round.

    Supports pause (freeze power-up) and extension (time_boost power-up).
    """

    def __init__(self, duration: float = 30.0) -> None:
        """Initialize the timer with a duration in seconds."""
        self._duration = duration
        self._start_time: float | None = None
        self._bonus_time: float = 0.0
        self._pause_remaining: float = 0.0
        self._pause_started_at: float | None = None

    def start(self) -> None:
        """Start the countdown timer."""
        self._start_time = time.monotonic()
        self._bonus_time = 0.0
        self._pause_remaining = 0.0
        self._pause_started_at = None

    def get_remaining(self) -> float:
        """Get remaining time in seconds."""
        if self._start_time is None:
            return self._duration
        elapsed = time.monotonic() - self._start_time
        effective_duration = self._duration + self._bonus_time
        remaining = effective_duration - elapsed + self._pause_remaining
        return max(0.0, remaining)

    def is_expired(self) -> bool:
        """Check if the timer has expired."""
        return self._start_time is not None and self.get_remaining() <= 0

    def pause_for_player(self, seconds: float) -> None:
        """Add pause credit — effectively freezes the timer for the given duration.

        Used by the freeze power-up to penalize an opponent. Records the wall-clock
        start of the freeze so the speed-bonus calculation can credit only the
        portion of the freeze that has actually elapsed (see get_elapsed / #254).
        """
        self._pause_remaining += seconds
        if self._pause_started_at is None:
            self._pause_started_at = time.monotonic()

    def add_time(self, seconds: float) -> None:
        """Add bonus time to the timer.

        Used by the time_boost power-up.
        """
        self._bonus_time += seconds

    def get_elapsed(self) -> float:
        """Get effective elapsed time since start — used for speed bonus calculation.

        Subtracts accumulated pause credit (e.g. from Freeze power-up) so that
        frozen players are not penalised with a lower speed bonus. Crucially it
        credits only the portion of the freeze that has ACTUALLY elapsed in
        wall-clock time, not the full 5s up front — otherwise a frozen player who
        answers immediately would harvest up to 5s of free speed bonus (#254).
        """
        if self._start_time is None:
            return 0.0
        now = time.monotonic()
        pause_credit = self._pause_remaining
        if self._pause_started_at is not None:
            # Only count the freeze time that has truly passed so far.
            elapsed_pause = now - self._pause_started_at
            pause_credit = min(self._pause_remaining, max(0.0, elapsed_pause))
        return max(0.0, now - self._start_time - pause_credit)

    def reset(self, duration: float | None = None) -> None:
        """Reset the timer, optionally with a new duration."""
        if duration is not None:
            self._duration = duration
        self._start_time = None
        self._bonus_time = 0.0
        self._pause_remaining = 0.0
        self._pause_started_at = None
