"""A reload of the host page in the lobby keeps the lobby and the host's seat (#1209).

Found by the v1.26.0-RC8 live test and reproduced on the dev server at every
tag from v1.25.0 to RC8: with the host playing along ("Join as player" on
``/quizify/admin``) and a guest on a phone, a reload of the host page

* landed on the setup screen. The setup → lobby step is a client-side view
  switch (``start-game-btn``), and the LOBBY branch of ``handleGameState`` only
  keeps a lobby that is already on screen, so a fresh page stayed on setup;
* dropped the host's seat 5 s later (``LOBBY_DISCONNECT_GRACE_PERIOD``). The
  seat joined over the host page's own socket; the reload closed that socket,
  and the new one only ever sent ``admin_auth`` / ``admin_connect``, never a
  ``reconnect``. #1164 put the in-memory ``_adminJoinedAs`` back, which drew
  "You're playing as …" on a seat the server was about to remove.

The fix keeps both in this tab's sessionStorage (not the localStorage every tab
and phone of the browser shares): the seat token from ``joined`` /
``reconnected``, and the lobby with the settings Start sends. On the host
confirmation in LOBBY the page sends ``reconnect`` with that token and, once per
page load, puts the settings and the lobby view back.

Server half: a live aiohttp socket against the real handler. Page half: the
real ``handleMessage``, ``handleGameState``, ``showView``, ``redirectToPlayer``,
``handleGameReset`` and the #1209 helpers from ``admin.js`` under node, with a
sessionStorage that outlives the simulated reload.
"""

from __future__ import annotations

import asyncio
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from custom_components.quizify.server import WS_PATH
from custom_components.quizify.server import websocket as ws_mod
from tests.test_not_host_page_1053 import _block, _Room, _token_in

_REPO = Path(__file__).resolve().parent.parent
_ADMIN_JS = _REPO / "custom_components" / "quizify" / "www" / "js" / "admin.js"
_STUB = Path(__file__).resolve().parent / "fixtures" / "dom_stub.js"

_NEEDS_NODE = pytest.mark.skipif(
    shutil.which("node") is None, reason="node not installed"
)

GRACE = 0.2


# ---------------------------------------------------------------------------
# Server: the seat follows a reconnect over the new host socket
# ---------------------------------------------------------------------------


async def _read_to_marker(ws) -> list[dict]:  # noqa: ANN001
    return await _Room._read_to_marker(ws)


async def _lobby_with_host_seat(room: _Room):  # noqa: ANN202
    """Host page joins as player over its own socket, a guest on a phone."""
    frames, admin_ws = await room.open_admin_page(None, keep=True)
    admin_token = _token_in(frames)
    assert admin_token
    await admin_ws.send_json(
        {"type": "join", "name": "Host", "is_admin": True, "admin_token": admin_token}
    )
    joined = [f for f in await _read_to_marker(admin_ws) if f.get("type") == "joined"]
    assert joined, "the host page was not seated"
    assert room.client is not None
    guest_ws = await room.client.ws_connect(WS_PATH)
    await guest_ws.send_json({"type": "join", "name": "Guest"})
    await _read_to_marker(guest_ws)
    return admin_ws, admin_token, joined[0]["session_token"], guest_ws


@pytest.mark.asyncio
async def test_the_reloaded_page_used_to_lose_the_seat_after_the_grace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The cause, as the RC8 page did it: handshake only, no reconnect."""
    monkeypatch.setattr(ws_mod, "LOBBY_DISCONNECT_GRACE_PERIOD", GRACE)
    async with _Room(tmp_path) as room:
        admin_ws, admin_token, _seat, guest_ws = await _lobby_with_host_seat(room)
        await admin_ws.close()  # the reload
        _frames, new_ws = await room.open_admin_page(admin_token, keep=True)
        await asyncio.sleep(GRACE * 4)
        names = [p.name for p in room.game.players.values()]
        await new_ws.close()
        await guest_ws.close()
    assert names == ["Guest"]


@pytest.mark.asyncio
async def test_a_reconnect_over_the_new_host_socket_keeps_the_seat(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(ws_mod, "LOBBY_DISCONNECT_GRACE_PERIOD", GRACE)
    async with _Room(tmp_path) as room:
        admin_ws, admin_token, seat, guest_ws = await _lobby_with_host_seat(room)
        await admin_ws.close()  # the reload
        _frames, new_ws = await room.open_admin_page(admin_token, keep=True)
        await new_ws.send_json(
            {"type": "reconnect", "session_token": seat, "name": "Host"}
        )
        answer = await _read_to_marker(new_ws)
        await asyncio.sleep(GRACE * 4)
        players = {p.name: p.connected for p in room.game.players.values()}
        host = room.game.get_player("Host")
        guest_frames = await _read_to_marker(guest_ws)
        await new_ws.close()
        await guest_ws.close()

    reconnected = [f for f in answer if f.get("type") == "reconnected"]
    assert reconnected and reconnected[0]["session_token"] != seat, "token rotated"
    assert players == {"Host": True, "Guest": True}
    assert host is not None and host.is_admin, "the crown stays with the seat"
    # Nobody else was touched: the guest is not told its seat moved.
    assert "session_moved" not in [f.get("type") for f in guest_frames]


# ---------------------------------------------------------------------------
# Page: the real admin.js, across a simulated reload
# ---------------------------------------------------------------------------


_SCRIPT = r"""
require(%(stub)s);
QZ.els(['setup-screen', 'lobby-screen', 'game-view', 'not-host-screen',
        'lobby-restart-notice']);
