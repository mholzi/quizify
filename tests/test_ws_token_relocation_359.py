"""Regression tests for issue #359 (P2 security).

The admin session token used to be read from the WS URL query string
(``?token=...``), so the long-lived credential landed in aiohttp/HA access
logs, reverse-proxy logs and browser history.

Fix: the token is now accepted via an ``X-Quizify-Token`` header OR — because a
browser WS handshake can't set headers — via a first-message ``admin_auth``
frame validated *before* admin is granted. The deprecated ``?token=`` query
param is kept as a backward-compatible fallback.

These drive the real ``handle()`` over a live aiohttp ``TestClient`` WebSocket
(mirrors ``tests/test_ws_handle_integration_293.py``).
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

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

    def create_task(self, coro):  # noqa: ANN001, ANN202
        return asyncio.ensure_future(coro)

    async def run_in_executor(self, func, *args):  # noqa: ANN001, ANN002
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, func, *args)


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


async def _connect(client: TestClient, handler: QuizifyWebSocketHandler, query=""):
    ws = await client.ws_connect(WS_PATH + query)
    for _ in range(50):
        await asyncio.sleep(0.01)
        if len(handler._conn.connections) >= 1:
            break
    return ws


async def _wait_admin(handler: QuizifyWebSocketHandler, count: int) -> None:
    for _ in range(50):
        await asyncio.sleep(0.01)
        if len(handler._conn._admin_connections) == count:
            return


class TestAdminAuthFirstMessage:
    @pytest.mark.asyncio
    async def test_admin_auth_frame_grants_admin(
        self, handler: QuizifyWebSocketHandler
    ) -> None:
        """A first-message ``admin_auth`` frame carrying the valid token grants
        admin — with NO token anywhere in the URL."""
        token = handler._conn.get_or_create_admin_token()  # token already exists
        client = await _make_client(handler)
        try:
            # Bare ?role=admin, no ?token= → not admin at handshake (a token
            # already exists on disk, so the bootstrap path does not fire).
            ws = await _connect(handler=handler, client=client, query="?role=admin")
            assert len(handler._conn._admin_connections) == 0

            # Now authenticate out-of-URL.
            await ws.send_json({"type": "admin_auth", "token": token})
            await _wait_admin(handler, 1)
            assert len(handler._conn._admin_connections) == 1
            await ws.close()
        finally:
            await client.close()

    @pytest.mark.asyncio
    async def test_admin_auth_bad_token_stays_player_fail_soft(
        self, handler: QuizifyWebSocketHandler
    ) -> None:
        """A bad ``admin_auth`` token never closes the socket — the connection
        stays a plain player (fail-soft, host can't lock itself out)."""
        handler._conn.get_or_create_admin_token()
        client = await _make_client(handler)
        try:
            ws = await _connect(handler=handler, client=client, query="?role=admin")
            await ws.send_json({"type": "admin_auth", "token": "wrong-token"})
            await asyncio.sleep(0.1)
            assert len(handler._conn._admin_connections) == 0
            # Socket still open + usable.
            assert not ws.closed
            assert len(handler._conn.connections) == 1
            await ws.close()
        finally:
            await client.close()


class TestQueryParamFallback:
    @pytest.mark.asyncio
    async def test_deprecated_token_query_param_still_grants_admin(
        self, handler: QuizifyWebSocketHandler
    ) -> None:
        """The deprecated ``?token=`` fallback must keep working for mixed
        old/new clients."""
        token = handler._conn.get_or_create_admin_token()
        client = await _make_client(handler)
        try:
            ws = await _connect(
                handler=handler, client=client, query=f"?role=admin&token={token}"
            )
            await _wait_admin(handler, 1)
            assert len(handler._conn._admin_connections) == 1
            await ws.close()
        finally:
            await client.close()


class TestHeaderToken:
    @pytest.mark.asyncio
    async def test_x_quizify_token_header_grants_admin(
        self, handler: QuizifyWebSocketHandler
    ) -> None:
        """The token may also arrive in the ``X-Quizify-Token`` header (kept
        out of the URL / logs) for non-browser clients."""
        token = handler._conn.get_or_create_admin_token()
        client = await _make_client(handler)
        try:
            ws = await client.ws_connect(
                WS_PATH + "?role=admin", headers={"X-Quizify-Token": token}
            )
            await _wait_admin(handler, 1)
            assert len(handler._conn._admin_connections) == 1
            await ws.close()
        finally:
            await client.close()


class TestTokenNotRequiredInUrl:
    @pytest.mark.asyncio
    async def test_url_no_longer_carries_token(
        self, handler: QuizifyWebSocketHandler
    ) -> None:
        """End-to-end proof that admin control is reachable without the token
        ever appearing in the WS URL: bare ?role=admin + admin_auth frame."""
        token = handler._conn.get_or_create_admin_token()
        client = await _make_client(handler)
        try:
            ws = await _connect(handler=handler, client=client, query="?role=admin")
            await ws.send_json({"type": "admin_auth", "token": token})
            await _wait_admin(handler, 1)
            assert handler._conn.is_admin_connection(
                next(iter(handler._conn._admin_connections))
            )
            await ws.close()
        finally:
            await client.close()
