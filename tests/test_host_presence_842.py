"""The room is told whether anybody is hosting it (#842).

The escape hatch (#207 → #299 → #803) turns on one question: is a host still
connected? The server has always been able to answer it — #726 taught
``_is_reset_authorized`` that a live ``?role=admin`` socket IS a connected
admin, even though ``get_admin()`` only ever looks at the player registry and
stays ``None`` all evening for a host who runs the game from /quizify/admin
without joining.

The phones had no such signal. They read the roster, the roster is that same
registry, and in the flow the v1.16.0-RC1 live test used it named no host at
all — so "no connected host here" was true from the first question to the last,
and the hatch armed on every guest phone during the lightning recap while the
host sat looking at the same recap (#834).

Narrowing the phone's own reading fixes the false arm and costs the true one:
an admin-only host closing the tab broadcasts nothing — no player row to mark,
no roster frame, and ``phase_controller.pause()`` refuses every phase but
QUESTION_ACTIVE — so the room could no longer be told the host had died either.
That is the rescue the live test exercised on hardware, four hours before this
was written.

So the answer goes on the wire. ``_host_connected`` is the condition
``_is_reset_authorized`` already used, named once; ``not _host_connected(...)``
is exactly "any client may reset", so the flag the phone reads and the rule the
server enforces cannot drift apart. It rides every snapshot as
``host_connected`` and is broadcast as a ``host_presence`` frame whenever it
changes — in both directions.
"""

from __future__ import annotations

import re
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

_WEBSOCKET_PY = (
    _REPO_ROOT / "custom_components" / "quizify" / "server" / "websocket.py"
)


class _FakeRuntime:
    def __init__(self, tmp_path: Path) -> None:
        self.data_dir = tmp_path


def _ws() -> MagicMock:
    ws = MagicMock()
    ws.closed = False
    ws.send_json = AsyncMock()
    ws.send_str = AsyncMock()
    ws.close = AsyncMock()
    return ws


def _frames(ws: MagicMock, frame_type: str) -> list[dict]:
    """Every frame of one type this socket was sent, in order."""
    import json

    out = []
    for call in ws.send_str.call_args_list:
        payload = json.loads(call.args[0])
        if payload.get("type") == frame_type:
            out.append(payload)
    return out


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


# ---------------------------------------------------------------------------
# What the flag says
# ---------------------------------------------------------------------------


def test_an_admin_only_host_counts_as_hosting(handler, game) -> None:
    """The whole point. ``get_admin()`` is None for this entire game and
    somebody is very definitely hosting it."""
    handler._conn.add_connection(_ws(), is_admin=True, is_dashboard=False)

    assert game.get_admin() is None, "precondition: no admin PLAYER slot taken"
    assert handler._host_connected(game) is True


def test_an_admin_only_host_who_closed_the_tab_does_not(handler, game) -> None:
    """The case the phone could not see: no admin socket, no admin player, and
    nothing on the wire that ever said so."""
    guest_ws = _ws()
    handler._conn.add_connection(guest_ws, is_admin=False, is_dashboard=False)
    game.add_player("Guest", guest_ws)

    assert handler._host_connected(game) is False


def test_a_host_who_plays_counts_through_their_player_row(handler, game) -> None:
    """The #208 flow: admin.html redirects to player.html on Start, so the
    crown lives on a player row and there is no ?role=admin socket left."""
    host_ws = _ws()
    game.add_player("Host", host_ws)
    game.get_player("Host").is_admin = True

    assert handler._conn.has_admin_connections() is False
    assert handler._host_connected(game) is True

    host = game.get_admin()
    assert host is not None
    host.connected = False
    assert handler._host_connected(game) is False


def test_the_television_is_not_a_host(handler, game) -> None:
    """A spectator socket must not make the room look hosted — most installs
    have one open all evening."""
    handler._conn.add_connection(_ws(), is_admin=False, is_dashboard=True)

    assert handler._host_connected(game) is False


def test_the_flag_is_the_reset_rule_inverted(handler, game) -> None:
    """Named once and used twice on purpose: a phone that armed while the
    server would refuse the reset offers a button that does nothing."""
    guest_ws = _ws()
    handler._conn.add_connection(guest_ws, is_admin=False, is_dashboard=False)
    game.add_player("Guest", guest_ws)

    assert handler._is_reset_authorized(guest_ws, False, game) is True
    assert handler._host_connected(game) is False

    handler._conn.add_connection(_ws(), is_admin=True, is_dashboard=False)

    assert handler._is_reset_authorized(guest_ws, False, game) is False
    assert handler._host_connected(game) is True


# ---------------------------------------------------------------------------
# …and how it reaches the phones
# ---------------------------------------------------------------------------


def test_every_snapshot_carries_the_flag(handler, game) -> None:
    """The snapshot is what a phone is handed on join, on reconnect and on
    get_state, so it is where one that was not listening catches up."""
    handler._conn.add_connection(_ws(), is_admin=True, is_dashboard=False)

    assert handler._snapshot(game)["host_connected"] is True


def test_the_snapshot_flag_follows_the_host_out(handler, game) -> None:
    guest_ws = _ws()
    handler._conn.add_connection(guest_ws, is_admin=False, is_dashboard=False)
    game.add_player("Guest", guest_ws)

    assert handler._snapshot(game)["host_connected"] is False


@pytest.mark.asyncio
async def test_the_arrival_is_broadcast(handler, game) -> None:
    """A host opening /quizify/admin. Announced from handle() itself, one line
    after add_connection, because that socket is what the flag reports."""
    guest_ws = _ws()
    handler._conn.add_connection(guest_ws, is_admin=False, is_dashboard=False)
    game.add_player("Guest", guest_ws)
    await handler._announce_host_presence()

    handler._conn.add_connection(_ws(), is_admin=True, is_dashboard=False)
    await handler._announce_host_presence()

    assert _frames(guest_ws, "host_presence") == [
        {"type": "host_presence", "connected": False},
        {"type": "host_presence", "connected": True},
    ]


