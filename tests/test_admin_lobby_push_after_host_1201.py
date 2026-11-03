"""The admin page pushes its lobby settings only once it is the host (#1201).

``onOpen`` sent ``configure_tts``, ``configure_house``, ``set_language`` and
``set_difficulty`` right behind ``admin_connect``, before the page knew whether
this socket is the host. A page that is not (a guest on ``/quizify/admin``, a
host whose token was reset) drew four refusals, and the HA log four WARNING
lines per load that read like an attack.

The pushes now wait for the server's answer to ``admin_connect``: the
``game_state`` carrying ``admin_session_token``, which the server hands to the
host's socket and nobody else. These tests hold:

* ``onOpen`` sends the handshake and nothing else;
* a page the server refuses sends none of the four, ever;
* the host sends all four once the confirmation arrives, from the live UI
  state, so a pick made before the answer is not lost;
* every new socket (reconnect, HA restart) is confirmed again and re-sends
  them, and a socket's second token frame does not send them twice;
* end to end against the real handler: a non-host load gets exactly one
  refusal and the server logs no "Refused" warning for it.

The page half runs the real ``connect()``, ``send()``, ``routeMessage()`` and
the four push functions from ``admin.js`` under node.
"""

from __future__ import annotations

import json
import logging
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from tests.test_not_host_page_1053 import _block, _Room, _token_in

_REPO = Path(__file__).resolve().parent.parent
_ADMIN_JS = _REPO / "custom_components" / "quizify" / "www" / "js" / "admin.js"
_STUB = Path(__file__).resolve().parent / "fixtures" / "dom_stub.js"

_NEEDS_NODE = pytest.mark.skipif(
    shutil.which("node") is None, reason="node not installed"
)

LOBBY_PUSHES = ["configure_tts", "configure_house", "set_language", "set_difficulty"]
REFUSAL = {"type": "error", "code": "INVALID_ACTION", "message": "Admin only"}
CONFIRM = {"type": "game_state", "phase": "LOBBY", "admin_session_token": "t"}

_SCRIPT = r"""
require(%(stub)s);
var pending = {}, nextId = 1;
global.setTimeout = function (fn) { pending[nextId] = fn; return nextId++; };
global.clearTimeout = function (id) { delete pending[id]; };
global.WebSocket = { CONNECTING: 0, OPEN: 1, CLOSING: 2, CLOSED: 3 };
console.warn = function () {};

var made = [];
window.QuizifyClientCore = {
    backoffDelay: function () { return 1000; },
    createSocket: function (path, opts) {
        var s = { readyState: 0, opts: opts, sent: [],
                  send: function (d) {
                      if (this.readyState !== 1) throw new Error('send on non-open');
                      this.sent.push(JSON.parse(d));
                  } };
        made.push(s);
        return s;
    }
};
var TOKEN = %(token)s;
window.QuizifyUtils = { readAdminToken: function () { return TOKEN; } };
global.QuizifyUtils = window.QuizifyUtils;

var ws = null, reconnectAttempts = 0, MAX_RECONNECT_DELAY_MS = 30000;
var _lightningLive = false, currentPhase = 'LOBBY';
// #1209: no seat joined from this page.
var _adminJoinedAs = null;
function updateConnectionStatus() {}
function _syncWakeLock() {}
function showErrorToast() {}
function _t(k) { return k; }
function showView() {}
function closeAdminJoinModal() {}
function handleMessage() {}

// The live UI state the pushes read.
var selectedLanguage = 'en', selectedDifficulty = 'medium';
var TTS = { enabled: true, tts_entity: 'tts.a' }, HOUSE = { enabled: false };
function _readTtsConfig() { return Object.assign({}, TTS); }
function _readHouseConfig() { return Object.assign({}, HOUSE); }

%(code)s

function open(s) { s.readyState = 1; s.opts.onOpen(); }
function close(s) { s.readyState = 3; s.opts.onClose(); }
function frame(s, m) { s.opts.onMessage(m); }
function types(s) { return s.sent.map(function (f) { return f.type; }); }

var R = {};
%(steps)s
console.log(JSON.stringify(R));
"""


