"""Tests for QuizifyGameState — phase machine, scoring, late-join, eval.

The tests deliberately avoid mocking the question bank: they let it load
the real packs from disk, then constrain the language so output is
predictable. The fixture path mirrors how the standalone dev server
wires things, so failures here also catch wiring drift between game
state and runtime.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path
from unittest.mock import MagicMock

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT))

from custom_components.quizify.game.state import (  # noqa: E402
    GamePhase,
    QuizifyGameState,
)


class _FakeRuntime:
    """Stand-in for the StandaloneRuntime / HARuntime — only data_dir needed."""

    def __init__(self, tmp_path: Path) -> None:
        self.data_dir = tmp_path


def _fake_ws() -> MagicMock:
    """A WebSocket stub. The game only uses it as an opaque handle in tests."""
    ws = MagicMock()
    ws.closed = False
    return ws


@pytest.fixture
def state(tmp_path: Path) -> QuizifyGameState:
    """Fresh game state in LOBBY with a temp data dir."""
    runtime = _FakeRuntime(tmp_path)
    return QuizifyGameState(runtime=runtime, entry_id="test")


# ---------- Phase machine ----------


class TestPhaseTransitions:
    def test_starts_in_lobby(self, state: QuizifyGameState) -> None:
        assert state.phase == GamePhase.LOBBY
        assert state.round == 0

    def test_start_game_stays_in_lobby_until_first_question(
        self, state: QuizifyGameState
    ) -> None:
        # start_game prepares the game but doesn't advance phase —
        # start_next_question does. This separation lets the server
        # apply a grace period (L3 fix) between the two.
        state.add_player("Alice", _fake_ws())
        state.start_game(language="de", num_rounds=3)
        assert state.phase == GamePhase.LOBBY

    def test_start_next_question_advances_to_question_active(
        self, state: QuizifyGameState
    ) -> None:
        state.add_player("Alice", _fake_ws())
        state.start_game(language="de", num_rounds=3)
        q = state.start_next_question()
        assert q is not None
        assert state.phase == GamePhase.QUESTION_ACTIVE
        assert state.round == 1

    def test_double_start_raises(self, state: QuizifyGameState) -> None:
        state.add_player("Alice", _fake_ws())
        state.start_game(language="de", num_rounds=3)
        state.start_next_question()
        with pytest.raises(ValueError):
            state.start_game(language="de", num_rounds=3)

    def test_end_game_transitions_to_finale(self, state: QuizifyGameState) -> None:
        state.add_player("Alice", _fake_ws())
        state.start_game(language="de", num_rounds=2)
        state.end_game()
        assert state.phase == GamePhase.FINALE

    def test_reset_to_lobby_returns_to_lobby(self, state: QuizifyGameState) -> None:
        state.add_player("Alice", _fake_ws())
        state.start_game(language="de", num_rounds=2)
        state.start_next_question()
        state.end_game()
        state.reset_to_lobby()
        assert state.phase == GamePhase.LOBBY
        assert state.round == 0


# ---------- Late join (the bug that bit us in real testing) ----------


class TestLateJoin:
    """Regression coverage for the 'round 1 evaluated in 1s' bug.

    Found by /qa on 2026-05-22: when the only connected player joined
    after start_next_question, the tick loop's "all timers expired or
    missing" check broke immediately, ending the round in ~1s. The fix
    (state.py:166-184) creates a fresh timer for late joiners.
    """

    def test_late_joiner_gets_a_timer(self, state: QuizifyGameState) -> None:
        state.add_player("Alice", _fake_ws())
        state.start_game(language="de", num_rounds=3, timer_duration=30)
        state.start_next_question()
        # Bob shows up after round started
        success, _err = state.add_player("Bob", _fake_ws())
        assert success
        bob_timer = state.get_player_timer("Bob")
        assert bob_timer is not None, "late joiner must get a timer"
        # Should not be expired immediately
        assert not bob_timer.is_expired()

    def test_late_joiner_can_submit_answer(self, state: QuizifyGameState) -> None:
        state.add_player("Alice", _fake_ws())
        state.start_game(language="de", num_rounds=3, timer_duration=30)
        state.start_next_question()
        state.add_player("Bob", _fake_ws())
        # Bob picks an answer — should be accepted (not ERR_NOT_IN_GAME or expired)
        result = state.submit_answer("Bob", 0)
        # AnswerResult dataclass or error string — must not be an error
        assert not isinstance(result, str), f"got error: {result!r}"

    def test_late_joiner_marked_joined_late(self, state: QuizifyGameState) -> None:
        state.add_player("Alice", _fake_ws())
        state.start_game(language="de", num_rounds=3)
        state.start_next_question()
        state.add_player("Bob", _fake_ws())
        bob = state.get_player("Bob")
        assert bob is not None
        assert bob.joined_late is True

    def test_late_joiner_does_not_force_all_submitted(
        self, state: QuizifyGameState
    ) -> None:
        """all_submitted() must exclude late joiners — they shouldn't
        keep the round open after the original participants are done.
        """
        state.add_player("Alice", _fake_ws())
        state.add_player("Bob", _fake_ws())
        state.start_game(language="de", num_rounds=3)
        state.start_next_question()
        state.add_player("Charlie", _fake_ws())  # late
        state.submit_answer("Alice", 0)
        state.submit_answer("Bob", 0)
        # Round should be ready to evaluate even though Charlie didn't submit
        assert state._player_registry.all_submitted() is True


# ---------- Scoring ----------


class TestScoring:
    def test_correct_answer_increments_score(self, state: QuizifyGameState) -> None:
        state.add_player("Alice", _fake_ws())
        state.start_game(language="de", num_rounds=3, difficulty="easy")
        state.start_next_question()
        question = state._current_question
        correct_idx = next(
            i for i, a in enumerate(question.answers) if a.correct
        )
        result = state.submit_answer("Alice", correct_idx)
        # Real result object, not error string
        assert not isinstance(result, str)
        assert result.correct is True
        assert result.points_earned > 0
        alice = state.get_player("Alice")
        assert alice.score == result.points_earned

    def test_wrong_answer_zero_points(self, state: QuizifyGameState) -> None:
        state.add_player("Alice", _fake_ws())
        state.start_game(language="de", num_rounds=3, difficulty="easy")
        state.start_next_question()
        question = state._current_question
        wrong_idx = next(
            i for i, a in enumerate(question.answers) if not a.correct
        )
        result = state.submit_answer("Alice", wrong_idx)
        assert not isinstance(result, str)
        assert result.correct is False
        assert result.points_earned == 0

    def test_streak_resets_on_wrong(self, state: QuizifyGameState) -> None:
        state.add_player("Alice", _fake_ws())
        state.start_game(language="de", num_rounds=3, difficulty="easy")

        def correct_idx() -> int:
            return next(
                i for i, a in enumerate(state._current_question.answers) if a.correct
            )

        def wrong_idx() -> int:
            return next(
                i for i, a in enumerate(state._current_question.answers) if not a.correct
            )

        state.start_next_question()
        state.submit_answer("Alice", correct_idx())
        state.evaluate_round()
        alice = state.get_player("Alice")
        assert alice.streak == 1

        state.start_next_question()
        state.submit_answer("Alice", wrong_idx())
        state.evaluate_round()
        assert alice.streak == 0

    def test_streak_tracks_max(self, state: QuizifyGameState) -> None:
        state.add_player("Alice", _fake_ws())
        state.start_game(language="de", num_rounds=3, difficulty="easy")

        for _ in range(3):
            state.start_next_question()
            correct = next(
                i for i, a in enumerate(state._current_question.answers) if a.correct
            )
            state.submit_answer("Alice", correct)
            state.evaluate_round()

        alice = state.get_player("Alice")
        assert alice.streak == 3
        assert alice.max_streak == 3

    def test_double_submit_rejected(self, state: QuizifyGameState) -> None:
        state.add_player("Alice", _fake_ws())
        state.start_game(language="de", num_rounds=3, difficulty="easy")
        state.start_next_question()
        state.submit_answer("Alice", 0)
        result = state.submit_answer("Alice", 1)
        assert isinstance(result, str)  # error code


# ---------- Eval ----------


class TestEvaluation:
    def test_evaluate_round_transitions_to_reveal(self, state: QuizifyGameState) -> None:
        state.add_player("Alice", _fake_ws())
        state.start_game(language="de", num_rounds=3)
        state.start_next_question()
        state.submit_answer("Alice", 0)
        state.evaluate_round()
        assert state.phase == GamePhase.ANSWER_REVEAL

    def test_evaluate_idempotent(self, state: QuizifyGameState) -> None:
        """A double evaluate_round() must not corrupt scores. There's a
        guard against double-evaluation in evaluate_round() — verify it."""
        state.add_player("Alice", _fake_ws())
        state.start_game(language="de", num_rounds=3, difficulty="easy")
        state.start_next_question()
        correct = next(
            i for i, a in enumerate(state._current_question.answers) if a.correct
        )
        state.submit_answer("Alice", correct)
        first = state.evaluate_round()
        alice_score = state.get_player("Alice").score
        state.evaluate_round()
        assert state.get_player("Alice").score == alice_score, (
            "double evaluate must not double-score"
        )

    def test_unanswered_round_evaluates_to_zero(self, state: QuizifyGameState) -> None:
        state.add_player("Alice", _fake_ws())
        state.start_game(language="de", num_rounds=3)
        state.start_next_question()
        # No submit — just evaluate
        state.evaluate_round()
        alice = state.get_player("Alice")
        assert alice.score == 0
        assert alice.round_history[-1] == "timeout"


# ---------- Player registry ----------


class TestPlayerRegistry:
    def test_duplicate_name_rejected(self, state: QuizifyGameState) -> None:
        state.add_player("Alice", _fake_ws())
        success, err = state.add_player("Alice", _fake_ws())
        assert not success
        assert err is not None

    def test_remove_player_clears_timer(self, state: QuizifyGameState) -> None:
        state.add_player("Alice", _fake_ws())
        state.add_player("Bob", _fake_ws())
        state.start_game(language="de", num_rounds=3)
        state.start_next_question()
        assert state.get_player_timer("Alice") is not None
        state.remove_player("Alice")
        assert state.get_player_timer("Alice") is None

    def test_get_state_snapshot_has_required_keys(
        self, state: QuizifyGameState
    ) -> None:
        state.add_player("Alice", _fake_ws())
        state.start_game(language="de", num_rounds=3)
        state.start_next_question()
        snapshot = state.get_state_snapshot()
        # Keys the client relies on — if any of these vanish, the UI
        # silently breaks (which is how the C3 finale-stats bug shipped).
        assert "phase" in snapshot
        assert "round" in snapshot
        assert "total_rounds" in snapshot
        assert "players" in snapshot or "leaderboard" in snapshot


# ---------- Per-player shuffle ----------


class TestPerPlayerShuffle:
    """Each player should see A/B/C in their own order so couch-neighbours
    can't shout the right letter at each other."""

    def test_per_player_shuffles_are_isolated(self, state: QuizifyGameState) -> None:
        state.add_player("Alice", _fake_ws())
        state.add_player("Bob", _fake_ws())
        state.start_game(language="de", num_rounds=2)
        # Simulate what the websocket layer does for per-player shuffles.
        state.set_player_shuffle("Alice", [0, 1, 2])
        state.set_player_shuffle("Bob", [2, 1, 0])
        assert state.get_player_shuffle("Alice") == [0, 1, 2]
        assert state.get_player_shuffle("Bob") == [2, 1, 0]

    def test_get_player_shuffle_falls_back_to_canonical(
        self, state: QuizifyGameState
    ) -> None:
        state.add_player("Alice", _fake_ws())
        state.start_game(language="de", num_rounds=2)
        state.set_round_shuffle([1, 0, 2], ["B", "A", "C"])
        # Unknown player → canonical
        assert state.get_player_shuffle("Stranger") == [1, 0, 2]

    def test_clear_player_shuffles_wipes_them(self, state: QuizifyGameState) -> None:
        state.set_player_shuffle("Alice", [0, 1, 2])
        state.clear_player_shuffles()
        assert state.player_shuffles == {}


