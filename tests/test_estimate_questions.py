"""Tests for the estimate / closest-guess question type (#275)."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

QUESTIONS_DIR = _REPO_ROOT / "custom_components" / "quizify" / "questions"

from custom_components.quizify.const import ERR_ALREADY_SUBMITTED  # noqa: E402
from custom_components.quizify.game.phase_controller import (  # noqa: E402
    GamePhase,
)
from custom_components.quizify.game.questions import (  # noqa: E402
    QUESTION_TYPE_ESTIMATE,
    QUESTION_TYPE_MULTIPLE_CHOICE,
    Answer,
    Question,
    QuestionBank,
    _parse_question,
)
from custom_components.quizify.game.scoring import (  # noqa: E402
    ESTIMATE_EXACT_BONUS,
    calculate_estimate_scores,
)
from custom_components.quizify.game.state import QuizifyGameState  # noqa: E402
from custom_components.quizify.game.types import Difficulty  # noqa: E402
from custom_components.quizify.server.round_message_builder import (  # noqa: E402
    RoundMessageBuilder,
)
from custom_components.quizify.server.serializers import (  # noqa: E402
    serialize_question_for_admin,
    serialize_question_for_player,
)


def _est(**overrides):
    base = {
        "id": "e1",
        "question": "How many bones?",
        "type": "estimate",
        "answer": 206,
        "min": 0,
        "max": 500,
    }
    base.update(overrides)
    return base


def _mc(**overrides):
    base = {
        "id": "m1",
        "question": "What is 1+1?",
        "answers": [
            {"text": "2", "correct": True},
            {"text": "3"},
            {"text": "4"},
        ],
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------


class TestEstimateParsing:
    def test_valid_estimate_parses(self) -> None:
        q = _parse_question(_est(unit="bones", step=2), "Cat")
        assert q is not None
        assert q.is_estimate
        assert q.type == QUESTION_TYPE_ESTIMATE
        assert q.estimate_answer == 206
        assert q.estimate_min == 0
        assert q.estimate_max == 500
        assert q.estimate_unit == "bones"
        assert q.estimate_step == 2
        assert q.answers == []

    def test_estimate_defaults_step_to_one(self) -> None:
        q = _parse_question(_est(), "Cat")
        assert q is not None
        assert q.estimate_step == 1.0
        assert q.estimate_unit == ""

    def test_estimate_missing_answer_rejected(self) -> None:
        data = _est()
        del data["answer"]
        assert _parse_question(data, "Cat") is None

    def test_estimate_missing_range_rejected(self) -> None:
        data = _est()
        del data["max"]
        assert _parse_question(data, "Cat") is None

    def test_estimate_inverted_range_rejected(self) -> None:
        assert _parse_question(_est(min=500, max=0), "Cat") is None

    def test_estimate_equal_range_rejected(self) -> None:
        assert _parse_question(_est(min=10, max=10, answer=10), "Cat") is None

    def test_estimate_answer_outside_range_rejected(self) -> None:
        assert _parse_question(_est(answer=600), "Cat") is None
        assert _parse_question(_est(answer=-5), "Cat") is None

    def test_estimate_non_numeric_rejected(self) -> None:
        assert _parse_question(_est(answer="lots"), "Cat") is None
        assert _parse_question(_est(min="x"), "Cat") is None

    def test_unknown_type_rejected(self) -> None:
        assert _parse_question(_mc(type="ranking"), "Cat") is None

    def test_bad_step_falls_back_to_one(self) -> None:
        q = _parse_question(_est(step=0), "Cat")
        assert q is not None
        assert q.estimate_step == 1.0
        q2 = _parse_question(_est(step="bad"), "Cat")
        assert q2 is not None
        assert q2.estimate_step == 1.0


class TestMultipleChoiceUnaffected:
    def test_mc_without_type_defaults_to_multiple_choice(self) -> None:
        q = _parse_question(_mc(), "Cat")
        assert q is not None
        assert q.type == QUESTION_TYPE_MULTIPLE_CHOICE
        assert not q.is_estimate
        assert len(q.answers) == 3

    def test_mc_with_explicit_type_parses(self) -> None:
        q = _parse_question(_mc(type="multiple_choice"), "Cat")
        assert q is not None
        assert not q.is_estimate

    def test_mc_still_requires_three_answers(self) -> None:
        data = _mc(answers=[{"text": "2", "correct": True}, {"text": "3"}])
        assert _parse_question(data, "Cat") is None

    def test_mc_still_requires_one_correct(self) -> None:
        data = _mc(
            answers=[{"text": "2"}, {"text": "3"}, {"text": "4"}]
        )
        assert _parse_question(data, "Cat") is None


class TestEstimatePackLoads:
    def test_builtin_estimate_packs_load(self) -> None:
        bank = QuestionBank()
        for slug in ("schaetzfragen-de", "estimation-en", "estimacion-es"):
            qs = bank.load_category(slug)
            assert 10 <= len(qs) <= 20
            assert all(q.is_estimate for q in qs)
            for q in qs:
                assert q.estimate_min < q.estimate_max
                assert q.estimate_min <= q.estimate_answer <= q.estimate_max

    def test_no_estimate_question_is_dropped_on_load(self) -> None:
        """An answer outside min/max is discarded without a word by
        ``_parse_estimate_question`` — the pack simply loads one question
        shorter. The window above cannot see that (14 of 15 is still inside
        10-20), so compare against the file instead."""
        bank = QuestionBank()
        for slug in ("schaetzfragen-de", "estimation-en", "estimacion-es"):
            path = QUESTIONS_DIR / f"{slug}.json"
            on_disk = len(json.loads(path.read_text(encoding="utf-8"))["questions"])
            loaded = len(bank.load_category(slug))
            assert loaded == on_disk, (
                f"{slug}: {on_disk} questions in the file, {loaded} survived the "
                "parser — one was silently dropped, most likely an answer "
                "outside its min/max range"
            )


# ---------------------------------------------------------------------------
# Closeness scoring
# ---------------------------------------------------------------------------


class TestEstimateScoring:
    def test_ranking_by_distance(self) -> None:
        scores = calculate_estimate_scores(
            {"Anna": 210, "Du": 180, "Marina": 240, "Tom": 150}, 206
        )
        assert scores["Anna"]["rank"] == 1
        assert scores["Du"]["rank"] == 2
        assert scores["Marina"]["rank"] == 3
        assert scores["Tom"]["rank"] == 4
        # Closest gets the most points; strictly decreasing by rank.
        assert (
            scores["Anna"]["points"]
            > scores["Du"]["points"]
            > scores["Marina"]["points"]
            > scores["Tom"]["points"]
        )

    def test_distance_is_absolute(self) -> None:
        scores = calculate_estimate_scores({"A": 200, "B": 212}, 206)
        # Both are 6 away → tie.
        assert scores["A"]["distance"] == 6
        assert scores["B"]["distance"] == 6

    def test_ties_share_rank_and_points(self) -> None:
        scores = calculate_estimate_scores({"A": 200, "B": 212, "C": 150}, 206)
        assert scores["A"]["rank"] == 1
        assert scores["B"]["rank"] == 1
        assert scores["A"]["points"] == scores["B"]["points"]
        # Competition ranking: next distinct distance jumps to rank 3.
        assert scores["C"]["rank"] == 3

    def test_exact_hit_bonus(self) -> None:
        scores = calculate_estimate_scores({"A": 206, "B": 100}, 206)
        assert scores["A"]["exact"] is True
        assert scores["A"]["distance"] == 0
        # The exact hitter's award includes the flat bonus on top of rank-1.
        no_bonus = calculate_estimate_scores({"A": 205, "B": 100}, 206)
        assert scores["A"]["points"] == no_bonus["A"]["points"] + ESTIMATE_EXACT_BONUS

    def test_difficulty_multiplier(self) -> None:
        easy = calculate_estimate_scores({"A": 210}, 206, Difficulty.EASY)
        hard = calculate_estimate_scores({"A": 210}, 206, Difficulty.HARD)
        assert hard["A"]["points"] > easy["A"]["points"]

    def test_empty_guesses(self) -> None:
        assert calculate_estimate_scores({}, 206) == {}

    def test_all_participants_score_something(self) -> None:
        # Even a wildly wrong guess that participated earns the floor.
        scores = calculate_estimate_scores({"A": 206, "B": 99999}, 206)
        assert scores["B"]["points"] >= 5


# ---------------------------------------------------------------------------
# Round flow integration (submit_guess + evaluate)
# ---------------------------------------------------------------------------


class _FakeRuntime:
    def __init__(self, tmp_path: Path) -> None:
        self.data_dir = tmp_path


def _fake_ws() -> MagicMock:
    ws = MagicMock()
    ws.closed = False
    return ws


def _make_estimate_question() -> Question:
    return Question(
        id="e-flow",
        question="How many bones?",
        answers=[],
        type=QUESTION_TYPE_ESTIMATE,
        estimate_answer=206,
        estimate_min=0,
        estimate_max=500,
        estimate_unit="bones",
        estimate_step=1,
    )


@pytest.fixture
def est_state(tmp_path: Path) -> QuizifyGameState:
    """A game state forced into an active estimate round with 3 players."""
    state = QuizifyGameState(runtime=_FakeRuntime(tmp_path), entry_id="test")
    state.add_player("Anna", _fake_ws())
    state.add_player("Tom", _fake_ws())
    state.add_player("Marina", _fake_ws())
    state.start_game(language="de", num_rounds=3, timer_duration=30)
    state.start_next_question()
    # Force the active question to a known estimate question.
    q = _make_estimate_question()
    state._current_question = q
    for p in state._player_registry.players.values():
        p.reset_round()
    return state


class TestSubmitGuessFlow:
    def test_guess_is_clamped_to_range(self, est_state: QuizifyGameState) -> None:
        assert est_state.submit_guess("Anna", 99999) is None
        assert est_state._player_registry.get_player("Anna").current_guess == 500
        assert est_state.submit_guess("Tom", -50) is None
        assert est_state._player_registry.get_player("Tom").current_guess == 0

    def test_double_submit_rejected(self, est_state: QuizifyGameState) -> None:
        assert est_state.submit_guess("Anna", 200) is None
        assert est_state.submit_guess("Anna", 100) == ERR_ALREADY_SUBMITTED

    def test_all_submitted_auto_evaluates(self, est_state: QuizifyGameState) -> None:
        est_state.submit_guess("Anna", 210)
        est_state.submit_guess("Tom", 150)
        est_state.submit_guess("Marina", 240)
        # Three players, all guessed → round auto-evaluates into the reveal.
        assert est_state.phase == GamePhase.ANSWER_REVEAL
        summary = est_state.get_round_summary()
        assert summary is not None
        assert summary.estimate is not None

    def test_closest_player_wins_and_scores_most(
        self, est_state: QuizifyGameState
    ) -> None:
        est_state.submit_guess("Anna", 210)   # 4 off → closest
        est_state.submit_guess("Tom", 150)    # 56 off
        est_state.submit_guess("Marina", 240)  # 34 off
        est = est_state.get_round_summary().estimate
        assert est["winner"] == "Anna"
        anna = est_state._player_registry.get_player("Anna")
        tom = est_state._player_registry.get_player("Tom")
        assert anna.score > tom.score
        assert anna.round_score > 0

    def test_non_guesser_scores_zero(self, est_state: QuizifyGameState) -> None:
        est_state.submit_guess("Anna", 210)
        est_state.submit_guess("Tom", 150)
        # Marina never guesses → timer expiry evaluates the round.
        est_state.evaluate_round()
        marina = est_state._player_registry.get_player("Marina")
        assert marina.round_score == 0
        assert marina.streak == 0

    def test_reveal_carries_every_guess(self, est_state: QuizifyGameState) -> None:
        est_state.submit_guess("Anna", 210)
        est_state.submit_guess("Tom", 150)
        est_state.submit_guess("Marina", 206)  # exact
        est = est_state.get_round_summary().estimate
        names = {g["player_name"] for g in est["guesses"]}
        assert names == {"Anna", "Tom", "Marina"}
        marina_entry = next(g for g in est["guesses"] if g["player_name"] == "Marina")
        assert marina_entry["exact"] is True
        assert marina_entry["distance"] == 0
        assert est["answer"] == 206
        assert est["min"] == 0
        assert est["max"] == 500
        assert est["unit"] == "bones"

    def test_estimate_round_summary_payload_shape(
        self, est_state: QuizifyGameState
    ) -> None:
        est_state.submit_guess("Anna", 210)
        est_state.submit_guess("Tom", 150)
        est_state.submit_guess("Marina", 240)
        payload = RoundMessageBuilder().build_round_summary(est_state)
        assert payload is not None
        assert payload["question_type"] == "estimate"
        assert "estimate" in payload
        assert payload["estimate"]["winner"] == "Anna"
        # No MC answer grid leaks into an estimate reveal.
        assert payload["all_answers"] == []


class TestQuestionStartedPayload:
    def test_player_payload_hides_answer_and_ships_range(self) -> None:
        q = _make_estimate_question()
        payload = serialize_question_for_player(
            question=q,
            shuffled_answers=[],
            round_num=1,
            total_rounds=3,
            timer_duration=30,
        )
        assert payload["question_type"] == "estimate"
        assert payload["answers"] == []
        assert payload["estimate"]["min"] == 0
        assert payload["estimate"]["max"] == 500
        assert payload["estimate"]["unit"] == "bones"
        # The true answer is NEVER sent to the player.
        assert "answer" not in payload["estimate"]
        assert q.estimate_answer not in payload.values()

    def test_admin_payload_includes_answer(self) -> None:
        q = _make_estimate_question()
        payload = serialize_question_for_admin(
            question=q,
            round_num=1,
            total_rounds=3,
            timer_duration=30,
        )
        assert payload["question_type"] == "estimate"
        assert payload["estimate"]["answer"] == 206

    def test_mc_payload_unaffected(self) -> None:
        q = Question(
            id="m",
            question="Q",
            answers=[
                Answer(text="a", correct=True),
                Answer(text="b", correct=False),
                Answer(text="c", correct=False),
            ],
        )
        payload = serialize_question_for_player(
            question=q,
            shuffled_answers=["a", "b", "c"],
            round_num=1,
            total_rounds=3,
            timer_duration=30,
        )
        assert payload["question_type"] == "multiple_choice"
        assert payload["answers"] == ["a", "b", "c"]
        assert "estimate" not in payload