@pytest.mark.asyncio
async def test_the_departure_is_broadcast(handler, game) -> None:
    """The half nothing else reports. Driven through the real disconnect path
    rather than the announcer, because that is the path an admin-only host's
    closing tab actually takes."""
    admin_ws = _ws()
    handler._conn.add_connection(admin_ws, is_admin=True, is_dashboard=False)
    guest_ws = _ws()
    handler._conn.add_connection(guest_ws, is_admin=False, is_dashboard=False)
    game.add_player("Guest", guest_ws)
    await handler._announce_host_presence()

    # Exactly what handle()'s finally block does: read the flag, drop the
    # socket, then hand the disconnect on.
    was_admin = handler._conn.is_admin_connection(admin_ws)
    handler._conn.remove_connection(admin_ws)
    await handler._handle_disconnect(admin_ws, was_admin=was_admin)

    assert _frames(guest_ws, "host_presence") == [
        {"type": "host_presence", "connected": True},
        {"type": "host_presence", "connected": False},
    ]


@pytest.mark.asyncio
async def test_a_second_admin_tab_leaving_is_not_a_departure(handler, game) -> None:
    """Two host tabs open is ordinary. Closing one changes nothing, and a phone
    that armed on it would be offering a reset the server refuses."""
    first, second = _ws(), _ws()
    handler._conn.add_connection(first, is_admin=True, is_dashboard=False)
    handler._conn.add_connection(second, is_admin=True, is_dashboard=False)
    guest_ws = _ws()
    handler._conn.add_connection(guest_ws, is_admin=False, is_dashboard=False)
    game.add_player("Guest", guest_ws)
    await handler._announce_host_presence()

    handler._conn.remove_connection(second)
    await handler._handle_disconnect(second, was_admin=True)

    assert _frames(guest_ws, "host_presence") == [
        {"type": "host_presence", "connected": True}
    ]


@pytest.mark.asyncio
async def test_only_changes_go_on_the_wire(handler, game) -> None:
    """The announcer is called from six places so that no transition is missed;
    the dedupe is what stops that costing the room six identical frames."""
    guest_ws = _ws()
    handler._conn.add_connection(guest_ws, is_admin=False, is_dashboard=False)
    game.add_player("Guest", guest_ws)
    handler._conn.add_connection(_ws(), is_admin=True, is_dashboard=False)

    for _ in range(4):
        await handler._announce_host_presence()

    assert _frames(guest_ws, "host_presence") == [
        {"type": "host_presence", "connected": True}
    ]


@pytest.mark.asyncio
async def test_the_host_playing_keeps_the_flag_up_when_their_tab_closes(
    handler, game
) -> None:
    """The Start-game redirect: the ?role=admin socket closes on purpose while
    the same person is joining as a player. Announcing "gone" there would arm
    every guest phone in the room mid-game."""
    admin_ws = _ws()
    handler._conn.add_connection(admin_ws, is_admin=True, is_dashboard=False)
    guest_ws = _ws()
    handler._conn.add_connection(guest_ws, is_admin=False, is_dashboard=False)
    game.add_player("Guest", guest_ws)
    game.add_player("Host", _ws())
    game.get_player("Host").is_admin = True
    await handler._announce_host_presence()

    handler._conn.remove_connection(admin_ws)
    await handler._handle_disconnect(admin_ws, was_admin=True)

    assert _frames(guest_ws, "host_presence") == [
        {"type": "host_presence", "connected": True}
    ]


# ---------------------------------------------------------------------------
# The hooks, where they cannot be reached from a test
# ---------------------------------------------------------------------------


def test_the_connect_path_announces() -> None:
    """``handle()`` needs a real aiohttp request to run, so the two arrival
    hooks inside it are read out of the source instead: the socket that is
    admin from the first byte, and the one #359 upgrades with admin_auth."""
    source = _WEBSOCKET_PY.read_text("utf-8")

    fresh = source.split(
        "self._conn.add_connection(ws, is_admin=is_admin, is_dashboard=is_dashboard)",
        1,
    )[1][:800]
    assert "await self._announce_host_presence()" in fresh

    upgrade = source.split("Admin authenticated via admin_auth frame", 1)[1][:600]
    assert "await self._announce_host_presence()" in upgrade


def test_every_snapshot_the_handler_sends_goes_through_the_wrapper() -> None:
    """One straight call to the serializer left in websocket.py is a frame that
    reaches a phone without the flag — which is a phone back to guessing."""
    source = _WEBSOCKET_PY.read_text("utf-8")
    body = source.split('"""', 1)[1].split('"""', 1)[1]
    calls = re.findall(r"(?<!def )serialize_state_snapshot\(", body)

    assert len(calls) == 1, (
        "serialize_state_snapshot is called directly in websocket.py outside "
        "_snapshot(); every snapshot the handler sends has to carry "
        "host_connected"
    )


def test_the_reset_rule_reads_the_same_helper() -> None:
    """If these two ever compute the answer separately they will drift, and the
    drift is invisible until a guest taps a button that does nothing."""
    source = _WEBSOCKET_PY.read_text("utf-8")
    body = source.split("def _is_reset_authorized(", 1)[1].split("\n    def ", 1)[0]

    assert "return not self._host_connected(game_state)" in body


def test_the_announcer_is_not_left_unused(handler) -> None:
    """Guards against the hooks being removed while the helper stays: the
    announcer is only worth having if something calls it."""
    source = _WEBSOCKET_PY.read_text("utf-8")

    assert source.count("await self._announce_host_presence()") >= 5
