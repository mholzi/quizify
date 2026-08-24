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
from custom_components.quizify.game.powerups import PowerUpEffect, PowerUpType  # noqa: E402
from custom_components.quizify.game.questions import Answer, Question  # noqa: E402


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
        # Before the last real participant submits, the round is still open and
        # all_submitted() excludes the late joiner (it does not block on him).
        assert state._player_registry.all_submitted() is False
        state.submit_answer("Bob", 0)
        # Once both real participants have answered the round auto-evaluates
        # (advances past QUESTION_ACTIVE) without waiting on the late joiner —
        # i.e. the late joiner never forced the room to run the full timer.
        assert state.phase is not GamePhase.QUESTION_ACTIVE


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


# ---------- Lobby music (#56) ----------


class _FakeHass:
    """Captures async_create_task coroutines + the service calls inside them."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str, dict]] = []
        self.services = self  # so hass.services.async_call resolves here

    async def async_call(self, domain, service, data, blocking=False):  # noqa: ANN001, ARG002
        self.calls.append((domain, service, data))

    def async_create_task(self, coro):  # noqa: ANN001
        # Drive the coroutine to completion synchronously so we can assert
        # the recorded service calls without an event loop.
        try:
            coro.send(None)
        except StopIteration:
            pass


class TestLobbyMusic:
    """Issue #56: lobby music plays SERVER-SIDE on the configured HA
    media_player (the same entity used for TTS) while waiting in the lobby,
    and stops as soon as the game starts. Inert until a URL is configured —
    no audio asset ships with the integration."""

    def _make(self, state: QuizifyGameState, *, mp="media_player.kitchen", url=None):
        from custom_components.quizify.lobby_music import QuizifyLobbyMusic

        state.lobby_music_url = url
        hass = _FakeHass()
        lm = QuizifyLobbyMusic(
            hass=hass,
            media_player_entity_id=mp,
            game_state=state,
        )
        return lm, hass

    def test_inert_without_media_player(self, state: QuizifyGameState) -> None:
        lm, hass = self._make(state, mp=None, url="/local/x.mp3")
        assert lm.is_configured is False
        state.phase = GamePhase.LOBBY
        lm._on_state_changed()
        assert hass.calls == []

    def test_inert_without_url(self, state: QuizifyGameState) -> None:
        lm, hass = self._make(state, url=None)
        state.phase = GamePhase.LOBBY
        lm._on_state_changed()
        assert hass.calls == []

    def test_plays_and_loops_in_lobby(self, state: QuizifyGameState) -> None:
        lm, hass = self._make(state, mp="media_player.kitchen", url="/local/x.mp3")
        state.phase = GamePhase.LOBBY
        lm._on_state_changed()
        services = [(d, s) for d, s, _ in hass.calls]
        assert ("media_player", "play_media") in services
        assert ("media_player", "repeat_set") in services
        play = next(data for d, s, data in hass.calls if s == "play_media")
        assert play["entity_id"] == "media_player.kitchen"
        assert play["media_content_id"] == "/local/x.mp3"
        assert play["media_content_type"] == "music"
        rep = next(data for d, s, data in hass.calls if s == "repeat_set")
        assert rep["repeat"] == "all"

    def test_stops_when_game_starts(self, state: QuizifyGameState) -> None:
        lm, hass = self._make(state, mp="media_player.kitchen", url="/local/x.mp3")
        state.phase = GamePhase.LOBBY
        lm._on_state_changed()
        hass.calls.clear()
        state.phase = GamePhase.QUESTION_ACTIVE
        lm._on_state_changed()
        assert [(d, s) for d, s, _ in hass.calls] == [
            ("media_player", "media_stop")
        ]

    def test_no_duplicate_stop(self, state: QuizifyGameState) -> None:
        lm, hass = self._make(state, mp="media_player.kitchen", url="/local/x.mp3")
        # Never entered lobby (still default LOBBY but no play yet) — jump
        # straight to a non-lobby phase: no stop because nothing was playing.
        state.phase = GamePhase.QUESTION_ACTIVE
        lm._on_state_changed()
        assert hass.calls == []


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

    def test_wager_timeout_loses_stake(self, state: QuizifyGameState) -> None:
        """Reversed by #653: a final-round player who wagers and never submits
        LOSES the stake, exactly as if they had answered wrongly.

        This was the opposite until now (#301), so that a sleeping phone cost
        nothing. The Hot Seat auction (#616) could not inherit that rule — a
        stake that buys the right to answer would be free to anyone who simply
        sat the question out — and two settlement rules were worse than one."""
        state.add_player("Alice", _fake_ws())
        state.add_player("Bob", _fake_ws())  # so the round doesn't auto-evaluate
        state.start_game(language="de", num_rounds=2, difficulty="easy")
        state.start_next_question()
        state.evaluate_round()
        self._play_first_rounds_to_set_up_score(state, "Alice", score_target=100)
        state.start_next_question()  # final round
        assert state.round == state.total_rounds
        alice = state.get_player("Alice")
        alice.wager = 100  # bet everything — but never submit
        # Bob submits so the round can be evaluated without Alice.
        question = state._current_question
        correct_idx = next(i for i, a in enumerate(question.answers) if a.correct)
        state.submit_answer("Bob", correct_idx)
        state.evaluate_round()
        # Alice timed out on a 100% wager: the whole bank is gone.
        assert alice.submitted is False
        assert alice.score == 0


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


# ---------- Power-up targeting ----------


def _give(state: QuizifyGameState, name: str, powerup: PowerUpType) -> None:
    """Drop a specific power-up into the player's inventory.

    Avoids the random assignment path so tests can target a single type
    without flaking.
    """
    state._powerup_manager._inventory[name] = powerup


class TestPowerUpTargeting:
    """Freeze and Steal need a target. The UI sends one via the picker,
    but the server also accepts target_id=None and picks a random active
    opponent — so the power-up never silently no-ops."""

    def test_freeze_with_explicit_target_freezes_that_player(
        self, state: QuizifyGameState
    ) -> None:
        state.add_player("Alice", _fake_ws())
        state.add_player("Bob", _fake_ws())
        state.add_player("Carol", _fake_ws())
        state.start_game(language="de", num_rounds=3, difficulty="easy")
        state.start_next_question()
        _give(state, "Alice", PowerUpType.FREEZE)

        effect = state.use_powerup("Alice", target_id="Bob")
        assert isinstance(effect, PowerUpEffect)
        assert effect.type == PowerUpType.FREEZE
        assert effect.target_player == "Bob"

    def test_freeze_with_null_target_picks_random_opponent(
        self, state: QuizifyGameState
    ) -> None:
        state.add_player("Alice", _fake_ws())
        state.add_player("Bob", _fake_ws())
        state.add_player("Carol", _fake_ws())
        state.start_game(language="de", num_rounds=3, difficulty="easy")
        state.start_next_question()
        _give(state, "Alice", PowerUpType.FREEZE)

        effect = state.use_powerup("Alice", target_id=None)
        assert isinstance(effect, PowerUpEffect)
        # Source must never freeze itself.
        assert effect.target_player in {"Bob", "Carol"}

    def test_steal_with_null_target_picks_random_opponent_and_moves_points(
        self, state: QuizifyGameState
    ) -> None:
        state.add_player("Alice", _fake_ws())
        state.add_player("Bob", _fake_ws())
        state.start_game(language="de", num_rounds=3, difficulty="easy")
        state.start_next_question()
        # Give Bob some round score so there's something worth stealing.
        # STEAL only targets submitted players (#254), so mark Bob submitted.
        bob = state.get_player("Bob")
        bob.submitted = True
        bob.round_score = 100
        bob.score = 100
        _give(state, "Alice", PowerUpType.STEAL)

        effect = state.use_powerup("Alice", target_id=None)
        assert isinstance(effect, PowerUpEffect)
        assert effect.target_player == "Bob"
        assert effect.stolen_points == 50  # half of 100
        assert state.get_player("Alice").score == 50
        assert state.get_player("Bob").score == 50

    def test_freeze_with_no_opponents_returns_error_and_keeps_powerup(
        self, state: QuizifyGameState
    ) -> None:
        """Single-player game: nothing to freeze. The server must reject
        instead of consuming the power-up silently."""
        state.add_player("Alice", _fake_ws())
        state.start_game(language="de", num_rounds=3, difficulty="easy")
        state.start_next_question()
        _give(state, "Alice", PowerUpType.FREEZE)

        result = state.use_powerup("Alice", target_id=None)
        assert isinstance(result, str)  # error code
        # Inventory still holds the power-up — try-again is allowed.
        assert state._powerup_manager.get_powerup("Alice") == PowerUpType.FREEZE

    def test_freeze_self_target_falls_back_to_random_opponent(
        self, state: QuizifyGameState
    ) -> None:
        """A malformed/old client that passes own name as target_id must
        not freeze itself — server treats it as 'no valid target' and
        picks an opponent."""
        state.add_player("Alice", _fake_ws())
        state.add_player("Bob", _fake_ws())
        state.start_game(language="de", num_rounds=3, difficulty="easy")
        state.start_next_question()
        _give(state, "Alice", PowerUpType.FREEZE)

        effect = state.use_powerup("Alice", target_id="Alice")
        assert isinstance(effect, PowerUpEffect)
        assert effect.target_player == "Bob"

    def test_joker_ignores_target_id(self, state: QuizifyGameState) -> None:
        """Self-targeted power-ups (joker, double_points, time_boost)
        must work regardless of target_id — they don't need one."""
        state.add_player("Alice", _fake_ws())
        state.start_game(language="de", num_rounds=3, difficulty="easy")
        state.start_next_question()
        # Pin a known multiple-choice question so joker always has a wrong
        # answer to remove. start_next_question() picks randomly from the live
        # packs, which now include estimate questions (#275) — those carry no
        # A/B/C answers, so an unseeded RNG could otherwise land on one and
        # leave joker_remove_index None (suite-order flake).
        state._current_question = Question(
            id="joker-mc",
            question="Pick one",
            answers=[
                Answer(text="right", correct=True),
                Answer(text="wrong 1", correct=False),
                Answer(text="wrong 2", correct=False),
                Answer(text="wrong 3", correct=False),
            ],
        )
        _give(state, "Alice", PowerUpType.JOKER)

        effect = state.use_powerup("Alice", target_id=None)
        assert isinstance(effect, PowerUpEffect)
        assert effect.type == PowerUpType.JOKER
        # joker removes one of the three wrong-answer indices (1, 2, or 3).
        assert effect.joker_remove_index in {1, 2, 3}

    @pytest.mark.parametrize(
        "pu_type",
        [PowerUpType.JOKER, PowerUpType.DOUBLE_POINTS, PowerUpType.TIME_BOOST],
    )
    def test_self_powerup_rejected_after_submit(
        self, state: QuizifyGameState, pu_type: PowerUpType
    ) -> None:
        """Joker / DoublePoints / TimeBoost only help BEFORE the source locks
        in — activating them after submit consumes the inventory for nothing.
        Server now rejects and keeps the power-up for a future round."""
        state.add_player("Alice", _fake_ws())
        state.add_player("Bob", _fake_ws())
        state.start_game(language="de", num_rounds=3, difficulty="easy")
        state.start_next_question()
        _give(state, "Alice", pu_type)
        # Submit before trying to use the power-up.
        state.submit_answer("Alice", 0)
        result = state.use_powerup("Alice", target_id=None)
        assert isinstance(result, str), f"{pu_type.value} should reject post-submit"
        assert state._powerup_manager.get_powerup("Alice") == pu_type, (
            f"{pu_type.value} should stay in inventory after rejected use"
        )

    def test_freeze_skips_submitted_target_in_random_pick(
        self, state: QuizifyGameState
    ) -> None:
        """When freeze falls back to a random opponent and one of them has
        already submitted, the fallback should pick someone who can still
        be timer-paused."""
        state.add_player("Alice", _fake_ws())
        state.add_player("Bob", _fake_ws())
        state.add_player("Carol", _fake_ws())
        state.start_game(language="de", num_rounds=3, difficulty="easy")
        state.start_next_question()
        # Bob locks in early.
        state.submit_answer("Bob", 0)
        _give(state, "Alice", PowerUpType.FREEZE)

        effect = state.use_powerup("Alice", target_id=None)
        assert isinstance(effect, PowerUpEffect)
        assert effect.target_player == "Carol", "Freeze fallback must skip submitted Bob"

    def test_freeze_rejects_explicit_submitted_target(self, state: QuizifyGameState) -> None:
        """Even with an explicit picker selection, freezing a player who has
        already submitted is a no-op — reject so the inventory survives."""
        state.add_player("Alice", _fake_ws())
        state.add_player("Bob", _fake_ws())
        state.start_game(language="de", num_rounds=3, difficulty="easy")
        state.start_next_question()
        state.submit_answer("Bob", 0)
        _give(state, "Alice", PowerUpType.FREEZE)

        result = state.use_powerup("Alice", target_id="Bob")
        assert isinstance(result, str), "Freeze on submitted target should be rejected"
        assert state._powerup_manager.get_powerup("Alice") == PowerUpType.FREEZE

    def test_steal_rejects_explicit_unsubmitted_target(self, state: QuizifyGameState) -> None:
        """STEAL on a target that hasn't submitted yet steals 0 points and burns
        the power-up. The server must reject so the inventory survives (#254)."""
        state.add_player("Alice", _fake_ws())
        state.add_player("Bob", _fake_ws())
        state.start_game(language="de", num_rounds=3, difficulty="easy")
        state.start_next_question()
        # Bob has NOT submitted — his round_score isn't locked in yet.
        _give(state, "Alice", PowerUpType.STEAL)

        result = state.use_powerup("Alice", target_id="Bob")
        assert isinstance(result, str), "Steal on unsubmitted target should be rejected"
        assert state._powerup_manager.get_powerup("Alice") == PowerUpType.STEAL
        # No points moved.
        assert state.get_player("Bob").score == state.get_player("Bob").round_score

    def test_steal_random_pick_skips_unsubmitted_targets(
        self, state: QuizifyGameState
    ) -> None:
        """Random STEAL fallback must skip players who haven't submitted (they'd
        yield 0 stolen points) and choose a submitted opponent instead (#254)."""
        state.add_player("Alice", _fake_ws())
        state.add_player("Bob", _fake_ws())
        state.add_player("Carol", _fake_ws())
        state.start_game(language="de", num_rounds=3, difficulty="easy")
        state.start_next_question()
        # Only Carol has locked in (and has something worth stealing).
        carol = state.get_player("Carol")
        carol.submitted = True
        carol.round_score = 80
        carol.score = 80
        _give(state, "Alice", PowerUpType.STEAL)

        effect = state.use_powerup("Alice", target_id=None)
        assert isinstance(effect, PowerUpEffect)
        assert effect.target_player == "Carol", "Steal fallback must skip unsubmitted Bob"
        assert effect.stolen_points == 40

    def test_steal_with_no_submitted_opponents_returns_error_and_keeps_powerup(
        self, state: QuizifyGameState
    ) -> None:
        """If no opponent has submitted, STEAL has no valid target. Reject and
        keep the power-up rather than burning it for 0 points (#254)."""
        state.add_player("Alice", _fake_ws())
        state.add_player("Bob", _fake_ws())
        state.start_game(language="de", num_rounds=3, difficulty="easy")
        state.start_next_question()
        _give(state, "Alice", PowerUpType.STEAL)

        result = state.use_powerup("Alice", target_id=None)
        assert isinstance(result, str)
        assert state._powerup_manager.get_powerup("Alice") == PowerUpType.STEAL
