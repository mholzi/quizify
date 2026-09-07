"""A live frame and a snapshot must leave the phone in the same state (#858).

Found on real hardware during the v1.16.0-RC3 live test, three weekends after
#834 built the guests' escape hatch and #842/#848 wired the signal that arms
it. The lightning recap armed. The Hot Seat result armed. The **ordinary
reveal** — the screen every single round ends on, and the one a host-less room
therefore waits on most — never did. Four phones, `["host_presence:false"]`
recorded on every one of them, `reveal-reset-controls` still `display: none`
after 137 seconds.

The mechanism is not the signal. It is which half of the client can act on it:

* ``handleGameState`` selects the stage from the phase in one table-driven
  line, so a phone that *reloads* onto the reveal arms correctly.
* ``handleMessage`` selected it from three hand-written ``case`` blocks, and
  ``round_summary`` was not one of them. A phone that reaches the reveal by
  living through the round never receives a snapshot, so ``_resetStage`` was
  null, ``refreshStageReset`` returned at its first line, and a correct
  ``host_connected:false`` had nothing to arm.

That is the third instance of one shape in one weekend:

* **#832** — the host page waited for a full snapshot while the transition
  arrived as a single event.
* **#848** — a frame whose name its reader did not know.
* **#858** — a stage only the snapshot half could enter.

So this file does not test "``round_summary`` calls ``setResetStage``". It
tests the invariant that would have caught all three on this surface: **every
waiting stage the snapshot can select must also be reachable from the live
frame that opens it, and both routes must arm the same control.** The client
now carries the live half as a table too (``STAGE_ENTERED_BY``), applied once
at the bottom of ``handleMessage``; a fourth between-round stage is two rows,
and forgetting the second one is a red test rather than a stranded living room.

The last two tests run the real affordance code under
``tests/fixtures/dom_stub.js``, against the real classes ``player.html`` ships,
so what they assert is what a guest's phone shows — not what the source says.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO))

from custom_components.quizify.server.protocol import SERVER_FRAMES  # noqa: E402

_CC = _REPO / "custom_components" / "quizify"
_WWW = _CC / "www"
_JS = _WWW / "js"
_CORE = _JS / "player-core.js"
_HTML = _WWW / "player.html"
_STUB = Path(__file__).resolve().parent / "fixtures" / "dom_stub.js"

_NEEDS_NODE = pytest.mark.skipif(
    shutil.which("node") is None, reason="node not installed"
)


def _without_comments(source: str) -> str:
    source = re.sub(r"/\*.*?\*/", "", source, flags=re.S)
    return re.sub(r"^\s*//.*$", "", source, flags=re.M)


def _object_literal(name: str) -> str:
    source = _without_comments(_CORE.read_text("utf-8"))
    return source.split(f"var {name} = {{", 1)[1].split("};", 1)[0]


def _stage_controls() -> dict[str, tuple[str, str]]:
    """``STAGE_RESET_AFFORDANCES`` as the real client declares it."""
    found = re.findall(
        r"'([A-Z_]+)': \['([a-z-]+)', '([a-z-]+)'\]",
        _object_literal("STAGE_RESET_AFFORDANCES"),
    )
    assert found, "STAGE_RESET_AFFORDANCES is unreadable"
    return {phase: (btn, wrapper) for phase, btn, wrapper in found}


def _stage_openers() -> dict[str, str]:
    """``STAGE_ENTERED_BY`` — frame type → the stage it opens."""
    found = re.findall(r"'([a-z_]+)': '([A-Z_]+)'", _object_literal("STAGE_ENTERED_BY"))
    assert found, "STAGE_ENTERED_BY is unreadable"
    return dict(found)


def _handle_message_body() -> str:
    source = _without_comments(_CORE.read_text("utf-8"))
    return source.split("function handleMessage(msg) {", 1)[1].split(
        "function handleReactionBonus", 1
    )[0]


def _element_classes(ids: tuple[str, ...]) -> dict[str, list[str]]:
    """The real class list of each id, taken from ``player.html``.

    The arm/disarm dance turns on these: ``armResetAffordance`` refuses to arm
    a button that is already visible.
    """
    html = _HTML.read_text("utf-8")
    out: dict[str, list[str]] = {}
    for element_id in ids:
        m = re.search(rf'id="{element_id}" class="([^"]*)"', html)
        assert m, f"#{element_id} is missing from player.html"
        out[element_id] = m.group(1).split()
    return out


# ---------------------------------------------------------------------------
# The invariant
# ---------------------------------------------------------------------------


def test_every_waiting_stage_can_be_entered_by_a_live_frame() -> None:
    """The #858 invariant, and the only test here that had to be written.

    Stages are grouped by the control they own, because ANSWER_REVEAL and
    REVEAL are two names for one hatch — one live opener covers both. Every
    other group needs its own row in ``STAGE_ENTERED_BY``, or the phones that
    lived through the round have no way out of it.
    """
    controls = _stage_controls()
    openers = _stage_openers()

    reachable = {controls[stage][0] for stage in openers.values()}
    for phase, (btn, _wrapper) in controls.items():
        assert btn in reachable, (
            f"{phase} can be entered from a game_state snapshot but from no "
            f"live frame, so #{btn} only ever arms on a phone that happened to "
            "reload there — which is exactly #858. Add the frame that opens it "
            "to STAGE_ENTERED_BY."
        )


def test_the_reveal_is_the_one_the_live_test_found() -> None:
    """The regression itself, spelled out so a table edit cannot lose it."""
    openers = _stage_openers()
    controls = _stage_controls()

    assert "round_summary" in openers, (
        "round_summary is the frame that opens the ordinary reveal — the "
        "screen every round ends on (#858)"
    )
    assert controls[openers["round_summary"]][0] == "reveal-reset-btn"


def test_every_opener_is_a_frame_this_phone_actually_receives() -> None:
    """A row pointing at a frame the server does not send, or one the phone's
    router has no case for, is a table entry that can never fire."""
    openers = _stage_openers()
    body = _handle_message_body()

    for frame in openers:
        assert frame in SERVER_FRAMES, f"{frame} is not a declared server frame"
        assert f"case '{frame}':" in body, f"the phone has no case for {frame}"


def test_every_opener_names_a_stage_that_owns_a_control() -> None:
    controls = _stage_controls()
    for frame, phase in _stage_openers().items():
        assert phase in controls, f"{frame} opens {phase}, which owns no control"


def test_the_table_is_applied_once_and_not_from_inside_the_switch() -> None:
    """#803 wired two of three cases by hand and the third was never written.

    One call after the switch is what makes "somebody forgot the fourth"
    impossible; a ``case`` that arms its own stage again is the old shape
    growing back.
    """
    body = _handle_message_body()

    assert body.count("enterStageFor(msg)") == 1
    assert body.index("enterStageFor(msg)") > body.rindex("break;"), (
        "the table has to be applied after the switch, so a frame added later "
        "gets it for free"
    )
    assert "setResetStage(" not in body, (
        "a per-case setResetStage is the drift this table exists to remove"
    )


def test_the_snapshot_half_still_reads_its_own_table() -> None:
    """The other half of the parity. If this line goes, a reconnecting phone
    stops arming and the two halves have drifted the other way."""
    source = _without_comments(_CORE.read_text("utf-8"))
    body = source.split("function handleGameState(msg)", 1)[1][:2500]

    assert (
        "setResetStage(STAGE_RESET_AFFORDANCES[msg.phase] ? msg.phase : null)" in body
    )


# ---------------------------------------------------------------------------
# …and what the phone actually does
# ---------------------------------------------------------------------------


_SCRIPT = """
require({stub});

