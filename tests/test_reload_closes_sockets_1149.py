"""A reload must not leave clients on the retired WebSocket handler (#1149).

#606 fixed where *new* sockets go after a reload. Sockets already open stayed on
the old handler, whose game provider read ``hass.data``, so after the next setup
they drove the new game through a connection manager the round results never
reach: ``start_game`` was accepted, then the reveal and the finale went to the
new handler's empty manager and the room sat on 0:00. A phone that reloaded also
saw ``host_connected: false`` (#842) while the host was there, on the old side.

These tests use real sockets on HA's real aiohttp app and the real config-entry
state machine. Admin checks run against the real token store, so every grant
here could also have been refused, and the refusals are asserted too.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from unittest.mock import patch

import pytest
from aiohttp import WSMsgType

pytest.importorskip("homeassistant")
pytest.importorskip("pytest_homeassistant_custom_component")

from homeassistant.core import HomeAssistant  # noqa: E402
from homeassistant.setup import async_setup_component  # noqa: E402
from pytest_homeassistant_custom_component.common import (  # noqa: E402
    MockConfigEntry,
)

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from custom_components.quizify.const import (  # noqa: E402
    DOMAIN,
    ERR_ADMIN_REQUIRED,
    ERR_GAME_NOT_STARTED,
    ERR_INVALID_ACTION,
)
from custom_components.quizify.server import WS_PATH  # noqa: E402
from custom_components.quizify.server.connection import (  # noqa: E402
    ConnectionManager,
)

pytestmark = pytest.mark.usefixtures("enable_custom_integrations")

RECEIVE_TIMEOUT = 5


@pytest.fixture(autouse=True)
def _stub_frontend_panel():
    """Patch the frontend panel helpers (same harness as #313 / #606)."""
    with (
        patch("homeassistant.components.frontend.async_register_built_in_panel"),
        patch("homeassistant.components.frontend.async_remove_panel"),
    ):
        yield


@pytest.fixture
async def room(hass: HomeAssistant, aiohttp_client):
    """A set-up entry and a client on HA's own aiohttp app."""
    assert await async_setup_component(hass, "http", {"http": {}})
    await hass.async_block_till_done()
    entry = MockConfigEntry(domain=DOMAIN, unique_id=DOMAIN)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id) is True
    await hass.async_block_till_done()
    client = await aiohttp_client(hass.http.app)
    yield entry, client
    await client.close()


