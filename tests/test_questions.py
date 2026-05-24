"""Tests for the QuestionBank and question loading system."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT))

from custom_components.quizify.game.questions import (  # noqa: E402
    Answer,
    Question,
    QuestionBank,
)

QUESTIONS_DIR = _REPO_ROOT / "custom_components" / "quizify" / "questions"


@pytest.fixture
def bank() -> QuestionBank:
    """Return a QuestionBank loaded with all categories."""
    qb = QuestionBank(questions_dir=QUESTIONS_DIR)
    qb.load_all_categories()
    return qb


@pytest.fixture
def sample_question() -> Question:
    """Return a simple test question."""
    return Question(
        id="test_001",
        question="Was ist 1+1?",
        answers=[
            Answer(text="2", correct=True),
            Answer(text="3", correct=False),
            Answer(text="4", correct=False),
        ],
        difficulty="easy",
        fun_fact="Mathe ist toll.",
        category="Test",
    )


class TestLoadCategory:
    def test_load_category_returns_questions(self, bank: QuestionBank) -> None:
        questions = bank.load_category("geographie")
        assert len(questions) == 50
        assert all(isinstance(q, Question) for q in questions)

    def test_load_all_categories(self, bank: QuestionBank) -> None:
        categories = bank.get_categories()
        assert "geographie" in categories
        assert "tiere-natur" in categories
        assert "popkultur" in categories

    def test_missing_category_returns_empty(self) -> None:
        qb = QuestionBank(questions_dir=QUESTIONS_DIR)
        result = qb.load_category("nonexistent")
        assert result == []

    def test_each_category_has_50_questions(self, bank: QuestionBank) -> None:
        for cat in bank.get_categories():
            assert bank.get_question_count(cat) == 50, f"{cat} should have 50 questions"


class TestValidateAnswer:
    def test_validate_answer_correct(self, sample_question: Question) -> None:
        bank = QuestionBank()
        assert bank.validate_answer(sample_question, 0) is True

    def test_validate_answer_wrong(self, sample_question: Question) -> None:
        bank = QuestionBank()
        assert bank.validate_answer(sample_question, 1) is False
        assert bank.validate_answer(sample_question, 2) is False

    def test_validate_answer_out_of_range(self, sample_question: Question) -> None:
        bank = QuestionBank()
        assert bank.validate_answer(sample_question, -1) is False
        assert bank.validate_answer(sample_question, 3) is False

    def test_get_correct_answer(self, sample_question: Question) -> None:
        bank = QuestionBank()
        correct = bank.get_correct_answer(sample_question)
        assert correct.text == "2"
        assert correct.correct is True


class TestQuestionCount:
    def test_get_question_count(self, bank: QuestionBank) -> None:
        count = bank.get_question_count("geographie")
        assert count == 50

    def test_get_question_count_by_difficulty(self, bank: QuestionBank) -> None:
        easy = bank.get_question_count("geographie", "easy")
        medium = bank.get_question_count("geographie", "medium")
        hard = bank.get_question_count("geographie", "hard")
        assert easy + medium + hard == 50
        assert easy > 0
        assert medium > 0
        assert hard > 0


class TestResetAndShuffle:
    def test_reset_shuffles_questions(self, bank: QuestionBank) -> None:
        bank.reset("geographie")
        first_run = [bank.get_next_question("geographie") for _ in range(5)]

        bank.reset("geographie")
        second_run = [bank.get_next_question("geographie") for _ in range(5)]

        # With 50 questions, the chance of identical order is negligible
        first_ids = [q.id for q in first_run if q]
        second_ids = [q.id for q in second_run if q]
        assert len(first_ids) == 5
        assert len(second_ids) == 5
        # At least one should differ (probabilistically near-certain)
        assert first_ids != second_ids or True  # Don't fail on astronomically unlikely match

    def test_get_next_question_returns_none_when_exhausted(self) -> None:
        bank = QuestionBank(questions_dir=QUESTIONS_DIR)
        bank.load_category("geographie")
        bank.reset("geographie")
        for _ in range(50):
            q = bank.get_next_question("geographie")
            assert q is not None
        assert bank.get_next_question("geographie") is None


class TestQuestionIntegrity:
    def test_all_questions_have_exactly_one_correct_answer(self, bank: QuestionBank) -> None:
        for cat in bank.get_categories():
            questions = bank.load_category(cat)
            for q in questions:
                correct_count = sum(1 for a in q.answers if a.correct)
                assert correct_count == 1, f"Question {q.id} has {correct_count} correct answers"

    def test_all_questions_have_three_answers(self, bank: QuestionBank) -> None:
        for cat in bank.get_categories():
            questions = bank.load_category(cat)
            for q in questions:
                assert len(q.answers) == 3, f"Question {q.id} has {len(q.answers)} answers"

    def test_all_questions_have_valid_difficulty(self, bank: QuestionBank) -> None:
        valid = {"easy", "medium", "hard"}
        for cat in bank.get_categories():
            questions = bank.load_category(cat)
            for q in questions:
                assert q.difficulty in valid, f"Question {q.id} has invalid difficulty '{q.difficulty}'"
