"""A mid-game disconnect longer than the grace period keeps the player (#1093).

``_handle_disconnect`` scheduled ``remove_after_timeout`` in every phase; after
60 s it called ``remove_player`` and ``clear_player_tokens``. A guest who locked
the phone in round 6 while leading on 80 points vanished from the leaderboard
and the television, the token reconnect failed, and the re-join created a fresh
player seeded with the room average. #936 had fixed this for one-person teams
only.

Mid-game the row, the score and the session token now stay; the timed removal
happens once the room is back in the lobby.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.quizify.game.state import GamePhase, QuizifyGameState
from custom_components.quizify.server import websocket as ws_mod
from custom_components.quizify.server.connection import ConnectionManager
from custom_components.quizify.server.websocket import QuizifyWebSocketHandler

GRACE = 0.05


class _FakeRuntime:
    def __init__(self, tmp_path: Path) -> None:
        self.data_dir = tmp_path


def _ws() -> MagicMock:
    ws = MagicMock()
    ws.closed = False
    ws.send_json = AsyncMock()
    return ws


@pytest.fixture
def room(tmp_path: Path, monkeypatch):  # noqa: ANN201
    monkeypatch.setattr(ws_mod, "LOBBY_DISCONNECT_GRACE_PERIOD", GRACE)
    game = QuizifyGameState(runtime=None, entry_id="test")
    runtime = _FakeRuntime(tmp_path)
    handler = QuizifyWebSocketHandler(runtime=runtime, game_state_provider=lambda: game)
    handler._conn = ConnectionManager(runtime, lambda: game)
    handler._conn.broadcast = AsyncMock()
    handler._conn.PLAYER_SESSION_GRACE = GRACE
    anna_ws = _ws()
    game.add_player("Anna", handler._conn.connection_id(anna_ws))
    game.add_player("Ben", handler._conn.connection_id(_ws()))
    token = handler._conn.create_session_token("Anna")
    return game, handler, anna_ws, token


async def _anna_drops(handler: QuizifyWebSocketHandler, anna_ws) -> None:  # noqa: ANN001
    anna_ws.closed = True
    await handler._handle_disconnect(anna_ws)
    # Several grace periods: the old code removed after the first one.
    await asyncio.sleep(GRACE * 6)


def _start(game: QuizifyGameState) -> None:
    game.start_game(language="en", num_rounds=10, difficulty="easy")
    assert game.start_next_question() is not None
    assert game.phase == GamePhase.QUESTION_ACTIVE


@pytest.mark.asyncio
async def test_a_long_midgame_disconnect_keeps_row_score_and_token(room) -> None:  # noqa: ANN001
    game, handler, anna_ws, token = room
    _start(game)
    game.get_player("Anna").score = 80

    await _anna_drops(handler, anna_ws)

    anna = game.get_player("Anna")
    assert anna is not None, "the player was removed mid-game"
    assert anna.connected is False
    assert anna.score == 80
    assert any(p.name == "Anna" for p in game.get_players())
    assert handler._conn.get_player_for_token(token) == "Anna", (
        "the session token was cleared, so the reconnect would fail"
    )
    handler._conn.cancel_pending_removal("Anna")


@pytest.mark.asyncio
async def test_the_departed_player_leaves_once_the_room_is_back_in_the_lobby(
    room,  # noqa: ANN001
) -> None:
    game, handler, anna_ws, token = room
    _start(game)
    await _anna_drops(handler, anna_ws)
    assert game.get_player("Anna") is not None

    game.end_game()
    game.reset_to_lobby()
    await asyncio.sleep(GRACE * 3)

    assert game.get_player("Anna") is None
    assert handler._conn.get_player_for_token(token) is None


@pytest.mark.asyncio
async def test_a_lobby_disconnect_is_still_removed_after_the_grace(room) -> None:  # noqa: ANN001
    game, handler, anna_ws, token = room
    assert game.phase == GamePhase.LOBBY

    await _anna_drops(handler, anna_ws)

    assert game.get_player("Anna") is None
    assert handler._conn.get_player_for_token(token) is None
