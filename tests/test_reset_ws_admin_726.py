"""A live WS-admin blocks the reset escape hatch (#726).

#207 gave ``reset_game`` a deliberate escape hatch: when no connected admin
holds the crown, ANY client may reset, because the legitimate host has no other
way back out of the orphaned-crown state left by the #209 name race.

The check behind that intent read the wrong register. ``get_admin()`` asks the
*player* registry (``game/player_registry.py``), so it only ever finds a host who
joined the game as a player. A host who runs the evening from ``/quizify/admin``
— the documented way to host — never takes a player slot, so ``get_admin()``
stays ``None`` from lobby to finale while ``has_admin_connections()`` is True the
whole time. The hatch was therefore wide open for every guest, mid-game, and a
reset wipes players, scores and session tokens.
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
    ws.close = AsyncMock()
    return ws


@pytest.fixture
def game(tmp_path: Path) -> QuizifyGameState:
    return QuizifyGameState(runtime=_FakeRuntime(tmp_path), entry_id="test")


@pytest.fixture
def handler(game: QuizifyGameState, tmp_path: Path):
    runtime = _FakeRuntime(tmp_path)
    h = QuizifyWebSocketHandler(runtime=runtime, game_state_provider=lambda: game)
    h._conn = ConnectionManager(runtime, lambda: game)
    h._get_game_state = lambda: game  # type: ignore[assignment]
    return h


def test_guest_cannot_reset_while_the_host_tab_is_connected(
    handler, game: QuizifyGameState
) -> None:
    """The host runs the game from /quizify/admin and never joins as a player.

    ``get_admin()`` is None for the whole game — but somebody IS hosting, and the
    escape hatch is for the case where nobody is.
    """
    admin_ws = _ws()
    handler._conn.add_connection(admin_ws, is_admin=True, is_dashboard=False)

    guest_ws = _ws()
    handler._conn.add_connection(guest_ws, is_admin=False, is_dashboard=False)
    game.add_player("Guest", guest_ws)

    assert game.get_admin() is None, "precondition: no admin PLAYER slot is taken"
    assert handler._conn.has_admin_connections() is True

    assert handler._is_reset_authorized(guest_ws, False, game) is False


def test_the_orphaned_crown_hatch_still_opens(handler, game: QuizifyGameState) -> None:
    """With no admin socket at all, #207's recovery must still work — this is the
    regression the fix itself could cause."""
    guest_ws = _ws()
    handler._conn.add_connection(guest_ws, is_admin=False, is_dashboard=False)
    game.add_player("Guest", guest_ws)

    assert handler._conn.has_admin_connections() is False
    assert handler._is_reset_authorized(guest_ws, False, game) is True


def test_the_host_can_always_reset(handler, game: QuizifyGameState) -> None:
    """A WS-admin resetting its own game is the normal path and stays open."""
    admin_ws = _ws()
    handler._conn.add_connection(admin_ws, is_admin=True, is_dashboard=False)

    assert handler._is_reset_authorized(admin_ws, True, game) is True


def test_a_dashboard_alone_does_not_block_recovery(
    handler, game: QuizifyGameState
) -> None:
    """The television is a spectator socket, not a host. If it counted as an
    admin connection the #207 hatch would be sealed on every install that has a
    TV open — which is most of them."""
    tv_ws = _ws()
    handler._conn.add_connection(tv_ws, is_admin=False, is_dashboard=True)

    guest_ws = _ws()
    handler._conn.add_connection(guest_ws, is_admin=False, is_dashboard=False)
    game.add_player("Guest", guest_ws)

    assert handler._is_reset_authorized(guest_ws, False, game) is True
