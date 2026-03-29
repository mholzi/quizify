# TODO: Implement full question loading in Phase 2
"""Question bank and data models for Quizify."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

_LOGGER = logging.getLogger(__name__)


@dataclass
class Answer:
    """A single answer option."""

    text: str
    correct: bool


@dataclass
class Question:
    """A quiz question with multiple choice answers."""

    id: str
    question: str
    answers: list[Answer] = field(default_factory=list)
    difficulty: str = "medium"
    fun_fact: str = ""
    category: str = ""


class QuestionBank:
    """Manages loading and serving questions from JSON files."""

    def __init__(self) -> None:
        """Initialize the question bank."""
        self._questions: list[Question] = []
        self._categories: list[str] = []

    def load_categories(self) -> list[str]:
        """Load available question categories from disk."""
        return []

    def get_next_question(self) -> Question | None:
        """Get the next question for the current round."""
        return None
