"""Every screen that waits for a host tap needs a way out (#803).

The server's grace pause returns without acting unless the phase is
QUESTION_ACTIVE — ``phase_controller.pause()`` refuses everything else — so the
phone-side escape hatch is the only recovery for a host device that dies
between rounds. #299 built it twice: on the paused view, and on the normal
reveal (``maybeArmRevealReset`` → ``#reveal-reset-controls``).

Two more waiting screens have been added since, and both inherited the hole.
The lightning recap and the Hot Seat reveal are each left by ``next_question``
and by nothing else (``websocket.py`` handles both phases in ``_advance_round``),
and neither had a reset control nor anything to arm one. If the host's phone
dies during the lightning recap — or the host IS the seat holder and drops,
where the clock settles the stake per #653 and stops at HOT_SEAT_REVEAL — every
guest sits on a results screen forever. The server would have accepted
``reset_game`` from any of them (``_is_reset_authorized``); no view offered it.

#299 named "paused-view (and reveal)". This puts every waiting stage in one
table so the next one added does not inherit the hole a third time.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parent.parent
_CC = _REPO / "custom_components" / "quizify"
_WWW = _CC / "www"
_JS = _WWW / "js"
_CORE = _JS / "player-core.js"
_HTML = _WWW / "player.html"
_STUB = Path(__file__).resolve().parent / "fixtures" / "dom_stub.js"

_NEEDS_NODE = pytest.mark.skipif(
    shutil.which("node") is None, reason="node not installed"
)

#: The stages, and the control each one owns. Kept here as the human-readable
#: half of STAGE_RESET_AFFORDANCES; the test below reads the real table too, so
#: a stage added there without markup fails rather than passing quietly.
STAGES = {
    "ANSWER_REVEAL": ("reveal-reset-btn", "reveal-reset-controls"),
    "REVEAL": ("reveal-reset-btn", "reveal-reset-controls"),
    "LIGHTNING_RECAP": ("lightning-recap-reset-btn", "lightning-recap-reset-controls"),
    "HOT_SEAT_REVEAL": ("hotseat-reset-btn", "hotseat-reset-controls"),
}


def _without_comments(source: str) -> str:
    source = re.sub(r"/\*.*?\*/", "", source, flags=re.S)
    return re.sub(r"^\s*//.*$", "", source, flags=re.M)


def _element_classes() -> dict[str, list[str]]:
    """The real class list of every id the affordance touches.

    Taken from player.html rather than invented, because the arm/disarm dance
    turns on it: ``armResetAffordance`` refuses to arm a button that is already
    visible, and the wrapper's ``hidden`` is what keeps an empty bordered slot
    off the screen for the 60 seconds before the timer fires.
    """
    html = _HTML.read_text("utf-8")
    out: dict[str, list[str]] = {}
    for ids in STAGES.values():
        for element_id in ids:
            m = re.search(rf'id="{element_id}" class="([^"]*)"', html)
            assert m, f"#{element_id} is missing from player.html"
            out[element_id] = m.group(1).split()
    return out


# ---------------------------------------------------------------------------
# The markup exists
# ---------------------------------------------------------------------------


def test_every_waiting_stage_has_a_reset_control() -> None:
    classes = _element_classes()

    for phase, (btn, wrapper) in STAGES.items():
        assert "hidden" in classes[wrapper], (
            f"{wrapper} must start hidden — the slot is empty until the timer "
            f"fires, and an empty bordered box on the {phase} screen for 60s "
            "is worse than no box"
        )
        assert "hidden" in classes[btn], (
            f"{btn} must ship hidden — armResetAffordance refuses to arm a "
            "button that is already visible, so a control without it can only "
            "ever be armed on a phone that happened to pass through another "
            "phase first"
        )


def test_the_two_new_controls_reuse_the_existing_string() -> None:
    """No new key for a button that says exactly what the other two say."""
    html = _HTML.read_text("utf-8")

    for btn in ("lightning-recap-reset-btn", "hotseat-reset-btn"):
        line = next(line for line in html.splitlines() if f'id="{btn}"' in line)
        assert 'data-i18n="admin.resetGame"' in line


def test_the_hot_seat_control_is_not_inside_the_panel_that_tears_itself_down() -> None:
    """``handleResult`` calls ``reset()``, which hides #hotseat-panel — the
    reveal owns the outcome. A hatch inside the panel would be hidden at the
    exact moment it is needed."""
    html = _HTML.read_text("utf-8")
    panel_start = html.index('id="hotseat-panel"')
    panel_end = html.index("</section>", panel_start)

    assert html.index('id="hotseat-reset-controls"') > panel_end


def test_every_control_is_wired_to_send_reset_game() -> None:
    source = _without_comments(_CORE.read_text("utf-8"))
    body = source.split("function setupResetAffordance()", 1)[1].split("\n    }", 1)[0]

    for _phase, (btn, _wrapper) in STAGES.items():
        assert f"'{btn}'" in body
    assert "'paused-reset-btn'" in body
    assert "send('reset_game', {})" in body


def test_the_table_and_the_markup_agree() -> None:
    """Reads the real table, so a stage added to the code without a control in
    the page is a failure here rather than a dead button at midnight."""
    source = _CORE.read_text("utf-8")
    table = source.split("var STAGE_RESET_AFFORDANCES = {", 1)[1].split("};", 1)[0]
    html = _HTML.read_text("utf-8")

    found = re.findall(r"'([A-Z_]+)': \['([a-z-]+)', '([a-z-]+)'\]", table)
    assert {phase for phase, _btn, _wrapper in found} == set(STAGES), found
    for phase, (btn, wrapper) in STAGES.items():
        assert f"'{btn}', '{wrapper}'" in table, phase
        assert f'id="{btn}"' in html
        assert f'id="{wrapper}"' in html


# ---------------------------------------------------------------------------
# It is armed from the frames that actually arrive
# ---------------------------------------------------------------------------


def test_the_snapshot_path_arms_whichever_stage_it_lands_on() -> None:
    """One line covering all four phases, rather than a per-case call that the
    next phase would forget."""
    source = _without_comments(_CORE.read_text("utf-8"))
    body = source.split("function handleGameState(msg)", 1)[1][:2500]

    assert "_rememberRoster(msg)" in body
    assert (
        "setResetStage(STAGE_RESET_AFFORDANCES[msg.phase] ? msg.phase : null)" in body
    )


def test_the_live_events_arm_their_own_stage() -> None:
    """Neither `lightning_recap` nor `hot_seat_result` carries a phase or a
    roster, and neither is followed by anything while the room waits — so the
    handler has to say which stage it just opened."""
    source = _without_comments(_CORE.read_text("utf-8"))

    recap = source.split("case 'lightning_recap':", 1)[1].split("break;", 1)[0]
    assert "setResetStage('LIGHTNING_RECAP')" in recap

    result = source.split("case 'hot_seat_result':", 1)[1].split("break;", 1)[0]
    assert "setResetStage('HOT_SEAT_REVEAL')" in result


def test_the_roster_frame_re_decides_it() -> None:
    """`player_left` is the frame that says the host's phone just died, and on
    a waiting screen nothing follows it. Without this, only a host who was
    ALREADY gone when the stage opened would ever be noticed."""
    source = _without_comments(_CORE.read_text("utf-8"))
    body = source.split("case 'player_left':", 1)[1].split("break;", 1)[0]

    assert "_rememberRoster(msg)" in body
    assert "refreshStageReset()" in body


# ---------------------------------------------------------------------------
# … and what it actually does
# ---------------------------------------------------------------------------


_SCRIPT = """
require({stub});

