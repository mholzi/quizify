"""Back closes a sheet, asks mid-game, and the seat survives the tab (#1183).

UX review 7, draft ③: a guest who came by QR camera has a tab without
history. Android's back button closed it, the session lived in
sessionStorage and died with it, and the server — on purpose, security rule
#7 — does not hand a disconnected name back after the lobby. The guest scanned
again, read "that name is taken", meant themselves, and started over at 0.

Two halves, both run for real under node here:

* ``player-core.js`` keeps one history entry in front of the page while the
  phone is in a game. Back closes the topmost sheet first; mid-game it opens
  "Leave the game?"; in the lobby it leaves as before.
* ``client-core.js`` keeps the session in localStorage (pinned in
  ``test_shared_client_render_787``), so a new scan reconnects by token.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

_WWW = Path(__file__).resolve().parent.parent / "custom_components" / "quizify" / "www"
_CORE = _WWW / "js" / "player-core.js"
_STUB = Path(__file__).resolve().parent / "fixtures" / "dom_stub.js"

_NEEDS_NODE = pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")


def _block(source: str, start: str, end_fn: str) -> str:
    a = source.index(start)
    b = source.index(end_fn, a)
    return source[a : source.index("\n    }\n", b) + len("\n    }\n")]


_SCRIPT = """
require(%(stub)s);
var docListeners = {};
document.addEventListener = function (type, fn) {
    (docListeners[type] || (docListeners[type] = [])).push(fn);
};
document.removeEventListener = function (type, fn) {
    var fns = docListeners[type] || [];
    var at = fns.indexOf(fn);
    if (at !== -1) fns.splice(at, 1);
};
document.dispatchEvent = function (e) {
    (docListeners[e.type] || []).slice().forEach(function (fn) { fn(e); });
};
global.KeyboardEvent = function (type, init) {
    this.type = type; this.key = init.key; this.preventDefault = function () {};
};
document.activeElement = null;
function setTimeout(fn) { fn(); }
var pops = [];
var hist = { pushes: 0, backs: 0 };
window.history = {
    pushState: function () { hist.pushes++; },
    back: function () { hist.backs++; }
};
window.addEventListener = function (type, fn) { if (type === 'popstate') pops.push(fn); };
function pressBack() { pops.forEach(function (fn) { fn({}); }); }

['paused-reset-btn', 'reveal-reset-btn', 'lightning-recap-reset-btn', 'hotseat-reset-btn',
 'guest-reset-modal-title', 'guest-reset-modal-text'].forEach(function (id) { QZ.el(id); });
var modal = QZ.el('guest-reset-modal');
modal.classList.add('modal', 'hidden');
var confirmBtn = QZ.el('guest-reset-confirm-btn');
var cancelBtn = QZ.el('guest-reset-cancel-btn');
modal.contains = function (el) { return el === confirmBtn || el === cancelBtn; };
var zoom = QZ.el('image-zoom-overlay');
zoom.classList.add('hidden');
document.addEventListener('keydown', function (e) {
    if (e.key === 'Escape') zoom.classList.add('hidden');
});

var sent = [];
function send(type) { sent.push(type); return true; }
var state = { isAdmin: false, playerName: 'Oma Ilse', currentPhase: 'QUESTION_ACTIVE' };

%(code)s

setupResetAffordance();
setupBackGesture();
var out = {};
function open() { return !modal.classList.contains('hidden'); }

armBackGuard();
armBackGuard();
out.oneGuard = hist.pushes === 1;

// A sheet is open: back closes it and re-arms, nothing else.
zoom.classList.remove('hidden');
pressBack();
out.sheetClosed = zoom.classList.contains('hidden');
out.noLeaveSheet = !open();
out.rearmedAfterSheet = hist.pushes === 2;

// Mid-game: back asks, "Keep playing" stays and re-arms.
pressBack();
out.asks = open();
out.asksWithLeaveText = document.getElementById('guest-reset-modal-title')
    .getAttribute('data-i18n') === 'game.leaveConfirm';
out.keepIsPrimary = cancelBtn.className === 'btn btn-primary';
cancelBtn.click();
out.keptPlaying = !open() && hist.backs === 0 && hist.pushes === 3;

// "Leave" goes back for real, sends nothing, and never re-arms.
pressBack();
confirmBtn.click();
out.left = hist.backs === 1 && sent.length === 0;
armBackGuard();
out.noRearmAfterLeave = hist.pushes === 3;

// The other kinds get their own buttons back.
confirmThen(null, 'end', function () { sent.push('end_game'); });
out.endRestyled = confirmBtn.className === 'btn btn-danger'
    && cancelBtn.className === 'btn btn-secondary';
confirmBtn.click();
out.endSent = sent.indexOf('end_game') !== -1;

console.log(JSON.stringify(out));
"""


def _code() -> str:
    source = _CORE.read_text("utf-8")
    start = source.index("    var CONFIRM_KINDS = {")
    setup = source.index("    function setupResetAffordance()", start)
    confirm = source[start : source.index("\n    }\n", setup) + len("\n    }\n")]
    back = _block(source, "    var BACK_SHEETS", "    function setupBackGesture()")
    return confirm + "\n" + back


@_NEEDS_NODE
def test_back_closes_sheets_then_asks_mid_game() -> None:
    script = _SCRIPT % {"stub": json.dumps(str(_STUB)), "code": _code()}
    run = subprocess.run(["node", "-e", script], capture_output=True, text=True, check=True)
    out = json.loads(run.stdout.strip().splitlines()[-1])
    assert out == {
        "oneGuard": True,
        "sheetClosed": True,
        "noLeaveSheet": True,
        "rearmedAfterSheet": True,
        "asks": True,
        "asksWithLeaveText": True,
        "keepIsPrimary": True,
        "keptPlaying": True,
        "left": True,
        "noRearmAfterLeave": True,
        "endRestyled": True,
        "endSent": True,
    }


def test_the_guard_is_armed_on_join_and_reconnect() -> None:
    source = _CORE.read_text("utf-8")
    body = source[source.index("            case 'reconnected':") :][:900]
    assert "armBackGuard();" in body
    assert "setupBackGesture();" in source


def test_the_leave_texts_exist_in_every_language() -> None:
    for lang in ("de", "en", "es"):
        game = json.loads((_WWW / "i18n" / f"{lang}.json").read_text("utf-8"))["game"]
        for key in ("leaveConfirm", "leaveWarning", "leave", "keepPlaying"):
            assert game.get(key), f"{lang}: game.{key}"
    de = json.loads((_WWW / "i18n" / "de.json").read_text("utf-8"))["game"]
    # The approved wording (#1183, design gate), verbatim.
    assert de["leaveConfirm"] == "Spiel verlassen?"
    assert de["leaveWarning"] == (
        "Deine Punkte bleiben. Scanne den Code wieder, dann bist du zurück."
    )


def test_the_session_no_longer_dies_with_the_tab() -> None:
    core = (_WWW / "js" / "client-core.js").read_text("utf-8")
    save = core[core.index("function saveSession(") :]
    save = save[: save.index("\n    }\n")]
    assert "_durable() || _tab()" in save
    assert "localStorage" in core
