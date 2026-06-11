"""Regression test: issue #244 — admin must not double-join as a player.

The bug:
  - The admin "Join as Player" flow registers the host as a player on the
    SAME admin WebSocket. After the admin joined once, the "Join as Player"
    control stayed active, so a second tap (with a different typed name)
    created a duplicate/ghost player from the one admin session.

Expected (defense in depth, cousin of #207's connected-admin guard):
  - The server rejects a second join over a connection that already holds a
    connected player under a *different* name — no duplicate player is
    created.
  - The first admin-as-player join still succeeds.
  - A re-join under the *same* name over a fresh connection (the admin's
    /admin → /player redirect / lobby refresh) is still allowed.
  - Normal players joining over their own connections are unaffected.
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT))

from custom_components.quizify.game.state import QuizifyGameState  # noqa: E402
from custom_components.quizify.server.connection import ConnectionManager  # noqa: E402
from custom_components.quizify.server.websocket import (  # noqa: E402
    QuizifyWebSocketHandler,
)


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
    runtime = _FakeRuntime(game._runtime.data_dir)  # type: ignore[attr-defined]
    h = QuizifyWebSocketHandler(
        runtime=runtime,
        game_state_provider=lambda: game,
    )
    h._conn = ConnectionManager(runtime, lambda: game)
    h._conn.broadcast = AsyncMock()
    h._conn.send_error = AsyncMock()
    return h


def _names(game: QuizifyGameState) -> list[str]:
    return [p.name for p in game.get_players()]


class TestAdminDoubleJoin:
    @pytest.mark.asyncio
    async def test_second_self_join_rejected(
        self, handler: QuizifyWebSocketHandler, game: QuizifyGameState
    ) -> None:
        # First admin-as-player join over the admin WS — succeeds.
        admin_ws = _ws()
        await handler._handle_join(
            admin_ws, {"name": "Host", "is_admin": True}, game
        )
        assert _names(game) == ["Host"]
        handler._conn.send_error.assert_not_called()

        # Same connection tries to join AGAIN under a different name —
        # must be rejected, no second/ghost player created.
        await handler._handle_join(
            admin_ws, {"name": "HostGhost", "is_admin": True}, game
        )
        assert _names(game) == ["Host"]
        assert game.get_player("HostGhost") is None
        handler._conn.send_error.assert_awaited()

    @pytest.mark.asyncio
    async def test_first_self_join_still_works(
        self, handler: QuizifyWebSocketHandler, game: QuizifyGameState
    ) -> None:
        admin_ws = _ws()
        await handler._handle_join(
            admin_ws, {"name": "Host", "is_admin": True}, game
        )
        assert game.get_player("Host") is not None
        assert game.get_player("Host").is_admin is True
        handler._conn.send_error.assert_not_called()

    @pytest.mark.asyncio
    async def test_rejoin_same_name_new_connection_allowed(
        self, handler: QuizifyWebSocketHandler, game: QuizifyGameState
    ) -> None:
        # /admin → /player redirect opens a fresh WS and re-joins under the
        # same name — must stay a single player (idempotent), not rejected.
        # The old admin WS has dropped by then (browser navigated away), so
        # mark it closed before the re-join, mirroring the live flow.
        ws1 = _ws()
        await handler._handle_join(ws1, {"name": "Host", "is_admin": True}, game)
        ws1.closed = True
        host = game.get_player("Host")
        host.connected = False
        ws2 = _ws()
        await handler._handle_join(ws2, {"name": "Host", "is_admin": True}, game)
        assert _names(game) == ["Host"]
        handler._conn.send_error.assert_not_called()

    @pytest.mark.asyncio
    async def test_normal_players_unaffected(
        self, handler: QuizifyWebSocketHandler, game: QuizifyGameState
    ) -> None:
        # Each player on their own connection — all join fine.
        await handler._handle_join(_ws(), {"name": "Host", "is_admin": True}, game)
        await handler._handle_join(_ws(), {"name": "Alice"}, game)
        await handler._handle_join(_ws(), {"name": "Bob"}, game)
        assert _names(game) == ["Host", "Alice", "Bob"]