async def _unload(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    assert await hass.config_entries.async_unload(entry.entry_id) is True
    await hass.async_block_till_done()


async def _setup_again(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    assert await hass.config_entries.async_setup(entry.entry_id) is True
    await hass.async_block_till_done()


async def _next_of_type(ws, *types: str) -> dict:
    """Read frames until one of *types* arrives, failing on a closed socket."""
    while True:
        msg = await asyncio.wait_for(ws.receive(), RECEIVE_TIMEOUT)
        assert msg.type == WSMsgType.TEXT, f"socket ended waiting for {types}: {msg}"
        data = msg.json()
        if data.get("type") in types:
            return data


async def _admin(client, token: str | None = None) -> tuple:
    """Open an admin page's socket, sending what admin.js sends on open."""
    ws = await client.ws_connect(f"{WS_PATH}?role=admin")
    if token:
        await ws.send_json({"type": "admin_auth", "token": token})
    await ws.send_json({"type": "admin_connect"})
    frame = await _next_of_type(ws, "game_state", "error")
    return ws, frame


async def _assert_closed_by_server(ws) -> None:
    msg = await asyncio.wait_for(ws.receive(), RECEIVE_TIMEOUT)
    while msg.type == WSMsgType.TEXT:
        msg = await asyncio.wait_for(ws.receive(), RECEIVE_TIMEOUT)
    assert msg.type == WSMsgType.CLOSE, msg
    assert msg.data == 1012


async def _server_side_finished(conn: ConnectionManager) -> None:
    """Wait for the read loops' ``finally``, which runs after the client's close."""
    async with asyncio.timeout(RECEIVE_TIMEOUT):
        while conn.connections:
            await asyncio.sleep(0.01)


async def test_unload_closes_every_role_of_socket(
    hass: HomeAssistant, room
) -> None:
    """Admin, television and phone all get a close, so all three reconnect."""
    entry, client = room
    admin, frame = await _admin(client)
    assert frame["type"] == "game_state"
    dashboard = await client.ws_connect(f"{WS_PATH}?role=dashboard")
    phone = await client.ws_connect(WS_PATH)
    await phone.send_json({"type": "join", "name": "Anna"})
    await _next_of_type(phone, "joined", "game_state")

    await _unload(hass, entry)

    for ws in (admin, dashboard, phone):
        await _assert_closed_by_server(ws)


async def test_closing_arms_nothing_on_the_retired_handler(
    hass: HomeAssistant, room
) -> None:
    """The disconnect path runs after teardown and must not start timers."""
    entry, client = room
    old_conn = hass.data[DOMAIN]["ws_handler"].conn
    admin, _ = await _admin(client)
    phone = await client.ws_connect(WS_PATH)
    await phone.send_json({"type": "join", "name": "Anna"})
    await _next_of_type(phone, "joined", "game_state")

    await _unload(hass, entry)
    await _assert_closed_by_server(admin)
    await _assert_closed_by_server(phone)
    await _server_side_finished(old_conn)

    assert not old_conn.has_pending_admin_disconnect()
    assert not old_conn._pending_removals


async def test_admin_token_reauthenticates_on_the_live_handler(
    hass: HomeAssistant, room
) -> None:
    """The host keeps their rights after the forced reconnect, strangers don't."""
    entry, client = room
    old_handler = hass.data[DOMAIN]["ws_handler"]
    admin, frame = await _admin(client)
    token = frame["admin_session_token"]
    # The token write is fire-and-forget on some paths; the reload must read it.
    await hass.async_block_till_done()

    await _unload(hass, entry)
    await _assert_closed_by_server(admin)
    await _setup_again(hass, entry)
    live = hass.data[DOMAIN]["ws_handler"]
    assert live is not old_handler

    # Refused: no token, then a wrong one.
    tokenless, frame = await _admin(client)
    assert frame["type"] == "error"
    assert frame["code"] == ERR_INVALID_ACTION
    wrong, frame = await _admin(client, "not-the-token")
    assert frame["type"] == "error"
    assert not live.conn.has_admin_connections()

    # Allowed: the token the page kept in storage.
    host, frame = await _admin(client, token)
    assert frame["type"] == "game_state"
    assert frame["admin_session_token"] == token
    assert frame["host_connected"] is True
    assert live.conn.has_admin_connections()
    assert not old_handler.conn.has_admin_connections()

    for ws in (tokenless, wrong, host):
        await ws.close()


async def test_host_presence_reaches_a_phone_that_got_back_first(
    hass: HomeAssistant, room
) -> None:
    """#842: the phone hears the host return, so the guest reset disarms."""
    entry, client = room
    admin, frame = await _admin(client)
    token = frame["admin_session_token"]
    await hass.async_block_till_done()

    await _unload(hass, entry)
    await _assert_closed_by_server(admin)
    await _setup_again(hass, entry)

    phone = await client.ws_connect(WS_PATH)
    await phone.send_json({"type": "get_state"})
    snapshot = await _next_of_type(phone, "game_state")
    assert snapshot["host_connected"] is False

    host, _ = await _admin(client, token)
    presence = await _next_of_type(phone, "host_presence")
    assert presence["host_connected"] is True

    await host.close()
    await phone.close()


async def test_socket_left_open_cannot_touch_the_new_game(
    hass: HomeAssistant, room
) -> None:
    """A socket that survives unload and setup gets ERR_GAME_NOT_STARTED.

    The close is suppressed to force the leftover the bound game exists for.
    Before the fix this ``join`` landed in the new game and start_game was
    accepted, which is the room stuck on 0:00 from the issue.
    """
    entry, client = room
    old_handler = hass.data[DOMAIN]["ws_handler"]
    admin, _ = await _admin(client)
    phone = await client.ws_connect(WS_PATH)

    with patch.object(ConnectionManager, "close_all_connections", autospec=True):
        await _unload(hass, entry)
    await _setup_again(hass, entry)
    new_game = hass.data[DOMAIN]["game"]
    phase_before = new_game.phase

    await phone.send_json({"type": "join", "name": "Ghost"})
    frame = await _next_of_type(phone, "error", "joined", "game_state")
    assert frame.get("code") == ERR_GAME_NOT_STARTED
    assert new_game.get_player("Ghost") is None

    await admin.send_json({"type": "start_game"})
    frame = await _next_of_type(admin, "error", "game_state")
    assert frame.get("code") == ERR_GAME_NOT_STARTED
    assert new_game.phase == phase_before
    # Control: the refusal comes from retirement, not from a broken game. The
    # live handler does answer the same socket type, and the guard on admin
    # commands still refuses a phone that is not the host.
    fresh = await client.ws_connect(WS_PATH)
    await fresh.send_json({"type": "join", "name": "Real"})
    await _next_of_type(fresh, "joined", "game_state")
    assert new_game.get_player("Real") is not None
    await fresh.send_json({"type": "end_game"})
    frame = await _next_of_type(fresh, "error")
    assert frame["code"] in (ERR_ADMIN_REQUIRED, ERR_INVALID_ACTION)

    await old_handler.conn.close_all_connections()
    await fresh.close()


async def test_handshake_in_flight_during_unload_is_closed_too(
    hass: HomeAssistant, room
) -> None:
    """A socket not yet registered when unload ran still must not stay behind."""
    entry, client = room
    handler = hass.data[DOMAIN]["ws_handler"]
    real_grant = handler._grant_admin

    async def _grant_while_unloading(role, token, request):
        await handler.retire()
        return await real_grant(role, token, request)

    with patch.object(handler, "_grant_admin", side_effect=_grant_while_unloading):
        ws = await client.ws_connect(WS_PATH)
        await _assert_closed_by_server(ws)

    await _server_side_finished(handler.conn)
    await _unload(hass, entry)


class _RefusingSocket:
    """A socket whose close misbehaves, as a half-dead phone's can."""

    def __init__(self, behaviour: str) -> None:
        self.behaviour = behaviour
        self.close_calls: list[dict] = []

    async def close(self, **kwargs) -> bool:
        self.close_calls.append(kwargs)
        if self.behaviour == "hang":
            await asyncio.Event().wait()
        if self.behaviour == "raise":
            raise ConnectionResetError("peer gone")
        return True


async def test_a_bad_socket_does_not_stop_the_others_or_the_unload() -> None:
    """Hanging and raising closes are bounded, and the rest still close."""
    conn = ConnectionManager.__new__(ConnectionManager)
    hanging = _RefusingSocket("hang")
    raising = _RefusingSocket("raise")
    healthy = _RefusingSocket("ok")
    conn.connections = {hanging, raising, healthy}  # type: ignore[assignment]

    with patch.object(ConnectionManager, "_SEND_TIMEOUT", 0.01):
        await asyncio.wait_for(conn.close_all_connections(), 1)

    for ws in (hanging, raising, healthy):
        assert len(ws.close_calls) == 1
        assert ws.close_calls[0]["code"] == 1012
