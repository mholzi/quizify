"""Regression tests for issue #227 — failed reconnect must be observable.

Issue #227: opening the player with a dead/stale session (e.g.
``/quizify/player?name=Foo&reconnect=1`` with no joinable game) left the
client on a blank screen — the ``reconnect_failed`` case in player-core.js
cleared the session but never routed to a visible view.

The *fix* is client-side JS (``reconnect_failed`` now calls
``showView('join-view')``, plus a ``default:`` fallback in the game_state
view router). That routing cannot be exercised from Python.

What *is* server-observable — and what the client fix depends on — is that
the server actually emits a ``reconnect_failed`` message for an unknown or
stale session token. These tests lock that contract so a future server
change can't silently drop the message and re-introduce the blank screen.
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


def _ws() -> MagicMock:
    ws = MagicMock()
    ws.closed = False
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
    return h


def _sent_types(ws: MagicMock) -> list[str]:
    """Message ``type`` strings the server sent on *ws* (via _safe_send)."""
    return [
        call.args[0].get("type")
        for call in ws.send_json.await_args_list
        if call.args and isinstance(call.args[0], dict)
    ]


class TestReconnectFailedContract:
    @pytest.mark.asyncio
    async def test_unknown_token_emits_reconnect_failed(
        self, handler: QuizifyWebSocketHandler, game: QuizifyGameState
    ) -> None:
        """A reconnect with a token the server has never seen must reply
        ``reconnect_failed`` — the trigger the client uses to fall back to
        the join screen (issue #227)."""
        ws = _ws()
        await handler._handle_reconnect(
            ws, {"session_token": "no-such-token", "name": "Foo"}, game
        )
        assert "reconnect_failed" in _sent_types(ws)

    @pytest.mark.asyncio
    async def test_stale_token_after_player_removed_emits_reconnect_failed(
        self, handler: QuizifyWebSocketHandler, game: QuizifyGameState
    ) -> None:
        """Token is valid but the player was fully removed (game ended/reset):
        the server must still emit ``reconnect_failed`` and drop the token."""
        token = handler._conn.create_session_token("Foo")
        # Player exists only in the token map, never added to game_state →
        # game_state.get_player("Foo") is None, the stale-token branch.
        ws = _ws()
        await handler._handle_reconnect(
            ws, {"session_token": token, "name": "Foo"}, game
        )
        assert "reconnect_failed" in _sent_types(ws)
        # Stale token must be purged so it can't resurrect later.
        assert handler._conn.get_player_for_token(token) is None
