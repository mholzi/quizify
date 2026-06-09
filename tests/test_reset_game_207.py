"""Regression tests for issue #207 — reset_game must fully clear the
session AND actually deliver the reset signal to every connected client.

The pre-#207 bug: ``_handle_reset_game`` closed all player WebSockets
*before* broadcasting ``game_reset`` / ``game_state``. The host who
pressed reset is frequently an admin-as-player (joined the lobby on the
admin WS), so their own socket was closed first and the reset broadcast
never reached them — their UI stayed frozen on the stale lobby with the
old roster, even though the server had already wiped everything.

These tests pin:
  1. State-level: reset drops every player (incl. the admin) and returns
     the phase to LOBBY.
  2. Delivery-level: ``game_reset`` + ``game_state`` are broadcast while
     the player sockets are STILL OPEN, so real clients (admin + players)
     receive the signal and can return to their initial views.
"""

from __future__ import annotations

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


def _ws() -> MagicMock:
    """A fake WebSocket whose .close() flips .closed to True, mirroring
    aiohttp behaviour so the broadcast skip-closed filter sees it close."""
    ws = MagicMock()
    ws.closed = False
    ws.send_json = AsyncMock()

    async def _close() -> None:
        ws.closed = True

    ws.close = AsyncMock(side_effect=_close)
    return ws


@pytest.fixture
def game(tmp_path: Path) -> QuizifyGameState:
    runtime = _FakeRuntime(tmp_path)
    return QuizifyGameState(runtime=runtime, entry_id="test")


@pytest.fixture
def handler(game: QuizifyGameState, tmp_path: Path):
    """Handler with a REAL ConnectionManager (so add_connection / the
    skip-closed broadcast filter run for real) but a broadcast recorder
    that snapshots *which still-open sockets* each message reaches."""
    runtime = _FakeRuntime(tmp_path)
    h = QuizifyWebSocketHandler(runtime=runtime, game_state_provider=lambda: game)
    h._conn = ConnectionManager(runtime, lambda: game)

    delivered: dict[int, list[str]] = {}

    async def _record(message: dict) -> None:
        msg_type = message.get("type")
        for ws in list(h._conn.connections):
            if not ws.closed:
                delivered.setdefault(id(ws), []).append(msg_type)

    h._conn.broadcast = _record  # type: ignore[assignment]
    h._delivered = delivered  # type: ignore[attr-defined]
    return h


@pytest.mark.asyncio
async def test_reset_clears_players_admin_and_phase(
    handler: QuizifyWebSocketHandler, game: QuizifyGameState
) -> None:
    """State invariant: after reset the registry is empty (admin included)
    and the phase is back at the initial LOBBY."""
    admin_ws = _ws()
    handler._conn.add_connection(admin_ws, is_admin=True, is_dashboard=False)
    game.add_player("Admin", admin_ws)
    game.get_player("Admin").is_admin = True
    game.add_player("Bob", _ws())
    game.add_player("Eve", _ws())
    game.start_game(language="de", num_rounds=3, difficulty="easy")
    game.start_next_question()
    assert game.phase == GamePhase.QUESTION_ACTIVE
    assert len(game.get_players()) == 3

    await handler._handle_reset_game(admin_ws, game)

    assert game.phase == GamePhase.LOBBY
    assert game.get_players() == []
    assert game.get_player("Admin") is None

    snapshot = game.get_state_snapshot()
    assert snapshot["phase"] == "LOBBY"
    assert snapshot["players"] == []


@pytest.mark.asyncio
async def test_reset_signal_reaches_admin_as_player_before_socket_closes(
    handler: QuizifyWebSocketHandler, game: QuizifyGameState
) -> None:
    """The #207 regression. The host is an admin-as-player in the lobby:
    their player slot lives on the same WS as their admin connection.
    Reset MUST broadcast game_reset + game_state to that socket while it
    is still open — otherwise the admin tab never returns to setup."""
    admin_ws = _ws()
    handler._conn.add_connection(admin_ws, is_admin=True, is_dashboard=False)
    game.add_player("Admin", admin_ws)
    game.get_player("Admin").is_admin = True

    bob_ws = _ws()
    handler._conn.add_connection(bob_ws, is_admin=False, is_dashboard=False)
    game.add_player("Bob", bob_ws)

    assert game.phase == GamePhase.LOBBY

    await handler._handle_reset_game(admin_ws, game)

    delivered = handler._delivered  # type: ignore[attr-defined]
    # Both the admin-as-player tab AND the player phone must have received
    # the reset signal followed by the fresh (empty) game_state.
    assert delivered.get(id(admin_ws)) == ["game_reset", "game_state"], (
        "admin-as-player did not receive the reset broadcast before its "
        "socket was closed (issue #207)"
    )
    assert delivered.get(id(bob_ws)) == ["game_reset", "game_state"], (
        "player phone did not receive the reset broadcast (issue #207)"
    )
    # Stale sockets are still ultimately closed (cleanup of dead phones).
    assert admin_ws.close.await_count == 1
    assert bob_ws.close.await_count == 1


@pytest.mark.asyncio
async def test_reset_during_active_game_delivers_to_all_players(
    handler: QuizifyWebSocketHandler, game: QuizifyGameState
) -> None:
    """Reset mid-game (QUESTION_ACTIVE) must still deliver the reset to
    every connected player so their phones leave the question view."""
    admin_ws = _ws()
    handler._conn.add_connection(admin_ws, is_admin=True, is_dashboard=False)
    game.add_player("Admin", admin_ws)
    game.get_player("Admin").is_admin = True

    p1_ws = _ws()
    p2_ws = _ws()
    handler._conn.add_connection(p1_ws, is_admin=False, is_dashboard=False)
    handler._conn.add_connection(p2_ws, is_admin=False, is_dashboard=False)
    game.add_player("P1", p1_ws)
    game.add_player("P2", p2_ws)
    game.start_game(language="de", num_rounds=3, difficulty="easy")
    game.start_next_question()
    assert game.phase == GamePhase.QUESTION_ACTIVE

    await handler._handle_reset_game(admin_ws, game)

    delivered = handler._delivered  # type: ignore[attr-defined]
    for ws in (admin_ws, p1_ws, p2_ws):
        assert delivered.get(id(ws)) == ["game_reset", "game_state"]
    assert game.phase == GamePhase.LOBBY
    assert game.get_players() == []
