"""Host and TV keep their screens awake; a guest reset asks first (#1131).

The player page always held a screen wake lock during a question. The host
page and the TV held none, so a host running the evening from a phone without
joining as a player let it auto-lock mid-question. The OS closed the admin
socket, every phone saw the host as gone, and 60 seconds later a red "Reset
game" button sat on every guest phone. One tap sent ``reset_game`` with no
confirmation, and the server, with no host connected, wiped the game.

Two fixes, both tested here by running the real code under node:

* ``admin.js`` and ``dashboard.js`` hold a wake lock from the first wager or
  question through the last reveal, drop it in the lobby and on the finale,
  and take it back when the tab becomes visible again.
* The four guest reset buttons open ``#guest-reset-modal`` (the host's
  ``#reset-game-modal`` on the phone). Only its confirm button sends
  ``reset_game``; cancel, the backdrop and Escape close it.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parent.parent
_WWW = _REPO / "custom_components" / "quizify" / "www"
_JS = _WWW / "js"
_I18N = _WWW / "i18n"
_ADMIN = _JS / "admin.js"
_DASHBOARD = _JS / "dashboard.js"
_CORE = _JS / "player-core.js"
_PLAYER_HTML = _WWW / "player.html"
_ADMIN_HTML = _WWW / "admin.html"
_STUB = Path(__file__).resolve().parent / "fixtures" / "dom_stub.js"

_NEEDS_NODE = pytest.mark.skipif(
    shutil.which("node") is None, reason="node not installed"
)

_RESET_BUTTONS = (
    "hotseat-reset-btn",
    "reveal-reset-btn",
    "paused-reset-btn",
    "lightning-recap-reset-btn",
)


def _node(script: str) -> dict:
    out = subprocess.run(
        ["node", "-e", script], capture_output=True, text=True, check=True
    )
    return json.loads(out.stdout.strip().splitlines()[-1])


# ---------------------------------------------------------------------------
# Wake lock on the host page and the TV
# ---------------------------------------------------------------------------


def _wake_lock_helper(path: Path) -> str:
    source = path.read_text("utf-8")
    start = source.index("    var WAKE_LOCK_PHASES")
    sync = source.index("    function _syncWakeLock()", start)
    end = source.index("\n    }\n", sync) + len("\n    }\n")
    return source[start:end]


_WAKE_SCRIPT = """
var log = {{ requests: 0, releases: 0 }};
var lockListeners = [];
var behaviour = 'grant';
var pendingResolve = null;

function makeLock() {{
    var lock = {{
        released: false,
        release: function () {{
            lock.released = true;
            log.releases++;
            return Promise.resolve();
        }},
        addEventListener: function (type, fn) {{
            if (type === 'release') lockListeners.push(fn);
        }}
    }};
    return lock;
}}

var fakeNavigator = {{
    wakeLock: {{
        request: function (kind) {{
            log.requests++;
            log.kind = kind;
            if (behaviour === 'deny') {{
                return Promise.reject(new Error('NotAllowedError'));
            }}
            if (behaviour === 'hold') {{
                return new Promise(function (resolve) {{
                    pendingResolve = function () {{ resolve(makeLock()); }};
                }});
            }}
            return Promise.resolve(makeLock());
        }}
    }}
}};
var fakeDocument = {{ visibilityState: 'visible' }};

function build(nav) {{
    return (function (navigator, document) {{
        var currentPhase = 'LOBBY';
        {helper}
        return {{
            go: function (phase) {{ currentPhase = phase; _syncWakeLock(); }},
            held: function () {{ return !!_wakeLock; }}
        }};
    }})(nav, fakeDocument);
}}

function flush() {{ return new Promise(function (r) {{ setTimeout(r, 0); }}); }}

