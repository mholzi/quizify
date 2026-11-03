"""The host's own seat stays in the host's tab (#1212).

Side finding from #1209 (#1210). With "Join as player" on ``/quizify/admin``
the host page wrote the seat it was given (``joined`` / ``reconnected``) into
the shared player session: ``quizify_session_token`` / ``quizify_player_name``
in localStorage, which every tab of the browser shares (#1183). A
``/quizify/player`` tab opened later in the same browser read it on connect
and reconnected as the host, silently (same running game, so no #1193
prompt): a second view of the host's seat, admin controls included.

The fix keeps the host seat under keys of its own in the tab's sessionStorage
(``QuizifyClientCore.saveHostSeat``), and the host's player page
(``?admin=true``, reached by a same-tab redirect) switches the session to it
(``useHostSeat``). A player page never reads those keys.

Run for real under node: the real ``client-core.js``, the ``joined`` /
``reconnected`` handling from ``admin.js`` and ``connect()`` from
``player-core.js``, in a browser model where localStorage is one store for
every tab and sessionStorage is one store per tab, surviving its reloads.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from tests.test_session_handover_1193 import _client_code

_REPO = Path(__file__).resolve().parent.parent
_JS = _REPO / "custom_components" / "quizify" / "www" / "js"
_ADMIN_JS = _JS / "admin.js"
_CORE_JS = _JS / "player-core.js"
_CLIENT_CORE = _JS / "client-core.js"
_STUB = Path(__file__).resolve().parent / "fixtures" / "dom_stub.js"

_NEEDS_NODE = pytest.mark.skipif(
    shutil.which("node") is None, reason="node not installed"
)


def _admin_frames() -> str:
    """The admin page's seat bookkeeping: _storeGet/_storeSet and the
    ``joined`` .. ``reconnect_failed`` cases of handleMessage."""
    source = _ADMIN_JS.read_text("utf-8")

    def fn(start: str) -> str:
        a = source.index(start)
        return source[a : source.index("\n    }\n", a) + len("\n    }\n")]

    a = source.index("            case 'joined':")
    b = source.index("            case 'game_state':", a)
    cases = source[a:b]
    return (
        "var ADMIN_SEAT_TOKEN_KEY = 'quizify_admin_seat_token';\n"
        + fn("    function _storeGet(key) {")
        + fn("    function _storeSet(key, value) {")
        + "function adminFrame(msg) {\n  switch (msg.type) {\n"
        + cases
        + "  }\n}\n"
    )


_HARNESS = r"""
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
// One browser: localStorage is shared by every tab, sessionStorage is the
// tab's own and survives its reloads and same-tab navigations.
var shared = mkStore();
var tabs = {};
global.localStorage = shared;
window.localStorage = shared;

// A page load in tab `name` at `search`: fresh module state, the tab's own
// sessionStorage, the browser's localStorage.
function page(name, search) {
  if (!tabs[name]) tabs[name] = mkStore();
  global.sessionStorage = tabs[name];
  window.sessionStorage = tabs[name];
  global.location = { search: search || '', pathname: '/quizify/player',
                      reload: function () {} };
  QZ.load(%(client_core)s);
  C = window.QuizifyClientCore;
  // What player-core init() does for ?admin=true.
  if (new URLSearchParams(search || '').get('admin') === 'true' && C.useHostSeat) {
    C.useHostSeat();
  }
  state = {}; sockets = []; sent = [];
}
var C;

global.WebSocket = { CONNECTING: 0, OPEN: 1, CLOSED: 3 };
global.URLSearchParams = URLSearchParams;
global.QuizifyUtils = { readAdminToken: function () { return null; } };
var views = [], sockets = [], sent = [];
var pu = {
  getSession: function () { return C.getSession(); },
  saveSession: function (t, n) { return C.saveSession(t, n); },
  clearSession: function () { return C.clearSession(); },
  saveSessionGame: function (g) { return C.saveSessionGame(g); },
  showView: function (id) { views.push(id); },
  updateConnectionIndicator: function () {},
  hideReconnectingOverlay: function () {},
  createWebSocket: function (path, handlers) {
    var ws = { readyState: 1, handlers: handlers,
      close: function () { this.readyState = 3; } };
    sockets.push(ws);
    return ws;
  }
};
var state = {};
var game = null;
var els = { nameInput: QZ.el('name-input'), joinBtn: QZ.el('join-btn') };
['session-view', 'session-title', 'session-primary-btn', 'session-other-btn']
  .forEach(function (id) { QZ.el(id); });
function _t() { return function (k) { return k; }; }
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
var _adminJoinedAs = null;

