# TODO: Implement full game engine in Phase 3
"""Game state management for Quizify."""

from __future__ import annotations

import logging

_LOGGER = logging.getLogger(__name__)


class QuizifyGameState:
    """Manages the overall game state for a Quizify session."""

    def __init__(self) -> None:
        """Initialize game state."""
        self.game_id: str | None = None

    def start_game(self) -> None:
        """Start a new game session."""
