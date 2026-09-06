"""The host's departure has to leave the server, not just be called (#848).

#842 / PR #840 added ``_announce_host_presence()`` at six call sites and a test
that counted them. The v1.16.0-RC2 live test then measured the room from a
guest phone with ``WebSocket.prototype`` wrapped before page load, three times
across one evening: the host's tab opening announced ``host_presence
{connected: true}``, the HA log carried ``Admin disconnected, keeping game
alive for 120s`` when it closed — and no frame was ever sent for the close. So
the guests' #803 escape hatch never armed, and with the host page unable to
rejoin (#846) the evening had no way out at all.

The branch runs. The broadcast does not.

``_handle_disconnect`` is called from the ``finally`` of ``handle()``, and by
then aiohttp has cancelled that request's task. The next ``await`` that yields
to the loop raises ``CancelledError``, and the announcement is exactly such an
await: ``ConnectionManager.broadcast`` fans out through ``asyncio.gather``,
which needs one loop iteration before any child runs. The announcement reached
the flag — ``_host_presence_sent`` flipped to False — and died before a byte
went out. Which is also why the retries stayed silent: the flag then said the
room had been told.

Two things follow, and both are what this file pins. The departure is
announced from an independent task (the shape ``_schedule_admin_pause`` and
``schedule_admin_timeout`` already use in this same path, for this same
reason), and the flag is only left flipped when the broadcast actually
returned.

The tests below drive a real admin WebSocket open and then closed over a live
aiohttp server and read what a real guest socket receives, because that is the
only kind of test that would have caught this: a call site that exists is not a
call site that reaches a phone.
"""

from __future__ import annotations

import asyncio
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT))

from custom_components.quizify.game.state import QuizifyGameState  # noqa: E402
from custom_components.quizify.server import WS_PATH  # noqa: E402
from custom_components.quizify.server.connection import ConnectionManager  # noqa: E402
from custom_components.quizify.server.websocket import (  # noqa: E402
    QuizifyWebSocketHandler,
)

_STUB = Path(__file__).resolve().parent / "fixtures" / "dom_stub.js"
_CORE = (
    _REPO_ROOT / "custom_components" / "quizify" / "www" / "js" / "player-core.js"
)

_NEEDS_NODE = pytest.mark.skipif(
    shutil.which("node") is None, reason="node not installed"
)


class _FakeRuntime:
    def __init__(self, tmp_path: Path) -> None:
        self.data_dir = tmp_path

    def create_task(self, coro):
        return asyncio.ensure_future(coro)

    async def run_in_executor(self, func, *args):
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, func, *args)


@pytest.fixture
def game(tmp_path: Path) -> QuizifyGameState:
    return QuizifyGameState(runtime=_FakeRuntime(tmp_path), entry_id="test")


@pytest.fixture
def handler(game: QuizifyGameState) -> QuizifyWebSocketHandler:
    runtime = _FakeRuntime(game._runtime.data_dir)  # type: ignore[attr-defined]
    h = QuizifyWebSocketHandler(runtime=runtime, game_state_provider=lambda: game)
    h._conn = ConnectionManager(runtime, lambda: game)
    return h


async def _make_client(handler: QuizifyWebSocketHandler) -> TestClient:
    app = web.Application()
    app.router.add_get(WS_PATH, handler.handle)
    client = TestClient(TestServer(app))
    await client.start_server()
    return client


async def _presence(ws, seconds: float = 0.5) -> list[dict]:
    """Every ``host_presence`` frame this socket receives within *seconds*.

    A real read off a real socket rather than an assertion about ``send_str``
    mocks — the whole point of the file.
    """
    frames: list[dict] = []
    loop = asyncio.get_running_loop()
    deadline = loop.time() + seconds
    while loop.time() < deadline:
        try:
            msg = await asyncio.wait_for(ws.receive(), timeout=0.05)
        except (TimeoutError, asyncio.TimeoutError):
            continue
        if msg.type is not web.WSMsgType.TEXT:
            break
        payload = json.loads(msg.data)
        if payload.get("type") == "host_presence":
            frames.append(payload)
    return frames


async def _host_arrives_and_leaves(
    handler: QuizifyWebSocketHandler, client: TestClient, *, via_auth_frame: bool
) -> tuple[list[dict], list[dict]]:
    """Open a guest socket, then open and close a host socket around it."""
    guest = await client.ws_connect(WS_PATH)
    await asyncio.sleep(0.1)
    token = handler._conn.get_or_create_admin_token()

    if via_auth_frame:
        # What the shipped admin page does since #359: the token travels in
        # the first frame, never in the URL.
        admin = await client.ws_connect(WS_PATH + "?role=admin")
        await admin.send_json({"type": "admin_auth", "token": token})
    else:
        admin = await client.ws_connect(WS_PATH + f"?role=admin&token={token}")

    on_arrival = await _presence(guest)

    await admin.close()
    on_departure = await _presence(guest)

    handler._conn.cancel_admin_disconnect()
    await guest.close()
    return on_arrival, on_departure


