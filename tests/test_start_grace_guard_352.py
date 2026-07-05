"""Regression test (#352): the start_game / play_again grace-window
continuation must re-validate game state before firing round 1.

Both _handle_start_game and _handle_play_again mint a fresh game_id,
then `await asyncio.sleep(START_REDIRECT_GRACE)` (~2.5s) to give the
admin-as-player tab time to redirect+reconnect, then UNCONDITIONALLY
called `_start_next_question`. During that yield another admin socket
can:

  * send reset_game  -> game_id cleared, players dropped. The stale
    continuation would fire round 1 into a reset / zero-player lobby.
  * send a second start_game -> a NEW game_id is minted. The first
    continuation would then double-advance and wedge the round (two
    _start_next_question calls, timers fighting).

The fix snapshots the minted game_id BEFORE the sleep and, after
waking, only starts the question when `game_id` is unchanged AND the
phase is still LOBBY. Otherwise it bails.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT))

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


def _ws(closed: bool = False) -> MagicMock:
    ws = MagicMock()
    ws.closed = closed
    ws.send_json = AsyncMock()
    return ws


@pytest.fixture
def game(tmp_path: Path) -> QuizifyGameState:
    runtime = _FakeRuntime(tmp_path)
    return QuizifyGameState(runtime=runtime, entry_id="test")


@pytest.fixture
def handler(game: QuizifyGameState) -> QuizifyWebSocketHandler:
    runtime = _FakeRuntime(game._runtime.data_dir)  # type: ignore[attr-defined]
    h = QuizifyWebSocketHandler(
        runtime=runtime,
        game_state_provider=lambda: game,
    )
    h._conn = ConnectionManager(runtime, lambda: game)
    h._conn.broadcast = AsyncMock()
    # Short grace so the tests don't wait 2.5s.
    h.START_REDIRECT_GRACE = 0.1
    return h


_START_DATA = {
    "num_rounds": 3,
    "difficulty": "easy",
    "language": "de",
    "lightning_enabled": False,
}


class TestStartGraceGuard:
    @pytest.mark.asyncio
    async def test_reset_during_grace_starts_no_question(
        self, handler: QuizifyWebSocketHandler, game: QuizifyGameState
    ) -> None:
        """A reset_game landing during the grace window (game_id cleared)
        must abort the stale continuation — no round is started into the
        reset lobby."""
        admin_ws = _ws()
        game.add_player("Admin", admin_ws)
        game.get_player("Admin").is_admin = True

        start_spy = AsyncMock()
        handler._start_next_question = start_spy  # type: ignore[assignment]

        task = asyncio.ensure_future(
            handler._handle_start_game(admin_ws, dict(_START_DATA), game)
        )
        # Let it reach start_game + the grace sleep.
        await asyncio.sleep(handler.START_REDIRECT_GRACE / 2)
        assert game.game_id is not None  # start_game minted an id
        assert game.phase == GamePhase.LOBBY

        # Concurrent reset_game lands: clears game_id, back to a fresh lobby.
        game.reset_to_lobby()
        assert game.game_id is None

        await task

        start_spy.assert_not_called()
        assert game.phase == GamePhase.LOBBY

    @pytest.mark.asyncio
    async def test_second_start_during_grace_does_not_wedge(
        self, handler: QuizifyWebSocketHandler, game: QuizifyGameState
    ) -> None:
        """A second start_game arriving during the first one's grace window
        mints a new game_id. Exactly ONE question must start (the winner's),
        not two — otherwise the round double-advances / wedges."""
        admin_ws = _ws()
        game.add_player("Admin", admin_ws)
        game.get_player("Admin").is_admin = True

        start_spy = AsyncMock()
        handler._start_next_question = start_spy  # type: ignore[assignment]

        first = asyncio.ensure_future(
            handler._handle_start_game(admin_ws, dict(_START_DATA), game)
        )
        # Let the first reach its grace sleep.
        await asyncio.sleep(handler.START_REDIRECT_GRACE / 2)
        first_game_id = game.game_id
        assert first_game_id is not None

        # Second start_game from another admin socket, while first is asleep.
        second = asyncio.ensure_future(
            handler._handle_start_game(admin_ws, dict(_START_DATA), game)
        )
        await asyncio.sleep(handler.START_REDIRECT_GRACE / 4)
        # A brand-new game_id was minted by the second start.
        assert game.game_id is not None
        assert game.game_id != first_game_id

        await asyncio.gather(first, second)

        # Only the winning (second) continuation fired the question.
        assert start_spy.await_count == 1

    @pytest.mark.asyncio
    async def test_clean_start_still_starts_question(
        self, handler: QuizifyWebSocketHandler, game: QuizifyGameState
    ) -> None:
        """No interference: the continuation must still fire normally."""
        admin_ws = _ws()
        game.add_player("Admin", admin_ws)
        game.get_player("Admin").is_admin = True

        start_spy = AsyncMock()
        handler._start_next_question = start_spy  # type: ignore[assignment]

        await handler._handle_start_game(admin_ws, dict(_START_DATA), game)

        start_spy.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_play_again_reset_during_grace_starts_no_question(
        self, handler: QuizifyWebSocketHandler, game: QuizifyGameState
    ) -> None:
        """Same guard on the play_again path: a reset during its grace
        window aborts the continuation."""
        admin_ws = _ws()
        game.add_player("Admin", admin_ws)
        game.get_player("Admin").is_admin = True
        # Prime last_settings so play_again takes the rematch path.
        game.start_game(num_rounds=3, difficulty="easy", language="de")
        game.reset_to_lobby()
        assert game.last_settings

        start_spy = AsyncMock()
        handler._start_next_question = start_spy  # type: ignore[assignment]

        task = asyncio.ensure_future(handler._handle_play_again(admin_ws, game))
        await asyncio.sleep(handler.START_REDIRECT_GRACE / 2)
        assert game.game_id is not None

        game.reset_to_lobby()  # concurrent reset lands

        await task

        start_spy.assert_not_called()
        assert game.phase == GamePhase.LOBBY