# ---------- Pause / resume ----------


class TestPauseResume:
    def test_pause_during_question(self, state: QuizifyGameState) -> None:
        state.add_player("Alice", _fake_ws())
        state.start_game(language="de", num_rounds=3)
        state.start_next_question()
        assert state.pause() is True
        assert state.phase == GamePhase.PAUSED

    def test_pause_no_op_in_lobby(self, state: QuizifyGameState) -> None:
        assert state.pause() is False
        assert state.phase == GamePhase.LOBBY

    def test_resume_restores_question_active(self, state: QuizifyGameState) -> None:
        state.add_player("Alice", _fake_ws())
        state.start_game(language="de", num_rounds=3)
        state.start_next_question()
        state.pause()
        assert state.resume() is True
        assert state.phase == GamePhase.QUESTION_ACTIVE

    def test_resume_no_op_when_not_paused(self, state: QuizifyGameState) -> None:
        assert state.resume() is False

    def test_pause_reason_round_trip(self, state: QuizifyGameState) -> None:
        state.add_player("Alice", _fake_ws())
        state.start_game(language="de", num_rounds=3)
        state.start_next_question()
        state.pause(reason="admin_disconnected")
        assert state.get_pause_reason() == "admin_disconnected"
        state.resume()
        assert state.get_pause_reason() is None


