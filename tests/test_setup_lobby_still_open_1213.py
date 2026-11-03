"""Setup says when the lobby is still open, and leads back to it (#1213).

Found in the v1.26.0-RC9 live test: "Settings" in the host lobby
(``lobby-back-btn``) switches the host page to setup, but the server keeps the
lobby. The phones still say "You're in", the TV still waits for players, and
the setup screen gave no sign of any of it. The only way back was "Open
lobby", which reads like it starts something new.

Decision (Markus): keep the lobby. The back button stays a host-side settings
change. While the server lobby has players, the setup screen shows "Lobby
still open · N players" (``setup-lobby-open``) with "Back to lobby"
(``setup-lobby-return-btn``). That button returns to the same lobby and sends
nothing, so there is no new game and nobody has to rejoin. The settings
changed in between go out with Start in the lobby.

Driven by the real ``admin.js`` under node: the DOM refs (``views``/``els``),
the click wiring, ``routeMessage`` / ``handleMessage`` / ``handleGameState`` /
``renderLobbyPlayers`` / ``handleGameReset`` and the #1209 lobby-reload helpers,
with the real ``i18n.js`` and the shipped English bundle. A tap goes through
``tap()``, which refuses a button that is hidden (its own ``hidden`` class or
an ancestor's) or disabled, as the browser does.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from tests.i18n_node import render
from tests.test_not_host_page_1053 import _block

_REPO = Path(__file__).resolve().parent.parent
_WWW = _REPO / "custom_components" / "quizify" / "www"
_ADMIN_JS = _WWW / "js" / "admin.js"
_ADMIN_HTML = _WWW / "admin.html"
_STUB = Path(__file__).resolve().parent / "fixtures" / "dom_stub.js"

_NEEDS_NODE = pytest.mark.skipif(
    shutil.which("node") is None, reason="node not installed"
)

LANGUAGES = ("de", "en", "es")

_SCRIPT = r"""
require(%(stub)s);
QZ.serveI18n(%(i18n)s);
QZ.load(%(i18njs)s);
QZ.els(['setup-screen', 'lobby-screen', 'game-view', 'not-host-screen',
        'lobby-restart-notice', 'start-game-btn', 'lobby-back-btn']);
// The notice and its button as admin.html nests them: the button sits inside
// the notice, so a hidden notice hides the button.
%(markup)s

window.QuizifyRenderShared = {
    rosterList: function (p) { return Array.isArray(p) ? p : Object.values(p || {}); }
};
window.QuizifyClientCore = { saveHostSeat: function () {} };
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
var QuizifyUtils = { writeAdminToken: function () {},
                     escapeHtml: function (s) { return s; } };
var _presetsLoaded = true, _ttsEls = null, _houseEls = null;
var _ttsEntitiesLoaded = true, _houseEntitiesLoaded = true;
var _lobbyTeams = [], _lastLobbyPlayers = [];
var _adminJoinedAs = null, _redirecting = false;
var currentPhase = 'LOBBY', playerCount = 0;
var sent = [], joinUrl = 0, ws = {};
var selectedRounds = 10, selectedDifficulty = 'medium', selectedTimer = 30,
    selectedLightning = true, selectedHotSeat = true, selectedPowerups = true,
    selectedWager = true, selectedLanguage = 'en', selectedCategories = [];
function send(type, payload) {
    sent.push(Object.assign({ type: type }, payload));
    return true;
}
function _pushTtsConfig() {}
function _pushHouseConfig() {}
function _pushLanguage() {}
function _pushDifficulty() {}
function setLiveControls() {}
function applyTeamModeSetupNotes() {}
function _applyCustomPreset(p) { selectedRounds = p.rounds; }
function initJoinUrl() { joinUrl += 1; }

%(code)s

