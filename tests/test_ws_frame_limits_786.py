"""Regression tests for issue #786 (security).

``web.WebSocketResponse(heartbeat=...)`` was created without ``max_msg_size``,
so aiohttp's 4 MiB default applied to every unauthenticated player/dashboard
socket. The flood guard counts **frames**, not bytes (15/s per connection), and
``msg.json()`` runs synchronously on Home Assistant's shared event loop — so one
LAN guest could push 15 x 4 MiB of JSON per second through a single socket, and
hold up to fifteen sockets. Real Quizify frames are a few hundred bytes.

The related edge: a deeply nested frame makes ``msg.json()`` raise
``RecursionError``, which ``except ValueError`` did not catch, so it escaped the
read loop and tore the connection down with a traceback instead of answering
with the structured error every other malformed frame gets.
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import pytest
from aiohttp import WSMessage, WSMsgType, web
from aiohttp.test_utils import TestClient, TestServer

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT))

from custom_components.quizify.const import ERR_INVALID_ACTION  # noqa: E402
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


class TestFrameSizeCap:
    def test_the_cap_is_far_below_aiohttps_default(self) -> None:
        """4 MiB is aiohttp's default; the largest real frame is < 1 KiB."""
        assert QuizifyWebSocketHandler.MAX_MSG_SIZE == 16 * 1024

    @pytest.mark.asyncio
    async def test_an_oversized_frame_is_closed_not_parsed(
        self, handler: QuizifyWebSocketHandler
    ) -> None:
        """aiohttp answers 1009 (message too big) before buffering or parsing."""
        client = await _make_client(handler)
        try:
            ws = await client.ws_connect(WS_PATH)
            oversized = json.dumps(
                {"type": "join", "name": "x" * (handler.MAX_MSG_SIZE * 2)}
            )
            assert len(oversized) > handler.MAX_MSG_SIZE
            await ws.send_str(oversized)

            msg = await asyncio.wait_for(ws.receive(), timeout=5)
            assert msg.type in (WSMsgType.CLOSE, WSMsgType.CLOSED, WSMsgType.ERROR), (
                f"the oversized frame was accepted and processed: {msg!r}"
            )
            if msg.type is WSMsgType.CLOSE:
                assert msg.data == 1009, f"expected close code 1009, got {msg.data}"
            await ws.close()
        finally:
            await client.close()

    @pytest.mark.asyncio
    async def test_a_normal_frame_still_gets_through(
        self, handler: QuizifyWebSocketHandler
    ) -> None:
        """The cap must not bite a real message."""
        client = await _make_client(handler)
        try:
            ws = await client.ws_connect(WS_PATH)
            await ws.send_str(json.dumps({"type": "definitely_not_a_type"}))
            msg = await asyncio.wait_for(ws.receive_json(), timeout=5)
            assert msg["type"] == "error"
            await ws.close()
        finally:
            await client.close()


class TestRecursionErrorOnParse:
    @pytest.mark.asyncio
    async def test_a_recursion_error_answers_instead_of_tearing_down(
        self,
        handler: QuizifyWebSocketHandler,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """``RecursionError`` is not a ``ValueError``.

        Forced rather than provoked with real nesting: the depth at which
        ``json.loads`` gives up is a property of the running CPython (and of
        ``sys.getrecursionlimit()``), so a payload that raises today may parse
        fine on the next interpreter — and since #786 also caps frames at
        16 KiB, the deepest payload that fits is right on that boundary. What
        this pins is the handler's behaviour when the parse *does* raise.
        """

        def _boom(self, *args, **kwargs):  # noqa: ANN001, ANN002, ANN003, ANN202
            raise RecursionError("maximum recursion depth exceeded")

        monkeypatch.setattr(WSMessage, "json", _boom)

        client = await _make_client(handler)
        try:
            ws = await client.ws_connect(WS_PATH)
            await ws.send_str('{"type": "join", "name": "Guest"}')

            msg = await asyncio.wait_for(ws.receive(), timeout=5)
            assert msg.type is WSMsgType.TEXT, (
                "the RecursionError escaped the read loop and closed the "
                f"socket instead of answering: {msg!r}"
            )
            payload = json.loads(msg.data)
            assert payload["type"] == "error"
            assert payload["code"] == ERR_INVALID_ACTION

            # And the socket is still usable afterwards.
            assert not ws.closed
            await ws.close()
        finally:
            await client.close()