var CLASSES = {classes};
Object.keys(CLASSES).forEach(function (id) {{
    var el = QZ.el(id);
    CLASSES[id].forEach(function (c) {{ el.classList.add(c); }});
}});

// Timers under the test's control: the affordance waits 60 real seconds.
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

var HOST_HERE = [
    {{ name: 'Host', is_admin: true, connected: true }}, {{ name: 'Anna' }}
];
var HOST_GONE = [
    {{ name: 'Host', is_admin: true, connected: false }}, {{ name: 'Anna' }}
];

function visible(wrapper) {{
    var w = document.getElementById(wrapper);
    var b = document.getElementById(wrapper.replace('-controls', '-btn'));
    return {{
        wrapper: !w.classList.contains('hidden'),
        button: !b.classList.contains('hidden')
    }};
}}

function reset(isAdmin) {{
    timers = [];
    state.isAdmin = !!isAdmin;
    Object.keys(CLASSES).forEach(function (id) {{
        var el = QZ.el(id);
        el.classList.remove('hidden');
        CLASSES[id].forEach(function (c) {{ el.classList.add(c); }});
        el.disabled = false;
    }});
    _lastRoster = [];
    _resetStage = null;
    Object.keys(_resetAffordanceTimers).forEach(function (k) {{
        _resetAffordanceTimers[k] = null;
    }});
}}