// What a finger can press: nothing hidden, nothing disabled.
function tap(el) {
    if (!el || el.disabled || el.hidden) return false;
    for (var n = el; n; n = n.parentNode) {
        if (n.classList && n.classList.contains('hidden')) return false;
    }
    el.click();
    return true;
}
function byId(id) { return document.getElementById(id); }
function active() {
    return Object.keys(views).filter(function (k) {
        return views[k] && views[k].classList.contains('active');
    });
}
function notice() {
    var box = byId('setup-lobby-open');
    var text = byId('setup-lobby-open-text');
    return {
        shown: !!box && !box.classList.contains('hidden'),
        text: text ? text.textContent : null
    };
}
// A fresh load in the same tab: memory is gone, sessionStorage stays.
function page() {
    _adminJoinedAs = null; _redirecting = false; currentPhase = 'LOBBY';
    _notHost = false; _hostConfirmedSock = null; _lobbyReloadDone = false;
    ws = {}; sent = []; joinUrl = 0; playerCount = 0; _lastLobbyPlayers = [];
    Object.keys(views).forEach(function (k) {
        if (views[k]) views[k].classList.remove('active');
    });
    views.setup.classList.add('active');
    var box = byId('setup-lobby-open');
    if (box) box.classList.add('hidden');      // as admin.html ships it
}
var TWO = [{ name: 'Anna', connected: true }, { name: 'Ben', connected: true }];
function confirm(players) {
    routeMessage({ type: 'game_state', phase: 'LOBBY', admin_session_token: 'adm',
                    players: players });
}

