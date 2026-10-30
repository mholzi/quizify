"""The host page keeps exactly one admin socket (#1151).

``connect()`` opened a socket unconditionally and ``onClose`` armed
``setTimeout(connect, delay)`` without keeping the id. A host who unlocked the
tablet during a backoff got the visibilitychange socket and then the timer's
socket: two admin sockets, every frame rendered twice. A late close from the
first one then nulled ``ws`` although the second was live.

These run the real ``connect()`` from ``admin.js`` under node with a socket
double that can be OPEN, CONNECTING or CLOSED and timers that honour
``clearTimeout``.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parent.parent
_ADMIN_JS = _REPO / "custom_components" / "quizify" / "www" / "js" / "admin.js"
_STUB = Path(__file__).resolve().parent / "fixtures" / "dom_stub.js"

_NEEDS_NODE = pytest.mark.skipif(
    shutil.which("node") is None, reason="node not installed"
)


def _block(source: str, signature: str) -> str:
    if signature not in source:
        return ""
    start = source.index(signature)
    depth = 0
    seen = False
    for i in range(start, len(source)):
        if source[i] == "{":
            depth += 1
            seen = True
        elif source[i] == "}":
            depth -= 1
            if seen and depth == 0:
                return source[start : i + 1]
    raise AssertionError(f"unbalanced braces after {signature!r}")


_SCRIPT = r"""
require(%(stub)s);

// Timers that honour clearTimeout, run on demand.
var pending = {}, nextId = 1;
global.setTimeout = function (fn) { pending[nextId] = fn; return nextId++; };
global.clearTimeout = function (id) { delete pending[id]; };
function runTimers() {
    var ids = Object.keys(pending);
    ids.forEach(function (id) { var f = pending[id]; delete pending[id]; f(); });
}

global.WebSocket = { CONNECTING: 0, OPEN: 1, CLOSING: 2, CLOSED: 3 };

// A socket double with a real state: it refuses frames unless OPEN.
var made = [];
window.QuizifyClientCore = {
    backoffDelay: function () { return 1000; },
    createSocket: function (path, opts) {
        var s = { readyState: 0, opts: opts, sent: [],
                  send: function (d) {
                      if (this.readyState !== 1) throw new Error('send on non-open');
                      this.sent.push(d);
                  } };
        made.push(s);
        return s;
    }
};
window.QuizifyUtils = { readAdminToken: function () { return null; } };
global.QuizifyUtils = window.QuizifyUtils;

var ws = null, reconnectAttempts = 0, MAX_RECONNECT = 50;
var MAX_RECONNECT_DELAY_MS = 30000;
var routed = 0;
function updateConnectionStatus() {}
function routeMessage() { routed++; }
function _syncWakeLock() {}
function _pushTtsConfig() {} function _pushHouseConfig() {}
function _pushLanguage() {} function _pushDifficulty() {}
function send() { return true; }
var _lightningLive = false, currentPhase = 'LOBBY';

%(code)s

function open(s) { s.readyState = 1; s.opts.onOpen(s); }
function close(s) { s.readyState = 3; s.opts.onClose(); }
var R = {};

// HA restarts: socket A opens then drops, a backoff timer is armed.
connect(); var A = made[0]; open(A); close(A);
R.afterDrop = { ws: ws === null, timers: Object.keys(pending).length };

// The host unlocks the tablet: the visibilitychange handler calls connect().
connect();
R.afterForeground = { sockets: made.length, timers: Object.keys(pending).length };
// The old timer must not open a second socket.
runTimers();
R.afterTimer = { sockets: made.length };

// A third caller while B is still connecting (the retry dot).
connect();
R.afterRetry = { sockets: made.length };

// B opens; a late close from a replaced socket must not null the live one.
var B = made[made.length - 1]; open(B);
connect();
R.afterOpenConnect = { sockets: made.length };
var stale = { readyState: 3, opts: null };
// A: CLOSING socket replaced by a fresh one.
B.readyState = 2;                 // CLOSING
connect();                        // visibility during CLOSING opens C
var C = made[made.length - 1]; open(C);
var before = made.length;
B.readyState = 3; B.opts.onClose();   // late close of the replaced socket
R.lateClose = { wsIsC: ws === C, sockets: made.length, before: before,
                timers: Object.keys(pending).length };
// A late frame from the replaced socket is ignored.
var r0 = routed; B.opts.onMessage({}); R.lateFrame = routed - r0;
var r1 = routed; C.opts.onMessage({}); R.liveFrame = routed - r1;
console.log(JSON.stringify(R));
"""


def _run() -> dict:
    source = _ADMIN_JS.read_text("utf-8")
    code = "var _reconnectTimer = null;\n" + _block(source, "function connect() {")
    script = _SCRIPT % {"stub": json.dumps(str(_STUB)), "code": code}
    out = subprocess.run(
        ["node", "-e", script], capture_output=True, text=True, check=True
    )
    return json.loads(out.stdout)


@_NEEDS_NODE
def test_a_foreground_during_a_backoff_opens_one_socket() -> None:
    r = _run()
    assert r["afterDrop"] == {"ws": True, "timers": 1}
    assert r["afterForeground"]["sockets"] == 2
    assert r["afterForeground"]["timers"] == 0, "the backoff timer must be cancelled"
    assert r["afterTimer"]["sockets"] == 2


@_NEEDS_NODE
def test_connect_does_not_stack_on_a_connecting_or_open_socket() -> None:
    r = _run()
    assert r["afterRetry"]["sockets"] == 2
    assert r["afterOpenConnect"]["sockets"] == 2


@_NEEDS_NODE
def test_a_replaced_sockets_late_close_and_frames_are_ignored() -> None:
    r = _run()["lateClose"]
    assert r["wsIsC"] is True
    assert r["sockets"] == r["before"]
    assert r["timers"] == 0
    out = _run()
    assert out["lateFrame"] == 0
    assert out["liveFrame"] == 1
