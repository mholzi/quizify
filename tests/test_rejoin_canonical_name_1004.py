"""A case-different reclaim answers with the slot's own name (#1004).

``PlayerRegistry.get_player`` folds case, so a player stored as ``Hostie`` who
rejoins as ``HOSTIE`` gets their own slot back. ``_handle_join`` then kept
working with the *typed* name: the ``joined`` frame carried
``player_id: "HOSTIE"``, the phone stored that as its identity, found no roster
row under it and listed its own ``Hostie`` slot as "+ 1 more player". For the
host that row wore the crown, so the host read it as somebody else holding the
host rights.

The server state was right all along; only the names keyed after the join were
the typed spelling. Everything after a successful join now runs on the slot's
name.
"""

from __future__ import annotations

import asyncio
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

    def create_task(self, coro):  # noqa: ANN001
        return asyncio.ensure_future(coro)

    async def run_in_executor(self, func, *args):  # noqa: ANN001, ANN002
        return func(*args)


def _ws() -> MagicMock:
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
    h._conn.broadcast = AsyncMock()
    h._conn.send = AsyncMock()
    h._conn.send_error = AsyncMock()
    return h


def _seat_dropped(game: QuizifyGameState, name: str, *, admin: bool) -> None:
    """A player who joined, then lost the connection (wifi drop, closed tab)."""
    game.add_player(name)
    player = game.get_player(name)
    player.is_admin = admin
    player.connected = False


def _joined_frame(handler: QuizifyWebSocketHandler) -> dict:
    frames = [
        c.args[1]
        for c in handler._conn.send.await_args_list
        if c.args[1].get("type") == "joined"
    ]
    assert len(frames) == 1
    return frames[0]


@pytest.mark.asyncio
async def test_host_reclaim_in_other_case_is_told_the_slot_name(
    handler: QuizifyWebSocketHandler, game: QuizifyGameState
) -> None:
    """The issue's scenario: the host's tab rejoins as ``HOSTIE``."""
    _seat_dropped(game, "Hostie", admin=True)

    await handler._handle_join(_ws(), {"name": "HOSTIE", "is_admin": True}, game)

    assert [p.name for p in game.get_players()] == ["Hostie"]
    frame = _joined_frame(handler)
    assert frame["player_id"] == "Hostie"
    assert frame["is_admin"] is True


@pytest.mark.asyncio
async def test_player_reclaim_in_other_case_is_told_the_slot_name(
    handler: QuizifyWebSocketHandler, game: QuizifyGameState
) -> None:
    """Same without the admin flag: ``hostie`` rejoining a ``HOSTIE`` slot."""
    _seat_dropped(game, "HOSTIE", admin=False)

    await handler._handle_join(_ws(), {"name": "hostie"}, game)

    assert [p.name for p in game.get_players()] == ["HOSTIE"]
    assert _joined_frame(handler)["player_id"] == "HOSTIE"


@pytest.mark.asyncio
async def test_session_token_is_issued_under_the_slot_name(
    handler: QuizifyWebSocketHandler, game: QuizifyGameState
) -> None:
    """The token map compares names exactly; the typed spelling misses it."""
    _seat_dropped(game, "Hostie", admin=False)

    await handler._handle_join(_ws(), {"name": "HOSTIE"}, game)

    token = _joined_frame(handler)["session_token"]
    assert handler._conn.get_player_for_token(token) == "Hostie"


@pytest.mark.asyncio
async def test_pending_removal_of_the_slot_is_cancelled(
    handler: QuizifyWebSocketHandler, game: QuizifyGameState
) -> None:
    """The grace removal is keyed on the slot's name, not the typed one."""
    _seat_dropped(game, "Hostie", admin=False)

    async def _remove(_name: str, _timeout: float) -> None:
        await asyncio.sleep(60)

    handler._conn.schedule_player_removal("Hostie", 60, _remove)
    task = handler._conn._pending_removals["Hostie"]

    await handler._handle_join(_ws(), {"name": "HOSTIE"}, game)
    await asyncio.sleep(0)

    assert task.cancelled()
    assert "Hostie" not in handler._conn._pending_removals
