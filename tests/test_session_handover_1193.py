"""A stored seat asks before it rejoins, and a superseded tab is told (#1193).

Found by the nightly live test on v1.26.0-RC3. #1183 keeps the phone session
in localStorage for 12 h, so:

* a shared phone handed to the next guest rejoined under the previous guest's
  name, silently — also into a game that had ended, or into the next one
  after "Play again" (which keeps the roster and the tokens);
* with two tabs on one session the server rebinds the seat to the newest
  socket, and the older tab kept the broadcasts but never its own question:
  it sat on the empty skeleton.

Server half: ``reconnect`` with ``last_game_id`` (sent only by a freshly
loaded page) answers ``reconnect_stale`` instead of seating when that game is
over; a rebind sends ``session_moved`` — type only — to the socket it took
the seat from. Client half, run for real under node: the fresh page sends the
field, a socket blip does not, the prompt's two buttons do what they say, and
a moved tab leaves the shared storage alone.
"""

from __future__ import annotations

import json
import shutil
import subprocess
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
from custom_components.quizify.server.protocol import SERVER_FRAMES  # noqa: E402
from custom_components.quizify.server.websocket import (  # noqa: E402
    QuizifyWebSocketHandler,
)

_WWW = _REPO_ROOT / "custom_components" / "quizify" / "www"
_CORE = _WWW / "js" / "player-core.js"
_CLIENT_CORE = _WWW / "js" / "client-core.js"
_STUB = Path(__file__).resolve().parent / "fixtures" / "dom_stub.js"

_NEEDS_NODE = pytest.mark.skipif(
    shutil.which("node") is None, reason="node not installed"
)


# ---------------------------------------------------------------------------
# Server
# ---------------------------------------------------------------------------


class _FakeRuntime:
    def __init__(self, tmp_path: Path) -> None:
        self.data_dir = tmp_path


def _ws() -> MagicMock:
    ws = MagicMock()
    ws.closed = False
    ws.send_json = AsyncMock()
    return ws


def _frames(ws: MagicMock) -> list[dict]:
    return [c.args[0] for c in ws.send_json.await_args_list]


def _types(ws: MagicMock) -> list[str]:
    return [f.get("type") for f in _frames(ws)]


@pytest.fixture
def game(tmp_path: Path) -> QuizifyGameState:
    return QuizifyGameState(runtime=_FakeRuntime(tmp_path), entry_id="test")


@pytest.fixture
def handler(game: QuizifyGameState, tmp_path: Path) -> QuizifyWebSocketHandler:
    runtime = _FakeRuntime(tmp_path)
    h = QuizifyWebSocketHandler(runtime=runtime, game_state_provider=lambda: game)
    h._conn = ConnectionManager(runtime, lambda: game)
    h._conn.broadcast = AsyncMock()
    h._mark_roster_dirty = MagicMock()
    return h


def _seat(handler, game, name: str = "Anna") -> tuple[MagicMock, str]:
    """Anna joined on one socket and holds a token for it."""
    ws = _ws()
    handler._conn.add_connection(ws, is_admin=False, is_dashboard=False)
    ok, _err = game.add_player(name, handler._conn.connection_id(ws))
    assert ok
    return ws, handler._conn.create_session_token(name)


def _running(game, game_id: str = "game-a") -> None:
    game.game_id = game_id
    game.phase = GamePhase.QUESTION_ACTIVE


@pytest.mark.asyncio
async def test_a_rebind_tells_the_old_socket_and_nothing_else(handler, game) -> None:
    old_ws, token = _seat(handler, game)
    _running(game)
    new_ws = _ws()
    handler._conn.add_connection(new_ws, is_admin=False, is_dashboard=False)

    await handler._handle_reconnect(
        new_ws, {"session_token": token, "name": "Anna"}, game
    )

    assert "reconnected" in _types(new_ws)
    # Exactly one frame for the old tab, and it carries no name and no token.
    assert _frames(old_ws) == [{"type": "session_moved"}]
    # The old socket cannot act for the player any more.
    assert handler._player_for_ws(game, old_ws) is None
    assert handler._player_for_ws(game, new_ws).name == "Anna"


@pytest.mark.asyncio
async def test_a_plain_reload_notifies_nobody(handler, game) -> None:
    """The old socket is already closed: nothing to tell, nothing sent."""
    old_ws, token = _seat(handler, game)
    _running(game)
    old_ws.closed = True
    new_ws = _ws()

    await handler._handle_reconnect(
        new_ws, {"session_token": token, "name": "Anna"}, game
    )

    assert "reconnected" in _types(new_ws)
    assert _frames(old_ws) == []


