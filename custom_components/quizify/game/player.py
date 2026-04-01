"""Player session management for Quizify."""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from aiohttp import web


# Palette of distinct, accessible colours for player identification
PLAYER_COLORS = [
    "#FF6B6B",  # coral red
    "#4ECDC4",  # teal
    "#45B7D1",  # sky blue
    "#96CEB4",  # sage green
    "#FFEAA7",  # soft yellow
    "#DDA0DD",  # plum
    "#98D8C8",  # mint
    "#F7DC6F",  # gold
    "#BB8FCE",  # lavender
    "#F0A500",  # amber
    "#6BCB77",  # green
    "#FF9F43",  # orange
    "#A29BFE",  # periwinkle
    "#FD79A8",  # pink
    "#74B9FF",  # light blue
    "#55EFC4",  # aquamarine
    "#FDCB6E",  # mango
    "#E17055",  # terracotta
    "#00CEC9",  # cyan
    "#6C5CE7",  # purple
]


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
    color: str = ""  # assigned on join from PLAYER_COLORS palette
    joined_late: bool = False
    joined_at: float = field(default_factory=time.time)
    submitted: bool = False
    current_answer: int | None = None
    submission_time: float | None = None
    round_score: int = 0
    round_score_breakdown: dict = field(default_factory=dict)  # speed_bonus, streak_bonus, diff_mult
    round_history: list[str] = field(default_factory=list)

    # Superlative tracking
    answer_times: list[float] = field(default_factory=list)  # elapsed per correct answer
    round_scores: list[int] = field(default_factory=list)  # score per round
    max_streak: int = 0
    freezes_used: int = 0
    hard_score: int = 0

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
        self.round_score_breakdown = {}

    def record_round_result(self, result: str) -> None:
        """Record the result of a round ('correct', 'wrong', or 'timeout')."""
        self.round_history.append(result)

    def reset_for_new_game(self) -> None:
        """Reset all game-level stats for a new game."""
        self.joined_late = False
        self.score = 0
        self.streak = 0
        self.round_history = []
        self.answer_times = []
        self.round_scores = []
        self.max_streak = 0
        self.freezes_used = 0
        self.hard_score = 0
        self.reset_round()
