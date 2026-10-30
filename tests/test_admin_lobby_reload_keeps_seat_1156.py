"""A host who reloads the lobby after "Join as Player" keeps the seat (#1156).

``_adminJoinedAs`` lives in memory. After a reload in LOBBY the first
snapshot hit ``else if (!_adminJoinedAs) sessionStorage.removeItem(...)``, so
the stored name was wiped although the roster still listed the host. On Start
nothing redirected and the host sat in the roster as a disconnected player.

These run the real ``handleGameState`` from ``admin.js`` under node with a
real (refusing) ``sessionStorage`` double: a name that is not stored reads
back ``null``, and removing it really removes it.
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
QZ.els(['lobby-view', 'game-view']);
window.QuizifyRenderShared = {
    rosterList: function (p) {
        return Array.isArray(p) ? p : Object.values(p || {});
    }
};

var store = {};
var sessionStorage = {
    getItem: function (k) {
        return Object.prototype.hasOwnProperty.call(store, k) ? store[k] : null;
    },
    setItem: function (k, v) { store[k] = String(v); },
    removeItem: function (k) { delete store[k]; }
};

var _adminJoinedAs = null, _redirecting = false, currentPhase = null;
var redirects = [], drawn = [];
var _presetsLoaded = true, _ttsEls = null, _houseEls = null, _lobbyTeams = null;
var views = { lobby: QZ.el('lobby-view'), game: QZ.el('game-view') };
var QuizifyUtils = { writeAdminToken: function () {} };
function redirectToPlayer(n) { redirects.push(n); _redirecting = true; }
function renderLobbyPlayers(p) { drawn.push(_adminJoinedAs); }
function showView() {}

%(code)s

function fresh(stored, phase, players) {
    store = {}; if (stored) store.quizify_admin_name = stored;
    _adminJoinedAs = null; _redirecting = false; redirects = []; drawn = [];
    handleGameState({ phase: phase, players: players });
    return { joinedAs: _adminJoinedAs,
             stored: sessionStorage.getItem('quizify_admin_name'),
             seenByRoster: drawn[0] === undefined ? null : drawn[0] };
}
var R = {};
// The reload: host is on the roster (objects, with the crown), name stored.
R.reload = fresh('Host', 'LOBBY', [{ name: 'Host', is_admin: true }, { name: 'Anna' }]);
// Roster as a plain list of names.
R.reloadStrings = fresh('Host', 'LOBBY', ['Host', 'Anna']);
// And then Start: the next phase redirects, because the seat was restored.
handleGameState({ phase: 'QUESTION_ACTIVE', players: [{ name: 'Host' }] });
R.redirects = redirects.slice();
// A stale name the roster no longer lists must still be dropped (#846).
R.stale = fresh('Ghost', 'LOBBY', [{ name: 'Anna' }]);
// Nothing stored, nothing to restore.
R.none = fresh(null, 'LOBBY', [{ name: 'Host' }]);
console.log(JSON.stringify(R));
"""


def _run() -> dict:
    source = _ADMIN_JS.read_text("utf-8")
    code = "\n".join(
        [
            _block(source, "function _rosterHasName(players, name) {"),
            _block(source, "function handleGameState(msg) {"),
        ]
    )
    script = _SCRIPT % {"stub": json.dumps(str(_STUB)), "code": code}
    out = subprocess.run(
        ["node", "-e", script], capture_output=True, text=True, check=True
    )
    return json.loads(out.stdout)


@_NEEDS_NODE
def test_a_lobby_reload_restores_the_hosts_seat() -> None:
    r = _run()["reload"]
    assert r["joinedAs"] == "Host"
    assert r["stored"] == "Host"
    assert r["seenByRoster"] == "Host", "restored before the roster is drawn"


@_NEEDS_NODE
def test_the_restore_works_for_a_plain_name_roster() -> None:
    assert _run()["reloadStrings"]["joinedAs"] == "Host"


@_NEEDS_NODE
def test_start_after_the_reload_redirects_the_host_to_the_player_page() -> None:
    assert _run()["redirects"] == ["Host"]


@_NEEDS_NODE
def test_a_name_the_roster_does_not_list_is_still_dropped() -> None:
    r = _run()["stale"]
    assert r["joinedAs"] is None
    assert r["stored"] is None


@_NEEDS_NODE
def test_nothing_is_invented_without_a_stored_name() -> None:
    r = _run()["none"]
    assert r["joinedAs"] is None
    assert r["stored"] is None