# ---------- Wager round (gameplay idea #3) ----------


class TestWagerRound:
    """Final-round wager overrides the standard scoring. Players bet
    0-100% of their current score; correct adds, wrong subtracts."""

    def _play_first_rounds_to_set_up_score(self, state: QuizifyGameState, name: str, score_target: int = 100) -> None:
        """Just inject score directly — easier than playing N rounds to a
        specific total when the goal is to test the wager math."""
        player = state.get_player(name)
        assert player is not None
        player.score = score_target

    def test_wager_correct_adds_wager_points(self, state: QuizifyGameState) -> None:
        state.add_player("Alice", _fake_ws())
        state.start_game(language="de", num_rounds=2, difficulty="easy")
        # Run round 1 normally to set the round counter
        state.start_next_question()
        state.evaluate_round()
        # Set a known bank
        self._play_first_rounds_to_set_up_score(state, "Alice", score_target=100)
        # Start the final round
        state.start_next_question()
        assert state.round == state.total_rounds
        # Wager 50% (= 50 points). Correct answer should add 50 → 150.
        alice = state.get_player("Alice")
        alice.wager = 50
        question = state._current_question
        correct_idx = next(i for i, a in enumerate(question.answers) if a.correct)
        state.submit_answer("Alice", correct_idx)
        assert alice.score == 150

    def test_wager_wrong_subtracts_wager_points(self, state: QuizifyGameState) -> None:
        state.add_player("Alice", _fake_ws())
        state.start_game(language="de", num_rounds=2, difficulty="easy")
        state.start_next_question()
        state.evaluate_round()
        self._play_first_rounds_to_set_up_score(state, "Alice", score_target=100)
        state.start_next_question()
        alice = state.get_player("Alice")
        alice.wager = 30  # 30 pts at stake
        question = state._current_question
        wrong_idx = next(i for i, a in enumerate(question.answers) if not a.correct)
        state.submit_answer("Alice", wrong_idx)
        assert alice.score == 70

    def test_wager_cannot_drive_score_negative(self, state: QuizifyGameState) -> None:
        """Losing 100% wager when score is 10 should clamp at 0, not -wager."""
        state.add_player("Alice", _fake_ws())
        state.start_game(language="de", num_rounds=2, difficulty="easy")
        state.start_next_question()
        state.evaluate_round()
        self._play_first_rounds_to_set_up_score(state, "Alice", score_target=10)
        state.start_next_question()
        alice = state.get_player("Alice")
        alice.wager = 100  # 10 pts at stake
        question = state._current_question
        wrong_idx = next(i for i, a in enumerate(question.answers) if not a.correct)
        state.submit_answer("Alice", wrong_idx)
        assert alice.score == 0

    def test_wager_only_applies_on_final_round(self, state: QuizifyGameState) -> None:
        """A wager set on a non-final round must not override scoring."""
        state.add_player("Alice", _fake_ws())
        state.start_game(language="de", num_rounds=3, difficulty="easy")
        state.start_next_question()
        alice = state.get_player("Alice")
        alice.wager = 80  # should be ignored — round 1 of 3
        alice.score = 100
        question = state._current_question
        correct_idx = next(i for i, a in enumerate(question.answers) if a.correct)
        state.submit_answer("Alice", correct_idx)
        # Standard scoring applied (BASE_POINTS=10 + small bonuses);
        # NOT +80 from wager.
        assert alice.score < 150, "wager must not apply on non-final rounds"

    def test_wager_cleared_each_round(self, state: QuizifyGameState) -> None:
        """reset_round must wipe the wager so it can't leak across rounds."""
        state.add_player("Alice", _fake_ws())
        state.start_game(language="de", num_rounds=3)
        state.start_next_question()
        alice = state.get_player("Alice")
        alice.wager = 50
        state.evaluate_round()
        state.start_next_question()  # reset_round runs per player
        assert state.get_player("Alice").wager is None


# ---------- End game ----------


class TestEndGame:
    def test_end_game_with_no_rounds_still_works(
        self, state: QuizifyGameState
    ) -> None:
        state.add_player("Alice", _fake_ws())
        state.start_game(language="de", num_rounds=3)
        state.end_game()
        assert state.phase == GamePhase.FINALE

    def test_automatic_end_after_total_rounds(self, state: QuizifyGameState) -> None:
        state.add_player("Alice", _fake_ws())
        state.start_game(language="de", num_rounds=2)
        for _ in range(2):
            state.start_next_question()
            state.evaluate_round()
        # Next request for a question after round 2 must end the game.
        result = state.start_next_question()
        assert result is None
        assert state.phase == GamePhase.FINALE
