"""Regression tests for issue #701.

Over Nabu Casa every remote client reaches Home Assistant through snitun on
127.0.0.1, and HA's forwarded-header middleware ignores ``X-Forwarded-For``
for cloud requests — so ``request.remote`` is the same string for every phone,
the television and the admin. With the flat per-IP cap of 15 from #361 the
room filled up at roughly thirteen remote players: every further handshake got
an HTTP 429, and the phone sat in "reconnecting…" forever.

Loopback now gets its own, room-sized budget for both the concurrent-socket
cap and the join-flood guard, while a real remote address keeps the strict
per-IP limits the security fix put there.
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

from custom_components.quizify.const import MAX_PLAYERS  # noqa: E402
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


class TestLoopbackIsNotOneDevice:
    def test_loopback_addresses_are_recognised(
        self, handler: QuizifyWebSocketHandler
    ) -> None:
        assert handler._is_loopback("127.0.0.1") is True
        assert handler._is_loopback("127.1.2.3") is True
        assert handler._is_loopback("::1") is True

    def test_real_addresses_and_junk_are_not_loopback(
        self, handler: QuizifyWebSocketHandler
    ) -> None:
        assert handler._is_loopback("192.168.1.42") is False
        assert handler._is_loopback("2001:db8::1") is False
        # A hostname or anything unparseable must not be granted the exception.
        assert handler._is_loopback("some-host.local") is False
        assert handler._is_loopback("") is False

    def test_loopback_cap_covers_a_full_room(
        self, handler: QuizifyWebSocketHandler
    ) -> None:
        """A full room, everyone reloading at once, plus TV and admin."""
        cap = handler._connection_cap("127.0.0.1")
        assert cap >= MAX_PLAYERS * 2 + 2
        assert cap == handler.MAX_CONNECTIONS_PER_LOOPBACK

    def test_remote_address_keeps_the_strict_cap(
        self, handler: QuizifyWebSocketHandler
    ) -> None:
        assert handler._connection_cap("192.168.1.42") == handler.MAX_CONNECTIONS_PER_IP

    @pytest.mark.asyncio
    async def test_sixteenth_cloud_connection_is_accepted(
        self, handler: QuizifyWebSocketHandler
    ) -> None:
        """The bug itself: over Nabu Casa the 16th socket used to get a 429.

        The TestClient connects from 127.0.0.1, which is exactly the shape a
        cloud connection arrives in.
        """
        opened = []
        client = await _make_client(handler)
        try:
            for _ in range(handler.MAX_CONNECTIONS_PER_IP + 1):
                opened.append(await client.ws_connect(WS_PATH))
            assert len(opened) == handler.MAX_CONNECTIONS_PER_IP + 1
        finally:
            for ws in opened:
                await ws.close()
            await client.close()

    @pytest.mark.asyncio
    async def test_loopback_cap_still_bites_eventually(
        self, handler: QuizifyWebSocketHandler
    ) -> None:
        """The exception raises the ceiling, it does not remove it."""
        handler.MAX_CONNECTIONS_PER_LOOPBACK = 2
        opened = []
        client = await _make_client(handler)
        try:
            for _ in range(2):
                opened.append(await client.ws_connect(WS_PATH))
            for _ in range(50):
                await asyncio.sleep(0.01)
                if len(handler._conn.connections) == 2:
                    break
            with pytest.raises(WSServerHandshakeError) as exc:
                await client.ws_connect(WS_PATH)
            assert exc.value.status == 429
        finally:
            for ws in opened:
                await ws.close()
            await client.close()


class TestLoopbackJoinBudget:
    def test_loopback_gets_the_room_sized_join_limiter(
        self, handler: QuizifyWebSocketHandler
    ) -> None:
        assert handler._join_limiter_for("127.0.0.1") is handler._loopback_join_limiter
        assert handler._join_limiter_for("192.168.1.42") is handler._join_limiter

    def test_a_full_room_can_join_and_reconnect_over_the_cloud(
        self, handler: QuizifyWebSocketHandler
    ) -> None:
        """20 players joining, then rejoining twice, must not trip the guard.

        The flat 30-per-minute budget was a whole room plus barely a handful
        of reconnects — over Nabu Casa that is one shared bucket.
        """
        limiter = handler._join_limiter_for("127.0.0.1")
        for _ in range(MAX_PLAYERS * 3):
            assert limiter.check("127.0.0.1") is True

    def test_one_real_device_keeps_the_strict_join_budget(
        self, handler: QuizifyWebSocketHandler
    ) -> None:
        limiter = handler._join_limiter_for("192.168.1.42")
        allowed = sum(1 for _ in range(60) if limiter.check("192.168.1.42"))
        assert allowed == 30
