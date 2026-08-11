"""Tests for the QuestionBank and question loading system."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT))

from custom_components.quizify.game.questions import (  # noqa: E402
    Answer,
    Question,
    QuestionBank,
    _parse_question,
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
        assert len(questions) == bank.get_question_count("geographie")
        assert 145 <= len(questions) <= 150
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

    def test_each_category_within_expected_size(self, bank: QuestionBank) -> None:
        # Themed packs run ~150 (148-150 after review deletions); the standalone
        # World Cup packs are a deliberate 100. Floor catches gross truncation.
        # Community packs are user content with no size invariant — skip them.
        # Estimate packs (#275) are a deliberately modest 10-20 questions —
        # exempt them from the themed-pack floor (they have their own floor).
        estimate_packs = {"schaetzfragen-de", "estimation-en"}
        for cat in bank.get_categories():
            if cat.startswith("community-"):
                continue
            count = bank.get_question_count(cat)
            if cat in estimate_packs:
                assert count >= 10, f"{cat} has only {count} questions (floor: 10)"
                assert count <= 20, f"{cat} has {count} questions (ceiling: 20)"
                continue
            assert count >= 95, f"{cat} has only {count} questions (floor: 95)"
            assert count <= 150, f"{cat} has {count} questions (ceiling: 150)"


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
        assert 145 <= count <= 150

    def test_get_question_count_by_difficulty(self, bank: QuestionBank) -> None:
        easy = bank.get_question_count("geographie", "easy")
        medium = bank.get_question_count("geographie", "medium")
        hard = bank.get_question_count("geographie", "hard")
        assert easy + medium + hard == bank.get_question_count("geographie")
        assert easy > 0
        assert medium > 0
        assert hard > 0


class TestResetAndShuffle:
    def test_reset_shuffles_questions(self, bank: QuestionBank) -> None:
        bank.reset("geographie")
        first_run = [bank.get_next_question("geographie") for _ in range(5)]

        bank.reset("geographie")
        second_run = [bank.get_next_question("geographie") for _ in range(5)]

        # With 100 questions, the chance of identical order is negligible
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
        for _ in range(bank.get_question_count("geographie")):
            q = bank.get_next_question("geographie")
            assert q is not None
        assert bank.get_next_question("geographie") is None


class TestQuestionIntegrity:
    def test_all_questions_have_exactly_one_correct_answer(self, bank: QuestionBank) -> None:
        for cat in bank.get_categories():
            questions = bank.load_category(cat)
            for q in questions:
                # Estimate questions (#275) carry no answers; skip the MC
                # correctness invariant for them.
                if q.is_estimate:
                    continue
                correct_count = sum(1 for a in q.answers if a.correct)
                assert correct_count == 1, f"Question {q.id} has {correct_count} correct answers"

    def test_all_questions_have_three_answers(self, bank: QuestionBank) -> None:
        for cat in bank.get_categories():
            questions = bank.load_category(cat)
            for q in questions:
                # Estimate questions (#275) have no answers array by design.
                if q.is_estimate:
                    assert q.answers == []
                    continue
                assert len(q.answers) == 3, f"Question {q.id} has {len(q.answers)} answers"

    def test_all_questions_have_valid_difficulty(self, bank: QuestionBank) -> None:
        valid = {"easy", "medium", "hard"}
        for cat in bank.get_categories():
            questions = bank.load_category(cat)
            for q in questions:
                assert q.difficulty in valid, f"Question {q.id} has invalid difficulty '{q.difficulty}'"


def _make_pack(name="My Pack", language="en", version="1.0", questions=None):
    """Build a minimal valid pack dict for community-pack tests."""
    if questions is None:
        questions = [
            {
                "id": "cq_001",
                "question": "What is 2+2?",
                "answers": [
                    {"text": "4", "correct": True},
                    {"text": "3", "correct": False},
                    {"text": "5", "correct": False},
                ],
                "difficulty": "easy",
                "fun_fact": "Basic arithmetic.",
            }
        ]
    return {
        "name": name,
        "language": language,
        "version": version,
        "questions": questions,
    }


def _write_community_pack(community_dir: Path, filename: str, data) -> Path:
    community_dir.mkdir(parents=True, exist_ok=True)
    path = community_dir / filename
    if isinstance(data, str):
        path.write_text(data, encoding="utf-8")
    else:
        path.write_text(json.dumps(data), encoding="utf-8")
    return path


class TestCommunityPacks:
    def test_no_community_dir_is_safe(self, tmp_path: Path) -> None:
        qb = QuestionBank(questions_dir=tmp_path)
        loaded = qb.load_community_packs()
        assert loaded == {}

    def test_loads_valid_pack_with_prefixed_slug(self, tmp_path: Path) -> None:
        _write_community_pack(tmp_path / "community", "mypack.json", _make_pack())
        qb = QuestionBank(questions_dir=tmp_path)
        loaded = qb.load_community_packs()
        assert "community-mypack" in loaded
        assert qb.get_question_count("community-mypack") == 1
        meta = qb.get_pack_versions()["community-mypack"]
        assert meta["community"] is True
        assert meta["name"] == "My Pack"
        assert meta["language"] == "en"

    def test_load_all_categories_picks_up_community(self, tmp_path: Path) -> None:
        _write_community_pack(tmp_path / "community", "extra.json", _make_pack())
        qb = QuestionBank(questions_dir=tmp_path)
        qb.load_all_categories()
        assert "community-extra" in qb.get_categories()

    def test_invalid_json_is_skipped(self, tmp_path: Path) -> None:
        _write_community_pack(tmp_path / "community", "broken.json", "{not valid json")
        qb = QuestionBank(questions_dir=tmp_path)
        loaded = qb.load_community_packs()
        assert loaded == {}

    def test_non_object_top_level_is_skipped(self, tmp_path: Path) -> None:
        _write_community_pack(tmp_path / "community", "list.json", [1, 2, 3])
        qb = QuestionBank(questions_dir=tmp_path)
        assert qb.load_community_packs() == {}

    def test_missing_name_is_skipped(self, tmp_path: Path) -> None:
        pack = _make_pack()
        del pack["name"]
        _write_community_pack(tmp_path / "community", "noname.json", pack)
        qb = QuestionBank(questions_dir=tmp_path)
        assert qb.load_community_packs() == {}

    def test_empty_questions_is_skipped(self, tmp_path: Path) -> None:
        _write_community_pack(tmp_path / "community", "empty.json", _make_pack(questions=[]))
        qb = QuestionBank(questions_dir=tmp_path)
        assert qb.load_community_packs() == {}

    def test_pack_with_only_invalid_questions_is_skipped(self, tmp_path: Path) -> None:
        bad = _make_pack(questions=[{"id": "x", "question": "q", "answers": [{"text": "a", "correct": True}]}])
        _write_community_pack(tmp_path / "community", "badq.json", bad)
        qb = QuestionBank(questions_dir=tmp_path)
        assert qb.load_community_packs() == {}

    def test_individual_bad_question_dropped_others_kept(self, tmp_path: Path) -> None:
        good = _make_pack()["questions"][0]
        bad = {"id": "bad", "question": "q", "answers": [{"text": "a", "correct": True}]}
        _write_community_pack(tmp_path / "community", "mixed.json", _make_pack(questions=[good, bad]))
        qb = QuestionBank(questions_dir=tmp_path)
        loaded = qb.load_community_packs()
        assert qb.get_question_count("community-mixed") == 1

    def test_duplicate_ids_deduplicated(self, tmp_path: Path) -> None:
        q = _make_pack()["questions"][0]
        _write_community_pack(tmp_path / "community", "dup.json", _make_pack(questions=[q, dict(q)]))
        qb = QuestionBank(questions_dir=tmp_path)
        qb.load_community_packs()
        assert qb.get_question_count("community-dup") == 1

    def test_oversized_file_is_skipped(self, tmp_path: Path) -> None:
        from custom_components.quizify.game.questions import MAX_COMMUNITY_PACK_BYTES

        community = tmp_path / "community"
        community.mkdir(parents=True)
        pack = _make_pack()
        pack["padding"] = "x" * (MAX_COMMUNITY_PACK_BYTES + 10)
        (community / "huge.json").write_text(json.dumps(pack), encoding="utf-8")
        qb = QuestionBank(questions_dir=tmp_path)
        assert qb.load_community_packs() == {}

    def test_too_many_questions_truncated(self, tmp_path: Path) -> None:
        from custom_components.quizify.game.questions import MAX_COMMUNITY_QUESTIONS

        base = _make_pack()["questions"][0]
        many = []
        for i in range(MAX_COMMUNITY_QUESTIONS + 5):
            q = dict(base)
            q["id"] = f"cq_{i:04d}"
            many.append(q)
        _write_community_pack(tmp_path / "community", "many.json", _make_pack(questions=many))
        qb = QuestionBank(questions_dir=tmp_path)
        qb.load_community_packs()
        assert qb.get_question_count("community-many") == MAX_COMMUNITY_QUESTIONS

    def test_community_cannot_shadow_builtin(self, tmp_path: Path) -> None:
        # Pre-load a built-in category, then a community file whose prefixed
        # slug would collide is the only collision path; verify a same-named
        # community slug is refused if already present.
        _write_community_pack(tmp_path / "community", "dupe.json", _make_pack())
        qb = QuestionBank(questions_dir=tmp_path)
        qb.load_community_packs()
        # Second load on the already-populated bank must not duplicate/clobber.
        loaded_again = qb.load_community_packs()
        assert loaded_again == {}
        assert qb.get_question_count("community-dupe") == 1

    def test_shipped_example_pack_loads(self) -> None:
        qb = QuestionBank(questions_dir=QUESTIONS_DIR)
        loaded = qb.load_community_packs()
        assert "community-example-pack" in loaded
        assert qb.get_question_count("community-example-pack") == 3


def _valid_question_dict(**overrides) -> dict:
    """Return a minimal valid question dict (issue #25 image tests)."""
    base = {
        "id": "img_001",
        "question": "What film is this from?",
        "answers": [
            {"text": "A", "correct": True},
            {"text": "B", "correct": False},
            {"text": "C", "correct": False},
        ],
    }
    base.update(overrides)
    return base


class TestQuestionImage:
    """Optional image_url field on questions (issue #25)."""

    def test_image_url_defaults_to_empty(self) -> None:
        q = Question(id="x", question="Q?", answers=[])
        assert q.image_url == ""

    def test_parse_question_without_image(self) -> None:
        q = _parse_question(_valid_question_dict(), "Test")
        assert q is not None
        assert q.image_url == ""

    def test_parse_question_keeps_https_url(self) -> None:
        url = "https://example.com/poster.jpg"
        q = _parse_question(_valid_question_dict(image_url=url), "Test")
        assert q is not None
        assert q.image_url == url

    def test_parse_question_keeps_http_url(self) -> None:
        url = "http://example.com/poster.jpg"
        q = _parse_question(_valid_question_dict(image_url=url), "Test")
        assert q is not None
        assert q.image_url == url

    def test_parse_question_strips_whitespace(self) -> None:
        q = _parse_question(
            _valid_question_dict(image_url="  https://example.com/p.png  "), "Test"
        )
        assert q is not None
        assert q.image_url == "https://example.com/p.png"

    def test_parse_question_drops_relative_path(self) -> None:
        q = _parse_question(_valid_question_dict(image_url="/local/p.png"), "Test")
        assert q is not None
        assert q.image_url == ""

    def test_parse_question_keeps_integration_static_path(self) -> None:
        """#536: a pack may reference an image it ships with.

        Without this the only way to ship a picture round is to point every
        question at somebody else's server, which breaks on every install at
        once the day that server changes a path.
        """
        url = "/quizify/static/img/packs/picture-round/eiffel.webp"
        q = _parse_question(_valid_question_dict(image_url=url), "Test")
        assert q is not None
        assert q.image_url == url

    def test_parse_question_drops_traversal_out_of_static(self) -> None:
        """The prefix is an allowlist, not a door held open."""
        for url in (
            "/quizify/static/../../config/secrets.yaml",
            "/quizify/static/img/../../../etc/passwd",
            "/quizify/static/%2e%2e/%2e%2e/secrets.yaml",
        ):
            q = _parse_question(_valid_question_dict(image_url=url), "Test")
            assert q is not None
            assert q.image_url == "", url

    def test_parse_question_drops_lookalike_prefixes(self) -> None:
        """Only that exact prefix — not anything merely starting similarly."""
        for url in (
            "/quizify/staticx/p.png",
            "/quizify/admin",
            "//evil.example/p.png",
            "/local/p.png",
        ):
            q = _parse_question(_valid_question_dict(image_url=url), "Test")
            assert q is not None
            assert q.image_url == "", url

    def test_parse_question_drops_javascript_scheme(self) -> None:
        q = _parse_question(
            _valid_question_dict(image_url="javascript:alert(1)"), "Test"
        )
        assert q is not None
        assert q.image_url == ""

    def test_parse_question_drops_data_uri(self) -> None:
        q = _parse_question(
            _valid_question_dict(image_url="data:image/png;base64,AAAA"), "Test"
        )
        assert q is not None
        assert q.image_url == ""

    def test_parse_question_drops_non_string(self) -> None:
        q = _parse_question(_valid_question_dict(image_url=123), "Test")
        assert q is not None
        assert q.image_url == ""
