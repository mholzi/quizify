"""The host tab can pause, resume and skip a live question (#1017).

Running the evening from ``/quizify/admin`` without joining as a player is the
default flow (#846). On that page ``handleQuestionStarted`` and
``handleWagerProgress`` hid both Next and End, and the page had no Pause,
Resume or Skip at all — although the server accepts all three from the admin
socket, the phone's host bar sends them and the Lovelace host card maps them.
A host facing a broken question or a stuck bet window could only wait it out,
or press the header Reset and wipe every player.

These run the real ``admin.js`` handlers against the real ``admin.html``
class lists under node (``tests/fixtures/dom_stub.js``), so they assert what
the host sees and what a tap actually sends, not the shape of the source.
"""

from __future__ import annotations

import functools
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parent.parent
_WWW = _REPO / "custom_components" / "quizify" / "www"
_ADMIN_JS = _WWW / "js" / "admin.js"
_ADMIN_HTML = _WWW / "admin.html"
_HOST_CARD = _WWW / "cards" / "quizify-host-card.js"
_STUB = Path(__file__).resolve().parent / "fixtures" / "dom_stub.js"

_NEEDS_NODE = pytest.mark.skipif(
    shutil.which("node") is None, reason="node not installed"
)

#: els key -> element id, for every control the tests look at.
CONTROLS = {
    "skipBtn": "admin-skip-btn",
    "pauseBtn": "admin-pause-btn",
    "resumeBtn": "admin-resume-btn",
    "liveControls": "admin-live-controls",
    "nextQuestionBtn": "next-question-btn",
    "endGameBtn": "end-game-btn",
    "pausedIndicator": "admin-paused-indicator",
}


def _block(source: str, signature: str) -> str:
    """One brace-balanced declaration, or '' when it is not there — so the
    harness still runs against a page without the fix and the tests fail on
    what the host sees, not on the fixture."""
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
                end = i + 1
                if source[end : end + 1] == ";":
                    end += 1
                return source[start:end]
    raise AssertionError(f"unbalanced braces after {signature!r}")


def _html_classes() -> dict[str, list[str] | None]:
    """How each control ships in admin.html; ``None`` when it is absent."""
    html = _ADMIN_HTML.read_text("utf-8")
    out: dict[str, list[str] | None] = {}
    for element_id in CONTROLS.values():
        m = re.search(rf'<[^>]*\bid="{element_id}"[^>]*>', html)
        if not m:
            out[element_id] = None
            continue
        cls = re.search(r'class="([^"]*)"', m.group(0))
        out[element_id] = cls.group(1).split() if cls else []
    return out


_SCRIPT = r"""
require(%(stub)s);
global.setTimeout = function () {};
var CLASSES = %(classes)s;
var CONTROLS = %(controls)s;
Object.keys(CLASSES).forEach(function (id) {
    var el = QZ.el(id);
    (CLASSES[id] || []).forEach(function (c) { el.classList.add(c); });
});
QZ.els(['game-view', 'setup-screen', 'admin-finale-view', 'game-leaderboard',
        'admin-round', 'admin-question', 'admin-correct',
        'admin-timer-bar', 'admin-timer-bar-text']);

var views = {
    game: document.getElementById('game-view'),
    setup: document.getElementById('setup-screen'),
    finale: document.getElementById('admin-finale-view')
};
var els = {
    gameLeaderboard: document.getElementById('game-leaderboard'),
    adminRound: document.getElementById('admin-round'),
    adminQuestion: document.getElementById('admin-question'),
    adminCorrect: document.getElementById('admin-correct')
};
Object.keys(CONTROLS).forEach(function (k) {
    els[k] = document.getElementById(CONTROLS[k]);
});

var _redirecting = false;
var _adminJoinedAs = null;
var currentPhase = 'LOBBY';
var sessionStorage = { getItem: function () { return null; },
                       removeItem: function () {} };
var adminTimer = { start: function () {}, stop: function () {},
                   update: function () {} };
function _t(key) { return key; }
function _tOr(key, params, fallback) { return fallback; }
function showView(name) { currentView = name; }
function renderLeaderboard() {}
function setDetourDetail() {}
function clearAnswerProgress() {}
function renderLobbyPlayers() {}
var currentView = null;

var SENT = [];
function send(type) { SENT.push(type); return true; }
function on(el, ev, fn) { if (el) el.addEventListener(ev, fn); }

%(admin)s

function visible(key) {
    var id = CONTROLS[key];
    if (CLASSES[id] === null) return false;  // not on the page at all
    var el = els[key];
    if (el.classList.contains('hidden')) return false;
    var inRow = ['skipBtn', 'pauseBtn', 'resumeBtn'];
    if (inRow.indexOf(key) !== -1) {
        return visible('liveControls');
    }
    return true;
}
function snap() {
    var out = { phase: currentPhase, view: currentView };
    Object.keys(CONTROLS).forEach(function (k) { out[k] = visible(k); });
    out.clockGreyed = ['admin-timer-bar', 'admin-timer-bar-text'].every(
        function (id) {
            return document.getElementById(id).classList.contains('is-paused');
        });
    return out;
}

var R = {};
handleQuestionStarted({ question_text: 'Q?', timer_duration: 20,
                        round_num: 3, total_rounds: 10 });
R.QUESTION_ACTIVE = snap();
handleRoundSummary({ correct_answer: 'A', last_round: false });
R.ANSWER_REVEAL = snap();
handleWagerProgress({ round_num: 10, total_rounds: 10, locked_in: 1,
                      player_count: 3, window_duration: 15 });
R.WAGER_ACTIVE = snap();
handleGameState({ phase: 'PAUSED', pause_reason: 'admin_paused' });
R.PAUSED = snap();
handleQuestionStarted({ question_text: 'Q?', timer_duration: 20,
                        round_num: 3, total_rounds: 10 });
R.RESUMED = snap();
if (typeof setDetourNotice === 'function') {
    setDetourNotice('HOT_SEAT_AUCTION');
    R.HOT_SEAT_AUCTION = snap();
}

// Every tap, one at a time, on a freshly re-armed button.
var TAPS = {};
['skipBtn', 'pauseBtn', 'resumeBtn'].forEach(function (k) {
    SENT = [];
    els[k].disabled = false;
    els[k].click();
    TAPS[k] = SENT.slice();
});

console.log(JSON.stringify({ phases: R, taps: TAPS }));
"""


