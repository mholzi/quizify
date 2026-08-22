"""Regression tests for issue #363 — the STATIC websocket dispatch table.

``_handle_message`` used to rebuild a dict of ~17 lambda closures on EVERY
inbound message just to look up one key. #363 replaces that with a single
class-level ``_DISPATCH`` table built ONCE at class-definition time, keyed on
the ``MSG_*`` string constants and dispatched uniformly as
``handler(self, ws, data, game_state)``.

This must be strictly behaviour-preserving. These tests pin:
  1. The table is a single shared object (built once, not per message/instance).
  2. Every message type routes to the correct underlying ``_handle_*`` method
     (including the ``next_question``/``next_round`` alias) and nothing else.
  3. ``admin_connect`` / ``reset_game`` are NOT in the table (special paths).
  4. An unknown type is rejected with "Unknown message type" and reaches no
     handler.
  5. Admin-required types are rejected with ``ADMIN_REQUIRED`` for a non-admin
     connection and never reach their handler.
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
from custom_components.quizify.server import websocket as ws_mod  # noqa: E402
from custom_components.quizify.server.connection import ConnectionManager  # noqa: E402
from custom_components.quizify.server.websocket import (  # noqa: E402
    QuizifyWebSocketHandler,
)

# msg_type -> the _handle_* method it must dispatch to. ``next_round`` is an
# intentional alias of ``next_question`` (#270). ``admin_connect`` /
# ``reset_game`` are handled out-of-band and are NOT in this map.
ROUTING = {
    ws_mod.MSG_JOIN: "_handle_join",
    ws_mod.MSG_SUBMIT_ANSWER: "_handle_submit_answer",
    ws_mod.MSG_USE_POWERUP: "_handle_use_powerup",
    ws_mod.MSG_LIGHTNING_ANSWER: "_handle_lightning_answer",
    ws_mod.MSG_RECONNECT: "_handle_reconnect",
    ws_mod.MSG_GET_STATE: "_handle_get_state",
    ws_mod.MSG_REACTION: "_handle_reaction",
    ws_mod.MSG_SUBMIT_WAGER: "_handle_submit_wager",
    ws_mod.MSG_START_GAME: "_handle_start_game",
    ws_mod.MSG_NEXT_QUESTION: "_handle_next_question",
    ws_mod.MSG_NEXT_ROUND: "_handle_next_question",
    ws_mod.MSG_ADMIN_SKIP: "_handle_admin_skip",
    ws_mod.MSG_END_GAME: "_handle_end_game",
    ws_mod.MSG_PLAY_AGAIN: "_handle_play_again",
    ws_mod.MSG_PAUSE_GAME: "_handle_pause_game",
    ws_mod.MSG_RESUME_GAME: "_handle_resume_game",
    ws_mod.MSG_KICK_PLAYER: "_handle_kick_player",
    ws_mod.MSG_CONFIGURE_TTS: "_handle_configure_tts",
}

ADMIN_REQUIRED = {
    ws_mod.MSG_START_GAME,
    ws_mod.MSG_NEXT_QUESTION,
    ws_mod.MSG_NEXT_ROUND,
    ws_mod.MSG_ADMIN_SKIP,
    ws_mod.MSG_END_GAME,
    ws_mod.MSG_PLAY_AGAIN,
    ws_mod.MSG_PAUSE_GAME,
    ws_mod.MSG_RESUME_GAME,
    ws_mod.MSG_KICK_PLAYER,
    ws_mod.MSG_CONFIGURE_TTS,
}

# The set of distinct underlying handler methods (next_round aliases
# next_question, so it collapses).
ALL_HANDLER_METHODS = set(ROUTING.values())


class _FakeRuntime:
    def __init__(self, tmp_path: Path) -> None:
        self.data_dir = tmp_path


def _ws() -> MagicMock:
    ws = MagicMock()
    ws.closed = False
    ws.send_json = AsyncMock()
    ws.close = AsyncMock()
    return ws


@pytest.fixture
def game(tmp_path: Path) -> QuizifyGameState:
    runtime = _FakeRuntime(tmp_path)
    return QuizifyGameState(runtime=runtime, entry_id="test")


def _handler(game: QuizifyGameState, tmp_path: Path) -> QuizifyWebSocketHandler:
    runtime = _FakeRuntime(tmp_path)
    h = QuizifyWebSocketHandler(runtime=runtime, game_state_provider=lambda: game)
    h._conn = ConnectionManager(runtime, lambda: game)
    h._get_game_state = lambda: game  # type: ignore[assignment]

    errors: dict[int, list[dict]] = {}

    async def _send_error(ws, code, message) -> None:
        errors.setdefault(id(ws), []).append({"code": code, "message": message})

    h._conn.send_error = _send_error  # type: ignore[assignment]
    h._conn.broadcast = AsyncMock()  # type: ignore[assignment]
    h._errors = errors  # type: ignore[attr-defined]
    return h


def test_dispatch_table_is_built_once(
    game: QuizifyGameState, tmp_path: Path
) -> None:
    """The table is a single shared class object — not rebuilt per instance
    (and, by construction, not per message)."""
    h1 = _handler(game, tmp_path)
    h2 = _handler(game, tmp_path)
    assert h1._DISPATCH is QuizifyWebSocketHandler._DISPATCH
    assert h2._DISPATCH is QuizifyWebSocketHandler._DISPATCH


def test_special_types_not_in_table() -> None:
    """admin_connect / reset_game use special auth paths and must stay out."""
    assert ws_mod.MSG_ADMIN_CONNECT not in QuizifyWebSocketHandler._DISPATCH
    assert ws_mod.MSG_RESET_GAME not in QuizifyWebSocketHandler._DISPATCH


@pytest.mark.parametrize("msg_type", sorted(ROUTING))
@pytest.mark.asyncio
async def test_message_routes_to_correct_handler(
    msg_type: str, game: QuizifyGameState, tmp_path: Path
) -> None:
    """Each message type reaches its expected _handle_* method — and only
    that one — when dispatched through _handle_message."""
    h = _handler(game, tmp_path)
    # Grant admin so admin-required types reach their handler.
    h._is_authorized_admin = lambda *a, **k: True  # type: ignore[assignment]

    # Replace every distinct handler with an AsyncMock spy so we can prove
    # exactly one fired and no cross-talk happened.
    spies = {name: AsyncMock() for name in ALL_HANDLER_METHODS}
    for name, spy in spies.items():
        setattr(h, name, spy)

    ws = _ws()
    await h._handle_message(ws, {"type": msg_type}, is_admin=True)

    expected = ROUTING[msg_type]
    assert spies[expected].await_count == 1, f"{msg_type} did not route to {expected}"
    for name, spy in spies.items():
        if name != expected:
            assert spy.await_count == 0, f"{msg_type} wrongly also called {name}"

    # Uniform dispatch: ws is first positional, the game_state is last.
    call = spies[expected].await_args
    assert call.args[0] is ws
    assert call.args[-1] is game


@pytest.mark.asyncio
async def test_unknown_type_rejected_and_no_handler(
    game: QuizifyGameState, tmp_path: Path
) -> None:
    """An unknown message type is rejected and reaches no handler."""
    h = _handler(game, tmp_path)
    spies = {name: AsyncMock() for name in ALL_HANDLER_METHODS}
    for name, spy in spies.items():
        setattr(h, name, spy)

    ws = _ws()
    await h._handle_message(ws, {"type": "does_not_exist"}, is_admin=True)

    errs = h._errors.get(id(ws), [])  # type: ignore[attr-defined]
    assert errs and errs[-1]["message"] == "Unknown message type"
    assert all(spy.await_count == 0 for spy in spies.values())


@pytest.mark.parametrize("msg_type", sorted(ADMIN_REQUIRED))
@pytest.mark.asyncio
async def test_admin_required_rejected_without_admin(
    msg_type: str, game: QuizifyGameState, tmp_path: Path
) -> None:
    """Admin-required types are rejected with 'Admin only' for a non-admin
    connection and never reach their handler."""
    h = _handler(game, tmp_path)
    # A legitimate admin holds the crown so the guard has something to check.
    admin_ws = _ws()
    h._conn.add_connection(admin_ws, is_admin=True, is_dashboard=False)
    game.add_player("Host", admin_ws)
    game.get_player("Host").is_admin = True

    rogue_ws = _ws()
    h._conn.add_connection(rogue_ws, is_admin=False, is_dashboard=False)
    game.add_player("Rogue", rogue_ws)

    spies = {name: AsyncMock() for name in ALL_HANDLER_METHODS}
    for name, spy in spies.items():
        setattr(h, name, spy)

    assert game.phase == GamePhase.LOBBY
    await h._handle_message(rogue_ws, {"type": msg_type}, is_admin=False)

    errs = h._errors.get(id(rogue_ws), [])  # type: ignore[attr-defined]
    assert errs and errs[-1]["code"] == "ADMIN_REQUIRED"
    assert all(spy.await_count == 0 for spy in spies.values())
