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

    def start(self) -> None:
        """Start the countdown timer."""
        self._start_time = time.monotonic()
        self._bonus_time = 0.0
        self._pause_remaining = 0.0

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

        Used by the freeze power-up to penalize an opponent.
        """
        self._pause_remaining += seconds

    def add_time(self, seconds: float) -> None:
        """Add bonus time to the timer.

        Used by the time_boost power-up.
        """
        self._bonus_time += seconds

    def get_elapsed(self) -> float:
        """Get elapsed time since start — used for speed bonus calculation."""
        if self._start_time is None:
            return 0.0
        return time.monotonic() - self._start_time

    def reset(self, duration: float | None = None) -> None:
        """Reset the timer, optionally with a new duration."""
        if duration is not None:
            self._duration = duration
        self._start_time = None
        self._bonus_time = 0.0
        self._pause_remaining = 0.0
