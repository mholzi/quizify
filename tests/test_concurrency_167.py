"""Concurrency / state-cleanup regression tests for issue #167.

Triage finding: 4 of the 5 reported "race conditions" are NOT exploitable in
the cooperative single-threaded asyncio model — the relevant methods
(`evaluate_round`/`submit_answer`/`use_powerup`) are synchronous and run to
completion without an `await`, so two callers can never interleave between a
guard-check and the state mutation it protects. These tests lock in those
invariants so a future refactor that introduces an `await` mid-method breaks
loudly here.

Item #4 was a genuine residual: the *inbound* reaction-bonus counter
(`_reaction_bonuses_received`) was a dynamic attribute that `reset_for_new_game`
never cleared, so it leaked across games. That is fixed and covered below.

Item #3 gained an explicit invariant: a STEAL whose target has vanished now
returns an error instead of broadcasting a hollow 0-point effect.
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT))

from custom_components.quizify.const import ERR_ALREADY_SUBMITTED  # noqa: E402
from custom_components.quizify.game.player import PlayerSession  # noqa: E402
from custom_components.quizify.game.powerups import PowerUpType  # noqa: E402
from custom_components.quizify.game.state import (  # noqa: E402
    AnswerResult,
    GamePhase,
    QuizifyGameState,
)


class _FakeRuntime:
    def __init__(self, tmp_path: Path) -> None:
        self.data_dir = tmp_path


def _ws() -> MagicMock:
    ws = MagicMock()
    ws.closed = False
    ws.send_json = AsyncMock()
    return ws


@pytest.fixture
def game(tmp_path: Path) -> QuizifyGameState:
    return QuizifyGameState(runtime=_FakeRuntime(tmp_path), entry_id="test")


def _start_question(game: QuizifyGameState, names: list[str]) -> None:
    for n in names:
        game.add_player(n, _ws())
    game.start_game(language="de", num_rounds=3, difficulty="easy")
    q = game.start_next_question()
    assert q is not None
    assert game.phase == GamePhase.QUESTION_ACTIVE


# ---------------------------------------------------------------------------
# #1 — round evaluation fires exactly once (timer-expiry vs all-submitted)
# ---------------------------------------------------------------------------


class TestSingleRoundEvaluation:
    def test_evaluate_round_is_idempotent_and_broadcasts_once(
        self, game: QuizifyGameState, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The all-submitted path and the timer-expiry path both call
        evaluate_round(); the `_round_summary` guard must let only the first
        actually evaluate + broadcast. evaluate_round is synchronous, so there
        is no interleaving window."""
        events: list[str] = []
        monkeypatch.setattr(game, "_fire_broadcast", lambda ev: events.append(ev))

        _start_question(game, ["Alice", "Bob"])

        first = game.evaluate_round()   # e.g. timer expiry
        second = game.evaluate_round()  # e.g. all-submitted, racing

        assert first is not None  # first call evaluated
        # Second call is a no-op: the phase already advanced to ANSWER_REVEAL,
        # so it returns None (and the `_round_summary` guard backs that up).
        assert second is None
        assert game.phase == GamePhase.ANSWER_REVEAL
        assert events.count("round_evaluated") == 1

    def test_last_submit_then_timer_only_evaluates_once(
        self, game: QuizifyGameState, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """All players submit (auto-evaluate via submit_answer), then the timer
        path also fires evaluate_round — still exactly one round_evaluated."""
        events: list[str] = []
        monkeypatch.setattr(game, "_fire_broadcast", lambda ev: events.append(ev))

        _start_question(game, ["Alice", "Bob"])
        game.submit_answer("Alice", 0)
        game.submit_answer("Bob", 0)  # last submit → auto evaluate

        assert events.count("round_evaluated") == 1
        game.evaluate_round()  # timer fires after everyone already answered
        assert events.count("round_evaluated") == 1


# ---------------------------------------------------------------------------
# #2 — a second submission from the same player is rejected
# ---------------------------------------------------------------------------


class TestDoubleSubmit:
    def test_second_submit_returns_already_submitted(
        self, game: QuizifyGameState
    ) -> None:
        _start_question(game, ["Alice", "Bob"])
        first = game.submit_answer("Alice", 0)
        assert isinstance(first, AnswerResult)
        second = game.submit_answer("Alice", 1)
        assert second == ERR_ALREADY_SUBMITTED


# ---------------------------------------------------------------------------
# #3 — STEAL transfers points; vanished target returns an error (no hollow fx)
# ---------------------------------------------------------------------------


class TestStealPowerup:
    def test_steal_transfers_half_round_score(self, game: QuizifyGameState) -> None:
        _start_question(game, ["Alice", "Bob"])
        bob = game.get_player("Bob")
        bob.round_score = 10
        bob.score = 10
        game._powerup_manager._inventory["Alice"] = PowerUpType.STEAL

        effect = game.use_powerup("Alice", target_id="Bob")

        assert not isinstance(effect, str), f"unexpected error: {effect}"
        assert effect.stolen_points == 5
        assert game.get_player("Bob").round_score == 5
        assert game.get_player("Alice").round_score == 5

    def test_steal_with_vanished_target_returns_error(
        self, game: QuizifyGameState, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """If the target is gone at apply-time, STEAL returns an error instead
        of a 0-point hollow effect that would animate a steal that never
        happened."""
        _start_question(game, ["Alice", "Bob"])
        bob = game.get_player("Bob")
        bob.round_score = 10
        bob.score = 10
        game._powerup_manager._inventory["Alice"] = PowerUpType.STEAL

        registry = game._player_registry
        real_get = registry.get_player
        # Simulate Bob disappearing from the registry lookups.
        monkeypatch.setattr(
            registry,
            "get_player",
            lambda pid: None if pid == "Bob" else real_get(pid),
        )

        result = game.use_powerup("Alice", target_id="Bob")
        assert isinstance(result, str)  # error code, not a PowerUpEffect
        # No points were transferred.
        assert real_get("Bob").round_score == 10
        assert real_get("Alice").round_score == 0


# ---------------------------------------------------------------------------
# #4 — reaction-bonus counters reset between games (the real residual)
# ---------------------------------------------------------------------------


class TestReactionBonusReset:
    def test_received_counter_cleared_on_new_game(self) -> None:
        """`_reaction_bonuses_received` must NOT leak across games — otherwise a
        recipient capped in round N of game 1 is wrongly blocked in round N of
        game 2 (round numbers restart at 1)."""
        p = PlayerSession(name="Bob", ws=None)
        p._reaction_bonuses_received[1] = 3  # capped in round 1 of game 1
        p.reaction_bonuses_given.add(1)

        p.reset_for_new_game()

        assert p._reaction_bonuses_received == {}
        assert p.reaction_bonuses_given == set()

    def test_field_default_is_isolated_per_instance(self) -> None:
        """The default_factory must give each player its own dict (no shared
        mutable default)."""
        a = PlayerSession(name="A", ws=None)
        b = PlayerSession(name="B", ws=None)
        a._reaction_bonuses_received[2] = 1
        assert b._reaction_bonuses_received == {}
