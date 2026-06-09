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
    last_answer_correct: bool = False  # cached at submit_answer time, read in _do_evaluate_round
    # Elapsed seconds between round start and this player's submission.
    # Captured in submit_answer so per-question stats can record honest
    # times without re-deriving from speed_bonus arithmetic.
    last_elapsed: float = 0.0
    round_score: int = 0
    round_score_breakdown: dict = field(default_factory=dict)  # speed_bonus, streak_bonus, diff_mult
    round_history: list[str] = field(default_factory=list)

    # Superlative tracking
    answer_times: list[float] = field(default_factory=list)  # elapsed per correct answer
    round_scores: list[int] = field(default_factory=list)  # score per round
    max_streak: int = 0
    freezes_used: int = 0
    powerups_used: int = 0  # any type — shown on finale "POWER-UPS GENUTZT"
    hard_score: int = 0

    # Rounds the player actually participated in (incremented in _do_evaluate_round).
    # Used by get_average_score() to exclude players who haven't played a round yet,
    # so a late joiner doesn't inherit a 0 average inflated by other late joiners.
    rounds_played: int = 0

    # Wager-round bookkeeping (gameplay idea #3, Jeopardy-style final round).
    # Holds the player's wager as a PERCENT of their current score (0-100).
    # None = no wager submitted yet; on the final round, the wager overrides
    # normal scoring (+wager% if correct, -wager% if wrong). Reset at the
    # start of every round so the value can't leak across rounds.
    wager: int | None = None

    # Reaction-bonus bookkeeping (gameplay idea #11). Set of round numbers
    # in which this player has already granted a +1 reaction bonus —
    # enforces "one bonus per reactor per round" so spam-clicking emojis
    # can't farm points for everyone.
    reaction_bonuses_given: set[int] = field(default_factory=set)

    # Inbound side of the same mechanic: round_number -> count of +1 bonuses
    # this player has RECEIVED that round, capped per round. Previously stashed
    # as a dynamic attribute by the reaction handler; promoted to a real field
    # so reset_for_new_game() clears it. Without the reset it persisted across
    # games and — because round numbers restart at 1 each game — a player who
    # hit the cap in round N of the old game was wrongly blocked from receiving
    # bonuses in round N of the new game (#167).
    _reaction_bonuses_received: dict[int, int] = field(default_factory=dict)

    # Streak milestone bookkeeping. Cumulative bonus this game + count of
    # discrete milestone hits (3, 5, 10, ...). Surfaced on the finale stats
    # panel and rolled into all-time stats; reset in reset_for_new_game.
    streak_milestone_bonus_total: int = 0
    streak_milestones_hit: int = 0

    @property
    def is_active(self) -> bool:
        """True only if the player is genuinely still connected.

        ``connected`` alone is not enough: a dropped/closed WebSocket whose
        ``_handle_disconnect`` has not run yet leaves a stale ``connected =
        True`` ghost. Counting such a ghost as an active participant blocks
        all-submitted early reveal for the whole room.
        """
        return self.connected and self.ws is not None and not self.ws.closed

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
        self.last_answer_correct = False
        self.round_score = 0
        self.round_score_breakdown = {}
        # Wager is per-round — clear so the previous round's wager doesn't
        # accidentally apply on the next final-round attempt (replays etc.).
        self.wager = None

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
        self.powerups_used = 0
        self.hard_score = 0
        self.rounds_played = 0
        self.streak_milestone_bonus_total = 0
        self.streak_milestones_hit = 0
        self.wager = None
        self.reaction_bonuses_given = set()
        self._reaction_bonuses_received = {}
        self.reset_round()
