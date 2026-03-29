"""Player session management for Quizify."""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from aiohttp import web


@dataclass
class PlayerSession:
    """Represents a connected player."""

    name: str
    ws: web.WebSocketResponse
    session_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    score: int = 0
    streak: int = 0
    connected: bool = True
    is_admin: bool = False
    joined_late: bool = False
    joined_at: float = field(default_factory=time.time)
    submitted: bool = False
    current_answer: int | None = None
    submission_time: float | None = None
    round_score: int = 0

    def submit_answer(self, answer_index: int, timestamp: float) -> None:
        """Record an answer submission."""
        self.submitted = True
        self.current_answer = answer_index
        self.submission_time = timestamp

    def reset_round(self) -> None:
        """Reset round-specific state for new round."""
        self.submitted = False
        self.current_answer = None
        self.submission_time = None
        self.round_score = 0

    def reset_for_new_game(self) -> None:
        """Reset all game-level stats for a new game."""
        self.joined_late = False
        self.score = 0
        self.streak = 0
        self.reset_round()
