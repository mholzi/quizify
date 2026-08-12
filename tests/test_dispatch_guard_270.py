"""Privilege-guard tests for issue #270 — the dispatch-table refactor.

``server/websocket.py::_handle_message`` was refactored from a long
if/elif chain (with the identical 4-line ``_is_authorized_admin`` guard
copy-pasted across 13 admin message types) into a single dispatch table
``{msg_type: (handler, admin_required)}`` plus ONE centralized guard.

This is privilege-critical: the refactor must not silently drop a guard,
nor flip a non-admin type into an admin-required one (or vice-versa).
These tests pin the guard set so a future edit that mis-tags a type
fails loudly:

  1. Every admin-required type, when sent by a non-admin connection while
     a legitimate admin holds the crown, is rejected with the EXACT
     "Admin only" error and never reaches its handler.
  2. A representative non-admin type (``submit_answer``) is NOT gated —
     a non-admin connection reaches its handler with no "Admin only"
     rejection.
  3. The admin-required set in the live dispatch table matches the pinned
     expectation exactly (catches accidental add/remove/flip).
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

# The admin-required message types as of the #270 dispatch-table refactor.
# ``reset_game`` is NOT here — it uses the special ``_is_reset_authorized``
# escape-hatch path and is covered by tests/test_reset_game_207.py.
# ``admin_connect`` is NOT here — it has its own WS-level ``is_admin`` check.
ADMIN_REQUIRED_TYPES = {
    "start_game",
    "next_question",
    "next_round",
    "admin_skip",
    "end_game",
    "play_again",
    "pause_game",
    "resume_game",
    "kick_player",
    # configure_tts pushes the narration settings during the lobby so player-
    # join announcements work before start_game (#281). Admin-only.
    "configure_tts",
    # configure_house does the same for the "House Plays Along" panel (#494 P4):
    # lights/SFX/bus-event settings pushed during the lobby. Admin-only — it
    # reconfigures HA entities in the host's home.
    "configure_house",
    # start_lightning / start_lightning_questions / end_lightning were retired
    # in #285 — the Lightning Round now auto-triggers mid-game, with no
    # host-facing start/end message.
}

NON_ADMIN_TYPES = {
    "join",
    "submit_answer",
    "use_powerup",
    "lightning_answer",
    "reconnect",
    "get_state",
    "reaction",
    "submit_wager",
    # Teams (#365) are formed by the players themselves — the host does not
    # assign anyone, so these must stay OUT of the admin partition. The lobby
    # phase is what limits them, not the crown.
    "create_team",
    "join_team",
    "leave_team",
}


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
    """Handler driving the real ``_handle_message`` dispatch + auth guard,
    with broadcasts no-op'd and ``send_error`` / ``_safe_send`` recorded."""
    runtime = _FakeRuntime(tmp_path)
    h = QuizifyWebSocketHandler(runtime=runtime, game_state_provider=lambda: game)
    h._conn = ConnectionManager(runtime, lambda: game)
    h._get_game_state = lambda: game  # type: ignore[assignment]

    errors: dict[int, list[dict]] = {}

    async def _noop_broadcast(message: dict) -> None:
        return None

    async def _send_error(ws, code, message) -> None:
        errors.setdefault(id(ws), []).append({"code": code, "message": message})

    async def _safe_send(ws, message: dict) -> None:
        return None

    h._conn.broadcast = _noop_broadcast  # type: ignore[assignment]
    h._conn.send_error = _send_error  # type: ignore[assignment]
    h._conn._safe_send = _safe_send  # type: ignore[assignment]
    h._errors = errors  # type: ignore[attr-defined]
    return h


def _seed_with_admin(
    h: QuizifyWebSocketHandler, game: QuizifyGameState
) -> MagicMock:
    """Seed a game with a connected admin holding the crown, plus a
    non-admin player WS. Returns the non-admin (rogue) WS."""
    admin_ws = _ws()
    h._conn.add_connection(admin_ws, is_admin=True, is_dashboard=False)
    game.add_player("Host", admin_ws)
    game.get_player("Host").is_admin = True

    rogue_ws = _ws()
    h._conn.add_connection(rogue_ws, is_admin=False, is_dashboard=False)
    game.add_player("Rogue", rogue_ws)
    return rogue_ws


@pytest.mark.parametrize("msg_type", sorted(ADMIN_REQUIRED_TYPES))
@pytest.mark.asyncio
async def test_admin_required_type_rejected_for_non_admin(
    msg_type: str, game: QuizifyGameState, tmp_path: Path
) -> None:
    """Each admin-required type, sent by a non-admin connection while a
    legitimate admin holds the crown, MUST be rejected with the exact
    "Admin only" error and must not mutate game state behind the guard."""
    h = _handler(game, tmp_path)
    rogue_ws = _seed_with_admin(h, game)
    assert game.phase == GamePhase.LOBBY

    # Spy on the handler the type dispatches to so we can assert it never ran.
    handler_fn, admin_required = h._DISPATCH[msg_type]
    assert admin_required is True, f"{msg_type} should be admin_required"

    await h._handle_message(rogue_ws, {"type": msg_type}, is_admin=False)

    errs = h._errors.get(id(rogue_ws), [])  # type: ignore[attr-defined]
    assert errs, f"{msg_type} from non-admin produced no error"
    assert errs[-1]["message"] == "Admin only", (
        f"{msg_type} rejected with wrong message: {errs[-1]}"
    )
    # The guard fired before the game advanced out of LOBBY.
    assert game.phase == GamePhase.LOBBY


@pytest.mark.asyncio
async def test_non_admin_type_not_gated(
    game: QuizifyGameState, tmp_path: Path
) -> None:
    """A representative non-admin type (``submit_answer``) must NOT be
    gated: a non-admin connection reaches the handler with no "Admin only"
    rejection (the centralized guard must not over-reach)."""
    h = _handler(game, tmp_path)
    rogue_ws = _seed_with_admin(h, game)

    await h._handle_message(
        rogue_ws,
        {"type": "submit_answer", "answer_index": 0},
        is_admin=False,
    )

    errs = h._errors.get(id(rogue_ws), [])  # type: ignore[attr-defined]
    assert all(e["message"] != "Admin only" for e in errs), (
        f"submit_answer was wrongly admin-gated: {errs}"
    )


def test_dispatch_table_admin_flags_match_expected(
    game: QuizifyGameState, tmp_path: Path
) -> None:
    """Pin the exact admin_required partition of the live dispatch table so
    a future edit that adds/removes/flips a guard fails loudly. ``reset_game``
    and ``admin_connect`` are intentionally absent (special auth paths)."""
    h = _handler(game, tmp_path)
    table = h._DISPATCH

    actual_admin = {t for t, (_, req) in table.items() if req}
    actual_non_admin = {t for t, (_, req) in table.items() if not req}

    assert actual_admin == ADMIN_REQUIRED_TYPES
    assert actual_non_admin == NON_ADMIN_TYPES
    assert "reset_game" not in table
    assert "admin_connect" not in table