@pytest.mark.asyncio
async def test_the_closing_host_tab_reaches_a_guest_socket(handler) -> None:
    """The measurement from the live test, reproduced as a test.

    Fails on the code #840 shipped: the arrival arrives, the departure is
    never sent.
    """
    client = await _make_client(handler)
    try:
        arrival, departure = await _host_arrives_and_leaves(
            handler, client, via_auth_frame=False
        )
    finally:
        await client.close()

    assert arrival == [{"type": "host_presence", "host_connected": True}]
    assert departure == [{"type": "host_presence", "host_connected": False}], (
        "closing the last admin socket has to put the departure on the wire, "
        "not merely call the announcer from a task that is already cancelled"
    )


@pytest.mark.asyncio
async def test_the_same_holds_for_the_token_in_the_first_frame(handler) -> None:
    """The path the real admin page takes (#359), and the one the live test
    took: the log line was ``Admin authenticated via admin_auth frame``."""
    client = await _make_client(handler)
    try:
        arrival, departure = await _host_arrives_and_leaves(
            handler, client, via_auth_frame=True
        )
    finally:
        await client.close()

    assert arrival == [{"type": "host_presence", "host_connected": True}]
    assert departure == [{"type": "host_presence", "host_connected": False}]


@pytest.mark.asyncio
async def test_the_flag_ends_up_where_the_room_was_actually_told(handler) -> None:
    """``_host_presence_sent`` is the dedupe, so it has to record what left the
    server. It flipped either way before this fix, which is why the three
    reproductions in one evening all stayed silent instead of one of them
    catching up."""
    client = await _make_client(handler)
    try:
        await _host_arrives_and_leaves(handler, client, via_auth_frame=False)
    finally:
        await client.close()

    assert handler._host_presence_sent is False


@pytest.mark.asyncio
async def test_a_broadcast_that_never_happened_is_not_remembered(handler, game) -> None:
    """The second half. A dedupe flag written before the send is a flag that
    can claim a frame the room never got — and then suppress the retry."""

    async def _explode(message: dict) -> None:
        raise asyncio.CancelledError

    handler._conn.add_connection(_DummyWS(), is_admin=True, is_dashboard=False)
    await handler._announce_host_presence()
    assert handler._host_presence_sent is True

    handler._conn.remove_connection(next(iter(handler._conn.connections)))
    handler._conn.broadcast = _explode  # type: ignore[method-assign]
    with pytest.raises(asyncio.CancelledError):
        await handler._announce_host_presence()

    assert handler._host_presence_sent is True, (
        "the send never happened, so the flag must still say what the room "
        "was last actually told"
    )


class _DummyWS:
    """Enough of a socket for the connection registry; never written to."""

    closed = False

    async def send_str(self, payload: str) -> None:  # pragma: no cover
        raise AssertionError("this socket is never sent to")


@pytest.mark.asyncio
async def test_the_disconnect_path_schedules_rather_than_awaits(handler) -> None:
    """Named directly, because the difference is the entire bug.

    ``_handle_disconnect`` runs in a cancelled task; anything it awaits is
    unreachable. The counterpart in #842's file counted awaited call sites and
    passed all the way through a live test that proved none of them fired.
    """
    source = (
        _REPO_ROOT
        / "custom_components"
        / "quizify"
        / "server"
        / "websocket.py"
    ).read_text("utf-8")
    body = source.split("async def _handle_disconnect(", 1)[1].split(
        "\n    def _schedule_admin_pause(", 1
    )[0]

    assert "await self._announce_host_presence()" not in body
    assert body.count("self._announce_host_presence_soon()") == 2


# ---------------------------------------------------------------------------
# …and the phone does something with it
# ---------------------------------------------------------------------------
#
# The frame leaving the server is only half of "reaches a phone". #840's frame
# said ``connected``; ``_rememberHostFlag`` — the one reader of either key on
# any surface — reads ``host_connected``, the name the snapshot has always
# used. So even a departure that got out would have been read as "no host
# field here, keep the answer I have", and no hatch would have armed. The
# reader's ``typeof … === 'boolean'`` guard exists precisely so that an
# unrelated frame cannot wipe the flag; it is what made this silent instead of
# noisy.
#
# The script below takes the frame THIS server builds — not one written out by
# hand next to the assertion — and runs it through the real reader, the real
# three-way ``_hostPresence`` and the real ``refreshStageReset``.

