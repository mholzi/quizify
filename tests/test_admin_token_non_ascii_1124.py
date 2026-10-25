"""Regression tests: a malformed admin token must never raise (found in #1124).

``validate_admin_token`` passed the client token straight into
``hmac.compare_digest``, which raises ``TypeError`` for a ``str`` holding
non-ASCII characters and for a non-``str`` operand. That never granted admin,
but on the handshake path the exception fired after the per-IP connection
counter (#361) was incremented and before the ``finally`` that releases the
slot, so every such handshake burned one slot of that address's cap for good.
Over Nabu Casa every remote player shares the loopback address, so the whole
room could be locked out.

These pin both halves of the fix: the validator rejects without raising, and
the handshake releases its slot even when something before the read loop
raises.
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

_TOKEN = "0f8b4a52-1c3e-4d7a-9b1f-2a6c8e4d5f70"


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
def conn(game: QuizifyGameState) -> ConnectionManager:
    runtime = _FakeRuntime(game._runtime.data_dir)  # type: ignore[attr-defined]
    c = ConnectionManager(runtime, lambda: game)
    c._admin_session_token = _TOKEN
    c._admin_token_loaded = True
    return c


@pytest.fixture
def handler(game: QuizifyGameState, conn: ConnectionManager) -> QuizifyWebSocketHandler:
    runtime = _FakeRuntime(game._runtime.data_dir)  # type: ignore[attr-defined]
    h = QuizifyWebSocketHandler(runtime=runtime, game_state_provider=lambda: game)
    h._conn = conn
    return h


async def _make_client(handler: QuizifyWebSocketHandler) -> TestClient:
    app = web.Application()
    app.router.add_get(WS_PATH, handler.handle)
    client = TestClient(TestServer(app))
    await client.start_server()
    return client


async def _wait_ip_count(handler: QuizifyWebSocketHandler, total: int) -> None:
    for _ in range(100):
        if sum(handler._ip_connections.values()) == total:
            return
        await asyncio.sleep(0.01)


class TestValidateAdminToken:
    @pytest.mark.parametrize("token", ["ä", "ü" * 36, _TOKEN[:-1] + "é", "\udcff"])
    def test_non_ascii_token_is_rejected_without_raising(
        self, conn: ConnectionManager, token: str
    ) -> None:
        assert conn.validate_admin_token(token) is False

    @pytest.mark.parametrize(
        "token", [None, 123, 1.5, True, ["x"], {"t": _TOKEN}, _TOKEN.encode()]
    )
    def test_non_string_token_is_rejected_without_raising(
        self, conn: ConnectionManager, token: object
    ) -> None:
        assert conn.validate_admin_token(token) is False

    def test_empty_and_wrong_tokens_are_rejected(self, conn: ConnectionManager) -> None:
        assert conn.validate_admin_token("") is False
        assert conn.validate_admin_token("wrong") is False

    def test_correct_token_is_accepted(self, conn: ConnectionManager) -> None:
        assert conn.validate_admin_token(_TOKEN) is True

    def test_no_token_issued_never_validates(self, conn: ConnectionManager) -> None:
        conn._admin_session_token = None
        assert conn.validate_admin_token(_TOKEN) is False


class TestHandshakeReleasesSlot:
    @pytest.mark.asyncio
    async def test_repeated_non_ascii_handshakes_do_not_exhaust_the_cap(
        self, handler: QuizifyWebSocketHandler
    ) -> None:
        handler.MAX_CONNECTIONS_PER_IP = 3
        handler.MAX_CONNECTIONS_PER_LOOPBACK = 3
        client = await _make_client(handler)
        try:
            for _ in range(handler.MAX_CONNECTIONS_PER_IP * 3):
                ws = await client.ws_connect(
                    WS_PATH, params={"role": "admin", "token": "ä"}
                )
                await ws.close()
                await _wait_ip_count(handler, 0)
                assert sum(handler._ip_connections.values()) == 0
            assert not handler._conn.has_admin_connections()

            # The address still gets in, and the real token still grants admin.
            ws = await client.ws_connect(
                WS_PATH, params={"role": "admin", "token": _TOKEN}
            )
            await _wait_ip_count(handler, 1)
            assert handler._conn.has_admin_connections()
            await ws.close()
            await _wait_ip_count(handler, 0)
            assert handler._ip_connections == {}
        finally:
            await client.close()

    @pytest.mark.asyncio
    async def test_slot_is_released_when_the_handshake_raises(
        self, handler: QuizifyWebSocketHandler
    ) -> None:
        """Any exception between the increment and the read loop releases the
        slot, not just the token case."""
        handler.MAX_CONNECTIONS_PER_IP = 2
        handler.MAX_CONNECTIONS_PER_LOOPBACK = 2
        # The exception propagates out of handle(), so aiohttp never closes the
        # socket server-side; without a heartbeat it leaves no timer behind
        # for the HA test plugin's lingering-timer check.
        handler.HEARTBEAT_INTERVAL = None  # type: ignore[assignment]

        async def _boom(*_args: object, **_kwargs: object) -> bool:
            raise RuntimeError("handshake failure")

        handler._grant_admin = _boom  # type: ignore[method-assign]
        client = await _make_client(handler)
        try:
            for _ in range(handler.MAX_CONNECTIONS_PER_IP * 3):
                ws = await client.ws_connect(WS_PATH)
                await ws.close()
                await _wait_ip_count(handler, 0)
            assert handler._ip_connections == {}
            assert len(handler._conn.connections) == 0
        finally:
            await client.close()
