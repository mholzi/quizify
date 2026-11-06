"""A lost ``reconnected`` frame must not strand the seat (#1222).

``handle_reconnect`` rotates the session token and only then sends the new
token in the ``reconnected`` frame. A phone whose socket drops before it reads
that frame still holds the old token. Its next reconnect used to find neither a
live nor a retired token, got ``reason: "restart"`` ("Home Assistant was
restarted") and wiped its identity, while the server still held the seat.

The old token now stays an alias for the same player for
``ROTATED_TOKEN_GRACE`` seconds, until the new token is used, or until a
revocation path takes the player's tokens back. It never seats anyone else.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT))

from custom_components.quizify.game.state import QuizifyGameState  # noqa: E402
from custom_components.quizify.server import connection as connection_mod  # noqa: E402
from custom_components.quizify.server.connection import (  # noqa: E402
    ROTATED_TOKEN_GRACE,
    ConnectionManager,
)
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
    return QuizifyGameState(runtime=_FakeRuntime(tmp_path), entry_id="test")


@pytest.fixture
def handler(game: QuizifyGameState, tmp_path: Path) -> QuizifyWebSocketHandler:
    runtime = _FakeRuntime(tmp_path)
    h = QuizifyWebSocketHandler(runtime=runtime, game_state_provider=lambda: game)
    h._conn = ConnectionManager(runtime, lambda: game)
    h._conn.broadcast = AsyncMock()
    return h


def _frames(ws: MagicMock) -> list[dict]:
    return [
        c.args[0]
        for c in ws.send_json.await_args_list
        if c.args and isinstance(c.args[0], dict)
    ]


async def _reconnect(handler, game, token: str, name: str = "Anna") -> dict:
    """Reconnect with *token*; return the reconnected/reconnect_failed frame."""
    ws = _ws()
    await handler._handle_reconnect(ws, {"session_token": token, "name": name}, game)
    return next(
        f
        for f in _frames(ws)
        if f.get("type") in ("reconnected", "reconnect_failed")
    )


def _seat(handler, game, name: str) -> str:
    game.add_player(name, handler._conn.connection_id(_ws()))
    game.get_player(name).connected = False
    return handler._conn.create_session_token(name)


def _age_aliases(handler, seconds: float) -> None:
    """Move every rotation alias *seconds* into the past."""
    conn = handler._conn
    for t, (cur, name, at) in list(conn._rotated_tokens.items()):
        conn._rotated_tokens[t] = (cur, name, at - seconds)


@pytest.mark.asyncio
async def test_lost_frame_reconnect_within_grace_succeeds(handler, game) -> None:
    t0 = _seat(handler, game, "Anna")
    first = await _reconnect(handler, game, t0)
    assert first["type"] == "reconnected"
    t1 = first["session_token"]
    # The frame carrying t1 was lost: the phone comes back on t0.
    game.get_player("Anna").connected = False
    second = await _reconnect(handler, game, t0)
    assert second["type"] == "reconnected"
    assert second["player_id"] == "Anna"
    t2 = second["session_token"]
    assert t2 not in (t0, t1)
    # The token the phone never got is withdrawn; only the fresh one is live.
    assert handler._conn.get_player_for_token(t1) is None
    assert handler._conn.get_player_for_token(t2) == "Anna"


@pytest.mark.asyncio
async def test_after_grace_fails_without_saying_restart(handler, game) -> None:
    t0 = _seat(handler, game, "Anna")
    await _reconnect(handler, game, t0)
    _age_aliases(handler, ROTATED_TOKEN_GRACE + 1)
    failed = await _reconnect(handler, game, t0)
    assert failed["type"] == "reconnect_failed"
    assert failed["reason"] != "restart"
    assert failed["reason"] == "expired"


@pytest.mark.asyncio
async def test_repeated_alias_use_does_not_extend_the_window(handler, game) -> None:
    t0 = _seat(handler, game, "Anna")
    await _reconnect(handler, game, t0)
    _age_aliases(handler, ROTATED_TOKEN_GRACE - 5)
    assert (await _reconnect(handler, game, t0))["type"] == "reconnected"
    _age_aliases(handler, 10)
    failed = await _reconnect(handler, game, t0)
    assert failed["type"] == "reconnect_failed"
    assert failed["reason"] == "expired"


@pytest.mark.asyncio
async def test_using_the_new_token_retires_the_old_one(handler, game) -> None:
    t0 = _seat(handler, game, "Anna")
    t1 = (await _reconnect(handler, game, t0))["session_token"]
    game.get_player("Anna").connected = False
    assert (await _reconnect(handler, game, t1))["type"] == "reconnected"
    failed = await _reconnect(handler, game, t0)
    assert failed["type"] == "reconnect_failed"
    assert failed["reason"] == "expired"


@pytest.mark.asyncio
async def test_reset_kills_the_alias(handler, game) -> None:
    t0 = _seat(handler, game, "Anna")
    await _reconnect(handler, game, t0)
    handler._conn.clear_all_player_tokens()
    failed = await _reconnect(handler, game, t0)
    assert failed["type"] == "reconnect_failed"
    assert failed["reason"] == "reset"
    assert handler._conn._rotated_tokens == {}


@pytest.mark.asyncio
async def test_removing_the_guest_kills_the_alias(handler, game) -> None:
    t0 = _seat(handler, game, "Anna")
    await _reconnect(handler, game, t0)
    handler._conn.clear_player_tokens("Anna")
    failed = await _reconnect(handler, game, t0)
    assert failed["type"] == "reconnect_failed"
    assert failed["reason"] == "removed"


@pytest.mark.asyncio
async def test_revoking_the_new_token_kills_the_alias(handler, game) -> None:
    t0 = _seat(handler, game, "Anna")
    t1 = (await _reconnect(handler, game, t0))["session_token"]
    handler._conn.revoke_token(t1)
    assert handler._conn.get_player_for_token(t0) is None
    assert handler._conn.reconnect_failure_reason(t0) == "removed"


@pytest.mark.asyncio
async def test_alias_only_ever_seats_its_own_player(handler, game) -> None:
    t_anna = _seat(handler, game, "Anna")
    _seat(handler, game, "Ben")
    await _reconnect(handler, game, t_anna)
    # Whatever name the client claims, the alias resolves to Anna only.
    ws = _ws()
    await handler._handle_reconnect(
        ws, {"session_token": t_anna, "name": "Ben"}, game
    )
    frame = next(f for f in _frames(ws) if f.get("type") == "reconnected")
    assert frame["player_id"] == "Anna"
    assert game.get_player("Ben").connected is False
    # Once Anna is removed, the alias does not fall through to anyone else.
    handler._conn.clear_player_tokens("Anna")
    assert handler._conn.get_player_for_token(t_anna) is None


def test_alias_dies_if_its_token_is_reissued_to_someone_else(tmp_path) -> None:
    conn = ConnectionManager(_FakeRuntime(tmp_path), lambda: None)
    t0 = conn.create_session_token("Anna")
    t1 = conn.rotate_session_token(t0, "Anna")
    # Defensive: if the live token ever maps to another name, the alias is void.
    conn._session_tokens[t1] = ("Ben", time.monotonic())
    assert conn.get_player_for_token(t0) is None


def test_sweep_bounds_the_alias_map(tmp_path) -> None:
    conn = ConnectionManager(_FakeRuntime(tmp_path), lambda: None)
    t0 = conn.create_session_token("Anna")
    conn.rotate_session_token(t0, "Anna")
    for t, (cur, name, at) in list(conn._rotated_tokens.items()):
        conn._rotated_tokens[t] = (cur, name, at - ROTATED_TOKEN_GRACE - 1)
    conn.create_session_token("Ben")
    assert conn._rotated_tokens == {}
    assert conn.reconnect_failure_reason(t0) == connection_mod.RECONNECT_EXPIRED
