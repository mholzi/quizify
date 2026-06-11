"""Regression tests for the 2026-06-11 backend bug-fix batch.

Covers the handler/state-level fixes:

* #298 — next_round/next_question rejected in a *real* (un-started) lobby.
* #303 — num_rounds from the WS coerced + clamped to 1..50 (fallback 10).
* #308 — empty-pack start reports NO_QUESTIONS_REMAINING (not ALREADY_STARTED);
         admin "End lightning" scores the in-flight question's answers.
* #311 — admin_skip evaluates the round during QUESTION_ACTIVE.

These exercise the same handler entry points the WS dispatch calls, with the
ConnectionManager broadcast/send stubbed, mirroring test_start_game_fresh.py.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT))

from custom_components.quizify.const import (  # noqa: E402
    ERR_GAME_ALREADY_STARTED,
    ERR_INVALID_ACTION,
    ERR_NO_QUESTIONS_REMAINING,
)
from custom_components.quizify.game.state import (  # noqa: E402
    GamePhase,
    QuizifyGameState,
)
from custom_components.quizify.server.connection import ConnectionManager  # noqa: E402
from custom_components.quizify.server.websocket import (  # noqa: E402
    QuizifyWebSocketHandler,
)


class _FakeRuntime:
    def __init__(self, tmp_path: Path) -> None:
        self.data_dir = tmp_path

    def create_task(self, coro):
        return asyncio.ensure_future(coro)

    async def run_in_executor(self, func, *args):
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, func, *args)


def _ws() -> MagicMock:
    ws = MagicMock()
    ws.closed = False
    ws.send_json = AsyncMock()
    return ws


@pytest.fixture
def game(tmp_path: Path) -> QuizifyGameState:
    return QuizifyGameState(runtime=_FakeRuntime(tmp_path), entry_id="test")


@pytest.fixture
def handler(game: QuizifyGameState, monkeypatch) -> QuizifyWebSocketHandler:
    runtime = _FakeRuntime(game._runtime.data_dir)  # type: ignore[attr-defined]
    h = QuizifyWebSocketHandler(runtime=runtime, game_state_provider=lambda: game)
    h._conn = ConnectionManager(runtime, lambda: game)
    h._conn.broadcast = AsyncMock()
    h._conn.broadcast_to_admins_and_dashboards = AsyncMock()
    h._conn.send = AsyncMock()
    h._conn.send_error = AsyncMock()
    # Skip the admin-redirect grace sleep + neutralise the real-time tick loop.
    monkeypatch.setattr(
        "custom_components.quizify.server.websocket.asyncio.sleep", AsyncMock()
    )
    monkeypatch.setattr(h, "_start_timer_tick", lambda *a, **k: None)
    return h


# ---------------------------------------------------------------------------
# #298 — next_round / next_question in a real lobby
# ---------------------------------------------------------------------------


class TestNextQuestionLobbyGate:
    @pytest.mark.asyncio
    async def test_next_question_rejected_in_unstarted_lobby(
        self, handler: QuizifyWebSocketHandler, game: QuizifyGameState
    ) -> None:
        """A stray next_round in a real lobby (game never started → game_id is
        None) must be rejected, not advance into a ghost round / empty FINALE."""
        admin = _ws()
        game.add_player("Markus", admin)
        game.get_player("Markus").is_admin = True
        assert game.phase == GamePhase.LOBBY
        assert game.game_id is None

        await handler._handle_next_question(admin, game)

        handler._conn.send_error.assert_awaited()
        code = handler._conn.send_error.await_args.args[1]
        assert code == ERR_INVALID_ACTION
        # No round started, still a clean lobby.
        assert game.phase == GamePhase.LOBBY
        assert game.round == 0

    @pytest.mark.asyncio
    async def test_internal_start_first_question_still_works(
        self, handler: QuizifyWebSocketHandler, game: QuizifyGameState
    ) -> None:
        """The start→first-question path (start_game has set game_id) is not
        gated — the game must still advance to round 1."""
        admin = _ws()
        game.add_player("Markus", admin)
        game.get_player("Markus").is_admin = True

        await handler._handle_start_game(
            admin,
            {"category": "geographie", "num_rounds": 5, "language": "de",
             "timer_duration": 30},
            game,
        )
        assert game.game_id is not None
        assert game.phase == GamePhase.QUESTION_ACTIVE
        assert game.round == 1


# ---------------------------------------------------------------------------
# #303 — num_rounds validation
# ---------------------------------------------------------------------------


class TestNumRoundsValidation:
    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("7", 7),       # numeric string coerced
            (0, 1),         # clamp low
            (-5, 1),        # clamp negative
            (999, 50),      # clamp high
            ("garbage", 10),  # unparseable → default
            (None, 10),     # None → default
        ],
    )
    async def test_num_rounds_clamped(
        self,
        handler: QuizifyWebSocketHandler,
        game: QuizifyGameState,
        raw,
        expected: int,
    ) -> None:
        admin = _ws()
        game.add_player("Markus", admin)
        game.get_player("Markus").is_admin = True

        await handler._handle_start_game(
            admin,
            {"category": "geographie", "num_rounds": raw, "language": "de",
             "timer_duration": 30},
            game,
        )
        assert game.total_rounds == expected
        assert isinstance(game.total_rounds, int)


# ---------------------------------------------------------------------------
# #308 — empty-pack start error code
# ---------------------------------------------------------------------------


class TestEmptyPackErrorCode:
    @pytest.mark.asyncio
    async def test_empty_pack_reports_no_questions_remaining(
        self, handler: QuizifyWebSocketHandler, game: QuizifyGameState
    ) -> None:
        """Starting with a category that yields no questions must surface
        NO_QUESTIONS_REMAINING — not the misleading GAME_ALREADY_STARTED."""
        admin = _ws()
        game.add_player("Markus", admin)
        game.get_player("Markus").is_admin = True

        await handler._handle_start_game(
            admin,
            {"category": "this-pack-does-not-exist", "num_rounds": 5,
             "language": "de", "timer_duration": 30},
            game,
        )
        handler._conn.send_error.assert_awaited()
        code = handler._conn.send_error.await_args.args[1]
        assert code == ERR_NO_QUESTIONS_REMAINING
        assert code != ERR_GAME_ALREADY_STARTED


# ---------------------------------------------------------------------------
# #311 — admin_skip during QUESTION_ACTIVE
# ---------------------------------------------------------------------------


class TestAdminSkip:
    @pytest.mark.asyncio
    async def test_admin_skip_evaluates_live_question(
        self, handler: QuizifyWebSocketHandler, game: QuizifyGameState
    ) -> None:
        """admin_skip mid-question must evaluate the round immediately
        (→ ANSWER_REVEAL), instead of being a dead no-op (#311)."""
        admin = _ws()
        game.add_player("Markus", admin)
        game.get_player("Markus").is_admin = True
        game.start_game(category="geographie", num_rounds=5, language="de")
        game.start_next_question()
        assert game.phase == GamePhase.QUESTION_ACTIVE

        await handler._handle_admin_skip(admin, game)

        assert game.phase == GamePhase.ANSWER_REVEAL
        # Not reported as an invalid action.
        handler._conn.send_error.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_admin_skip_in_reveal_advances(
        self, handler: QuizifyWebSocketHandler, game: QuizifyGameState
    ) -> None:
        """In ANSWER_REVEAL admin_skip behaves like a normal advance."""
        admin = _ws()
        game.add_player("Markus", admin)
        game.get_player("Markus").is_admin = True
        game.start_game(category="geographie", num_rounds=5, language="de")
        game.start_next_question()
        game.evaluate_round()
        assert game.phase == GamePhase.ANSWER_REVEAL

        await handler._handle_admin_skip(admin, game)
        # Advanced into the next question.
        assert game.phase == GamePhase.QUESTION_ACTIVE
        assert game.round == 2


# ---------------------------------------------------------------------------
# #308 — admin "End lightning" scores the in-flight question
# ---------------------------------------------------------------------------


class TestEndLightningScoresInFlight:
    @pytest.mark.asyncio
    async def test_end_lightning_scores_current_answers(
        self, handler: QuizifyWebSocketHandler, game: QuizifyGameState
    ) -> None:
        """When the admin ends the lightning round early, an already-tapped
        correct answer on the in-flight question must still be scored (#308)
        — previously lr.advance() was missing so those answers were discarded.
        """
        handler._broadcast_lightning_recap = AsyncMock()  # type: ignore[attr-defined]

        player_ws = _ws()
        game.add_player("A", player_ws)
        assert game.start_lightning_round() is True
        assert game.begin_lightning_questions() is True
        lr = game.lightning
        assert lr is not None

        # Player A answers the in-flight question correctly.
        q = lr.current_question
        assert q is not None
        correct_orig = next(i for i, a in enumerate(q.answers) if a.correct)
        order = lr._shuffles["A"]
        shuffled_idx = order.index(correct_orig)
        assert lr.record_answer("A", shuffled_idx) is True
        # Not yet scored — scoring happens on advance().
        assert lr.scores["A"] == 0

        await handler._handle_end_lightning(player_ws, game)

        assert game.phase == GamePhase.LIGHTNING_RECAP
        # The in-flight correct answer was scored before teardown.
        assert lr.scores["A"] > 0
        handler._broadcast_lightning_recap.assert_awaited()
