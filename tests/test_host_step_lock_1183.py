"""A host step holds its button until the game has moved on (#1183).

The Lovelace card's main button reads "Next question" in the reveal and
"Reveal" during the question that tap starts. Without a lock a double tap — or
a second tap because the card had not repainted yet — skipped the fresh
question, and the server scored it at once: 0 points for everyone. The console
held the button 1.5 s; the phone bar and the card did not hold it at all.

The rule is the same on all three surfaces now: locked the moment the step is
sent, for at least 1.5 s, and until the phase changes; 8 s frees it if the
server refused and nothing moves. This runs the phone's helper for real, on a
fake clock.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

_WWW = Path(__file__).resolve().parent.parent / "custom_components" / "quizify" / "www"
_STUB = Path(__file__).resolve().parent / "fixtures" / "dom_stub.js"

_NEEDS_NODE = pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")

_SCRIPT = """
var now = 0;
var timers = [];
global.setTimeout = function (fn, ms) { timers.push({ at: now + ms, fn: fn }); return timers.length; };
Date.now = function () { return now; };
function advance(ms) {
    var until = now + ms;
    for (;;) {
        timers.sort(function (a, b) { return a.at - b.at; });
        var next = timers[0];
        if (!next || next.at > until) break;
        timers.shift();
        now = next.at;
        next.fn();
    }
    now = until;
}

require(%(stub)s);
QZ.load(%(utils)s);
var pu = window.QuizifyPlayerUtils;
var btn = QZ.el('skip-question-btn');
var sent = [];
function send(type) { sent.push(type); return true; }
var out = {};

pu.state.currentPhase = 'ANSWER_REVEAL';
out.first = pu.hostStep(btn, send, 'next_round');
out.disabledAtOnce = btn.disabled === true;
out.second = pu.hostStep(btn, send, 'next_round');
// The phase moves at once, but the floor still holds.
pu.state.currentPhase = 'QUESTION_ACTIVE';
advance(1000);
out.heldAt1000 = btn.disabled === true;
advance(600);
out.freedAfterFloorAndPhase = btn.disabled === false;

// Nothing moves: held past the floor, freed by the ceiling.
var b2 = QZ.el('pause-game-btn');
pu.hostStep(b2, send, 'pause_game');
advance(3000);
out.heldWhilePhaseStands = b2.disabled === true;
advance(5500);
out.freedByCeiling = b2.disabled === false;

// A send that never left holds nothing (#621).
var b3 = QZ.el('resume-game-btn');
out.unsent = pu.hostStep(b3, function () { return false; }, 'resume_game');
out.unsentNotHeld = b3.disabled !== true;

out.sent = sent;
console.log(JSON.stringify(out));
"""


@_NEEDS_NODE
def test_the_phone_lock_holds_for_the_floor_and_the_phase() -> None:
    script = _SCRIPT % {
        "stub": json.dumps(str(_STUB)),
        "utils": json.dumps(str(_WWW / "js" / "player-utils.js")),
    }
    run = subprocess.run(["node", "-e", script], capture_output=True, text=True, check=True)
    out = json.loads(run.stdout.strip().splitlines()[-1])

    assert out["first"] is True and out["disabledAtOnce"]
    assert out["second"] is False, "a second tap during the lock must not send"
    assert out["heldAt1000"], "the 1.5 s floor holds even after the phase moved"
    assert out["freedAfterFloorAndPhase"]
    assert out["heldWhilePhaseStands"], "held until the phase changes"
    assert out["freedByCeiling"], "a refused step must not leave the host stuck"
    assert out["unsent"] is False and out["unsentNotHeld"]
    assert out["sent"] == ["next_round", "pause_game"]


def test_all_three_surfaces_share_the_numbers() -> None:
    utils = (_WWW / "js" / "player-utils.js").read_text("utf-8")
    card = (_WWW / "cards" / "quizify-host-card.js").read_text("utf-8")
    admin = (_WWW / "js" / "admin.js").read_text("utf-8")
    for src in (utils, card):
        assert "HOST_STEP_MIN_MS = 1500" in src
        assert "HOST_STEP_MAX_MS = 8000" in src
    body = admin[admin.index("function _debouncedSend(btn, msgType)") :]
    body = body[: body.index("\n    }\n")]
    assert "currentPhase === phaseAtTap" in body and "1500" in body and "8000" in body


_SCRIPT_1192 = """
var now = 0;
var timers = [];
global.setTimeout = function (fn, ms) {
    timers.push({ at: now + ms, fn: fn });
    return timers.length;
};
Date.now = function () { return now; };
function advance(ms) {
    var until = now + ms;
    for (;;) {
        timers.sort(function (a, b) { return a.at - b.at; });
        var next = timers[0];
        if (!next || next.at > until) break;
        timers.shift();
        now = next.at;
        next.fn();
    }
    now = until;
}

require(%(stub)s);
QZ.load(%(utils)s);
var pu = window.QuizifyPlayerUtils;
var btn = QZ.el('next-round-btn');
var sent = [];
function send(type) { sent.push(type); return true; }
var out = {};