_PHONE_SCRIPT = """
require({stub});
QZ.els([
    'reveal-reset-btn', 'reveal-reset-controls',
    'lightning-recap-reset-btn', 'lightning-recap-reset-controls',
    'hotseat-reset-btn', 'hotseat-reset-controls'
]);
[
    'reveal-reset-btn', 'reveal-reset-controls',
    'lightning-recap-reset-btn', 'lightning-recap-reset-controls',
    'hotseat-reset-btn', 'hotseat-reset-controls'
].forEach(function (id) {{ document.getElementById(id).classList.add('hidden'); }});

var state = {{ isAdmin: false }};
// The real 60 s wait, shortened. The delay is #299's, not this bug's.
var RESET_AFFORDANCE_DELAY_MS = 5;
var _resetAffordanceTimers = {{}};

{hatch}

// A guest sitting on the Hot Seat result with the host present, which is
// where the live test was each time the admin tab was closed.
_rememberHostFlag({arrival});
setResetStage('HOT_SEAT_REVEAL');

setTimeout(function () {{
    var withHost = !document.getElementById('hotseat-reset-controls')
        .classList.contains('hidden');

    // The host closes the tab.
    _rememberHostFlag({departure});
    refreshStageReset();

    setTimeout(function () {{
        console.log(JSON.stringify({{
            withHost: withHost,
            hostGone: !document.getElementById('hotseat-reset-controls')
                .classList.contains('hidden'),
            presence: _hostPresence()
        }}));
    }}, 40);
}}, 40);
"""


def _departure_frame() -> dict:
    """The exact ``host_presence`` payload the announcer broadcasts."""
    sent: list[dict] = []

    async def _drive() -> None:
        handler = QuizifyWebSocketHandler(
            runtime=_FakeRuntime(Path(".")), game_state_provider=lambda: None
        )
        game_state = QuizifyGameState(runtime=_FakeRuntime(Path(".")), entry_id="t")
        handler._get_game_state = lambda: game_state  # type: ignore[assignment]
        handler._conn = ConnectionManager(_FakeRuntime(Path(".")), lambda: game_state)

        async def _capture(message: dict) -> None:
            sent.append(message)

        handler._conn.broadcast = _capture  # type: ignore[method-assign]
        admin = _DummyWS()
        handler._conn.add_connection(admin, is_admin=True, is_dashboard=False)
        await handler._announce_host_presence()
        handler._conn.remove_connection(admin)
        await handler._announce_host_presence()

    asyncio.run(_drive())
    assert [f["type"] for f in sent] == ["host_presence", "host_presence"]
    return {"arrival": sent[0], "departure": sent[1]}


def _phone(frames: dict) -> dict:
    script = _PHONE_SCRIPT.format(
        stub=json.dumps(str(_STUB)),
        hatch=_slice_source(
            "    function armResetAffordance(btnId, wrapperId) {",
            "    function setupResetAffordance() {",
        ),
        arrival=json.dumps(frames["arrival"]),
        departure=json.dumps(frames["departure"]),
    )
    out = subprocess.run(
        ["node", "-e", script], capture_output=True, text=True, check=True
    )
    return json.loads(out.stdout)


def _slice_source(start: str, end: str) -> str:
    source = _CORE.read_text("utf-8")
    a = source.index(start)
    return source[a : source.index(end, a)]


def test_the_frame_is_named_the_way_its_only_reader_reads_it() -> None:
    """One fact, one name. The snapshot's key and the broadcast's key were
    different words for the same boolean, and the phone knew only one of
    them."""
    reader = _slice_source(
        "    function _rememberHostFlag(msg) {", "    function _rememberRoster(msg) {"
    )
    key = re.search(r"msg\.([a-z_]+) === 'boolean'", reader)
    assert key is not None
    assert key.group(1) in _departure_frame()["departure"], (
        "the phone reads msg.%s; the broadcast has to carry that key"
        % (key.group(1),)
    )


@_NEEDS_NODE
def test_the_hatch_stays_shut_while_the_host_is_there() -> None:
    """Guards the guard, and it is the half RC2 got right: a reset button in
    front of a room whose host is sitting at the admin page is #834."""
    result = _phone(_departure_frame())

    assert result["withHost"] is False


@_NEEDS_NODE
def test_the_departure_arms_the_hatch_on_the_hot_seat_result() -> None:
    """The end of the chain the live test measured as broken at both links:
    the frame now leaves the server, and the phone now reads it.

    The screenshot on #848 is this screen — four phones on "Hang tight — the
    host continues the game" at 10 s, 70 s and 110 s after the tab closed, with
    no way out for anybody.
    """
    result = _phone(_departure_frame())

    assert result["presence"] == "gone"
    assert result["hostGone"] is True