var CLASSES = {classes};
Object.keys(CLASSES).forEach(function (id) {{
    var el = QZ.el(id);
    CLASSES[id].forEach(function (c) {{ el.classList.add(c); }});
}});

// The affordance waits 60 real seconds; the test owns the clock.
var timers = [];
function setTimeout(fn, ms) {{
    timers.push({{ fn: fn, ms: ms, live: true }});
    return timers.length;
}}
function clearTimeout(handle) {{ if (handle) timers[handle - 1].live = false; }}
function tick() {{
    timers.slice().forEach(function (t) {{
        if (!t.live) return;
        t.live = false;
        t.fn();
    }});
}}

var state = {{ isAdmin: false }};

{affordance}

function visible(wrapper) {{
    var w = document.getElementById(wrapper);
    var b = document.getElementById(wrapper.replace('-controls', '-btn'));
    return {{
        wrapper: !w.classList.contains('hidden'),
        button: !b.classList.contains('hidden')
    }};
}}

function reset() {{
    timers = [];
    state.isAdmin = false;
    Object.keys(CLASSES).forEach(function (id) {{
        var el = QZ.el(id);
        el.classList.remove('hidden');
        CLASSES[id].forEach(function (c) {{ el.classList.add(c); }});
        el.disabled = false;
    }});
    _lastRoster = [];
    _hostSeenInRoster = false;
    _hostConnectedFlag = null;
    _resetStage = null;
    Object.keys(_resetAffordanceTimers).forEach(function (k) {{
        _resetAffordanceTimers[k] = null;
    }});
}}

// Every control this table knows about, so "nothing else moved" is checkable.
function snapshotOfEveryControl() {{
    var seen = {{}};
    Object.keys(STAGE_RESET_AFFORDANCES).forEach(function (phase) {{
        var wrapper = STAGE_RESET_AFFORDANCES[phase][1];
        seen[wrapper] = visible(wrapper);
    }});
    return seen;
}}

var out = {{ parity: {{}}, hostHere: {{}} }};