var R = {};
(async function () {
    await window.QuizifyI18n.setLanguage('en');
    page();
    %(steps)s
    console.log(JSON.stringify(R));
})().catch(function (e) { console.error(e && e.stack || e); process.exit(1); });
"""

_BLOCKS = (
    "const views = {",
    "const els = {",
    "function on(id, event, fn) {",
    "function _t(key, params) {",
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
    "function isTeamMode() {",
    "function _lobbyPalette() {",
    "function _renderSetupLobbyOpen() {",
    "function renderLobbyPlayers(players) {",
    "on(els.startGameBtn, 'click', function () {",
    "on(els.lobbyBackBtn, 'click', function () {",
    "on(els.setupLobbyReturnBtn, 'click', function () {",
)

_FLAGS = (
    r"^\s*var LOBBY_RELOAD_KEY = .*;$",
    r"^\s*var ADMIN_SEAT_TOKEN_KEY = .*;$",
    r"^\s*var LOBBY_MIN_PLAYERS = .*;$",
)


def _markup() -> str:
    """The notice as admin.html ships it, or nothing when it is not there."""
    html = _ADMIN_HTML.read_text("utf-8")
    m = re.search(r'<div id="setup-lobby-open" class="([^"]*)"', html)
    if not m:
        return ""
    return (
        "var _box = QZ.el('setup-lobby-open');"
        f" _box.className = {json.dumps(m.group(1))};"
        f" {json.dumps(m.group(1))}.split(/\\s+/).forEach(function (c) {{"
        " if (c) _box.classList.add(c); });"
        " _box.appendChild(QZ.el('setup-lobby-open-text'));"
        " _box.appendChild(QZ.el('setup-lobby-return-btn'));"
    )


def _whole(source: str, sig: str) -> str:
    """A declaration, or a click wiring ``on(…, function () {…});`` whole."""
    block = _block(source, sig)
    return block + ");" if sig.startswith("on(") else block


def _run(steps: str) -> dict:
    source = _ADMIN_JS.read_text("utf-8")
    flags = [m.group(0) for p in _FLAGS if (m := re.search(p, source, re.M))]
    code = "\n\n".join(
        [
            *flags,
            "var _notHost = false;",
            "var _hostConfirmedSock = null;",
            "var _lobbyReloadDone = false;",
        ]
        + [_whole(source, sig) for sig in _BLOCKS if sig in source]
    )
    script = _SCRIPT % {
        "stub": json.dumps(str(_STUB)),
        "i18n": json.dumps(str(_WWW / "i18n")),
        "i18njs": json.dumps(str(_WWW / "js" / "i18n.js")),
        "markup": _markup(),
        "code": code,
        "steps": steps,
    }
    out = subprocess.run(
        ["node", "-e", script], capture_output=True, text=True, check=False
    )
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


@_NEEDS_NODE
def test_back_to_settings_shows_the_open_lobby_and_leads_back_to_it() -> None:
    r = _run(
        """
        confirm(TWO);
        R.firstOnSetup = notice();               // players joined before the lobby view
        tap(byId('start-game-btn'));
        tap(byId('lobby-back-btn'));             // "Settings"
        R.afterBack = { view: active(), notice: notice() };
        routeMessage({ type: 'player_joined',
                       players: TWO.concat([{ name: 'Cleo', connected: true }]) });
        R.afterJoin = notice();
        selectedRounds = 15;                     // a change made on setup
        sent = [];
        R.tapped = tap(byId('setup-lobby-return-btn'));
        R.afterReturn = { view: active(), sent: sent, joinUrl: joinUrl,
                          remembered: JSON.parse(store.quizify_admin_lobby || 'null') };
        """
    )
    assert r["firstOnSetup"] == {"shown": True, "text": "Lobby still open · 2 players"}
    assert r["afterBack"] == {
        "view": ["setup"],
        "notice": {"shown": True, "text": "Lobby still open · 2 players"},
    }
    assert r["afterJoin"] == {"shown": True, "text": "Lobby still open · 3 players"}
    assert r["tapped"] is True
    ret = r["afterReturn"]
    assert ret["view"] == ["lobby"]
    # Same lobby: nothing goes to the server (no start, no reset, no rejoin).
    assert ret["sent"] == []
    assert ret["joinUrl"] == 2, "the QR and the TV link are drawn again"
    # The setup change is what Start (and a reload in the lobby) will use.
    assert ret["remembered"]["rounds"] == 15


@_NEEDS_NODE
def test_one_player_reads_singular() -> None:
    r = _run(
        """
        confirm([{ name: 'Anna', connected: true }]);
        R.notice = notice();
        """
    )
    assert r["notice"] == {"shown": True, "text": "Lobby still open · 1 player"}


@_NEEDS_NODE
def test_an_empty_lobby_shows_nothing_and_offers_no_way_back() -> None:
    r = _run(
        """
        confirm([]);
        R.notice = notice();
        R.tapped = tap(byId('setup-lobby-return-btn'));
        R.view = active();
        """
    )
    assert r["notice"]["shown"] is False
    assert r["tapped"] is False, "a hidden button cannot be pressed"
    assert r["view"] == ["setup"]


@_NEEDS_NODE
def test_a_reset_takes_the_notice_away() -> None:
    r = _run(
        """
        confirm(TWO);
        tap(byId('start-game-btn'));
        tap(byId('lobby-back-btn'));
        routeMessage({ type: 'game_reset' });
        R.notice = notice();
        R.view = active();
        """
    )
    assert r["notice"]["shown"] is False
    assert r["view"] == ["setup"]


@_NEEDS_NODE
def test_a_reload_on_setup_with_the_lobby_open_shows_the_notice() -> None:
    """#1209 forgets the lobby *view* on the back button, so a reload after
    "Settings" lands on setup while the server lobby is still open: that is
    exactly when the host needs the line."""
    r = _run(
        """
        confirm(TWO);
        tap(byId('start-game-btn'));
        tap(byId('lobby-back-btn'));
        page();                                  // the reload
        confirm(TWO);
        R.afterReload = { view: active(), notice: notice() };
        R.tapped = tap(byId('setup-lobby-return-btn'));
        R.view = active();
        R.remembered = !!store.quizify_admin_lobby;
        """
    )
    assert r["afterReload"] == {
        "view": ["setup"],
        "notice": {"shown": True, "text": "Lobby still open · 2 players"},
    }
    assert r["tapped"] is True
    assert r["view"] == ["lobby"]
    assert r["remembered"] is True, "a further reload comes back to the lobby (#1209)"


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_every_language_says_it_with_a_plural() -> None:
    calls = [
        (lang, "setup.lobbyStillOpen", {"count": n})
        for lang in LANGUAGES
        for n in (1, 4)
    ] + [(lang, "setup.backToLobby", None) for lang in LANGUAGES]
    out = render(calls)
    assert out == [
        "Lobby noch offen · 1 Spieler",
        "Lobby noch offen · 4 Spieler",
        "Lobby still open · 1 player",
        "Lobby still open · 4 players",
        "La sala sigue abierta · 1 jugador",
        "La sala sigue abierta · 4 jugadores",
        "Zurück zur Lobby",
        "Back to lobby",
        "Volver a la sala",
    ]


def test_the_notice_ships_hidden_inside_the_setup_screen() -> None:
    html = _ADMIN_HTML.read_text("utf-8")
    setup = html.index('id="setup-screen"')
    lobby = html.index('id="lobby-screen"')
    at = html.find('id="setup-lobby-open"')
    assert setup < at < lobby, "the notice belongs to the setup screen"
    tag = re.search(r'<div id="setup-lobby-open" class="([^"]*)"', html)
    assert tag and "hidden" in tag.group(1).split()
    # Built from the existing notice and button styles, no new look.
    assert "lobby-e-notice" in tag.group(1).split()
    assert re.search(
        r'id="setup-lobby-return-btn" class="btn btn-secondary"', html
    )