@pytest.mark.asyncio
async def test_the_host_pages_own_socket_is_not_warned(handler, game) -> None:
    """The join-as-player redirect hands the seat over on purpose."""
    admin_ws = _ws()
    handler._conn.add_connection(admin_ws, is_admin=True, is_dashboard=False)
    game.add_player("Host", handler._conn.connection_id(admin_ws))
    token = handler._conn.create_session_token("Host")
    _running(game)

    await handler._handle_reconnect(
        _ws(), {"session_token": token, "name": "Host"}, game
    )

    assert "session_moved" not in _types(admin_ws)


@pytest.mark.asyncio
async def test_a_reload_into_the_same_game_stays_silent(handler, game) -> None:
    """#1183's point: back closed the tab, the guest scans again, plays on."""
    _old, token = _seat(handler, game)
    _running(game, "game-a")
    ws = _ws()

    await handler._handle_reconnect(
        ws, {"session_token": token, "name": "Anna", "last_game_id": "game-a"}, game
    )

    assert "reconnected" in _types(ws)
    assert "reconnect_stale" not in _types(ws)


@pytest.mark.asyncio
async def test_a_reload_in_the_lobby_before_any_game_stays_silent(
    handler, game
) -> None:
    _old, token = _seat(handler, game)
    assert game.game_id is None and game.phase == GamePhase.LOBBY
    ws = _ws()

    await handler._handle_reconnect(
        ws, {"session_token": token, "name": "Anna", "last_game_id": ""}, game
    )

    assert "reconnected" in _types(ws)


@pytest.mark.parametrize(
    ("phase", "current", "stored"),
    [
        (GamePhase.FINALE, "game-a", "game-a"),  # the game it sat in has ended
        (GamePhase.QUESTION_ACTIVE, "game-b", "game-a"),  # "Play again" since
        (GamePhase.LOBBY, "game-b", "game-a"),
        (GamePhase.QUESTION_ACTIVE, "game-a", ""),  # left the lobby, game began
    ],
)
@pytest.mark.asyncio
async def test_a_seat_from_a_game_that_is_over_asks_first(
    handler, game, phase, current, stored
) -> None:
    old_ws, token = _seat(handler, game)
    old_ws.closed = True
    game.game_id = current
    game.phase = phase
    ws = _ws()

    await handler._handle_reconnect(
        ws, {"session_token": token, "name": "Anna", "last_game_id": stored}, game
    )

    assert _frames(ws) == [{"type": "reconnect_stale"}]
    # Nobody was seated and the token still works for "Continue".
    assert handler._player_for_ws(game, ws) is None
    assert handler._conn.get_player_for_token(token) == "Anna"

    await handler._handle_reconnect(ws, {"session_token": token, "name": "Anna"}, game)
    assert "reconnected" in _types(ws)


@pytest.mark.asyncio
async def test_a_reconnect_without_the_field_is_unchanged(handler, game) -> None:
    """A socket blip, an older page, the host's redirect: no question asked."""
    _old, token = _seat(handler, game)
    game.game_id = "game-a"
    game.phase = GamePhase.FINALE
    ws = _ws()

    await handler._handle_reconnect(ws, {"session_token": token, "name": "Anna"}, game)

    assert "reconnected" in _types(ws)


def test_both_frames_are_declared_and_empty() -> None:
    for frame in ("session_moved", "reconnect_stale"):
        spec = SERVER_FRAMES[frame]
        assert spec.required == frozenset() and spec.optional == frozenset()


# ---------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------


def _block(source: str, start: str, end: str) -> str:
    a = source.index(start)
    return source[a : source.index(end, a)]


def _fn(source: str, start: str) -> str:
    a = source.index(start)
    return source[a : source.index("\n    }\n", a) + len("\n    }\n")]


def _client_code() -> str:
    source = _CORE.read_text("utf-8")
    identity = _block(
        source,
        "    function forgetIdentity()",
        "    // ============================================\n    // Connect",
    )
    connect = _fn(source, "    function connect() {")
    session = _block(
        source,
        "    function handleSessionMoved()",
        "    // Put the join form back in a usable state",
    )
    return identity + "\n" + connect + "\n" + session


