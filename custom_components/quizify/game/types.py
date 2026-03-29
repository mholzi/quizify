"""Shared types for the game package."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class RoundResult:
    """Result data calculated at end of each round for reveal display."""

    correct_answer_index: int = 0
    correct_answer_text: str = ""
    fun_fact: str = ""
    players_correct: list[str] = field(default_factory=list)
    players_wrong: list[str] = field(default_factory=list)
    fastest_player: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """Convert to JSON-serializable dictionary."""
        return {
            "correct_answer_index": self.correct_answer_index,
            "correct_answer_text": self.correct_answer_text,
            "fun_fact": self.fun_fact,
            "players_correct": self.players_correct,
            "players_wrong": self.players_wrong,
            "fastest_player": self.fastest_player,
        }
