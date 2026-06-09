"""Unit tests for the extracted RoundMessageBuilder (#189).

The builder assembles the per-round question/result payloads that the
WebSocket handler then sends. These tests pin the wire shapes against the
real serializers, using real Answer/Question/RoundSummary/PlayerSession
objects plus a minimal fake game-state that exposes exactly the attributes
and methods the builder reads — no connection manager, no pack loading.
"""

from __future__ import annotations

import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT))

from custom_components.quizify.game.player import PlayerSession  # noqa: E402
from custom_components.quizify.game.questions import Answer, Question  # noqa: E402
from custom_components.quizify.game.state import RoundSummary  # noqa: E402
from custom_components.quizify.server.round_message_builder import (  # noqa: E402
    RoundMessageBuilder,
)


def _question() -> Question:
    return Question(
        id="q1",
        question="Capital of France?",
        answers=[
            Answer(text="Paris", correct=True),
            Answer(text="Berlin", correct=False),
            Answer(text="Rome", correct=False),
            Answer(text="Madrid", correct=False),
        ],
        difficulty="easy",
        fun_fact="Paris has been the capital since 508 AD.",
        category="Geography",
    )


class _FakeGameState:
    """Minimal stand-in exposing only what RoundMessageBuilder touches."""

    def __init__(
        self,
        *,
        question: Question,
        players: list[PlayerSession],
        round_num: int = 2,
        total_rounds: int = 5,
        round_duration: float = 20.0,
        player_shuffles: dict[str, list[int]] | None = None,
        shuffle_map: list[int] | None = None,
        round_summary: RoundSummary | None = None,
    ) -> None:
        self._question = question
        self._players = players
        self.round = round_num
        self.total_rounds = total_rounds
        self.round_duration = round_duration
        self._player_shuffles = player_shuffles or {}
        # canonical shuffle: shuffled_pos -> original_index
        self.shuffle_map = shuffle_map if shuffle_map is not None else [0, 1, 2, 3]
        self.shuffled_answers = [question.answers[i].text for i in self.shuffle_map]
        self._round_summary = round_summary

    def get_players(self) -> list[PlayerSession]:
        return list(self._players)

    def get_player_shuffle(self, player_name: str) -> list[int]:
        return self._player_shuffles.get(player_name) or self.shuffle_map

    def get_round_summary(self) -> RoundSummary | None:
        return self._round_summary


class TestBuildPlayerQuestion:
    def test_uses_players_own_shuffle_order(self):
        q = _question()
        player = PlayerSession(name="Alice", ws=None, score=42)
        gs = _FakeGameState(
            question=q,
            players=[player],
            player_shuffles={"Alice": [2, 0, 3, 1]},
        )
        msg = RoundMessageBuilder().build_player_question(
            gs, question=q, player=player, is_final=False
        )
        # Answers projected in Alice's own order
        assert msg["answers"] == ["Rome", "Paris", "Madrid", "Berlin"]
        assert msg["type"] == "question_started"
        assert msg["round_num"] == 2
        assert msg["total_rounds"] == 5
        assert msg["timer_duration"] == 20.0
        assert msg["is_final_round"] is False
        assert msg["player_score"] == 42
        # No correct flag leaked to players
        assert "correct_answer" not in msg

    def test_falls_back_to_canonical_shuffle_when_no_player_shuffle(self):
        q = _question()
        player = PlayerSession(name="Bob", ws=None)
        gs = _FakeGameState(
            question=q, players=[player], shuffle_map=[3, 2, 1, 0]
        )
        msg = RoundMessageBuilder().build_player_question(
            gs, question=q, player=player, is_final=True
        )
        assert msg["answers"] == ["Madrid", "Rome", "Berlin", "Paris"]
        assert msg["is_final_round"] is True


class TestBuildAdminQuestion:
    def test_includes_correct_answer_and_full_options(self):
        q = _question()
        gs = _FakeGameState(question=q, players=[])
        msg = RoundMessageBuilder().build_admin_question(gs, question=q)
        assert msg["correct_answer"] == "Paris"
        assert msg["answers"] == [
            {"text": "Paris", "correct": True},
            {"text": "Berlin", "correct": False},
            {"text": "Rome", "correct": False},
            {"text": "Madrid", "correct": False},
        ]
        assert msg["round_num"] == 2
        assert msg["timer_duration"] == 20.0


class TestBuildGameStateWithLeaderboard:
    def test_carries_leaderboard_and_round_metadata(self):
        q = _question()
        p1 = PlayerSession(name="Alice", ws=None, score=100)
        p2 = PlayerSession(name="Bob", ws=None, score=50)
        gs = _FakeGameState(question=q, players=[p1, p2])
        gs.phase = type("Ph", (), {"value": "question_active"})()
        msg = RoundMessageBuilder().build_game_state_with_leaderboard(
            gs, players=[p1, p2]
        )
        assert msg["type"] == "game_state"
        assert msg["phase"] == "question_active"
        assert msg["round"] == 2
        assert msg["total_rounds"] == 5
        assert msg["player_count"] == 2
        # players and leaderboard are the same serialized list, sorted by score
        assert msg["players"] == msg["leaderboard"]
        assert [p["name"] for p in msg["leaderboard"]] == ["Alice", "Bob"]
        assert msg["leaderboard"][0]["score"] == 100


class TestBuildRoundSummary:
    def test_none_when_no_summary(self):
        gs = _FakeGameState(question=_question(), players=[], round_summary=None)
        assert RoundMessageBuilder().build_round_summary(gs) is None

    def test_resolves_indices_and_answer_table(self):
        q = _question()
        # Player answered "Paris" (original index 0 = correct)
        alice = PlayerSession(name="Alice", ws=None, score=30)
        alice.submitted = True
        alice.current_answer = 0
        alice.round_score = 30
        alice.round_score_breakdown = {"speed_bonus": 5, "streak_bonus": 10}
        alice.streak = 3
        # Player did not answer
        bob = PlayerSession(name="Bob", ws=None)
        bob.submitted = False

        summary = RoundSummary(
            question=q,
            correct_answer=q.answers[0],
            fun_fact=q.fun_fact,
        )
        # canonical shuffle puts correct (orig idx 0) at shuffled pos 2
        gs = _FakeGameState(
            question=q,
            players=[alice, bob],
            shuffle_map=[1, 2, 0, 3],
            round_summary=summary,
            round_num=5,
            total_rounds=5,
        )
        msg = RoundMessageBuilder().build_round_summary(gs)
        assert msg is not None
        assert msg["correct_answer_index"] == 2  # shuffled position of orig 0
        assert msg["correct_answer_index_original"] == 0
        assert msg["correct_answer"] == "Paris"
        assert msg["fun_fact"] == q.fun_fact
        assert msg["last_round"] is True  # round 5 of 5

        answers = {a["player_name"]: a for a in msg["all_answers"]}
        assert answers["Alice"]["answer_index"] == 0
        assert answers["Alice"]["answer_text"] == "Paris"
        assert answers["Alice"]["correct"] is True
        assert answers["Alice"]["points_earned"] == 30
        assert answers["Alice"]["speed_bonus"] == 5
        assert answers["Alice"]["streak_bonus"] == 10
        assert answers["Alice"]["streak"] == 3
        assert answers["Bob"]["no_answer"] is True
        assert answers["Bob"]["answer_index"] is None
        assert answers["Bob"]["answer_text"] == "—"
