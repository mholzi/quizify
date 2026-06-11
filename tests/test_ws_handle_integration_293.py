"""WS connection-layer integration tests (issue #293).

The whole ``QuizifyWebSocketHandler.handle()`` entry point — admin auth matrix,
disconnect cleanup, and the admin-disconnect grace — had zero coverage (49%).
These drive the *real* handler over a live aiohttp ``TestClient`` WebSocket so
the auth grant rules and the disconnect path (including the #293 fix that
captures the admin flag BEFORE remove_connection) are exercised end to end.

Plus a reconnect happy-path test at the handler level: a valid session token
re-attaches the player and the server replies ``reconnected`` with a rotated
token.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT))

from custom_components.quizify.game.state import QuizifyGameState  # noqa: E402
from custom_components.quizify.server import WS_PATH  # noqa: E402
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


def _ws_mock() -> MagicMock:
    ws = MagicMock()
    ws.closed = False
    ws.send_json = AsyncMock()
    return ws


@pytest.fixture
def game(tmp_path: Path) -> QuizifyGameState:
    return QuizifyGameState(runtime=_FakeRuntime(tmp_path), entry_id="test")


@pytest.fixture
def handler(game: QuizifyGameState) -> QuizifyWebSocketHandler:
    runtime = _FakeRuntime(game._runtime.data_dir)  # type: ignore[attr-defined]
    h = QuizifyWebSocketHandler(runtime=runtime, game_state_provider=lambda: game)
    h._conn = ConnectionManager(runtime, lambda: game)
    return h


async def _make_client(handler: QuizifyWebSocketHandler) -> TestClient:
    app = web.Application()
    app.router.add_get(WS_PATH, handler.handle)
    client = TestClient(TestServer(app))
    await client.start_server()
    return client


async def _connect(client: TestClient, query: str = ""):
    """Open a WS and yield to the loop so the server's handle() has run far
    enough to register the connection (add_connection) before we assert on it."""
    ws = await client.ws_connect(WS_PATH + query)
    # The client's ws_connect returns at handshake time; the server's
    # add_connection runs a tick later. Let the server task progress.
    for _ in range(20):
        await asyncio.sleep(0.01)
        if client.app.router:  # always truthy; just yields control
            if len(_handler_of(client)._conn.connections) >= 1:
                break
    return ws


def _handler_of(client: TestClient) -> QuizifyWebSocketHandler:
    # The route handler is the bound handle() method; recover its instance.
    for route in client.app.router.routes():
        h = route.handler
        if hasattr(h, "__self__"):
            return h.__self__  # type: ignore[return-value]
    raise AssertionError("handler not found on app")


# ---------------------------------------------------------------------------
# #293 — admin auth matrix over the live handle()
# ---------------------------------------------------------------------------


class TestAdminAuthMatrix:
    @pytest.mark.asyncio
    async def test_fresh_install_bootstrap_grants_admin(
        self, handler: QuizifyWebSocketHandler
    ) -> None:
        """First-ever ?role=admin connect on a fresh install bootstraps admin
        and a token is persisted."""
        client = await _make_client(handler)
        try:
            ws = await _connect(client, "?role=admin")
            # Bootstrap must have granted admin and persisted a token.
            assert handler._conn._admin_session_token is not None
            assert len(handler._conn._admin_connections) == 1
            await ws.close()
        finally:
            await client.close()

    @pytest.mark.asyncio
    async def test_valid_token_grants_admin(
        self, handler: QuizifyWebSocketHandler
    ) -> None:
        """A connect carrying the persisted token is granted admin (reconnect
        path)."""
        # Seed a known token.
        token = handler._conn.get_or_create_admin_token()
        client = await _make_client(handler)
        try:
            ws = await _connect(client, f"?role=admin&token={token}")
            assert len(handler._conn._admin_connections) == 1
            await ws.close()
        finally:
            await client.close()

    @pytest.mark.asyncio
    async def test_invalid_token_rejected_to_player(
        self, handler: QuizifyWebSocketHandler
    ) -> None:
        """A wrong token (after a token already exists) is NOT granted admin —
        the connection drops to player role only."""
        handler._conn.get_or_create_admin_token()  # a token now exists
        client = await _make_client(handler)
        try:
            ws = await _connect(client, "?role=admin&token=wrong-token")
            assert len(handler._conn._admin_connections) == 0
            await ws.close()
        finally:
            await client.close()

    @pytest.mark.asyncio
    async def test_no_token_player_only(
        self, handler: QuizifyWebSocketHandler
    ) -> None:
        """A plain player connect (no role) is never admin."""
        handler._conn.get_or_create_admin_token()
        client = await _make_client(handler)
        try:
            ws = await _connect(client)
            assert len(handler._conn._admin_connections) == 0
            assert len(handler._conn.connections) == 1
            await ws.close()
        finally:
            await client.close()


# ---------------------------------------------------------------------------
# #293 — disconnect cleanup + admin grace actually fires
# ---------------------------------------------------------------------------


class TestDisconnectCleanup:
    @pytest.mark.asyncio
    async def test_disconnect_removes_connection(
        self, handler: QuizifyWebSocketHandler
    ) -> None:
        client = await _make_client(handler)
        try:
            ws = await _connect(client)
            assert len(handler._conn.connections) == 1
            await ws.close()
            # Give the server's finally-block a moment to run.
            await asyncio.sleep(0.1)
            assert len(handler._conn.connections) == 0
        finally:
            await client.close()

    @pytest.mark.asyncio
    async def test_admin_disconnect_schedules_grace_timeout(
        self, handler: QuizifyWebSocketHandler
    ) -> None:
        """#293 core fix: on the LAST admin disconnect the admin-session grace
        timeout must actually be scheduled. Before the fix, remove_connection
        ran first so is_admin_connection() was already False and the timeout
        never armed."""
        client = await _make_client(handler)
        try:
            ws = await _connect(client, "?role=admin")
            assert len(handler._conn._admin_connections) == 1
            await ws.close()
            await asyncio.sleep(0.1)
            # The grace task must be pending now (it clears the token after
            # ADMIN_SESSION_GRACE seconds; we only assert it was armed).
            assert handler._conn.has_pending_admin_disconnect()
            # Clean up the pending task so it doesn't leak.
            handler._conn.cancel_admin_disconnect()
        finally:
            await client.close()


# ---------------------------------------------------------------------------
# #293 — reconnect happy path (handler level)
# ---------------------------------------------------------------------------


class TestReconnectHappyPath:
    @pytest.mark.asyncio
    async def test_valid_token_reattaches_and_rotates(
        self, handler: QuizifyWebSocketHandler, game: QuizifyGameState
    ) -> None:
        handler._conn.broadcast = AsyncMock()  # type: ignore[method-assign]

        # A player exists and holds a valid session token.
        old_ws = _ws_mock()
        game.add_player("Dora", old_ws)
        token = handler._conn.create_session_token("Dora")
        # Simulate the player having dropped.
        game.get_player("Dora").connected = False

        new_ws = _ws_mock()
        await handler._handle_reconnect(
            new_ws, {"session_token": token, "name": "Dora"}, game
        )

        # Player re-attached to the new ws + marked connected.
        player = game.get_player("Dora")
        assert player.connected is True
        assert player.ws is new_ws

        # Server replied `reconnected` with a rotated (different) token.
        sent = [
            c.args[0] for c in new_ws.send_json.await_args_list
            if c.args and isinstance(c.args[0], dict)
        ]
        reconnected = next(m for m in sent if m.get("type") == "reconnected")
        assert reconnected["player_id"] == "Dora"
        assert reconnected["session_token"] != token
        # Old token revoked, new token resolves to the player.
        assert handler._conn.get_player_for_token(token) is None
        assert (
            handler._conn.get_player_for_token(reconnected["session_token"]) == "Dora"
        )
