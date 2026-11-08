"""A reload whose first reconnect is lost retries with the stored token (#1234).

Found live on v1.27.0-RC2. #1222 made the server accept the not-yet-delivered
rotated token for 60 s, but the phone never asked a second time: the reconnect
ladder in ``player-utils.js`` only ran ``if (state.playerName)``, and on a
fresh page the name arrives with the ``reconnected`` ack. A socket lost after
``reconnect`` went out and before that ack left the guest on the join form,
with the pill on "Connected" and the seat still held on the server.

Now ``connect()`` marks the attempt (``state.restoringSession``) and the
ladder runs for it too. The answer, or anything that forgets the identity,
takes the mark down again, so a refused or emptied session is never retried.

Runs the real ``client-core.js`` + ``player-utils.js`` and the real
``connect()`` / ``forgetIdentity()`` from ``player-core.js`` under node.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parent.parent
_JS = _REPO / "custom_components" / "quizify" / "www" / "js"
_CORE = _JS / "player-core.js"
_STUB = Path(__file__).resolve().parent / "fixtures" / "dom_stub.js"

_NEEDS_NODE = pytest.mark.skipif(
    shutil.which("node") is None, reason="node not installed"
)


def _block(source: str, start: str, end: str) -> str:
    a = source.index(start)
    return source[a : source.index(end, a)]


def _fn(source: str, start: str) -> str:
    a = source.index(start)
    return source[a : source.index("\n    }\n", a) + len("\n    }\n")]


def _core_code() -> str:
    source = _CORE.read_text("utf-8")
    identity = _block(
        source,
        "    function forgetIdentity()",
        "    // ============================================\n    // Connect",
    )
    return identity + "\n" + _fn(source, "    function connect() {")


_HARNESS = r"""
require(%(stub)s);
QZ.els(['join-view', 'lobby-view', 'game-view', 'connection-lost-view',
        'reconnecting-overlay', 'reconnect-countdown', 'conn-status',
        'conn-status-announce', 'name-input', 'join-btn']);
function mkStore() {
  const v = {};
  return {
    setItem: function (k, x) { v[k] = String(x); },
    getItem: function (k) { return v[k] === undefined ? null : v[k]; },
    removeItem: function (k) { delete v[k]; }
  };
}
global.localStorage = mkStore();
global.sessionStorage = mkStore();
var timers = [];
global.setTimeout = window.setTimeout = function (fn, ms) {
  timers.push(fn); return timers.length;
};
global.clearTimeout = window.clearTimeout = function () {};
global.setInterval = window.setInterval = function () { return 0; };
global.clearInterval = window.clearInterval = function () {};
document.visibilityState = 'visible';

var sockets = [];
var sent = [];
function FakeSocket(url) { this.readyState = 0; sockets.push(this); }
FakeSocket.CONNECTING = 0; FakeSocket.OPEN = 1;
FakeSocket.CLOSING = 2; FakeSocket.CLOSED = 3;
FakeSocket.prototype.send = function (d) { sent.push(JSON.parse(d)); };
FakeSocket.prototype.close = function () { this.readyState = 3; };
global.WebSocket = window.WebSocket = FakeSocket;
global.location = window.location = { protocol: 'http:', host: 'ha:8123', search: '' };
global.URLSearchParams = URLSearchParams;
global.QuizifyUtils = window.QuizifyUtils = {
  MAX_NAME_LENGTH: 20, readAdminToken: function () { return null; },
  formatPoints: String, formatTime: String, escapeHtml: String
};
window.QuizifyI18n = { isReady: function () { return true; },
  initPageTranslations: function () {}, t: function (k) { return k; } };
QZ.load(%(client_core)s);
QZ.load(%(utils)s);
var C = window.QuizifyClientCore;
var pu = window.QuizifyPlayerUtils;
var state = pu.state;
var els = { nameInput: document.getElementById('name-input'),
            joinBtn: document.getElementById('join-btn') };
var _wsOpenTimeout = null;
function send(type, payload) {
  if (!state.ws || state.ws.readyState !== 1) return false;
  state.ws.send(JSON.stringify(Object.assign({ type: type }, payload || {})));
  return true;
}
function handleMessage() {}
function beginJoinPending() {}

%(code)s

