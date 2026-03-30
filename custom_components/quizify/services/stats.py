"""Game statistics tracking service for Quizify."""

from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from ..analytics import QuizifyAnalytics

_LOGGER = logging.getLogger(__name__)


class StatsService:
    """Tracks game statistics and records to analytics storage."""

    def __init__(self, analytics: QuizifyAnalytics) -> None:
        """Initialize stats service."""
        self._analytics = analytics

    async def record_game(
        self,
        game_id: str,
        category: str | None,
        difficulty: str,
        num_rounds: int,
        players: dict[str, int],
        duration_seconds: int,
        started_at: int | None = None,
    ) -> None:
        """Record a completed game to analytics.

        Args:
            game_id: Unique game identifier
            category: Category played (or 'mixed')
            difficulty: Difficulty level
            num_rounds: Number of rounds played
            players: Dict of player_name -> final_score
            duration_seconds: Game duration in seconds
            started_at: Unix timestamp of game start (optional)
        """
        now = int(time.time())
        player_count = len(players)
        total_score = sum(players.values())
        avg_score = total_score / player_count if player_count > 0 else 0

        # Determine winner
        winner = max(players, key=players.get) if players else ""  # type: ignore[arg-type]

        record = {
            "game_id": game_id,
            "started_at": started_at or (now - duration_seconds),
            "ended_at": now,
            "duration_seconds": duration_seconds,
            "player_count": player_count,
            "category": category or "mixed",
            "question_count": num_rounds,
            "rounds_played": num_rounds,
            "average_score": round(avg_score, 1),
            "difficulty": difficulty,
            "player_scores": players,
            "winner": winner,
        }

        await self._analytics.add_game(record)

        _LOGGER.info(
            "Game %s recorded: %d players, %d rounds, winner=%s",
            game_id,
            player_count,
            num_rounds,
            winner,
        )