window.QuizifyRenderShared = {
    rosterList: function (p) { return Array.isArray(p) ? p : Object.values(p || {}); }
};
var saved = [];
window.QuizifyClientCore = {
    // #1212: the host seat, in this tab; never the shared player session.
    saveHostSeat: function (t, n) { saved.push([t, n]); }
};

// One tab's sessionStorage: it survives page() (the reload), nothing else does.
var store = {};
var sessionStorage = {
    getItem: function (k) {
        return Object.prototype.hasOwnProperty.call(store, k) ? store[k] : null;
    },
    setItem: function (k, v) { store[k] = String(v); },
    removeItem: function (k) { delete store[k]; }
};
var location = { href: 'http://quizify.local/quizify/admin', search: '' };
window.location = location;

var views = { setup: QZ.el('setup-screen'), lobby: QZ.el('lobby-screen'),
              game: QZ.el('game-view'), notHost: QZ.el('not-host-screen') };
var els = {};
var QuizifyUtils = { writeAdminToken: function () {} };
var _presetsLoaded = true, _ttsEls = null, _houseEls = null, _lobbyTeams = null;
var _ttsEntitiesLoaded = true, _houseEntitiesLoaded = true;
var _adminJoinedAs, _redirecting, currentPhase, sent, applied, joinUrl, ws;
var selectedRounds, selectedDifficulty, selectedTimer, selectedLightning,
    selectedHotSeat, selectedPowerups, selectedWager, selectedLanguage,
    selectedCategories;
function send(type, payload) {
    sent.push(Object.assign({ type: type }, payload));
    return true;
}
// The four #1201 pushes, as the wire sees them.
function _pushTtsConfig() { sent.push({ type: 'configure_tts' }); }
function _pushHouseConfig() { sent.push({ type: 'configure_house' }); }
function _pushLanguage() {
    sent.push({ type: 'set_language', language: selectedLanguage });
}
function _pushDifficulty() {
    sent.push({ type: 'set_difficulty', difficulty: selectedDifficulty });
}
function renderLobbyPlayers() {}
function setLiveControls() {}
// Stands in for the real one (chips and toggles): records the preset and
// writes the variables the pushes and Start read.
function _applyCustomPreset(p) {
    applied.push(p);
    selectedRounds = p.rounds; selectedDifficulty = p.difficulty;
    selectedLanguage = p.language; selectedCategories = p.packs;
}
function initJoinUrl() { joinUrl += 1; }

%(code)s

// A fresh page load in the same tab: memory is gone, the static HTML has
// setup active, sessionStorage is still there.
function page() {
    _adminJoinedAs = null; _redirecting = false; currentPhase = 'LOBBY';
    _notHost = false; _hostConfirmedSock = null; ws = {};
    sent = []; applied = []; joinUrl = 0; saved = [];
    selectedRounds = 10; selectedDifficulty = 'medium'; selectedTimer = 30;
    selectedLightning = true; selectedHotSeat = true; selectedPowerups = true;
    selectedWager = true; selectedLanguage = 'en'; selectedCategories = [];
    %(reset_flags)s
    Object.keys(views).forEach(function (k) { views[k].classList.remove('active'); });
    views.setup.classList.add('active');
}
function types() { return sent.map(function (f) { return f.type; }); }
function active() {
    return Object.keys(views).filter(function (k) {
        return views[k].classList.contains('active');
    });
}
// The two buttons, exactly as admin.js wires them (asserted against the
// source in test_the_buttons_write_and_forget_the_lobby).
function startButton() { showView('lobby'); initJoinUrl(); _rememberLobby(); }
function backButton() { showView('setup'); _forgetLobbyReload(); }
// The host picks settings, opens the lobby and joins as a player
// (doJoinAsPlayer + the server's answer).
function hostJoinsAndOpensLobby() {
    selectedRounds = 5; selectedDifficulty = 'hard'; selectedWager = false;
    selectedLanguage = 'de'; selectedCategories = ['science-de'];
    startButton();
    _adminJoinedAs = 'Host';
    sessionStorage.setItem('quizify_admin_name', 'Host');
    routeMessage({ type: 'joined', session_token: 'seat-1' });
}
var ROSTER = [{ name: 'Host', connected: false }, { name: 'Guest', connected: true }];
function confirm(players) {
    routeMessage({ type: 'game_state', phase: 'LOBBY', admin_session_token: 'adm',
                    players: players || ROSTER });
}

