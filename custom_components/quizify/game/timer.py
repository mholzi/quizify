# TODO: Implement full timer in Phase 3
"""Round timer management for Quizify."""

from __future__ import annotations

import time


class QuestionTimer:
    """Manages the countdown timer for each question round."""

    def __init__(self, duration: int = 30) -> None:
        """Initialize the timer with a duration in seconds."""
        self._duration = duration
        self._start_time: float | None = None

    def start(self) -> None:
        """Start the countdown timer."""
        self._start_time = time.time()

    def get_remaining(self) -> float:
        """Get remaining time in seconds."""
        if self._start_time is None:
            return float(self._duration)
        elapsed = time.time() - self._start_time
        return max(0.0, self._duration - elapsed)

    def is_expired(self) -> bool:
        """Check if the timer has expired."""
        return self.get_remaining() <= 0