_HARNESS = """
require(%(stub)s);
function mkStore() {
  const v = {};
  return {
    _v: v,
    setItem: function (k, x) { v[k] = String(x); },
    getItem: function (k) { return v[k] === undefined ? null : v[k]; },
    removeItem: function (k) { delete v[k]; }
  };
}
global.localStorage = mkStore();
global.sessionStorage = mkStore();
QZ.load(%(client_core)s);
var C = window.QuizifyClientCore;

global.WebSocket = { CONNECTING: 0, OPEN: 1, CLOSED: 3 };
var reloads = 0;
global.location = { search: '', reload: function () { reloads++; } };
global.URLSearchParams = URLSearchParams;
global.QuizifyUtils = { readAdminToken: function () { return null; } };

var views = [];
var sockets = [];
var sent = [];
var pu = {
  getSession: C.getSession, saveSession: C.saveSession,
  clearSession: C.clearSession, saveSessionGame: C.saveSessionGame,
  showView: function (id) { views.push(id); },
  updateConnectionIndicator: function () {},
  hideReconnectingOverlay: function () {},
  createWebSocket: function (path, handlers) {
    var ws = { readyState: 1, handlers: handlers, closed: false,
      close: function () { this.closed = true; this.readyState = 3; } };
    sockets.push(ws);
    return ws;
  }
};
var state = {};
var game = null;
var els = { nameInput: QZ.el('name-input'), joinBtn: QZ.el('join-btn') };
['session-view', 'session-title', 'session-primary-btn', 'session-other-btn']
  .forEach(function (id) { QZ.el(id); });
function _t() { return function (k, p) { return p ? k + JSON.stringify(p) : k; }; }
function send(type, payload) {
  sent.push(Object.assign({ type: type }, payload || {}));
  return true;
}
function handleMessage() {}
function beginJoinPending() {}
function endJoinPending() {}
function hideJoinHint() {}
function clearGameChrome() {}
function _releaseWakeLock() {}
var _wsOpenTimeout = null;

%(code)s

setupSessionChoice();
function openSocket() {
  connect();
  var ws = sockets[sockets.length - 1];
  ws.handlers.onOpen(ws);
  return ws;
}
function lastSent() { return sent[sent.length - 1]; }
var out = {};

%(scenario)s

console.log(JSON.stringify(out));
"""


def _run(scenario: str) -> dict:
    script = _HARNESS % {
        "stub": json.dumps(str(_STUB)),
        "client_core": json.dumps(str(_CLIENT_CORE)),
        "code": _client_code(),
        "scenario": scenario,
    }
    run = subprocess.run(
        ["node", "-e", script], capture_output=True, text=True, check=False
    )
    assert run.returncode == 0, run.stderr
    return json.loads(run.stdout.strip().splitlines()[-1])


@_NEEDS_NODE
def test_a_fresh_page_names_the_stored_game_and_a_blip_does_not() -> None:
    out = _run(
        """
C.saveSession('tok-1', 'Anna');
C.saveSessionGame('game-a');
openSocket();
out.fresh = lastSent();

// Seated: a blip resumes this tab's own seat, silently, without the field.
state.sessionToken = 'tok-2'; state.playerName = 'Anna'; state.playerId = 'Anna';
sockets[sockets.length - 1].readyState = 3;
openSocket();
out.blip = lastSent();

// A session saved before #1193 has no game: the old silent reconnect.
state = {};
C.clearSession();
C.saveSession('tok-3', 'Ben');
openSocket();
out.legacy = lastSent();
"""
    )
    assert out["fresh"] == {
        "type": "reconnect",
        "session_token": "tok-1",
        "name": "Anna",
        "last_game_id": "game-a",
    }
    assert out["blip"] == {
        "type": "reconnect",
        "session_token": "tok-2",
        "name": "Anna",
    }
    assert out["legacy"] == {
        "type": "reconnect",
        "session_token": "tok-3",
        "name": "Ben",
    }


@_NEEDS_NODE
def test_continue_rejoins_without_asking_again() -> None:
    out = _run(
        """
C.saveSession('tok-1', 'Anna');
C.saveSessionGame('game-a');
var ws = openSocket();
showSessionChoice('stale', 'Anna');
var title = document.getElementById('session-title');
out.view = views[views.length - 1];
out.title = title.textContent;
out.params = title.getAttribute('data-i18n-params');
out.primary = document.getElementById('session-primary-btn').textContent;
document.getElementById('session-primary-btn').click();
out.sent = lastSent();
// A socket drop before the answer: the reconnect still does not ask again.
ws.readyState = 3;
openSocket();
out.afterDrop = lastSent();
"""
    )
    assert out["view"] == "session-view"
    assert out["title"] == 'session.continueAs{"name":"Anna"}'
    assert json.loads(out["params"]) == {"name": "Anna"}
    assert out["primary"] == "session.continue"
    assert out["sent"] == {
        "type": "reconnect",
        "session_token": "tok-1",
        "name": "Anna",
    }
    assert out["afterDrop"] == {
        "type": "reconnect",
        "session_token": "tok-1",
        "name": "Anna",
    }


