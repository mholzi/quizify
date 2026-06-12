"""Shared types for the game package."""

from __future__ import annotations

from enum import Enum


class Difficulty(str, Enum):  # noqa: UP042 — StrEnum changes str()/serialization
    """Question difficulty levels."""

    EASY = "easy"
    MEDIUM = "medium"
    HARD = "hard"


TIME_LIMITS: dict[Difficulty, int] = {
    Difficulty.EASY: 20,
    Difficulty.MEDIUM: 15,
    Difficulty.HARD: 10,
}

DIFFICULTY_MULTIPLIERS: dict[Difficulty, float] = {
    Difficulty.EASY: 1.0,
    Difficulty.MEDIUM: 1.5,
    Difficulty.HARD: 2.0,
}
