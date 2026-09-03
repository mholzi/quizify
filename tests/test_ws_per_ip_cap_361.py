"""Regression tests for issue #361 (P2 security).

``handle()`` accepted unlimited WebSocket connections: the existing flood guard
is per-connection (keyed on ``id(ws)``), so opening N sockets bypassed it. Fix:
track connections per ``request.remote`` and refuse ``ws.prepare`` beyond a
generous cap (returning HTTP 429), plus a per-IP ``SlidingWindowLimiter`` on
join attempts. The per-IP count is released on disconnect.

These drive the real ``handle()`` over a live aiohttp ``TestClient`` WebSocket
(mirrors ``tests/test_ws_handle_integration_293.py``).
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest
from aiohttp import WSServerHandshakeError, web
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


async def _wait_conns(handler: QuizifyWebSocketHandler, count: int) -> None:
    for _ in range(50):
        await asyncio.sleep(0.01)
        if len(handler._conn.connections) == count:
            return


class TestPerIpConnectionCap:
    @pytest.mark.asyncio
    async def test_nth_connection_from_one_ip_is_refused(
        self, handler: QuizifyWebSocketHandler
    ) -> None:
        """Once the per-IP cap is reached, the next connection from that IP is
        refused with HTTP 429 (before the socket is upgraded); the earlier
        sockets stay open."""
        handler.MAX_CONNECTIONS_PER_IP = 3  # keep the test fast
        # The TestClient connects from 127.0.0.1, which since #701 gets the
        # larger loopback cap; lower it too so this still exercises the cap.
        handler.MAX_CONNECTIONS_PER_LOOPBACK = 3
        client = await _make_client(handler)
        opened = []
        try:
            for _ in range(handler.MAX_CONNECTIONS_PER_IP):
                opened.append(await client.ws_connect(WS_PATH))
            await _wait_conns(handler, handler.MAX_CONNECTIONS_PER_IP)

            # The next (cap+1) connection from the same IP is refused.
            with pytest.raises(WSServerHandshakeError) as exc:
                await client.ws_connect(WS_PATH)
            assert exc.value.status == 429

            # The already-open sockets are unaffected.
            assert len(handler._conn.connections) == handler.MAX_CONNECTIONS_PER_IP
        finally:
            for ws in opened:
                await ws.close()
            await client.close()

    @pytest.mark.asyncio
    async def test_a_different_ip_is_unaffected(
        self, handler: QuizifyWebSocketHandler
    ) -> None:
        """The cap is strictly per source IP: a foreign IP being maxed out must
        not block connections from a different (real) client IP."""
        handler.MAX_CONNECTIONS_PER_IP = 3
        # Pretend a completely different IP has already saturated its own quota.
        handler._ip_connections["10.13.37.99"] = 999
        client = await _make_client(handler)  # this client is 127.0.0.1
        try:
            ws = await client.ws_connect(WS_PATH)
            await _wait_conns(handler, 1)
            assert len(handler._conn.connections) == 1
            # The foreign IP's count is untouched and independent.
            assert handler._ip_connections["10.13.37.99"] == 999
            await ws.close()
        finally:
            await client.close()

    @pytest.mark.asyncio
    async def test_count_is_released_on_disconnect(
        self, handler: QuizifyWebSocketHandler
    ) -> None:
        """Disconnecting frees the per-IP slot; when the last one closes the
        key is dropped entirely so the dict stays bounded."""
        handler.MAX_CONNECTIONS_PER_IP = 3
        client = await _make_client(handler)
        try:
            ws = await client.ws_connect(WS_PATH)
            await _wait_conns(handler, 1)
            # The source IP now has exactly one tracked connection.
            assert sum(handler._ip_connections.values()) == 1
            key = next(iter(handler._ip_connections))

            await ws.close()
            # Give the server's finally-block a moment to run.
            for _ in range(50):
                await asyncio.sleep(0.01)
                if key not in handler._ip_connections:
                    break
            # Count released; empty key popped.
            assert key not in handler._ip_connections
        finally:
            await client.close()


class TestPerIpJoinLimiter:
    @pytest.mark.asyncio
    async def test_join_flood_from_one_ip_is_rate_limited(
        self, handler: QuizifyWebSocketHandler, game: QuizifyGameState
    ) -> None:
        """A burst of join attempts from one IP beyond the per-IP window is
        answered with an error rather than accepted."""
        game.start_game(language="de", num_rounds=3, difficulty="easy")
        # Shrink the window so the test doesn't need 30 joins.
        from custom_components.quizify.server.rate_limit import SlidingWindowLimiter

        handler._join_limiter = SlidingWindowLimiter(
            max_requests=2, window=60.0, clock=lambda: asyncio.get_event_loop().time()
        )
        client = await _make_client(handler)
        try:
            ws = await client.ws_connect(WS_PATH)
            await _wait_conns(handler, 1)

            errors = []
            for i in range(4):
                await ws.send_json({"type": "join", "name": f"P{i}"})
            # Drain a few frames and count "Too many join attempts" errors.
            for _ in range(20):
                try:
                    msg = await asyncio.wait_for(ws.receive(), timeout=0.1)
                except TimeoutError:
                    break
                if msg.type == web.WSMsgType.TEXT:
                    data = msg.json()
                    if "join" in str(data.get("message", "")).lower():
                        errors.append(data)
            assert errors, "expected at least one per-IP join rate-limit error"
            await ws.close()
        finally:
            await client.close()
