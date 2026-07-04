"""Regression tests for issue #351 (P1 security).

The admin session token (the persisted credential that gates full game
control and, since #140/#168, blocks LAN admin-takeover) used to be wiped
~120s into **every** admin-as-player game.

Root cause: at game start the host's ``/quizify/admin`` tab redirects to
``/quizify/player``. The only ``?role=admin`` WS closes → ``_handle_disconnect``
sees no admin connections and calls ``schedule_admin_timeout()``. Nothing
cancelled it (the admin now lives on the player page, so no ``admin_connect``
ever arrives), so ``ADMIN_SESSION_GRACE`` later the token was cleared and the
``admin_token.json`` file deleted — re-opening the bootstrap-takeover window
for the next LAN client presenting ``?role=admin``.

Two-layer fix:
  1. ``_handle_join`` / ``_handle_reconnect`` cancel the pending admin-disconnect
     timeout when the (re)connecting player holds the crown.
  2. ``admin_timeout()`` refuses to clear the token while a *connected* admin
     player still holds the crown (belt-and-suspenders).
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

    async def run_in_executor(self, func, *args):  # noqa: ANN001, ANN002
        return func(*args)


class _FakePlayer:
    def __init__(self, *, is_admin: bool, connected: bool) -> None:
        self.is_admin = is_admin
        self.connected = connected


class _FakeGameState:
    def __init__(self, players: list[_FakePlayer]) -> None:
        self._players = players

    def get_players(self) -> list[_FakePlayer]:
        return self._players


def _ws(closed: bool = False) -> MagicMock:
    ws = MagicMock()
    ws.closed = closed
    ws.send_json = AsyncMock()
    return ws


async def _run_admin_timeout(conn: ConnectionManager) -> None:
    """Schedule + await the admin-timeout with an instant grace."""
    conn.ADMIN_SESSION_GRACE = 0
    conn.schedule_admin_timeout()
    await asyncio.gather(conn._admin_disconnect_task, return_exceptions=True)


# ---------------------------------------------------------------------------
# Layer 2 — admin_timeout() guard
# ---------------------------------------------------------------------------


class TestAdminTimeoutGuard:
    @pytest.mark.asyncio
    async def test_keeps_token_when_admin_player_connected(
        self, tmp_path: Path
    ) -> None:
        # The host is present as a connected admin player → token must survive
        # even when the grace fires (this is the every-game redirect case).
        gs = _FakeGameState([_FakePlayer(is_admin=True, connected=True)])
        conn = ConnectionManager(_FakeRuntime(tmp_path), lambda: gs)
        await conn.try_bootstrap_admin()
        token = conn._admin_session_token
        assert token is not None

        await _run_admin_timeout(conn)

        assert conn._admin_session_token == token

    @pytest.mark.asyncio
    async def test_clears_token_when_no_admin_player(self, tmp_path: Path) -> None:
        # Host truly left (no admin among the players) → original behaviour:
        # the token is cleared so a fresh bootstrap can happen.
        gs = _FakeGameState([_FakePlayer(is_admin=False, connected=True)])
        conn = ConnectionManager(_FakeRuntime(tmp_path), lambda: gs)
        await conn.try_bootstrap_admin()
        assert conn._admin_session_token is not None

        await _run_admin_timeout(conn)

        assert conn._admin_session_token is None

    @pytest.mark.asyncio
    async def test_clears_token_when_admin_player_disconnected(
        self, tmp_path: Path
    ) -> None:
        # The admin player exists but is no longer connected (host closed the
        # tab and never came back) → clear, matching the prior semantics.
        gs = _FakeGameState([_FakePlayer(is_admin=True, connected=False)])
        conn = ConnectionManager(_FakeRuntime(tmp_path), lambda: gs)
        await conn.try_bootstrap_admin()
        assert conn._admin_session_token is not None

        await _run_admin_timeout(conn)

        assert conn._admin_session_token is None


# ---------------------------------------------------------------------------
# Layer 1 — end-to-end redirect + reconnect cancels the wipe
# ---------------------------------------------------------------------------


class TestAdminRedirectPreservesToken:
    @pytest.mark.asyncio
    async def test_redirect_and_reconnect_preserves_token(
        self, tmp_path: Path
    ) -> None:
        runtime = _FakeRuntime(tmp_path)
        game = QuizifyGameState(runtime=runtime, entry_id="test")
        h = QuizifyWebSocketHandler(runtime=runtime, game_state_provider=lambda: game)
        h._conn = ConnectionManager(runtime, lambda: game)
        h._conn.broadcast = AsyncMock()
        h._conn.send = AsyncMock()

        # Persisted admin token exists (bootstrapped on first admin connect).
        await h._conn.try_bootstrap_admin()
        token = h._conn._admin_session_token
        assert token is not None

        # Admin (as player) + Bob in a live question; admin_ws is the admin conn.
        admin_ws = _ws()
        bob_ws = _ws()
        game.add_player("Admin", admin_ws)
        game.get_player("Admin").is_admin = True
        game.add_player("Bob", bob_ws)
        game.start_game(language="de", num_rounds=3, difficulty="easy")
        assert game.start_next_question() is not None
        assert game.phase == GamePhase.QUESTION_ACTIVE
        h._conn.add_connection(admin_ws, is_admin=True, is_dashboard=False)
        session_token = h._conn.create_session_token("Admin")

        # Short grace so a missed cancel would wipe the token quickly.
        h._conn.ADMIN_SESSION_GRACE = 0.3

        # Admin tab redirects → the admin WS closes.
        admin_ws.closed = True
        h._conn.remove_connection(admin_ws)
        await h._handle_disconnect(admin_ws, was_admin=True)
        assert h._conn.has_pending_admin_disconnect()

        # The admin's new /quizify/player WS reconnects via its session token.
        new_ws = _ws()
        await h._handle_reconnect(new_ws, {"session_token": session_token}, game)

        # The reconnect cancelled the pending token-wipe (#351)…
        assert not h._conn.has_pending_admin_disconnect()
        # …and the persisted token is intact well past the (short) grace.
        await asyncio.sleep(h._conn.ADMIN_SESSION_GRACE * 2)
        assert h._conn._admin_session_token == token

        h._cancel_admin_pause()