#: One-line click wiring: ``on(els.x, 'click', function () { _debouncedSend(``.
_WIRING = r"\s*on\(els\.\w+, 'click', function \(\) \{ _debouncedSend\("


@functools.lru_cache(maxsize=1)
def _run() -> dict:
    source = _ADMIN_JS.read_text("utf-8")
    wiring = "\n".join(line for line in source.splitlines() if re.match(_WIRING, line))
    parts = [
        _block(source, "var LIVE_CONTROLS = {"),
        _block(source, "function setLiveControls(phase) {"),
        _block(source, "function handleGameState(msg) {"),
        _block(source, "function handleQuestionStarted(msg) {"),
        _block(source, "function handleWagerProgress(msg) {"),
        _block(source, "function handleRoundSummary(msg) {"),
        _block(source, "function setDetourNotice(phase, seatHolder) {"),
        _block(source, "function _debouncedSend(btn, msgType) {"),
        wiring,
    ]
    script = _SCRIPT % {
        "stub": json.dumps(str(_STUB)),
        "classes": json.dumps(_html_classes()),
        "controls": json.dumps(CONTROLS),
        "admin": "\n\n".join(p for p in parts if p),
    }
    out = subprocess.run(
        ["node", "-e", script], capture_output=True, text=True, check=True
    )
    return json.loads(out.stdout)


@_NEEDS_NODE
def test_a_live_question_offers_skip_pause_and_end() -> None:
    """The issue itself: during QUESTION_ACTIVE the host had nothing."""
    q = _run()["phases"]["QUESTION_ACTIVE"]

    assert q["skipBtn"] is True
    assert q["pauseBtn"] is True
    assert q["endGameBtn"] is True, "End must stay reachable during a question"
    assert q["resumeBtn"] is False
    assert q["nextQuestionBtn"] is False, "next_question is refused while live"


@_NEEDS_NODE
def test_the_bet_window_offers_skip_and_end_but_not_pause() -> None:
    """admin_skip closes the betting window (#656); pause is a no-op there
    (PhaseController.pause only pauses QUESTION_ACTIVE), so it is not offered."""
    w = _run()["phases"]["WAGER_ACTIVE"]

    assert w["skipBtn"] is True
    assert w["endGameBtn"] is True
    assert w["pauseBtn"] is False
    assert w["resumeBtn"] is False
    assert w["nextQuestionBtn"] is False


@_NEEDS_NODE
def test_a_paused_game_offers_resume_and_end_on_the_game_view() -> None:
    """PAUSED used to fall through to the default arm: the game view with
    whatever buttons the question had left — none that could resume."""
    p = _run()["phases"]["PAUSED"]

    assert p["view"] == "game"
    assert p["resumeBtn"] is True
    assert p["endGameBtn"] is True
    assert p["skipBtn"] is False
    assert p["pauseBtn"] is False
    assert p["nextQuestionBtn"] is False