(async function () {{
    var out = {{}};
    var wl = build(fakeNavigator);

    wl.go('LOBBY'); await flush();
    out.lobbyRequests = log.requests;

    wl.go('WAGER_ACTIVE'); await flush();
    out.wagerHeld = wl.held();
    out.kind = log.kind;

    // Every in-game phase keeps the one lock; none asks again.
    ['QUESTION_ACTIVE', 'ANSWER_REVEAL', 'PAUSED', 'HOT_SEAT_AUCTION',
     'HOT_SEAT', 'HOT_SEAT_REVEAL', 'LIGHTNING', 'LIGHTNING_RECAP',
     'QUESTION_ACTIVE'].forEach(wl.go);
    await flush();
    out.inGameRequests = log.requests;
    out.inGameHeld = wl.held();

    // The OS drops it when the tab hides; visible again -> taken back.
    lockListeners.forEach(function (fn) {{ fn(); }});
    out.heldAfterOsRelease = wl.held();
    fakeDocument.visibilityState = 'hidden';
    wl.go('QUESTION_ACTIVE'); await flush();
    out.requestsWhileHidden = log.requests;
    fakeDocument.visibilityState = 'visible';
    wl.go('QUESTION_ACTIVE'); await flush();
    out.reacquired = wl.held();
    out.requestsAfterVisible = log.requests;

    wl.go('FINALE'); await flush();
    out.finaleHeld = wl.held();
    out.finaleReleases = log.releases;

    // The game ends while the request is still in flight.
    behaviour = 'hold';
    var before = log.releases;
    wl.go('QUESTION_ACTIVE');
    wl.go('LOBBY');
    pendingResolve(); await flush(); await flush();
    out.lateLockReleased = log.releases - before;
    out.lateLockHeld = wl.held();

    // A refused request is silent, and the next phase may try again.
    behaviour = 'deny';
    var threw = false;
    try {{ wl.go('QUESTION_ACTIVE'); await flush(); }} catch (e) {{ threw = true; }}
    out.deniedThrew = threw;
    out.deniedHeld = wl.held();
    behaviour = 'grant';
    wl.go('ANSWER_REVEAL'); await flush();
    out.retryHeld = wl.held();

    // No Wake Lock API at all.
    var bare = build({{}});
    threw = false;
    try {{
        bare.go('QUESTION_ACTIVE');
        await flush();
        bare.go('LOBBY');
    }} catch (e) {{
        threw = true;
    }}
    out.unsupportedThrew = threw;

    console.log(JSON.stringify(out));
}})();
"""


@_NEEDS_NODE
@pytest.mark.parametrize("path", [_ADMIN, _DASHBOARD], ids=["admin", "dashboard"])
def test_the_screen_stays_awake_for_the_game_and_only_the_game(path: Path) -> None:
    out = _node(_WAKE_SCRIPT.format(helper=_wake_lock_helper(path)))

    assert out["lobbyRequests"] == 0, "the lobby/setup must not hold the screen"
    assert out["wagerHeld"] is True and out["kind"] == "screen"
    assert out["inGameRequests"] == 1, "one lock for the game, not one per phase"
    assert out["inGameHeld"] is True

    assert out["heldAfterOsRelease"] is False
    assert out["requestsWhileHidden"] == 1, "a hidden tab cannot take a lock"
    assert out["reacquired"] is True and out["requestsAfterVisible"] == 2

    assert out["finaleHeld"] is False and out["finaleReleases"] == 1

    assert out["lateLockReleased"] == 1 and out["lateLockHeld"] is False

    assert out["deniedThrew"] is False and out["deniedHeld"] is False
    assert out["retryHeld"] is True
    assert out["unsupportedThrew"] is False


@pytest.mark.parametrize("path", [_ADMIN, _DASHBOARD], ids=["admin", "dashboard"])
def test_every_message_and_every_return_to_the_tab_syncs_the_lock(path: Path) -> None:
    source = path.read_text("utf-8")

    on_message = source.split("onMessage: function (msg) {", 1)[1].split("},", 1)[0]
    synced = r"(routeMessage|handleMessage)\(msg\);\s*_syncWakeLock\(\);"
    assert re.search(synced, on_message), (
        "the socket's onMessage must sync the lock after the frame is handled "
        "— any handler may move currentPhase"
    )

    listeners = re.findall(
        r"document\.addEventListener\('visibilitychange', function \(\) \{"
        r"(.*?)\n    \}\);",
        source,
        flags=re.S,
    )
    assert any("_syncWakeLock()" in body for body in listeners)


# ---------------------------------------------------------------------------
# Guest reset goes through a confirm dialog
# ---------------------------------------------------------------------------


def test_the_guest_dialog_mirrors_the_host_one() -> None:
    html = _PLAYER_HTML.read_text("utf-8")
    start = html.index('id="guest-reset-modal"')
    tag = html[html.rindex("<div", 0, start) : html.index(">", start) + 1]
    block = html[start : html.index("<!-- QR Modal", start)]

    assert 'class="modal hidden"' in tag
    assert 'role="dialog"' in tag and 'aria-modal="true"' in tag
    assert 'aria-labelledby="guest-reset-modal-title"' in tag
    assert 'class="modal-backdrop"' in block
    assert 'data-i18n="admin.resetGameConfirm"' in block
    assert 'data-i18n="game.resetGuestWarning"' in block
    assert (
        'id="guest-reset-confirm-btn" class="btn btn-danger" '
        'data-i18n="admin.resetGame"'
    ) in block
    assert (
        'id="guest-reset-cancel-btn" class="btn btn-secondary" '
        'data-i18n="common.cancel"'
    ) in block

    host = _ADMIN_HTML.read_text("utf-8")
    assert 'data-i18n="admin.resetGameConfirm"' in host, "title key is the host's"


def test_the_warning_exists_in_every_language() -> None:
    texts = {}
    for lang in ("de", "en", "es"):
        bundle = json.loads((_I18N / f"{lang}.json").read_text("utf-8"))
        texts[lang] = bundle["game"]["resetGuestWarning"]
    assert texts["de"] == (
        "Alle Punkte und Spieler werden gelöscht, "
        "alle Handys landen wieder beim Beitreten."
    )
    assert len(set(texts.values())) == 3, "each language needs its own text"


def _affordance_and_dialog() -> str:
    source = _CORE.read_text("utf-8")
    start = source.index("    var RESET_AFFORDANCE_DELAY_MS")
    setup = source.index("    function setupResetAffordance()", start)
    end = source.index("\n    }\n", setup) + len("\n    }\n")
    return source[start:end]


_GUEST_SCRIPT = """
require({stub});