// The live test's exact shape: an admin-only host at /quizify/admin closes
// their tab, the server says so, and the phone is on the screen it reached by
// playing the round rather than by reloading into it.
reset();
_rememberHostFlag({{ host_connected: false }});
enterStageFor({{ type: 'round_summary', round: 4, total_rounds: 5 }});
out.revealBeforeTimer = visible('reveal-reset-controls');
tick();
out.revealLiveHostGone = visible('reveal-reset-controls');

// The two routes into every stage, compared control by control.
Object.keys(STAGE_ENTERED_BY).forEach(function (frame) {{
    var phase = STAGE_ENTERED_BY[frame];

    reset();
    _rememberHostFlag({{ host_connected: false }});
    enterStageFor({{ type: frame }});
    tick();
    var live = snapshotOfEveryControl();

    // …and the same room reached by a reconnect: one game_state snapshot,
    // handled by the line in handleGameState.
    reset();
    _rememberHostFlag({{ host_connected: false }});
    setResetStage(STAGE_RESET_AFFORDANCES[phase] ? phase : null);
    tick();
    var snapshot = snapshotOfEveryControl();

    out.parity[frame] = {{ live: live, snapshot: snapshot }};

    // A host who is still there gets no hatch on either route.
    reset();
    _rememberHostFlag({{ host_connected: true }});
    enterStageFor({{ type: frame }});
    tick();
    out.hostHere[frame] = snapshotOfEveryControl();
}});

// A frame that opens no stage leaves the current one alone: `timer_tick` and
// `answer_progress` arrive in their dozens while the reveal is on screen.
reset();
_rememberHostFlag({{ host_connected: false }});
enterStageFor({{ type: 'round_summary' }});
enterStageFor({{ type: 'timer_tick', remaining: 3 }});
enterStageFor({{ type: 'answer_progress' }});
tick();
out.revealSurvivesNoise = visible('reveal-reset-controls');

// The host's own phone has Next Round on this screen; a reset beside it is a
// misfire waiting to happen.
reset();
state.isAdmin = true;
_rememberHostFlag({{ host_connected: false }});
enterStageFor({{ type: 'round_summary' }});
tick();
out.revealAsAdmin = visible('reveal-reset-controls');

// #834: silence is not a death. A server too old to send the flag, with a
// host who never joined as a player, must still produce nothing.
reset();
enterStageFor({{ type: 'round_summary' }});
tick();
out.revealHostUnknown = visible('reveal-reset-controls');

console.log(JSON.stringify(out));
"""


def _run() -> dict:
    source = _CORE.read_text("utf-8")
    start = source.index("    var RESET_AFFORDANCE_DELAY_MS")
    end = source.index("    function setupResetAffordance()")
    ids = tuple(
        sorted({i for pair in _stage_controls().values() for i in pair})
    )
    script = _SCRIPT.format(
        stub=json.dumps(str(_STUB)),
        classes=json.dumps(_element_classes(ids)),
        affordance=source[start:end],
    )
    out = subprocess.run(
        ["node", "-e", script], capture_output=True, text=True, check=True
    )
    return json.loads(out.stdout)


@_NEEDS_NODE
def test_the_ordinary_reveal_arms_when_the_host_leaves() -> None:
    """The live-test reading, reproduced: host gone, phone on the reveal it
    played its way into, 60 seconds later there is a way out."""
    result = _run()

    assert result["revealLiveHostGone"] == {"wrapper": True, "button": True}


@_NEEDS_NODE
def test_it_still_waits_the_grace_window() -> None:
    """A reset button that appears the instant a host's Wi-Fi wobbles invites
    a guest to wipe a game that was coming back."""
    result = _run()

    assert result["revealBeforeTimer"] == {"wrapper": False, "button": False}


@_NEEDS_NODE
def test_both_routes_into_a_stage_leave_the_phone_identical() -> None:
    """The invariant, measured rather than read: for every stage, the phone
    that lived through the round and the phone that reconnected into it show
    the same controls."""
    result = _run()

    assert result["parity"], "no stages were compared"
    for frame, both in result["parity"].items():
        assert both["live"] == both["snapshot"], frame


@_NEEDS_NODE
def test_a_present_host_never_produces_a_hatch_on_any_stage() -> None:
    result = _run()

    for frame, controls in result["hostHere"].items():
        for wrapper, shown in controls.items():
            assert shown == {"wrapper": False, "button": False}, (frame, wrapper)


@_NEEDS_NODE
def test_the_frames_that_open_nothing_disturb_nothing() -> None:
    result = _run()

    assert result["revealSurvivesNoise"] == {"wrapper": True, "button": True}


@_NEEDS_NODE
def test_the_hosts_own_phone_is_left_alone() -> None:
    result = _run()

    assert result["revealAsAdmin"] == {"wrapper": False, "button": False}


@_NEEDS_NODE
def test_an_unknown_host_arms_nothing() -> None:
    """#834's rule survives the new entry point: three answers, not two."""
    result = _run()

    assert result["revealHostUnknown"] == {"wrapper": False, "button": False}