var R = {};
page();
%(steps)s
console.log(JSON.stringify(R));
"""

_BLOCKS = (
    "var NOT_HOST_SENDABLE = {",
    "function isHostRefusal(msg) {",
    "function enterNotHost() {",
    "function leaveNotHost() {",
    "function _isHostConfirmation(msg) {",
    "function _onHostConfirmed(msg) {",
    "function routeMessage(msg) {",
    "function _afterLobbyFrame(msg) {",
    "function _storeGet(key) {",
    "function _storeSet(key, value) {",
    "function _rememberLobby() {",
    "function _forgetLobbyReload() {",
    "function _resumeLobbyAfterReload(msg) {",
    "function _rosterHasName(players, name) {",
    "function showView(name) {",
    "function redirectToPlayer(name) {",
    "function handleGameReset() {",
    "function handleGameState(msg) {",
    "function handleMessage(msg) {",
)


def _run(steps: str) -> dict:
    source = _ADMIN_JS.read_text("utf-8")
    flags = [
        m.group(0)
        for pat in (
            r"^\s*var LOBBY_RELOAD_KEY = .*;$",
            r"^\s*var ADMIN_SEAT_TOKEN_KEY = .*;$",
            r"^\s*var _notHost = false;$",
            r"^\s*var _hostConfirmedSock = null;$",
        )
        if (m := re.search(pat, source, re.M))
    ]
    reset = "_lobbyReloadDone = false;" if "_lobbyReloadDone" in source else ""
    script = _SCRIPT % {
        "stub": json.dumps(str(_STUB)),
        "code": "\n\n".join(
            [*flags, "var _lobbyReloadDone = false;"]
            + [b for b in (_block(source, sig) for sig in _BLOCKS) if b]
        ),
        "reset_flags": reset,
        "steps": steps,
    }
    out = subprocess.run(
        ["node", "-e", script], capture_output=True, text=True, check=False
    )
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


@_NEEDS_NODE
def test_a_reload_in_the_lobby_takes_the_seat_and_the_lobby_back() -> None:
    r = _run(
        """
        hostJoinsAndOpensLobby();
        page();                                  // the reload
        R.beforeAnswer = active();
        confirm();
        R.view = active();
        R.sent = sent;
        R.applied = applied;
        R.joinUrl = joinUrl;
        R.joinedAs = _adminJoinedAs;
        routeMessage({ type: 'reconnected', session_token: 'seat-2' });
        R.seatToken = store.quizify_admin_seat_token;
        R.saved = saved;
        """
    )
    assert r["beforeAnswer"] == ["setup"]
    assert r["view"] == ["lobby"]
    # The seat first, then the four #1201 pushes, carrying the restored
    # language and difficulty rather than the page defaults.
    assert r["sent"] == [
        {"type": "reconnect", "session_token": "seat-1", "name": "Host"},
        {"type": "configure_tts"},
        {"type": "configure_house"},
        {"type": "set_language", "language": "de"},
        {"type": "set_difficulty", "difficulty": "hard"},
    ]
    assert r["joinUrl"] == 1, "the QR and the TV link are drawn again"
    assert r["applied"] == [
        {
            "rounds": 5,
            "difficulty": "hard",
            "timer": 30,
            "lightning": True,
            "hot_seat": True,
            "powerups": True,
            "wager": False,
            "language": "de",
            "packs": ["science-de"],
        }
    ], "Start after the reload sends what the host picked, not the defaults"
    assert r["joinedAs"] == "Host"
    # The rotated token replaces the dead one in both stores: this tab's and
    # the host seat the redirect at Start reconnects by (#1212: also this
    # tab's own, never the shared player session).
    assert r["seatToken"] == "seat-2"
    assert r["saved"] == [["seat-2", "Host"]]


@_NEEDS_NODE
def test_only_the_host_confirmation_acts() -> None:
    """#1201: nothing goes out before the server has confirmed the host.
    A broadcast or the reconnect's own projected snapshot carries no
    admin_session_token and changes neither the seat nor the view."""
    r = _run(
        """
        hostJoinsAndOpensLobby();
        page();
        routeMessage({ type: 'game_state', phase: 'LOBBY', players: ROSTER });
        R.sent = sent; R.view = active(); R.applied = applied.length;
        """
    )
    assert r == {"sent": [], "view": ["setup"], "applied": 0}


@_NEEDS_NODE
@pytest.mark.parametrize(
    "setup",
    [
        "delete store.quizify_admin_seat_token;",
        "ROSTER = [{ name: 'Guest', connected: true }];",
        "sessionStorage.removeItem('quizify_admin_name');",
    ],
    ids=["no-seat-token", "seat-already-removed", "never-joined"],
)
def test_no_reconnect_without_a_seat_of_our_own(setup: str) -> None:
    r = _run(
        f"""
        hostJoinsAndOpensLobby();
        page();
        {setup}
        confirm();
        R.sent = types(); R.view = active();
        R.seatToken = store.quizify_admin_seat_token || null;
        """
    )
    assert "reconnect" not in r["sent"]
    assert r["sent"] == [
        "configure_tts",
        "configure_house",
        "set_language",
        "set_difficulty",
    ]
    assert r["view"] == ["lobby"], "the lobby itself still comes back"
    assert r["seatToken"] is None


@_NEEDS_NODE
def test_a_socket_blip_retakes_the_seat_but_does_not_reapply_settings() -> None:
    r = _run(
        """
        hostJoinsAndOpensLobby();
        page(); confirm();
        routeMessage({ type: 'reconnected', session_token: 'seat-2' });
        sent = []; applied = [];
        selectedRounds = 15;                     // a pick made after the reload
        ws = {}; confirm();                      // new socket, same page
        R.sent = sent; R.applied = applied.length; R.rounds = selectedRounds;
        R.view = active();
        """
    )
    assert r["sent"][0] == {
        "type": "reconnect",
        "session_token": "seat-2",
        "name": "Host",
    }
    assert {"type": "set_difficulty", "difficulty": "hard"} in r["sent"]
    assert r["applied"] == 0
    assert r["rounds"] == 15
    assert r["view"] == ["lobby"]


@_NEEDS_NODE
def test_a_failed_reconnect_forgets_the_token() -> None:
    r = _run(
        """
        hostJoinsAndOpensLobby();
        page(); confirm();
        routeMessage({ type: 'reconnect_failed', reason: 'unknown' });
        R.seatToken = store.quizify_admin_seat_token || null;
        """
    )
    assert r["seatToken"] is None


@_NEEDS_NODE
@pytest.mark.parametrize(
    ("leave", "expect_seat_token"),
    [
        # Back to the settings: a reload stays there (the seat is kept).
        ("backButton();", True),
        # Reset / new game from any surface.
        ("routeMessage({ type: 'game_reset' });", False),
        # The game started (from any surface) without the host playing.
        (
            "_adminJoinedAs = null; sessionStorage.removeItem('quizify_admin_name');"
            " routeMessage({ type: 'game_state', phase: 'QUESTION_ACTIVE' });",
            False,
        ),
        # The game started with the host playing along: this tab becomes
        # the player page; a later /quizify/admin here (player-end "New
        # game") must start on setup.
        ("routeMessage({ type: 'game_state', phase: 'QUESTION_ACTIVE' });", False),
    ],
    ids=["back-to-setup", "reset", "game-started", "redirect-at-start"],
)
def test_leaving_the_lobby_reloads_onto_setup(
    leave: str, expect_seat_token: bool
) -> None:
    r = _run(
        f"""
        hostJoinsAndOpensLobby();
        {leave}
        R.lobbyKey = store.quizify_admin_lobby || null;
        R.seatToken = !!store.quizify_admin_seat_token;
        page();
        _redirecting = false;
        confirm([]);
        R.view = active(); R.applied = applied.length;
        """
    )
    assert r["lobbyKey"] is None
    assert r["seatToken"] is expect_seat_token
    assert r["view"] == ["setup"]
    assert r["applied"] == 0


@_NEEDS_NODE
def test_a_new_tab_starts_on_setup() -> None:
    """The lobby belongs to the tab that opened it: an empty sessionStorage
    (a second tab, a phone) is a fresh setup as before."""
    r = _run(
        """
        confirm(ROSTER);
        R.view = active(); R.sent = types();
        """
    )
    assert r["view"] == ["setup"]
    assert "reconnect" not in r["sent"]


def test_the_buttons_write_and_forget_the_lobby() -> None:
    """The page tests drive startButton()/backButton(); hold them to the
    real click handlers."""
    source = _ADMIN_JS.read_text("utf-8")
    start = _block(source, "on(els.startGameBtn, 'click', function () {")
    back = _block(source, "on(els.lobbyBackBtn, 'click', function () {")
    assert "showView('lobby')" in start and "_rememberLobby()" in start
    assert "showView('setup')" in back and "_forgetLobbyReload()" in back