%(player_code)s

%(admin_code)s

function openSocket() {
  connect();
  var ws = sockets[sockets.length - 1];
  ws.handlers.onOpen(ws);
  return sent[sent.length - 1] || null;
}
function sharedHolds(text) {
  return Object.keys(shared._v).some(function (k) {
    return String(shared._v[k]).indexOf(text) !== -1;
  });
}
var out = {};

%(scenario)s

console.log(JSON.stringify(out));
"""


def _run(scenario: str) -> dict:
    script = _HARNESS % {
        "stub": json.dumps(str(_STUB)),
        "client_core": json.dumps(str(_CLIENT_CORE)),
        "player_code": _client_code(),
        "admin_code": _admin_frames(),
        "scenario": scenario,
    }
    run = subprocess.run(
        ["node", "-e", script], capture_output=True, text=True, check=False
    )
    assert run.returncode == 0, run.stderr
    return json.loads(run.stdout.strip().splitlines()[-1])


def _reconnect(token: str) -> dict:
    return {"type": "reconnect", "session_token": token, "name": "Host"}


# The host joins as a player on the admin page; the server rotates the token
# once (a #1209 lobby reload).
_HOST_JOINS = """
page('host', '');
_adminJoinedAs = 'Host';
adminFrame({ type: 'joined', session_token: 'host-seat-1' });
page('host', '');
_adminJoinedAs = 'Host';
adminFrame({ type: 'reconnected', session_token: 'host-seat-2' });
"""


@_NEEDS_NODE
def test_a_player_tab_opened_later_does_not_reconnect_as_the_host() -> None:
    out = _run(
        _HOST_JOINS
        + """
out.leaked = sharedHolds('host-seat') || sharedHolds('Host');
// The host opens the player page in a new tab of the same browser.
page('other', '');
state.playerName = null;
out.session = C.getSession();
out.sent = openSocket();
"""
    )
    assert out["leaked"] is False, "the host seat went into the shared localStorage"
    assert out["session"] == {"token": None, "name": None, "game": None}
    assert out["sent"] is None, "the new tab reconnected with a stored seat"


@_NEEDS_NODE
def test_the_redirect_at_start_still_resumes_the_host_seat() -> None:
    """The same-tab redirect to /quizify/player?admin=true&reconnect=1 (and
    its reloads) reconnects by the host seat; the rotated token stays in the
    tab, and the shared session is never touched."""
    out = _run(
        _HOST_JOINS
        + """
// A guest's seat in another tab of this browser, already in localStorage.
page('guest', '');
C.saveSession('guest-seat', 'Guest');
page('host', '?name=Host&admin=true&reconnect=1');
state.playerName = 'Host';
out.redirect = openSocket();
// The player page's own `reconnected` (player-core) saves the rotated token.
pu.saveSession('host-seat-3', 'Host');
pu.saveSessionGame('game-a');
page('host', '?name=Host&admin=true&reconnect=1');   // a reload mid-game
state.playerName = 'Host';
out.reload = openSocket();
out.leaked = sharedHolds('host-seat');
// The host page's reset path clears its own seat, not the guest's.
pu.clearSession();
out.hostAfterClear = C.getSession();
page('guest', '');
out.guest = C.getSession();
"""
    )
    assert out["redirect"] == _reconnect("host-seat-2")
    assert out["reload"] == _reconnect("host-seat-3")
    assert out["leaked"] is False
    assert out["hostAfterClear"] == {"token": None, "name": None, "game": None}
    assert out["guest"] == {"token": "guest-seat", "name": "Guest", "game": None}


@_NEEDS_NODE
def test_an_admin_url_in_another_tab_gets_no_host_identity() -> None:
    """``?admin=true`` is only a URL: in a tab that never held the host seat
    it finds nothing, and it never falls back to the shared player session."""
    out = _run(
        _HOST_JOINS
        + """
page('guest', '');
C.saveSession('guest-seat', 'Guest');
page('crafted', '?name=X&admin=true&reconnect=1');
out.session = C.getSession();
"""
    )
    assert out["session"] == {"token": None, "name": None, "game": None}


def test_the_player_page_switches_to_the_host_seat_only_for_admin_true() -> None:
    source = _CORE_JS.read_text("utf-8")
    a = source.index("        if (urlParams.get('admin') === 'true') {")
    branch = source[a : source.index("\n        }\n", a)]
    assert "useHostSeat()" in branch
    assert source.count("useHostSeat()") == 1
    admin = _ADMIN_JS.read_text("utf-8")
    assert "QuizifyClientCore.saveSession(" not in admin