def _run(steps: str, token: str | None = "saved") -> dict:
    source = _ADMIN_JS.read_text("utf-8")
    flags = [
        m.group(0)
        for pat in (
            r"^\s*var _notHost = false;$",
            r"^\s*var _hostConfirmedSock = null;$",
            r"^\s*var _reconnectTimer = null;$",
            r"^\s*var LOBBY_RELOAD_KEY = .*;$",
            r"^\s*var ADMIN_SEAT_TOKEN_KEY = .*;$",
            r"^\s*var _lobbyReloadDone = false;$",
        )
        if (m := re.search(pat, source, re.M))
    ]
    blocks = [
        _block(source, sig)
        for sig in (
            "var NOT_HOST_SENDABLE = {",
            "function isHostRefusal(msg) {",
            "function enterNotHost() {",
            "function leaveNotHost() {",
            "function _sendLobbyPush(type, payload) {",
            "function _isHostConfirmation(msg) {",
            "function _onHostConfirmed(msg) {",
            "function routeMessage(msg) {",
            "function _afterLobbyFrame(msg) {",
            "function _resumeLobbyAfterReload(msg) {",
            "function _storeGet(key) {",
            "function _storeSet(key, value) {",
            "function _forgetLobbyReload() {",
            "function _rosterHasName(players, name) {",
            "function send(type, payload, opts) {",
            "function _LOG_UNDELIVERED(type) {",
            "function _pushLanguage() {",
            "function _pushDifficulty() {",
            "function _pushTtsConfig(cfg) {",
            "function _pushHouseConfig(cfg) {",
            "function connect() {",
        )
    ]
    script = _SCRIPT % {
        "stub": json.dumps(str(_STUB)),
        "token": json.dumps(token),
        "code": "\n\n".join(flags + [b for b in blocks if b]),
        "steps": steps.replace("$REFUSAL", json.dumps(REFUSAL)).replace(
            "$CONFIRM", json.dumps(CONFIRM)
        ),
    }
    out = subprocess.run(
        ["node", "-e", script], capture_output=True, text=True, check=False
    )
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


@_NEEDS_NODE
@pytest.mark.parametrize("token", ["saved", None], ids=["token", "no-token"])
def test_on_open_sends_the_handshake_and_nothing_else(token: str | None) -> None:
    r = _run(
        "connect(); var A = made[0]; open(A); R.sent = types(A);",
        token=token,
    )
    expected = (["admin_auth"] if token else []) + ["admin_connect"]
    assert r["sent"] == expected


@_NEEDS_NODE
def test_a_page_the_server_refuses_sends_no_lobby_push() -> None:
    r = _run(
        """
        connect(); var A = made[0]; open(A);
        frame(A, $REFUSAL);
        // The host taps chips on a page that is not the host: still nothing.
        selectedLanguage = 'es'; _pushLanguage(); _pushDifficulty();
        _pushTtsConfig(); _pushHouseConfig();
        // A broadcast without the token does not count as a confirmation.
        frame(A, { type: 'game_state', phase: 'LOBBY' });
        R.sent = types(A);
        """,
        token=None,
    )
    assert r["sent"] == ["admin_connect"]


@_NEEDS_NODE
def test_the_host_pushes_all_four_after_the_confirmation() -> None:
    r = _run(
        """
        connect(); var A = made[0]; open(A);
        R.beforeConfirm = types(A);
        frame(A, $CONFIRM);
        R.after = A.sent;
        """
    )
    assert r["beforeConfirm"] == ["admin_auth", "admin_connect"]
    assert [f["type"] for f in r["after"]] == [
        "admin_auth",
        "admin_connect",
        *LOBBY_PUSHES,
    ]
    by_type = {f["type"]: f for f in r["after"]}
    assert by_type["set_language"]["language"] == "en"
    assert by_type["set_difficulty"]["difficulty"] == "medium"
    assert by_type["configure_tts"]["tts_entity"] == "tts.a"