var docListeners = {{}};
document.addEventListener = function (type, fn) {{
    (docListeners[type] || (docListeners[type] = [])).push(fn);
}};
document.removeEventListener = function (type, fn) {{
    var fns = docListeners[type] || [];
    var at = fns.indexOf(fn);
    if (at !== -1) fns.splice(at, 1);
}};
document.activeElement = null;
function press(key) {{
    (docListeners.keydown || []).slice().forEach(function (fn) {{
        fn({{ key: key, shiftKey: false, preventDefault: function () {{}} }});
    }});
}}

var timers = [];
function setTimeout(fn, ms) {{ timers.push(fn); return timers.length; }}
function clearTimeout() {{}}
function tick() {{
    var t = timers.slice();
    timers = [];
    t.forEach(function (fn) {{ fn(); }});
}}

var BUTTONS = {buttons};
BUTTONS.forEach(function (id) {{
    var b = QZ.el(id);
    b.tagName = 'BUTTON';
    b.focus = function () {{ document.activeElement = b; }};
}});
var modal = QZ.el('guest-reset-modal');
modal.classList.add('modal', 'hidden');
var backdrop = document.createElement('div');
backdrop.className = 'modal-backdrop';
modal.appendChild(backdrop);
var confirmBtn = QZ.el('guest-reset-confirm-btn');
var cancelBtn = QZ.el('guest-reset-cancel-btn');
modal.appendChild(confirmBtn);
modal.appendChild(cancelBtn);
modal.contains = function (el) {{
    return el === confirmBtn || el === cancelBtn || el === backdrop;
}};
[confirmBtn, cancelBtn].forEach(function (b) {{
    b.focus = function () {{ document.activeElement = b; }};
}});

var sent = [];
function send(type, payload) {{ sent.push(type); }}
var state = {{ isAdmin: false }};

{code}

setupResetAffordance();

function isOpen() {{ return !modal.classList.contains('hidden'); }}
var out = {{}};

// Each of the four buttons: one tap opens the dialog and sends nothing.
out.perButton = BUTTONS.map(function (id) {{
    var b = document.getElementById(id);
    b.classList.remove('hidden');
    b.click();
    tick();
    var row = {{
        id: id,
        open: isOpen(),
        sent: sent.length,
        focus: document.activeElement === cancelBtn
    }};
    cancelBtn.click();
    row.closed = !isOpen();
    row.focusBack = document.activeElement === b;
    return row;
}});

var trigger = document.getElementById('reveal-reset-btn');
trigger.click(); tick();
backdrop.click();
out.backdropCloses = !isOpen();

trigger.click(); tick();
press('Escape');
out.escapeCloses = !isOpen();
out.escapeListenerGone = (docListeners.keydown || []).length === 0;

trigger.click(); tick();
document.activeElement = cancelBtn;
press('Tab');
out.tabWraps = document.activeElement === confirmBtn;
out.sentBeforeConfirm = sent.length;
confirmBtn.click();
out.sentAfterConfirm = sent.slice();
out.closedAfterConfirm = !isOpen();
out.triggerDisabled = trigger.disabled === true;

// The host comes back while the dialog is open: it goes with its button.
var paused = document.getElementById('paused-reset-btn');
paused.classList.remove('hidden');
paused.click(); tick();
disarmResetAffordance('paused-reset-btn', null);
out.closedWithItsButton = !isOpen();
confirmBtn.click();
out.sentAfterDisarm = sent.length;

console.log(JSON.stringify(out));
"""


@_NEEDS_NODE
def test_a_guest_tap_asks_before_it_wipes_the_game() -> None:
    out = _node(
        _GUEST_SCRIPT.format(
            stub=json.dumps(str(_STUB)),
            buttons=json.dumps(list(_RESET_BUTTONS)),
            code=_affordance_and_dialog(),
        )
    )

    for row in out["perButton"]:
        assert row["open"], f"{row['id']} must open the confirm dialog"
        assert row["sent"] == 0, f"{row['id']} sent reset_game without confirmation"
        assert row["focus"], "focus goes to Cancel, the safe choice"
        assert row["closed"] and row["focusBack"], "Cancel closes and returns focus"

    assert out["backdropCloses"]
    assert out["escapeCloses"] and out["escapeListenerGone"]
    assert out["tabWraps"]
    assert out["sentBeforeConfirm"] == 0
    assert out["sentAfterConfirm"] == ["reset_game"]
    assert out["closedAfterConfirm"] and out["triggerDisabled"]

    assert out["closedWithItsButton"]
    assert out["sentAfterDisarm"] == 1, "a dialog whose button is gone sends nothing"