function openLast() {
  var s = sockets[sockets.length - 1];
  s.readyState = 1; s.onopen();
  return s;
}
function dropLast() {
  var s = sockets[sockets.length - 1];
  s.readyState = 3; s.onclose();
}
function fireTimers() {
  var due = timers; timers = [];
  due.forEach(function (fn) { fn(); });
}
function indicator() {
  return document.getElementById('conn-status-announce').getAttribute('data-i18n');
}
var out = {};

%(scenario)s

console.log(JSON.stringify(out));
"""


def _run(scenario: str) -> dict:
    script = _HARNESS % {
        "stub": json.dumps(str(_STUB)),
        "client_core": json.dumps(str(_JS / "client-core.js")),
        "utils": json.dumps(str(_JS / "player-utils.js")),
        "code": _core_code(),
        "scenario": scenario,
    }
    run = subprocess.run(
        ["node", "-e", script], capture_output=True, text=True, check=False
    )
    assert run.returncode == 0, run.stderr
    return json.loads(run.stdout.strip().splitlines()[-1])


@_NEEDS_NODE
def test_a_lost_first_reconnect_retries_with_the_stored_token() -> None:
    out = _run(
        """
C.saveSession('tok-1', 'Anna');
C.saveSessionGame('game-a');
connect();
openLast();
out.first = sent.slice();
dropLast();
out.retryArmed = timers.length > 0;
out.indicator = indicator();
var overlayEl = document.getElementById('reconnecting-overlay');
out.overlay = !overlayEl.classList.contains('hidden');
out.nameStillUnset = state.playerName === null;
fireTimers();
openLast();
out.second = sent[sent.length - 1];
out.socketCount = sockets.length;
"""
    )
    frame = {
        "type": "reconnect",
        "session_token": "tok-1",
        "name": "Anna",
        "last_game_id": "game-a",
    }
    assert out["first"] == [frame]
    assert out["retryArmed"] is True
    assert out["indicator"] != "states.connected"
    assert out["overlay"] is True
    assert out["nameStillUnset"] is True
    # Same token, same name: the server's 60 s grace for the rotated token
    # (#1222) is what lets this second attempt land on the same seat.
    assert out["second"] == frame
    assert out["socketCount"] == 2


@_NEEDS_NODE
def test_a_page_with_no_stored_session_still_does_not_retry() -> None:
    out = _run(
        """
connect();
openLast();
out.sent = sent.length;
dropLast();
out.retryArmed = timers.length > 0;
"""
    )
    assert out == {"sent": 0, "retryArmed": False}


@_NEEDS_NODE
def test_a_refused_session_is_not_retried() -> None:
    """reconnect_failed / kick / reset all go through forgetIdentity (#981)."""
    out = _run(
        """
C.saveSession('tok-1', 'Anna');
connect();
openLast();
forgetIdentity();
dropLast();
out.retryArmed = timers.length > 0;
out.flag = state.restoringSession;
"""
    )
    assert out == {"retryArmed": False, "flag": False}


@_NEEDS_NODE
def test_a_retry_into_an_emptied_session_stops_the_ladder() -> None:
    """Storage cleared between attempts: nothing to send, nothing to retry."""
    out = _run(
        """
C.saveSession('tok-1', 'Anna');
connect();
openLast();
dropLast();
C.clearSession();
fireTimers();
openLast();
out.sentOnSecond = sent.length;
dropLast();
out.retryArmed = timers.length > 0;
"""
    )
    assert out == {"sentOnSecond": 1, "retryArmed": False}


def test_every_answer_to_the_reconnect_takes_the_mark_down() -> None:
    source = _CORE.read_text("utf-8")
    end = "                break;"
    seated = _block(source, "            case 'reconnected':", end)
    assert "state.restoringSession = false;" in seated
    stale = _block(source, "            case 'reconnect_stale':", end)
    assert "state.restoringSession = false;" in stale
    failed = _block(source, "            case 'reconnect_failed':", end)
    assert "forgetIdentity();" in failed
    for name in ("function forgetIdentity()", "function dropIdentity()"):
        assert "state.restoringSession = false;" in _fn(source, "    " + name)


def test_online_and_visibility_resume_a_seat_being_restored() -> None:
    source = _CORE.read_text("utf-8")
    online = _block(source, "window.addEventListener('online'", "});")
    assert "pu.hasSeatToResume()" in online
    visible = _block(source, "document.addEventListener('visibilitychange'", "});")
    assert "pu.hasSeatToResume()" in visible