@_NEEDS_NODE
def test_someone_else_gets_an_empty_form_and_the_old_seat_is_forgotten() -> None:
    out = _run(
        """
C.saveSession('tok-1', 'Anna');
C.saveSessionGame('game-a');
openSocket();
var before = sent.length;
els.nameInput.value = 'Anna';
showSessionChoice('stale', 'Anna');
document.getElementById('session-other-btn').click();
out.view = views[views.length - 1];
out.name = els.nameInput.value;
out.stored = C.getSession();
out.nothingSent = sent.length === before;
"""
    )
    assert out["view"] == "join-view"
    assert out["name"] == ""
    assert out["stored"] == {"token": None, "name": None, "game": None}
    assert out["nothingSent"] is True


@_NEEDS_NODE
def test_a_moved_tab_stops_leaves_storage_alone_and_can_take_the_seat_back() -> None:
    out = _run(
        """
// The newer tab wrote its rotated token into the shared storage.
C.saveSession('tok-new', 'Anna');
C.saveSessionGame('game-a');
var ws = openSocket();
state.sessionToken = 'tok-old'; state.playerName = 'Anna'; state.playerId = 'Anna';
handleSessionMoved();
out.view = views[views.length - 1];
out.title = document.getElementById('session-title').textContent;
out.socketClosed = ws.closed;
out.identityDropped = state.playerName === null && state.sessionToken === null;
out.storage = C.getSession();
// online / visibilitychange call connect(): nothing happens.
var count = sockets.length;
connect();
out.noReconnect = sockets.length === count;
document.getElementById('session-primary-btn').click();
out.reloads = reloads;
"""
    )
    assert out["view"] == "session-view"
    assert out["title"] == "session.movedTitle"
    assert out["socketClosed"] is True
    assert out["identityDropped"] is True
    assert out["storage"] == {"token": "tok-new", "name": "Anna", "game": "game-a"}
    assert out["noReconnect"] is True
    assert out["reloads"] == 1


@_NEEDS_NODE
def test_a_moved_tab_joining_as_someone_else_never_reads_the_other_seat() -> None:
    out = _run(
        """
C.saveSession('tok-new', 'Anna');
C.saveSessionGame('game-a');
openSocket();
state.sessionToken = 'tok-old'; state.playerName = 'Anna';
handleSessionMoved();
var before = sent.length;
document.getElementById('session-other-btn').click();
out.view = views[views.length - 1];
out.noReconnectSent = sent.slice(before)
  .every(function (m) { return m.type !== 'reconnect'; });
out.storage = C.getSession();
"""
    )
    assert out["view"] == "join-view"
    assert out["noReconnectSent"] is True
    # The other tab's seat is still there for the other tab.
    assert out["storage"]["token"] == "tok-new"


def test_the_router_handles_both_frames_and_mutes_a_moved_tab() -> None:
    source = _CORE.read_text("utf-8")
    router = source[source.index("    function handleMessage(msg) {") :][:900]
    assert "if (state.sessionMoved) return;" in router
    assert "case 'session_moved':" in router
    assert "case 'reconnect_stale':" in router
    # The seat's game is remembered from every snapshot that names it.
    snap = source[source.index("    function handleGameState(msg) {") :][:500]
    assert "pu.saveSessionGame(msg.game_id)" in snap


def test_the_view_is_registered_and_the_texts_exist_in_every_language() -> None:
    utils = (_WWW / "js" / "player-utils.js").read_text("utf-8")
    assert "'session-view'" in utils
    html = (_WWW / "player.html").read_text("utf-8")
    for el in (
        "session-view",
        "session-title",
        "session-primary-btn",
        "session-other-btn",
    ):
        assert f'id="{el}"' in html
    for lang in ("de", "en", "es"):
        session = json.loads((_WWW / "i18n" / f"{lang}.json").read_text("utf-8"))[
            "session"
        ]
        assert set(session) == {
            "movedTitle",
            "playHere",
            "continueAs",
            "continue",
            "someoneElse",
        }
        assert "{name}" in session["continueAs"]