// Round 1 reveal: the host taps Next.
pu.setPhase('ANSWER_REVEAL');
out.first = pu.hostStep(btn, send, 'next_round');
// Round 2 starts and is over inside the 1.5 s floor (everyone answered).
advance(300);
pu.setPhase('QUESTION_ACTIVE');
advance(200);
pu.setPhase('ANSWER_REVEAL');
// The very same phase name the tap recorded, but a different reveal.
advance(1200);
out.lockedAfterRelease = !!btn._qzHostLock;
out.disabledAfterRelease = btn.disabled === true;

// Next again, immediately: must go out now, not 8 s later.
out.second = pu.hostStep(btn, send, 'next_round');

// An enabled button must never be locked: something else re-enables it
// while the poll has not run yet.
var b2 = QZ.el('skip-question-btn');
pu.setPhase('ANSWER_REVEAL');
pu.hostStep(b2, send, 'next_round');
pu.setPhase('QUESTION_ACTIVE');
pu.setPhase('ANSWER_REVEAL');
b2.disabled = false;
out.enabledInsideFloor = pu.hostStep(b2, send, 'next_round');
now += 1500;
out.enabledAfterFloor = pu.hostStep(b2, send, 'next_round');

// A refused step on a standing state is still held (no regression of #1183).
var b3 = QZ.el('pause-game-btn');
pu.hostStep(b3, send, 'pause_game');
advance(3000);
out.heldWhileStateStands = pu.hostStep(b3, send, 'pause_game');

out.sent = sent;
console.log(JSON.stringify(out));
"""


@_NEEDS_NODE
def test_a_fast_question_does_not_swallow_the_next_tap_1192() -> None:
    script = _SCRIPT_1192 % {
        "stub": json.dumps(str(_STUB)),
        "utils": json.dumps(str(_WWW / "js" / "player-utils.js")),
    }
    run = subprocess.run(
        ["node", "-e", script], capture_output=True, text=True, check=True
    )
    out = json.loads(run.stdout.strip().splitlines()[-1])

    assert out["first"] is True
    assert out["lockedAfterRelease"] is False and out["disabledAfterRelease"] is False
    assert out["second"] is True, "Next after a sub-1.5 s question must send at once"
    assert out["enabledInsideFloor"] is False, "the floor still guards a double tap"
    assert out["enabledAfterFloor"] is True, "an enabled button must not be locked"
    assert out["heldWhileStateStands"] is False
    assert out["sent"] == ["next_round"] * 4 + ["pause_game"]


def test_every_phase_entry_goes_through_set_phase_1192() -> None:
    core = (_WWW / "js" / "player-core.js").read_text("utf-8")
    assert not re.search(r"state\.currentPhase\s*=[^=]", core), (
        "use pu.setPhase so the lock sees every state"
    )


_SCRIPT_1192_LIVE = """
var now = 0;
var timers = [];
global.setTimeout = function (fn, ms) { timers.push({ at: now + ms, fn: fn }); return timers.length; };
Date.now = function () { return now; };
function advance(ms) {
    var until = now + ms;
    for (;;) {
        timers.sort(function (a, b) { return a.at - b.at; });
        var next = timers[0];
        if (!next || next.at > until) break;
        timers.shift();
        now = next.at;
        next.fn();
    }
    now = until;
}

require(%(stub)s);
QZ.load(%(utils)s);
var pu = window.QuizifyPlayerUtils;
var sent = [];
function send(type) { sent.push(type); return true; }
var out = {};

// Frames of the live run (2026-11-03): tap at 0, question_started at +22 ms,
// round_summary at +881 ms, where the reveal render re-enables the button
// while the lock still stands; the host's tap lands at +1171 ms.
var btn = QZ.el('next-round-btn');
pu.setPhase('ANSWER_REVEAL');
out.first = pu.hostStep(btn, send, 'next_round');
advance(22);
pu.setPhase('QUESTION_ACTIVE');
advance(859);
pu.setPhase('ANSWER_REVEAL');
btn.disabled = false;
advance(290);
out.lockStillHeld = !!btn._qzHostLock;
out.tapOnFreshReveal = pu.hostStep(btn, send, 'next_round');

// A double tap stays guarded: the reveal appears 100 ms after the tap and the
// second tap follows 100 ms later.
var b2 = QZ.el('skip-question-btn');
advance(5000);
pu.setPhase('ANSWER_REVEAL');
pu.hostStep(b2, send, 'next_round');
advance(50);
pu.setPhase('QUESTION_ACTIVE');
advance(50);
pu.setPhase('ANSWER_REVEAL');
b2.disabled = false;
advance(100);
out.doubleTap = pu.hostStep(b2, send, 'next_round');

out.sent = sent;
console.log(JSON.stringify(out));
"""


@_NEEDS_NODE
def test_a_tap_on_a_fresh_reveal_inside_the_floor_is_not_dropped_1192() -> None:
    script = _SCRIPT_1192_LIVE % {
        "stub": json.dumps(str(_STUB)),
        "utils": json.dumps(str(_WWW / "js" / "player-utils.js")),
    }
    run = subprocess.run(["node", "-e", script], capture_output=True, text=True, check=True)
    out = json.loads(run.stdout.strip().splitlines()[-1])

    assert out["first"] is True
    assert out["lockStillHeld"], "the render re-enabled the button under a live lock"
    assert out["tapOnFreshReveal"] is True, "the host's tap on the new reveal must send"
    assert out["doubleTap"] is False, "a double tap on a just-appeared reveal stays guarded"
    assert out["sent"] == ["next_round", "next_round", "next_round"]