@_NEEDS_NODE
def test_a_paused_game_says_so_and_the_line_goes_with_the_pause() -> None:
    """#1024 review: the frozen clock alone ("14s") read like a stuck page.
    PAUSED shows the paused line and greys the clock; the next live phase
    takes both away again."""
    phases = _run()["phases"]

    assert phases["PAUSED"]["pausedIndicator"] is True
    assert phases["PAUSED"]["clockGreyed"] is True
    for phase in ("QUESTION_ACTIVE", "ANSWER_REVEAL", "WAGER_ACTIVE", "RESUMED"):
        assert phases[phase]["pausedIndicator"] is False, phase
        assert phases[phase]["clockGreyed"] is False, phase


def test_the_paused_line_reuses_the_existing_paused_title() -> None:
    """The phone's pause overlay already says "Spiel pausiert" through
    admin.pausedTitle; the host page says the same words, in all three
    languages, rather than a near-duplicate key."""
    html = _ADMIN_HTML.read_text("utf-8")
    block = re.search(
        r'<div[^>]*id="admin-paused-indicator"[^>]*>(.*?)</div>', html, re.S
    )
    assert block, "admin.html has no paused indicator"
    assert 'data-i18n="admin.pausedTitle"' in block.group(1)
    for code in ("de", "en", "es"):
        bundle = json.loads((_WWW / "i18n" / f"{code}.json").read_text("utf-8"))
        assert bundle["admin"].get("pausedTitle"), f"{code}: admin.pausedTitle"


def test_resume_does_not_share_a_look_with_end_game() -> None:
    """#1024 review: coral-primary Resume next to brick-danger End read as
    the same button. Resume is sage (the accent token), End is outlined."""
    classes = _html_classes()
    resume = classes["admin-resume-btn"] or []
    assert "btn-resume" in resume
    assert "btn-primary" not in resume and "btn-danger" not in resume

    css = (_WWW / "css" / "src" / "04-lobby.css").read_text("utf-8")
    resume_rule = _block(css, ".btn-resume {")
    assert "var(--color-accent-secondary)" in resume_rule
    end_rule = _block(css, ".admin-actions #end-game-btn {")
    assert "var(--color-bg-surface)" in end_rule
    assert "border:" in end_rule


@_NEEDS_NODE
def test_the_reveal_and_the_detour_offer_none_of_the_three() -> None:
    """Outside the table the server refuses or ignores them; a dead button is
    worse than none (#801)."""
    phases = _run()["phases"]

    for phase in ("ANSWER_REVEAL", "HOT_SEAT_AUCTION"):
        snap = phases[phase]
        assert snap["liveControls"] is False, phase
        assert snap["skipBtn"] is False, phase
        assert snap["pauseBtn"] is False, phase
        assert snap["resumeBtn"] is False, phase
        assert snap["endGameBtn"] is True, phase


@_NEEDS_NODE
def test_each_control_sends_the_message_the_phone_host_bar_sends() -> None:
    taps = _run()["taps"]

    assert taps["skipBtn"] == ["admin_skip"]
    assert taps["pauseBtn"] == ["pause_game"]
    assert taps["resumeBtn"] == ["resume_game"]


def test_the_table_matches_the_host_card() -> None:
    """The host card's PHASE_ACTIONS is the reference for which phase accepts
    which message; the admin page must not offer a control the card maps to
    a different message (or to none)."""
    card = _HOST_CARD.read_text("utf-8")
    admin = _ADMIN_JS.read_text("utf-8")
    table = _block(admin, "var LIVE_CONTROLS = {")
    assert table, "admin.js has no LIVE_CONTROLS table"

    def card_msg(phase: str) -> str:
        m = re.search(rf"{phase}: \{{[^}}]*msg: '(\w+)'", card)
        assert m, phase
        return m.group(1)

    assert card_msg("QUESTION_ACTIVE") == "admin_skip"
    assert card_msg("WAGER_ACTIVE") == "admin_skip"
    assert card_msg("PAUSED") == "resume_game"
    assert re.search(r"QUESTION_ACTIVE: \{[^}]*skip: true", table)
    assert re.search(r"WAGER_ACTIVE: \{[^}]*skip: true", table)
    assert re.search(r"PAUSED: \{[^}]*resume: true", table)


def test_no_new_i18n_keys_the_buttons_use_the_existing_ones() -> None:
    html = _ADMIN_HTML.read_text("utf-8")
    for element_id, key in (
        ("admin-skip-btn", "admin.skip"),
        ("admin-pause-btn", "admin.pause"),
        ("admin-resume-btn", "admin.resume"),
    ):
        m = re.search(rf'<button[^>]*id="{element_id}"[^>]*>', html)
        assert m, element_id
        assert f'data-i18n="{key}"' in m.group(0)
    for code in ("de", "en", "es"):
        bundle = json.loads((_WWW / "i18n" / f"{code}.json").read_text("utf-8"))
        for key in ("skip", "pause", "resume"):
            assert bundle["admin"].get(key), f"{code}: admin.{key}"
