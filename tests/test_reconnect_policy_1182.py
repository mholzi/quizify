"""The phone keeps reconnecting and says so honestly (#1182, L9/L11).

L11: ten attempts were 1+2+4+8+16+5x30 s, about three minutes. An HA restart
takes one to five, so the phone gave up just before Home Assistant came back
and showed "Connection lost" with nothing trying any more, while the TV kept
going forever. Now there is no cap while the page is visible, a hidden page
waits for visibilitychange/online instead of burning attempts, and from the
30-second mark the phone shows "Waiting for the game…" with "Connect now" and
the time to the next attempt.

L9: the first connection of a page painted "reconnecting" before any
connection had ever existed. It is "connecting" now, with its own key.

Runs the real ``client-core.js`` + ``player-utils.js`` under node.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parent.parent
_WWW = _REPO / "custom_components" / "quizify" / "www"
_JS = _WWW / "js"
_STUB = Path(__file__).resolve().parent / "fixtures" / "dom_stub.js"

pytestmark = pytest.mark.skipif(
    shutil.which("node") is None, reason="node not installed"
)

_SCRIPT = r"""
require(STUB);
QZ.els(['join-view', 'lobby-view', 'game-view', 'connection-lost-view',
        'reconnecting-overlay', 'reconnect-countdown', 'conn-status',
        'conn-status-announce']);
var delays = [];
var timers = Object.create(null);
var next = 1;
global.setTimeout = window.setTimeout = function (fn, ms) {
    var h = next++; timers[h] = fn; delays.push(ms); return h;
};
global.clearTimeout = window.clearTimeout = function (h) { delete timers[h]; };
global.setInterval = window.setInterval = function () { return 0; };
global.clearInterval = window.clearInterval = function () {};
var now = 1000000;
Date.now = function () { return now; };
document.visibilityState = 'visible';

var sockets = [];
function FakeSocket(url) { this.url = url; this.readyState = 0; sockets.push(this); }
FakeSocket.CONNECTING = 0; FakeSocket.OPEN = 1; FakeSocket.CLOSING = 2; FakeSocket.CLOSED = 3;
FakeSocket.prototype.send = function () {};
FakeSocket.prototype.close = function () { this.readyState = 3; };
global.WebSocket = window.WebSocket = FakeSocket;
global.sessionStorage = { getItem: function () { return null; }, setItem: function () {}, removeItem: function () {} };
global.location = window.location = { protocol: 'http:', host: 'ha:8123', search: '' };
global.QuizifyUtils = window.QuizifyUtils = {
    MAX_NAME_LENGTH: 20, readAdminToken: function () { return null; },
    formatPoints: String, formatTime: String, escapeHtml: String
};
window.QuizifyI18n = { isReady: function () { return true; }, initPageTranslations: function () {}, t: function (k, p) { return p ? k + JSON.stringify(p) : k; } };
QZ.load(CLIENT_CORE);
QZ.load(UTILS);
var pu = window.QuizifyPlayerUtils;
var state = pu.state;
function open() {
    var ws = pu.createWebSocket('/api/quizify/ws', { onReconnect: open });
    return ws;
}
function drop() {
    var s = sockets[sockets.length - 1];
    s.readyState = 3;
    s.onclose();
}
function visible(id) { return !document.getElementById(id).classList.contains('hidden'); }

var out = {};
pu.updateConnectionIndicator('connecting');
out.firstPaint = document.getElementById('conn-status-announce').getAttribute('data-i18n');

state.playerName = 'Anna';
pu.showView('game-view');
document.getElementById('reconnecting-overlay').classList.add('hidden');
open();
sockets[0].readyState = 1; sockets[0].onopen();
for (var i = 0; i < 20; i++) {
    drop();
    now += 1000;
    if (i === 1) out.early = { overlay: visible('reconnecting-overlay'), lost: visible('connection-lost-view') };
    open();
}
out.scheduled = delays.length;
out.maxDelay = Math.max.apply(null, delays);
out.late = {
    overlay: visible('reconnecting-overlay'),
    lost: visible('connection-lost-view'),
    countdown: document.getElementById('reconnect-countdown').textContent
};

// A hidden page burns no attempts.
delays.length = 0;
document.visibilityState = 'hidden';
var before = state.reconnectAttempts;
drop();
out.hidden = { scheduled: delays.length, attempts: state.reconnectAttempts - before };

// Back online.
document.visibilityState = 'visible';
open();
sockets[sockets.length - 1].readyState = 1;
sockets[sockets.length - 1].onopen();
out.recovered = { since: state.disconnectedSince, attempts: state.reconnectAttempts,
                  overlay: visible('reconnecting-overlay') };
console.log(JSON.stringify(out));
"""


@pytest.fixture(scope="module")
def result() -> dict:
    script = (
        _SCRIPT.replace("STUB", json.dumps(str(_STUB)))
        .replace("CLIENT_CORE", json.dumps(str(_JS / "client-core.js")))
        .replace("UTILS", json.dumps(str(_JS / "player-utils.js")))
    )
    out = subprocess.run(
        ["node", "-e", script], capture_output=True, text=True, check=True
    )
    return json.loads(out.stdout.strip().splitlines()[-1])


def test_the_first_connection_is_connecting_not_reconnecting(result: dict) -> None:
    assert result["firstPaint"] == "states.connecting"


def test_there_is_no_attempt_cap(result: dict) -> None:
    assert result["scheduled"] == 20
    assert result["maxDelay"] == 30000


def test_the_first_seconds_keep_the_overlay(result: dict) -> None:
    assert result["early"] == {"overlay": True, "lost": False}


def test_from_thirty_seconds_the_phone_says_it_keeps_trying(result: dict) -> None:
    assert result["late"]["lost"] is True
    assert result["late"]["overlay"] is False
    assert result["late"]["countdown"].startswith("states.nextAttempt")


def test_a_hidden_page_waits_instead_of_spending_attempts(result: dict) -> None:
    assert result["hidden"] == {"scheduled": 0, "attempts": 0}


def test_a_successful_reconnect_clears_the_outage(result: dict) -> None:
    assert result["recovered"] == {"since": 0, "attempts": 0, "overlay": False}


def test_the_waiting_screen_has_connect_now_and_no_dead_end() -> None:
    html = (_WWW / "player.html").read_text("utf-8")
    view = html[html.index('id="connection-lost-view"') :]
    view = view[: view.index("</div>\n    </div>")]
    assert 'data-i18n="states.waitingTitle"' in view
    assert 'data-i18n="states.connectNow"' in view
    assert 'id="reconnect-countdown"' in view


def test_phone_and_host_listen_for_the_network_coming_back() -> None:
    assert "addEventListener('online'" in (_JS / "player-core.js").read_text("utf-8")
    assert "addEventListener('online'" in (_JS / "admin.js").read_text("utf-8")
    assert "MAX_RECONNECT = " not in (_JS / "admin.js").read_text("utf-8")