@_NEEDS_NODE
def test_a_pick_made_before_the_confirmation_is_not_lost() -> None:
    r = _run(
        """
        connect(); var A = made[0]; open(A);
        // Chips and toggles changed in the moment before the server answered.
        selectedLanguage = 'es'; _pushLanguage();
        selectedDifficulty = 'hard'; _pushDifficulty();
        TTS.enabled = false; _pushTtsConfig();
        R.held = types(A);
        frame(A, $CONFIRM);
        R.after = A.sent.slice(2);
        """
    )
    assert r["held"] == ["admin_auth", "admin_connect"], "held back, not refused"
    by_type = {f["type"]: f for f in r["after"]}
    assert sorted(by_type) == sorted(LOBBY_PUSHES)
    assert by_type["set_language"]["language"] == "es"
    assert by_type["set_difficulty"]["difficulty"] == "hard"
    assert by_type["configure_tts"]["enabled"] is False


@_NEEDS_NODE
def test_a_confirmed_host_pushes_a_change_right_away_once() -> None:
    r = _run(
        """
        connect(); var A = made[0]; open(A); frame(A, $CONFIRM);
        var n = A.sent.length;
        selectedLanguage = 'de'; _pushLanguage();
        R.tap = A.sent.slice(n);
        // A second token frame on the same socket does not replay the four.
        n = A.sent.length; frame(A, $CONFIRM); R.again = A.sent.length - n;
        """
    )
    assert r["tap"] == [{"type": "set_language", "language": "de"}]
    assert r["again"] == 0


@_NEEDS_NODE
def test_a_reconnect_is_confirmed_again_and_re_sends_the_four() -> None:
    r = _run(
        """
        connect(); var A = made[0]; open(A); frame(A, $CONFIRM);
        // HA restarts: the socket drops, the backoff opens a new one.
        close(A);
        selectedDifficulty = 'easy'; _pushDifficulty();   // no socket: quiet no-op
        Object.keys(pending).forEach(function (id) {
            var f = pending[id]; delete pending[id]; f();
        });
        var B = made[1]; open(B);
        R.beforeConfirm = types(B);
        frame(B, $CONFIRM);
        R.after = B.sent.slice(2);
        """
    )
    assert r["beforeConfirm"] == ["admin_auth", "admin_connect"], (
        "the new socket is not the host until the server says so again"
    )
    assert [f["type"] for f in r["after"]] == LOBBY_PUSHES
    assert {f["type"]: f for f in r["after"]}["set_difficulty"]["difficulty"] == "easy"


@_NEEDS_NODE
def test_a_page_let_back_in_after_a_refusal_pushes_the_four() -> None:
    """quizify.reset_admin_session and a reconnect: the token frame that ends
    the not-host state is also the confirmation."""
    r = _run(
        """
        connect(); var A = made[0]; open(A);
        frame(A, $REFUSAL); frame(A, $CONFIRM);
        R.sent = types(A);
        """,
        token=None,
    )
    assert r["sent"] == ["admin_connect", *LOBBY_PUSHES]


# ---------------------------------------------------------------------------
# End to end: the server is no longer asked, so it logs nothing
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_non_host_load_draws_one_refusal_and_no_warning(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    async with _Room(tmp_path) as room:
        host_frames, host_ws = await room.open_admin_page(None, keep=True)
        assert _token_in(host_frames)
        caplog.clear()
        with caplog.at_level(logging.DEBUG):
            frames, _ = await room.open_admin_page(None)
        await host_ws.close()

    errors = [f for f in frames if f.get("type") == "error"]
    assert len(errors) == 1, f"one refusal (admin_connect), got {errors}"
    assert REFUSAL.items() <= errors[0].items()
    refused = [
        r.getMessage()
        for r in caplog.records
        if r.levelno >= logging.WARNING and "Refused" in r.getMessage()
    ]
    assert refused == []


@pytest.mark.asyncio
async def test_a_host_reconnect_still_applies_the_settings(tmp_path: Path) -> None:
    async with _Room(tmp_path) as room:
        first, _ = await room.open_admin_page(None)
        token = _token_in(first)
        room.game.language, room.game.difficulty = "de", "medium"
        frames, _ = await room.open_admin_page(token)
        assert _token_in(frames) == token
        assert (room.game.language, room.game.difficulty) == ("es", "hard")
        assert not [f for f in frames if f.get("type") == "error"]
