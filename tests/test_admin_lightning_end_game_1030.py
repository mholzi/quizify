"""End Game stays reachable on the host tab through the Lightning Round (#1030).

#1017 / #1024 kept End Game (and Skip / Pause / Resume) on the host tab for
every phase the game view shows. The Lightning Round has two views of its own
(``#admin-lightning-view``, ``#admin-lightning-recap-view``) and
``#end-game-btn`` lived only in the game view's sticky bar, so from the splash
to the recap the only control left was the header Reset, which wipes the room.

The fix moves the one End Game button into a ``[data-end-game-slot]`` bar of
whichever view is shown. These tests run the real ``showView`` and lightning
handlers from ``admin.js`` under node (``tests/fixtures/dom_stub.js``).

Pause is deliberately not offered there: ``PhaseController.pause`` only pauses
QUESTION_ACTIVE, so a Pause button in the lightning view would be dead.
"""

from __future__ import annotations

import functools
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from tests.test_admin_live_controls_1017 import _block

_REPO = Path(__file__).resolve().parent.parent
_WWW = _REPO / "custom_components" / "quizify" / "www"
_ADMIN_JS = _WWW / "js" / "admin.js"
_ADMIN_HTML = _WWW / "admin.html"
_PHASE_CONTROLLER = (
    _REPO / "custom_components" / "quizify" / "game" / "phase_controller.py"
)
_STUB = Path(__file__).resolve().parent / "fixtures" / "dom_stub.js"

_NEEDS_NODE = pytest.mark.skipif(
    shutil.which("node") is None, reason="node not installed"
)


def _view_markup(view_id: str) -> str:
    """The markup of one top-level ``.view`` div, up to the next view."""
    html = _ADMIN_HTML.read_text("utf-8")
    start = html.index(f'id="{view_id}"')
    nxt = re.search(r'<div id="[^"]+" class="view"', html[start + 1 :])
    end = start + 1 + nxt.start() if nxt else html.index("</main>")
    return html[start:end]


def test_every_view_that_runs_the_game_has_an_end_game_slot() -> None:
    for view_id in ("game-view", "admin-lightning-view", "admin-lightning-recap-view"):
        assert "data-end-game-slot" in _view_markup(view_id), view_id


def test_there_is_still_exactly_one_end_game_button() -> None:
    """One button that moves, not copies with their own wiring to drift."""
    html = _ADMIN_HTML.read_text("utf-8")
    assert len(re.findall(r'id="end-game-btn"', html)) == 1
    assert (
        "data-end-game-slot" in _view_markup("game-view").split('id="end-game-btn"')[0]
    ), "End Game must start in the game view's slot"


def test_no_pause_in_the_lightning_views_because_the_server_cannot() -> None:
    controller = _PHASE_CONTROLLER.read_text("utf-8")
    pause = controller[controller.index("def pause(") :]
    pause = pause[: pause.index("def resume(")]
    assert "self.phase != GamePhase.QUESTION_ACTIVE" in pause
    for view_id in ("admin-lightning-view", "admin-lightning-recap-view"):
        assert "admin-pause-btn" not in _view_markup(view_id), view_id


_SCRIPT = r"""
require(%(stub)s);
global.setTimeout = function () {};

function slotted(viewId) {
    var view = QZ.el(viewId);
    var slot = document.createElement('div');
    slot.setAttribute('data-end-game-slot', '');
    view.appendChild(slot);
    return slot;
}
var SLOTS = {
    game: slotted('game-view'),
    lightning: slotted('admin-lightning-view'),
    lightningRecap: slotted('admin-lightning-recap-view')
};
var endBtn = QZ.el('end-game-btn');
%(end_classes)s.forEach(function (c) { endBtn.classList.add(c); });
SLOTS.game.appendChild(endBtn);

QZ.els(['setup-screen', 'lobby-screen', 'admin-finale-view', 'game-leaderboard',
        'admin-round', 'admin-question', 'admin-correct', 'next-question-btn',
        'admin-timer-bar', 'admin-timer-bar-text']);

var views = {
    setup: document.getElementById('setup-screen'),
    lobby: document.getElementById('lobby-screen'),
    game: document.getElementById('game-view'),
    finale: document.getElementById('admin-finale-view'),
    lightning: document.getElementById('admin-lightning-view'),
    lightningRecap: document.getElementById('admin-lightning-recap-view')
};
var els = {
    gameLeaderboard: document.getElementById('game-leaderboard'),
    adminRound: document.getElementById('admin-round'),
    adminQuestion: document.getElementById('admin-question'),
    adminCorrect: document.getElementById('admin-correct'),
    nextQuestionBtn: document.getElementById('next-question-btn'),
    endGameBtn: endBtn
};

var _redirecting = false;
var _adminJoinedAs = null;
var currentPhase = 'LOBBY';
var sessionStorage = { getItem: function () { return null; },
                       removeItem: function () {} };
var adminTimer = { start: function () {}, stop: function () {},
                   update: function () {} };
function _t(key) { return key; }
function _tOr(key, params, fallback) { return fallback; }
function renderLeaderboard() {}
function setDetourDetail() {}
function clearAnswerProgress() {}
function renderLobbyPlayers() {}

%(admin)s

function where() {
    var out = null;
    Object.keys(SLOTS).forEach(function (k) {
        if (endBtn.parentNode === SLOTS[k]) out = k;
    });
    return out;
}
function snap() {
    var active = null;
    Object.keys(views).forEach(function (k) {
        if (views[k].classList.contains('active')) active = k;
    });
    return { view: active, slot: where(),
             endVisible: !endBtn.classList.contains('hidden') };
}

var R = {};
handleQuestionStarted({ question_text: 'Q?', timer_duration: 20,
                        round_num: 3, total_rounds: 5 });
R.QUESTION_ACTIVE = snap();
handleAdminLightningSplash({ num_questions: 5, seconds_per_question: 15 });
R.SPLASH = snap();
handleAdminLightningQuestion({ index: 2, num_questions: 5, question_text: 'L?' });
R.LIGHTNING = snap();
handleAdminLightningRecap({ leaderboard: [], questions: [] });
R.LIGHTNING_RECAP = snap();
handleQuestionStarted({ question_text: 'Q?', timer_duration: 20,
                        round_num: 4, total_rounds: 5 });
R.BACK_IN_GAME = snap();
handleGameState({ phase: 'LIGHTNING',
                  lightning: { index: 1, num_questions: 5,
                               question: { text: 'L?' } } });
R.RECONNECT_LIGHTNING = snap();
handleGameState({ phase: 'LIGHTNING_RECAP', lightning_recap: {} });
R.RECONNECT_RECAP = snap();

SENT = [];
endBtn.click();
R.TAP = { opened: OPENED.slice() };
console.log(JSON.stringify(R));
"""


