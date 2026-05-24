"""Regression test: admin's intentional admin.html→player.html redirect
after pressing Start must NOT cause every other player's screen to flash
"Lost connection to the host".

The bug pattern (reproduced live before the fix):
  t=0:     Admin and Bob both in lobby. Admin self-joined.
  t=0:     Admin presses Start. Server sleeps 2.5s.
  t=2.5s:  Server broadcasts QUESTION_ACTIVE. Bob renders the question.
  t=~3s:   Admin tab navigates from /quizify/admin to /quizify/player.
           Admin's WS closes. _handle_disconnect ran and IMMEDIATELY
           paused the game with reason=admin_disconnected.
  t=~3s:   Bob's screen switches to paused-view: "Lost connection to host".
  t=~4s:   Admin's NEW player WS reconnects via session token. Server
           auto-resumes. Bob's screen returns to the question.

User-visible symptom: "still not reliably starting — player sees question
briefly then sees not connected".

The fix defers the pause by ADMIN_REDIRECT_GRACE seconds. If admin
reconnects within that window the pause never fires.
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


# ---------- Fixtures ----------


class _FakeRuntime:
    def __init__(self, tmp_path: Path) -> None:
        self.data_dir = tmp_path


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
    """A handler wired to the game state. Shortens grace so tests are fast."""
    runtime = _FakeRuntime(game._runtime.data_dir)  # type: ignore[attr-defined]
    h = QuizifyWebSocketHandler(
        runtime=runtime,
        game_state_provider=lambda: game,
    )
    # Replace ConnectionManager's broadcast with a recorder so we can
    # check what (if anything) the server tried to send.
    h._conn = ConnectionManager(runtime, lambda: game)
    h._conn.broadcast = AsyncMock()
    # Shorter grace so tests don't wait 4s
    h.ADMIN_REDIRECT_GRACE = 0.2
    return h


# ---------- Helpers ----------


async def _put_in_question_active(
    game: QuizifyGameState, admin_ws: MagicMock, bob_ws: MagicMock
) -> None:
    """Add admin (with is_admin) + Bob, start a question."""
    game.add_player("Admin", admin_ws)
    admin = game.get_player("Admin")
    admin.is_admin = True
    game.add_player("Bob", bob_ws)
    game.start_game(language="de", num_rounds=3, difficulty="easy")
    q = game.start_next_question()
    assert q is not None
    assert game.phase == GamePhase.QUESTION_ACTIVE


# ---------- Tests ----------


class TestAdminRedirectPause:
    @pytest.mark.asyncio
    async def test_admin_disconnect_during_question_does_not_pause_immediately(
        self, handler: QuizifyWebSocketHandler, game: QuizifyGameState
    ) -> None:
        """Old behavior was to pause synchronously inside _handle_disconnect.
        The fix replaces that with a scheduled task — phase stays
        QUESTION_ACTIVE the moment the WS closes."""
        admin_ws = _ws()
        bob_ws = _ws()
        await _put_in_question_active(game, admin_ws, bob_ws)
        admin = game.get_player("Admin")
        admin_ws.closed = True

        await handler._handle_disconnect(admin_ws)

        # Pause is deferred — phase should still be QUESTION_ACTIVE.
        assert game.phase == GamePhase.QUESTION_ACTIVE
        assert handler._admin_pause_task is not None
        # Cleanup
        handler._cancel_admin_pause()

    @pytest.mark.asyncio
    async def test_admin_reconnects_in_time_cancels_pause(
        self, handler: QuizifyWebSocketHandler, game: QuizifyGameState
    ) -> None:
        """The normal /quizify/admin → /quizify/player redirect path:
        WS closes, ~1s later a new player WS reconnects. Pause must
        never fire."""
        admin_ws = _ws()
        bob_ws = _ws()
        await _put_in_question_active(game, admin_ws, bob_ws)
        admin = game.get_player("Admin")
        admin_ws.closed = True

        await handler._handle_disconnect(admin_ws)
        assert game.phase == GamePhase.QUESTION_ACTIVE

        # Admin's new player WS reconnects: simulate by flipping the
        # player's connected flag and cancelling the pause (what
        # _handle_reconnect / _handle_join do via _cancel_admin_pause).
        admin.connected = True
        admin.ws = _ws()
        handler._cancel_admin_pause()

        # Wait past the grace period. Pause must NOT have fired.
        await asyncio.sleep(handler.ADMIN_REDIRECT_GRACE * 2)
        assert game.phase == GamePhase.QUESTION_ACTIVE
        # _handle_disconnect itself broadcasts `player_left` — that's
        # fine. What MUST NOT have happened is a PAUSED state broadcast.
        bcasts = [c.args[0] for c in handler._conn.broadcast.await_args_list]
        paused = [
            m for m in bcasts
            if m.get("type") == "game_state" and m.get("phase") == "PAUSED"
        ]
        assert not paused, f"unexpected PAUSED broadcast: {paused}"

    @pytest.mark.asyncio
    async def test_admin_does_not_reconnect_pause_fires_after_grace(
        self, handler: QuizifyWebSocketHandler, game: QuizifyGameState
    ) -> None:
        """Real disconnect (closed tab, lost wifi). Pause must still fire,
        just delayed by the grace period instead of instantly."""
        admin_ws = _ws()
        bob_ws = _ws()
        await _put_in_question_active(game, admin_ws, bob_ws)
        admin_ws.closed = True

        await handler._handle_disconnect(admin_ws)
        assert game.phase == GamePhase.QUESTION_ACTIVE

        # Admin never reconnects. Wait past the grace.
        await asyncio.sleep(handler.ADMIN_REDIRECT_GRACE + 0.3)

        assert game.phase == GamePhase.PAUSED
        # Server broadcast the PAUSED state.
        bcasts = [c.args[0] for c in handler._conn.broadcast.await_args_list]
        paused_msgs = [
            m for m in bcasts
            if m.get("type") == "game_state"
            and m.get("phase") == "PAUSED"
            and m.get("pause_reason") == "admin_disconnected"
        ]
        assert paused_msgs, f"expected PAUSED broadcast, got {bcasts}"

    @pytest.mark.asyncio
    async def test_non_admin_disconnect_never_schedules_pause(
        self, handler: QuizifyWebSocketHandler, game: QuizifyGameState
    ) -> None:
        """A regular player disconnecting must NOT trigger the
        admin-pause path. Only admins pause the game."""
        admin_ws = _ws()
        bob_ws = _ws()
        await _put_in_question_active(game, admin_ws, bob_ws)
        bob_ws.closed = True

        await handler._handle_disconnect(bob_ws)

        # No pause task scheduled, game keeps running.
        assert handler._admin_pause_task is None
        assert game.phase == GamePhase.QUESTION_ACTIVE

    @pytest.mark.asyncio
    async def test_admin_disconnect_in_lobby_never_pauses(
        self, handler: QuizifyWebSocketHandler, game: QuizifyGameState
    ) -> None:
        """The pause is gated on QUESTION_ACTIVE — admin disconnects in
        LOBBY (e.g., closing admin.html before starting) must not
        schedule a pause."""
        admin_ws = _ws()
        bob_ws = _ws()
        game.add_player("Admin", admin_ws)
        game.get_player("Admin").is_admin = True
        game.add_player("Bob", bob_ws)
        # Still in LOBBY, no start_game called.
        assert game.phase == GamePhase.LOBBY
        admin_ws.closed = True

        await handler._handle_disconnect(admin_ws)

        assert handler._admin_pause_task is None
        assert game.phase == GamePhase.LOBBY

    @pytest.mark.asyncio
    async def test_rapid_disconnect_reconnect_disconnect_does_not_stack_tasks(
        self, handler: QuizifyWebSocketHandler, game: QuizifyGameState
    ) -> None:
        """Re-entrancy: scheduling a new pause cancels the prior one,
        so a flaky network producing several close+open cycles doesn't
        stack overlapping pause tasks."""
        admin_ws = _ws()
        bob_ws = _ws()
        await _put_in_question_active(game, admin_ws, bob_ws)
        admin = game.get_player("Admin")

        # First disconnect
        admin_ws.closed = True
        await handler._handle_disconnect(admin_ws)
        first_task = handler._admin_pause_task
        assert first_task is not None

        # Reconnect (cancels first task)
        admin.connected = True
        handler._cancel_admin_pause()
        assert handler._admin_pause_task is None

        # Disconnect again
        admin.connected = False
        await handler._handle_disconnect(admin_ws)
        second_task = handler._admin_pause_task

        # New task, distinct from the cancelled one
        assert second_task is not None
        assert second_task is not first_task
        # Yield to the loop so the cancellation actually completes.
        try:
            await first_task
        except asyncio.CancelledError:
            pass
        assert first_task.cancelled() or first_task.done()
        # Cleanup
        handler._cancel_admin_pause()
