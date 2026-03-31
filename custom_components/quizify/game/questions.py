"""Question bank and data models for Quizify."""

from __future__ import annotations

import json
import logging
import random
from dataclasses import dataclass, field
from pathlib import Path

_LOGGER = logging.getLogger(__name__)

QUESTIONS_DIR = Path(__file__).resolve().parent.parent / "questions"


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
    language: str = "de"


def _parse_question(data: dict, category_name: str) -> Question | None:
    """Parse a single question dict into a Question, or None on invalid data."""
    required = ("id", "question", "answers")
    for key in required:
        if key not in data:
            _LOGGER.warning(
                "Skipping question in '%s': missing field '%s'",
                category_name,
                key,
            )
            return None

    answers_raw = data["answers"]
    if not isinstance(answers_raw, list) or len(answers_raw) != 3:
        _LOGGER.warning(
            "Skipping question '%s': expected 3 answers, got %s",
            data["id"],
            len(answers_raw) if isinstance(answers_raw, list) else type(answers_raw),
        )
        return None

    correct_count = sum(1 for a in answers_raw if a.get("correct"))
    if correct_count != 1:
        _LOGGER.warning(
            "Skipping question '%s': expected 1 correct answer, got %d",
            data["id"],
            correct_count,
        )
        return None

    answers = [Answer(text=a["text"], correct=bool(a.get("correct", False))) for a in answers_raw]

    return Question(
        id=data["id"],
        question=data["question"],
        answers=answers,
        difficulty=data.get("difficulty", "medium"),
        fun_fact=data.get("fun_fact", ""),
        category=data.get("category", category_name),
    )


class QuestionBank:
    """Manages loading and serving questions from JSON files."""

    def __init__(self, questions_dir: Path | None = None) -> None:
        """Initialize the question bank."""
        self._questions_dir = questions_dir or QUESTIONS_DIR
        self._categories: dict[str, list[Question]] = {}
        self._queue: list[Question] = []
        self._queue_index: int = 0

    def load_category(self, category: str) -> list[Question]:
        """Load questions for a single category from its JSON file."""
        file_path = self._questions_dir / f"{category}.json"
        if not file_path.is_file():
            _LOGGER.warning("Category file not found: %s", file_path)
            return []

        try:
            raw = json.loads(file_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            _LOGGER.error("Invalid JSON in '%s': %s", file_path, exc)
            return []

        questions_data = raw.get("questions", [])
        category_name = raw.get("name", category)
        pack_language = raw.get("language", "de")
        questions: list[Question] = []

        for entry in questions_data:
            q = _parse_question(entry, category_name)
            if q is not None:
                q.language = pack_language
                questions.append(q)

        self._categories[category] = questions
        _LOGGER.debug("Loaded %d questions for category '%s'", len(questions), category)
        return questions

    def load_all_categories(self) -> dict[str, list[Question]]:
        """Discover and load all category JSON files."""
        if not self._questions_dir.is_dir():
            _LOGGER.warning("Questions directory not found: %s", self._questions_dir)
            return {}

        for file_path in sorted(self._questions_dir.glob("*.json")):
            category_slug = file_path.stem
            self.load_category(category_slug)

        return dict(self._categories)

    def get_categories(self) -> list[str]:
        """Return list of loaded category slugs."""
        return list(self._categories.keys())

    def get_next_question(
        self, category: str | None = None, difficulty: str | None = None
    ) -> Question | None:
        """Get the next question from the queue, advancing the index."""
        if not self._queue:
            self._build_queue(category, difficulty)

        if self._queue_index >= len(self._queue):
            return None

        question = self._queue[self._queue_index]
        self._queue_index += 1
        return question

    def shuffle_questions(self, questions: list[Question]) -> list[Question]:
        """Return a shuffled copy of the given question list."""
        shuffled = list(questions)
        random.shuffle(shuffled)
        return shuffled

    def reset(
        self,
        category: str | None = None,
        difficulty: str | None = None,
        language: str | None = None,
    ) -> None:
        """Reset and rebuild the question queue for a new game session."""
        self._queue_index = 0
        self._build_queue(category, difficulty, language)

    def get_question_count(
        self, category: str, difficulty: str | None = None
    ) -> int:
        """Return the number of questions for a category, optionally filtered by difficulty."""
        questions = self._categories.get(category, [])
        if difficulty is not None:
            return sum(1 for q in questions if q.difficulty == difficulty)
        return len(questions)

    def validate_answer(self, question: Question, answer_index: int) -> bool:
        """Check whether the given answer index is correct."""
        if not 0 <= answer_index < len(question.answers):
            return False
        return question.answers[answer_index].correct

    def get_correct_answer(self, question: Question) -> Answer:
        """Return the correct Answer for a question."""
        for answer in question.answers:
            if answer.correct:
                return answer
        # Should never happen with validated questions, but be safe.
        return question.answers[0]

    def _build_queue(
        self,
        category: str | None = None,
        difficulty: str | None = None,
        language: str | None = None,
    ) -> None:
        """Build and shuffle the internal question queue."""
        if category is not None:
            pool = list(self._categories.get(category, []))
        else:
            pool = [q for qs in self._categories.values() for q in qs]

        if difficulty is not None:
            pool = [q for q in pool if q.difficulty == difficulty]

        if language is not None:
            pool = [q for q in pool if q.language == language]

        self._queue = self.shuffle_questions(pool)
        self._queue_index = 0