@functools.lru_cache(maxsize=1)
def _run() -> dict:
    source = _ADMIN_JS.read_text("utf-8")
    html = _ADMIN_HTML.read_text("utf-8")
    m = re.search(r'<button[^>]*id="end-game-btn"[^>]*>', html)
    assert m
    end_classes = re.search(r'class="([^"]*)"', m.group(0)).group(1).split()
    parts = [
        _block(source, "function showView(name) {"),
        _block(source, "var LIVE_CONTROLS = {"),
        _block(source, "function setLiveControls(phase) {"),
        _block(source, "function handleGameState(msg) {"),
        _block(source, "function handleQuestionStarted(msg) {"),
        _block(source, "function _toggleAdminLightningSplash(showSplash) {"),
        _block(source, "function handleAdminLightningSplash(msg) {"),
        _block(source, "function handleAdminLightningQuestion(msg) {"),
        _block(source, "function handleAdminLightningRecap(recap) {"),
        # The real click wiring: one button, one confirm modal.
        "var OPENED = []; var SENT = [];\n"
        "function openConfirmModal(id) { OPENED.push(id); }\n"
        "function on(el, ev, fn) { if (el) el.addEventListener(ev, fn); }\n"
        + re.search(
            r"on\(els\.endGameBtn, 'click', function \(\) \{.*?\n    \}\);",
            source,
            re.S,
        ).group(0),
    ]
    script = _SCRIPT % {
        "stub": json.dumps(str(_STUB)),
        "end_classes": json.dumps(end_classes),
        "admin": "\n\n".join(p for p in parts if p),
    }
    out = subprocess.run(
        ["node", "-e", script], capture_output=True, text=True, check=True
    )
    return json.loads(out.stdout)


@_NEEDS_NODE
def test_end_game_is_offered_on_the_splash_the_questions_and_the_recap() -> None:
    """The issue itself: only the header Reset was left in these three."""
    r = _run()
    for key, view in (
        ("SPLASH", "lightning"),
        ("LIGHTNING", "lightning"),
        ("LIGHTNING_RECAP", "lightningRecap"),
    ):
        assert r[key]["view"] == view, key
        assert r[key]["slot"] == view, f"{key}: End Game not in the shown view"
        assert r[key]["endVisible"] is True, key


@_NEEDS_NODE
def test_a_reconnect_into_the_lightning_round_also_offers_end_game() -> None:
    r = _run()
    assert r["RECONNECT_LIGHTNING"]["slot"] == "lightning"
    assert r["RECONNECT_LIGHTNING"]["endVisible"] is True
    assert r["RECONNECT_RECAP"]["slot"] == "lightningRecap"
    assert r["RECONNECT_RECAP"]["endVisible"] is True


@_NEEDS_NODE
def test_end_game_goes_back_to_the_game_view_after_the_round() -> None:
    r = _run()
    assert r["QUESTION_ACTIVE"]["slot"] == "game"
    assert r["BACK_IN_GAME"]["view"] == "game"
    assert r["BACK_IN_GAME"]["slot"] == "game"
    assert r["BACK_IN_GAME"]["endVisible"] is True


@_NEEDS_NODE
def test_the_moved_button_still_opens_the_end_game_confirm() -> None:
    assert _run()["TAP"]["opened"] == ["end-game-modal"]
