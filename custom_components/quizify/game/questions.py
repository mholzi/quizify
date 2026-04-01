"""Question bank and data models for Quizify."""

from __future__ import annotations

import json
import logging
import random
from dataclasses import dataclass, field
from pathlib import Path

from ..const import ANSWERS_PER_QUESTION

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
    if not isinstance(answers_raw, list) or len(answers_raw) != ANSWERS_PER_QUESTION:
        _LOGGER.warning(
            "Skipping question '%s': expected %d answers, got %s",
            data["id"],
            ANSWERS_PER_QUESTION,
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
        self._pack_versions: dict[str, dict] = {}
        self._queue: list[Question] = []
        self._queue_index: int = 0
        self._loaded: bool = False
        # Question history: maps question_id -> unix timestamp last shown (or 0 if never)
        self._history: dict[str, float] = {}
        self._history_path: Path | None = None
        # Questions shown in the current game (to record at end)
        self._shown_this_game: list[str] = []

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
        pack_version = raw.get("version", "1.0")
        questions: list[Question] = []

        for entry in questions_data:
            q = _parse_question(entry, category_name)
            if q is not None:
                q.language = pack_language
                questions.append(q)

        self._categories[category] = questions
        self._pack_versions[category] = {
            "version": pack_version,
            "name": category_name,
            "language": pack_language,
            "question_count": len(questions),
        }
        _LOGGER.debug("Loaded %d questions for category '%s' (v%s)", len(questions), category, pack_version)
        return questions

    def load_all_categories(self) -> dict[str, list[Question]]:
        """Discover and load all category JSON files.

        Subsequent calls return the cached result without re-reading from disk.
        Call reload_categories() to force a fresh load.
        """
        if self._loaded:
            return dict(self._categories)

        if not self._questions_dir.is_dir():
            _LOGGER.warning("Questions directory not found: %s", self._questions_dir)
            return {}

        for file_path in sorted(self._questions_dir.glob("*.json")):
            category_slug = file_path.stem
            self.load_category(category_slug)

        self._loaded = True
        return dict(self._categories)

    def reload_categories(self) -> dict[str, list[Question]]:
        """Clear the cache and reload all categories from disk."""
        self._loaded = False
        self._categories = {}
        return self.load_all_categories()

    def get_categories(self) -> list[str]:
        """Return list of loaded category slugs."""
        return list(self._categories.keys())

    def get_pack_versions(self) -> dict[str, dict]:
        """Return metadata (version, name, language, question_count) for each loaded pack."""
        return dict(self._pack_versions)

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
        categories: list[str] | None = None,
        difficulty: str | None = None,
        language: str | None = None,
    ) -> None:
        """Reset and rebuild the question queue for a new game session.

        Pass ``categories`` (list of slugs) to restrict to a specific subset.
        Pass ``category`` (single slug) for single-category mode.
        Pass neither for mixed (all categories).
        """
        self._queue_index = 0
        self._build_queue(category, difficulty, language, categories)

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

    # ------------------------------------------------------------------
    # Question history
    # ------------------------------------------------------------------

    def load_history(self, history_path: Path) -> None:
        """Load question history from a JSON file."""
        self._history_path = history_path
        if history_path.exists():
            try:
                raw = json.loads(history_path.read_text(encoding="utf-8"))
                self._history = {k: float(v) for k, v in raw.items()}
                _LOGGER.debug("Loaded question history: %d entries", len(self._history))
            except (json.JSONDecodeError, ValueError) as exc:
                _LOGGER.warning("Failed to load question history: %s", exc)
                self._history = {}
        else:
            self._history = {}

    def save_history(self) -> None:
        """Persist question history to disk."""
        if self._history_path is None:
            return
        try:
            self._history_path.parent.mkdir(parents=True, exist_ok=True)
            self._history_path.write_text(
                json.dumps(self._history, indent=2), encoding="utf-8"
            )
        except OSError as exc:
            _LOGGER.warning("Failed to save question history: %s", exc)

    def record_shown(self, question_id: str) -> None:
        """Mark a question as shown now."""
        import time as _time
        self._history[question_id] = _time.time()
        self._shown_this_game.append(question_id)

    def flush_shown_history(self) -> None:
        """Save history at end of game and reset the shown-this-game list."""
        self._shown_this_game = []
        self.save_history()

    def _build_queue(
        self,
        category: str | None = None,
        difficulty: str | None = None,
        language: str | None = None,
        categories: list[str] | None = None,
    ) -> None:
        """Build and shuffle the internal question queue.

        Priority: ``categories`` list > single ``category`` > all (mixed).
        """
        if categories:
            pool = [q for slug in categories for q in self._categories.get(slug, [])]
        elif category is not None:
            pool = list(self._categories.get(category, []))
        else:
            pool = [q for qs in self._categories.values() for q in qs]

        if difficulty is not None:
            pool = [q for q in pool if q.difficulty == difficulty]

        if language is not None:
            pool = [q for q in pool if q.language == language]

        # Sort by history: never-shown questions first, then oldest-shown first.
        # Within each group, randomise to avoid predictable ordering.
        if self._history:
            import random as _random
            never_shown = [q for q in pool if q.id not in self._history]
            previously_shown = [q for q in pool if q.id in self._history]
            _random.shuffle(never_shown)
            previously_shown.sort(key=lambda q: self._history.get(q.id, 0))
            self._queue = never_shown + previously_shown
        else:
            self._queue = self.shuffle_questions(pool)
        self._queue_index = 0
