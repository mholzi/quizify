"""#981 — a failed reconnect must not walk the phone back into the game.

Clearing the player's identity was hand-spelled in five places. Four of them
dropped ``sessionToken``, ``playerName``, ``playerId`` and ``isAdmin``
together; the ``reconnect_failed`` case dropped the token alone, although its
own comment said it mirrored ``game_reset``.

The missing line is not bookkeeping. ``createWebSocket``'s onclose in
``player-utils.js`` arms the reconnect ladder on ``state.playerName``, and
``connect()``'s onOpen in ``player-core.js`` re-sends ``join`` under that name
— with ``is_admin: true`` if the tab was the host's. So the server saying "this
session is not joinable" put the guest on the join screen and, one socket close
later, quietly re-joined them as whoever they had been. The host's phone
re-claimed the crown that way.

The first test runs that sequence for real: the actual ``send`` / ``connect`` /
``forgetIdentity`` / ``reconnect_failed`` code lifted out of player-core.js, the
actual ladder in player-utils.js and the actual session storage in
client-core.js, over a fake socket and a fake clock. It fails against the
pre-#981 source — the harness is able to observe the unwanted ``join``, not
merely to permit its absence — which is the only reason it is worth having.

The rest guard the shape: one ``forgetIdentity()``, called at all five sites,
with nothing left hand-spelling the four fields next to a ``clearSession()``.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parent.parent
_JS = _REPO / "custom_components" / "quizify" / "www" / "js"
_CORE = _JS / "player-core.js"
_END = _JS / "player-end.js"
_UTILS = _JS / "player-utils.js"
_CLIENT_CORE = _JS / "client-core.js"
_STUB = Path(__file__).resolve().parent / "fixtures" / "dom_stub.js"

_NEEDS_NODE = pytest.mark.skipif(
    shutil.which("node") is None, reason="node not installed"
)


def _without_comments(source: str) -> str:
    source = re.sub(r"/\*.*?\*/", "", source, flags=re.S)
    return re.sub(r"^\s*//.*$", "", source, flags=re.M)


def _slice(path: Path, start: str, end: str) -> str:
    source = path.read_text("utf-8")
    a = source.index(start)
    b = source.index(end, a)
    return source[a:b]


def _node(script: str) -> dict:
    """Run *script* and read the result off its last line.

    The ladder logs to the console on its way up ("WS closed. Reconnecting
    in 2000ms"), and that log line is the real module's, not the harness's.
    Parsing the whole of stdout would turn the pre-#981 behaviour into a
    JSON error instead of a failed assertion about the `join` — a test that
    goes red for the wrong reason is a test that can go green for one.
    """
    out = subprocess.run(
        ["node", "-e", script], capture_output=True, text=True, check=True
    )
    return json.loads(out.stdout.strip().splitlines()[-1])


# ---------------------------------------------------------------------------
# The behaviour
# ---------------------------------------------------------------------------

#: Everything between the send helper and the document-title section: `send`,
#: `forgetIdentity` and `connect` — the three pieces of player-core this
#: sequence actually runs.
_CONNECT = (
    "    function send(type, payload) {",
    "    // ============================================\n    // Document title",
)

#: The one `case` under test, lifted whole out of `handleMessage`.
_RECONNECT_FAILED = (
    "            case 'reconnect_failed':",
    "            case 'host_presence':",
)

_LADDER_SCRIPT = """
require({stub});

// The page, as far as these three modules reach into it.
QZ.els(['join-view', 'lobby-view', 'game-view', 'connection-lost-view',
        'reconnecting-overlay', 'error-toast', 'name-input', 'join-btn']);
QZ.el('name-input').focus = function () {{}};

// ---- a clock we own -------------------------------------------------------
var timers = Object.create(null);
var nextHandle = 1;
function fakeSetTimeout(fn) {{ var h = nextHandle++; timers[h] = fn; return h; }}
function fakeClearTimeout(h) {{ delete timers[h]; }}
global.setTimeout = fakeSetTimeout;
global.clearTimeout = fakeClearTimeout;
window.setTimeout = fakeSetTimeout;
window.clearTimeout = fakeClearTimeout;
function tick() {{
    var due = timers;
    timers = Object.create(null);
    Object.keys(due).forEach(function (h) {{ due[h](); }});
}}

// ---- a socket we own ------------------------------------------------------
var sockets = [];
var sent = [];
function FakeSocket(url) {{
    this.url = url;
    this.readyState = FakeSocket.CONNECTING;
    sockets.push(this);
}}
FakeSocket.CONNECTING = 0;
FakeSocket.OPEN = 1;
FakeSocket.CLOSING = 2;
FakeSocket.CLOSED = 3;
FakeSocket.prototype.send = function (data) {{ sent.push(JSON.parse(data)); }};
FakeSocket.prototype.close = function () {{ this.readyState = FakeSocket.CLOSED; }};
global.WebSocket = FakeSocket;
window.WebSocket = FakeSocket;

function lastSocket() {{ return sockets[sockets.length - 1]; }}
function openSocket() {{
    var s = lastSocket();
    s.readyState = FakeSocket.OPEN;
    s.onopen();
}}
function dropSocket() {{
    var s = lastSocket();
    s.readyState = FakeSocket.CLOSED;
    s.onclose();
}}

// ---- the browser bits the real modules read -------------------------------
global.sessionStorage = {{
    _v: Object.create(null),
    getItem: function (k) {{ return this._v[k] === undefined ? null : this._v[k]; }},
    setItem: function (k, v) {{ this._v[k] = String(v); }},
    removeItem: function (k) {{ delete this._v[k]; }}
}};
global.location = {{ protocol: 'http:', host: 'homeassistant.local:8123', search: '' }};
window.location = global.location;
global.QuizifyUtils = window.QuizifyUtils = {{
    MAX_NAME_LENGTH: 20,
    readAdminToken: function () {{ return 'admin-token'; }},
    formatPoints: function (n) {{ return String(n); }},
    formatTime: function (n) {{ return String(n); }},
    escapeHtml: function (s) {{ return String(s); }}
}};

// ---- the real modules -----------------------------------------------------
QZ.load({client_core});
QZ.load({utils});

var pu = window.QuizifyPlayerUtils;
var state = pu.state;

// ---- the player-core scaffolding the sliced code closes over --------------
var els = {{
    nameInput: document.getElementById('name-input'),
    joinBtn: document.getElementById('join-btn')
}};
var game = null, reveal = null, lobby = null;
var joinPendings = 0;
function beginJoinPending() {{ joinPendings++; state.joinPending = true; }}
function endJoinPending() {{ state.joinPending = false; }}
function clearGameChrome() {{}}

{connect}

function handleMessage(msg) {{
    switch (msg.type || msg.event) {{
{reconnect_failed}
        default:
            break;
    }}
}}

// ---------------------------------------------------------------------------
// The sequence: a host's phone is in the game, its token goes stale, the
// server answers reconnect_failed and then closes the socket.
// ---------------------------------------------------------------------------

// Joined, and this tab is the host's.
state.playerName = 'Anna';
state.playerId = 'Anna';
state.sessionToken = 'stale-token';
state.isAdmin = true;
pu.saveSession('stale-token', 'Anna');

connect();
openSocket();          // onOpen sends `reconnect` with the stored session
var socketsBeforeTheFailure = sockets.length;
var sentBeforeTheFailure = sent.length;

handleMessage({{ type: 'reconnect_failed' }});

var afterTheMessage = {{
    stateName: state.playerName,
    stateId: state.playerId,
    stateToken: state.sessionToken,
    isAdmin: state.isAdmin,
    storedName: pu.getSession().name,
    storedToken: pu.getSession().token,
    onJoinView: !document.getElementById('join-view').classList.contains('hidden')
}};

// The server closes the socket right after the refusal. This is where the
// ladder either arms or does not.
dropSocket();
var ladderArmed = state.isReconnecting;
tick();                                 // whatever the ladder scheduled
if (sockets.length > socketsBeforeTheFailure) openSocket();
tick();                                 // and whatever THAT scheduled

var afterTheClose = sent.slice(sentBeforeTheFailure);

console.log(JSON.stringify({{
    afterTheMessage: afterTheMessage,
    ladderArmed: !!ladderArmed,
    reconnectAttempts: state.reconnectAttempts,
    newSockets: sockets.length - socketsBeforeTheFailure,
    sentAfterTheClose: afterTheClose,
    joinsAfterTheClose: afterTheClose.filter(function (m) {{
        return m.type === 'join' || m.type === 'reconnect';
    }}),
    joinPendings: joinPendings
}}));
"""


def _run_ladder() -> dict:
    return _node(
        _LADDER_SCRIPT.format(
            stub=json.dumps(str(_STUB)),
            client_core=json.dumps(str(_CLIENT_CORE)),
            utils=json.dumps(str(_UTILS)),
            connect=_slice(_CORE, *_CONNECT),
            reconnect_failed=_slice(_CORE, *_RECONNECT_FAILED),
        )
    )


@_NEEDS_NODE
def test_a_failed_reconnect_forgets_the_name() -> None:
    """The stored name is what the ladder reads, so it is the field that
    matters — but a half-forgotten identity is its own trap, so all four go."""
    after = _run_ladder()["afterTheMessage"]

    assert after["stateName"] is None
    assert after["stateId"] is None
    assert after["stateToken"] is None
    assert after["isAdmin"] is False
    assert after["storedName"] is None
    assert after["storedToken"] is None


@_NEEDS_NODE
def test_a_failed_reconnect_still_lands_on_the_join_screen() -> None:
    """#227's fix, which #981 must not cost: without the route the phone is
    left with every view hidden."""
    assert _run_ladder()["afterTheMessage"]["onJoinView"] is True


@_NEEDS_NODE
def test_the_close_after_a_failed_reconnect_does_not_re_join() -> None:
    """The defect itself. The server has just said this identity is not
    joinable; the next socket close used to send it straight back — as the
    host, admin token attached, because that is what the tab still thought it
    was."""
    result = _run_ladder()

    assert result["joinsAfterTheClose"] == [], (
        "after reconnect_failed the phone re-announced itself: "
        f"{result['joinsAfterTheClose']}"
    )
    assert result["sentAfterTheClose"] == []
    assert result["newSockets"] == 0
    assert result["ladderArmed"] is False
    assert result["reconnectAttempts"] == 0
    assert result["joinPendings"] == 0


# ---------------------------------------------------------------------------
# The shape: one spelling, five call sites
# ---------------------------------------------------------------------------


def test_the_helper_clears_all_four_fields_and_the_stored_session() -> None:
    body = _slice(_CORE, "    function forgetIdentity() {", "\n    }")

    assert "pu.clearSession();" in body
    for field in ("sessionToken", "playerName", "playerId"):
        assert f"state.{field} = null;" in body
    assert "state.isAdmin = false;" in body


def test_all_five_sites_call_it() -> None:
    """Four in player-core (game_reset, kicked, reconnect_failed,
    showJoinRefusal), one in player-end (New game)."""
    core = _without_comments(_CORE.read_text("utf-8"))
    end = _without_comments(_END.read_text("utf-8"))

    # The definition, the export, and the four call sites.
    assert core.count("forgetIdentity()") == 5
    assert "forgetIdentity: forgetIdentity" in core
    assert "window.QuizifyPlayer.forgetIdentity" in end


def test_nothing_hand_spells_the_identity_next_to_a_clearSession() -> None:
    """The mechanism that produced #981: a second copy is free to write, and
    a copy that forgets one line looks exactly like one that does not."""
    for module in (_CORE, _END):
        source = _without_comments(module.read_text("utf-8"))
        # …except in the one function whose job it is.
        source = source.replace(
            _without_comments(
                _slice(_CORE, "    function forgetIdentity() {", "\n    }")
            ),
            "",
        )
        for block in re.finditer(
            r"clearSession\(\);((?:[^\n]*\n){0,6})", source
        ):
            assert "state.playerName = null" not in block.group(1), (
                f"{module.name} spells the identity out again next to a "
                "clearSession() — that is the shape #981 removed"
            )