var out = {{}};

// 1. The host was already gone when the recap opened.
reset();
_rememberRoster({{ players: HOST_GONE }});
setResetStage('LIGHTNING_RECAP');
out.recapBeforeTimer = visible('lightning-recap-reset-controls');
tick();
out.recapHostGone = visible('lightning-recap-reset-controls');

// 2. The host is fine. Nothing appears, however long the room waits.
reset();
_rememberRoster({{ players: HOST_HERE }});
setResetStage('LIGHTNING_RECAP');
tick();
out.recapHostHere = visible('lightning-recap-reset-controls');

// 3. The host dies DURING the recap — one player_left frame, nothing else.
reset();
_rememberRoster({{ players: HOST_HERE }});
setResetStage('LIGHTNING_RECAP');
_rememberRoster({{ players: HOST_GONE }});
refreshStageReset();
tick();
out.recapHostDiesDuring = visible('lightning-recap-reset-controls');

// 4. The Hot Seat reveal, reached by its live event with no roster of its own.
reset();
_rememberRoster({{ players: HOST_GONE }});
setResetStage('HOT_SEAT_REVEAL');
tick();
out.hotSeatHostGone = visible('hotseat-reset-controls');

// 5. The host's own phone. They have Next Round; a reset next to it is a trap.
reset(true);
_rememberRoster({{ players: HOST_GONE }});
setResetStage('HOT_SEAT_REVEAL');
tick();
out.hotSeatAsAdmin = visible('hotseat-reset-controls');

// 6. Leaving the stage takes the hatch with it, armed or not.
reset();
_rememberRoster({{ players: HOST_GONE }});
setResetStage('LIGHTNING_RECAP');
tick();
out.beforeLeaving = visible('lightning-recap-reset-controls');
setResetStage(null);
out.afterLeaving = visible('lightning-recap-reset-controls');

// …and a hatch that had not fired yet must not fire on the next screen.
reset();
_rememberRoster({{ players: HOST_GONE }});
setResetStage('HOT_SEAT_REVEAL');
setResetStage(null);
tick();
out.armedThenLeft = visible('hotseat-reset-controls');

// 7. The reveal still behaves the way #299 built it.
reset();
_rememberRoster({{ players: HOST_GONE }});
setResetStage('ANSWER_REVEAL');
tick();
out.revealHostGone = visible('reveal-reset-controls');

// 8. ANSWER_REVEAL and REVEAL are two names for one control: entering under
//    the second name must not disarm what the first one armed.
reset();
_rememberRoster({{ players: HOST_GONE }});
setResetStage('ANSWER_REVEAL');
tick();
setResetStage('REVEAL');
out.revealAlias = visible('reveal-reset-controls');

// 9. Only one stage is ever offered at a time.
reset();
_rememberRoster({{ players: HOST_GONE }});
setResetStage('LIGHTNING_RECAP');
tick();
setResetStage('HOT_SEAT_REVEAL');
tick();
out.recapAfterMovingOn = visible('lightning-recap-reset-controls');
out.hotSeatAfterMovingOn = visible('hotseat-reset-controls');

console.log(JSON.stringify(out));
"""


def _run() -> dict:
    source = _CORE.read_text("utf-8")
    start = source.index("    var RESET_AFFORDANCE_DELAY_MS")
    end = source.index("    function setupResetAffordance()")
    script = _SCRIPT.format(
        stub=json.dumps(str(_STUB)),
        classes=json.dumps(_element_classes()),
        affordance=source[start:end],
    )
    out = subprocess.run(
        ["node", "-e", script], capture_output=True, text=True, check=True
    )
    return json.loads(out.stdout)


@_NEEDS_NODE
def test_the_lightning_recap_offers_a_way_out_when_the_host_is_gone() -> None:
    result = _run()

    assert result["recapHostGone"] == {"wrapper": True, "button": True}


@_NEEDS_NODE
def test_it_waits_the_grace_window_first() -> None:
    """A reset button that appears the instant a host's Wi-Fi wobbles is worse
    than none: it invites a guest to wipe a game that was coming back."""
    result = _run()

    assert result["recapBeforeTimer"] == {"wrapper": False, "button": False}


@_NEEDS_NODE
def test_a_healthy_host_never_produces_one() -> None:
    result = _run()

    assert result["recapHostHere"] == {"wrapper": False, "button": False}


@_NEEDS_NODE
def test_a_host_who_dies_during_the_recap_is_caught_too() -> None:
    """The likelier of the two shapes: the room reaches the recap fine and the
    host's phone gives up while everybody reads the standings."""
    result = _run()

    assert result["recapHostDiesDuring"] == {"wrapper": True, "button": True}


@_NEEDS_NODE
def test_the_hot_seat_reveal_offers_one_as_well() -> None:
    """The stranding case from the issue: the host was the seat holder, their
    phone died, the clock settled the stake (#653) and the game stopped."""
    result = _run()

    assert result["hotSeatHostGone"] == {"wrapper": True, "button": True}


@_NEEDS_NODE
def test_the_host_s_own_phone_is_left_alone() -> None:
    result = _run()

    assert result["hotSeatAsAdmin"] == {"wrapper": False, "button": False}


@_NEEDS_NODE
def test_leaving_the_stage_takes_the_hatch_with_it() -> None:
    """The host came back and advanced the game. A reset button still sitting
    on the next screen is a live wire."""
    result = _run()

    assert result["beforeLeaving"] == {"wrapper": True, "button": True}
    assert result["afterLeaving"] == {"wrapper": False, "button": False}


@_NEEDS_NODE
def test_a_pending_hatch_does_not_surface_on_the_next_screen() -> None:
    """The timer is 60 seconds long; most of the time the game moves on before
    it fires. It has to be cancelled, not merely hidden."""
    result = _run()

    assert result["armedThenLeft"] == {"wrapper": False, "button": False}


@_NEEDS_NODE
def test_the_reveal_still_works_the_way_299_built_it() -> None:
    result = _run()

    assert result["revealHostGone"] == {"wrapper": True, "button": True}


@_NEEDS_NODE
def test_the_reveal_s_two_phase_names_do_not_cancel_each_other() -> None:
    """ANSWER_REVEAL and REVEAL share one control. Disarming "every stage but
    the current one" by phase would have the alias switch it off."""
    result = _run()

    assert result["revealAlias"] == {"wrapper": True, "button": True}


@_NEEDS_NODE
def test_only_the_stage_on_screen_offers_a_reset() -> None:
    result = _run()

    assert result["recapAfterMovingOn"] == {"wrapper": False, "button": False}
    assert result["hotSeatAfterMovingOn"] == {"wrapper": True, "button": True}


# ---------------------------------------------------------------------------
# The server end of the same statement
# ---------------------------------------------------------------------------


def test_the_server_would_have_accepted_the_reset_all_along() -> None:
    """The hole was only ever on the phone: ``_is_reset_authorized`` lets any
    connected client reset once no connected admin holds the crown, which is
    exactly the state these two screens were stuck in."""
    source = (_CC / "server" / "websocket.py").read_text("utf-8")
    body = source.split("def _is_reset_authorized(", 1)[1]
    body = body.split("\n    async def", 1)[0]

    assert "return admin is None or not admin.connected" in body


def test_nothing_else_can_leave_these_two_phases() -> None:
    """Which is why they need the hatch: both are advanced by the host's
    next_question and by no timer."""
    source = (_CC / "server" / "websocket.py").read_text("utf-8")

    assert "if game_state.phase == GamePhase.LIGHTNING_RECAP:" in source
    assert "if game_state.phase == GamePhase.HOT_SEAT_REVEAL:" in source
