"""Three things the v1.16.0-RC1 live test found on the phones themselves.

**#834 — the host-gone hatch armed while the host was hosting.** #803 gave the
lightning recap and the Hot Seat result the #299/#207 escape hatch, and the
live test proved it: with the host's tab genuinely closed, a guest tapped Reset
game and the whole room came back. It also armed with the host sitting there
looking at the same recap. ``_hostIsConnected()`` asked the last roster for a
connected ``is_admin`` row and read "no" as "the host is dead" — but a host who
runs the evening from ``/quizify/admin`` without joining as a player is in no
roster at all, so every roster all evening answered "no". That is the same
blind spot #726 closed on the server (``_is_reset_authorized`` now refuses a
guest reset while a live ``?role=admin`` socket exists); the phone has no
equivalent signal, so the honest reading is a third answer, "unknown", and
unknown must not arm anything.

**#838 — the reaction bar rode the reset onto the join screen.** ``#reaction-bar``,
``#admin-control-bar`` and the toast are fixed to the page, not to a view, so
``showView('join-view')`` cannot take them with it. ``handleFinale`` has always
hidden the first two by hand; ``game_reset`` hid nothing, and a guest who used
the #803 hatch landed on a join screen with five live reaction buttons under
the Join button and "3 in a row!" still on screen.

**#839 — the timer's polite region was stale, and in the wrong language.**
``#timer-sr-announce`` is a sentence, not a counter: nothing overwrites it
until the timer next crosses 10s or 5s. Measured on a phone in a German game
with 43 seconds on the visible clock, the region read "5 seconds left" — the
previous game's question, in the previous game's language. It carried no
``data-i18n``, so the ``initPageTranslations`` sweep that re-renders every
other string on a language change (#809) had nothing to find.
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
_CORE = _JS / "player-core.js"
_GAME = _JS / "player-game.js"
_UTILS = _JS / "player-utils.js"
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
    out = subprocess.run(
        ["node", "-e", script], capture_output=True, text=True, check=True
    )
    return json.loads(out.stdout)


# ---------------------------------------------------------------------------
# #834 — the hatch reads the roster
# ---------------------------------------------------------------------------

#: The affordance, lifted whole out of player-core.js the way the #803 tests
#: lift it: everything from the 60s constant down to the click wiring.
_AFFORDANCE = ("    var RESET_AFFORDANCE_DELAY_MS", "    function setupResetAffordance()")

_HATCH_SCRIPT = """
require({stub});

['lightning-recap-reset-btn', 'lightning-recap-reset-controls',
 'hotseat-reset-btn', 'hotseat-reset-controls',
 'reveal-reset-btn', 'reveal-reset-controls'].forEach(function (id) {{
    QZ.el(id).classList.add('hidden');
}});

var timers = [];
function setTimeout(fn, ms) {{ timers.push({{ fn: fn, live: true }}); return timers.length; }}
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

// The room as the wire describes it. `GUESTS_ONLY` is the live-test room:
// three phones and a host who never took a player slot, so nothing in any
// roster is flagged is_admin.
var GUESTS_ONLY = [{{ name: 'Anna' }}, {{ name: 'Bea' }}, {{ name: 'Cem' }}];
var HOST_HERE = [{{ name: 'Host', is_admin: true, connected: true }}, {{ name: 'Anna' }}];
var HOST_GONE = [{{ name: 'Host', is_admin: true, connected: false }}, {{ name: 'Anna' }}];

function visible(wrapper) {{
    var w = document.getElementById(wrapper);
    var b = document.getElementById(wrapper.replace('-controls', '-btn'));
    return !w.classList.contains('hidden') && !b.classList.contains('hidden');
}}

function reset() {{
    timers = [];
    state.isAdmin = false;
    ['lightning-recap', 'hotseat', 'reveal'].forEach(function (base) {{
        QZ.el(base + '-reset-btn').classList.add('hidden');
        QZ.el(base + '-reset-controls').classList.add('hidden');
    }});
    Object.keys(_resetAffordanceTimers).forEach(function (k) {{
        _resetAffordanceTimers[k] = null;
    }});
    _lastRoster = [];
    _hostSeenInRoster = false;
    _resetStage = null;
}}

var out = {{}};

// The reported bug: the host is at the admin page, hosting, and is in no
// roster because they never joined as a player.
reset();
_rememberRoster({{ players: GUESTS_ONLY }});
setResetStage('LIGHTNING_RECAP');
tick();
out.recapHostNotAPlayer = visible('lightning-recap-reset-controls');

reset();
_rememberRoster({{ players: GUESTS_ONLY }});
setResetStage('HOT_SEAT_REVEAL');
tick();
out.hotSeatHostNotAPlayer = visible('hotseat-reset-controls');

// …and not through the side door either: a guest joining or dropping during
// the recap is another roster frame that names no host.
reset();
setResetStage('LIGHTNING_RECAP');
_rememberRoster({{ players: GUESTS_ONLY }});
refreshStageReset();
tick();
out.recapRosterChurn = visible('lightning-recap-reset-controls');

// The hatch #803 built, unbroken: the host WAS a player and their phone died.
reset();
_rememberRoster({{ players: HOST_GONE }});
setResetStage('HOT_SEAT_REVEAL');
tick();
out.hotSeatHostGone = visible('hotseat-reset-controls');

// The same, arriving as the one player_left frame that says so mid-stage.
reset();
_rememberRoster({{ players: HOST_HERE }});
setResetStage('LIGHTNING_RECAP');
tick();
out.recapWhileHostHere = visible('lightning-recap-reset-controls');
_rememberRoster({{ players: HOST_GONE }});
refreshStageReset();
tick();
out.recapAfterHostDied = visible('lightning-recap-reset-controls');

// And it survives the roster row disappearing when the grace period removes
// the dead host: absence after a sighting is still death, not ignorance.
reset();
_rememberRoster({{ players: HOST_HERE }});
setResetStage('LIGHTNING_RECAP');
_rememberRoster({{ players: HOST_GONE }});
refreshStageReset();
tick();
_rememberRoster({{ players: [{{ name: 'Anna' }}] }});
refreshStageReset();
out.recapAfterHostRemoved = visible('lightning-recap-reset-controls');

console.log(JSON.stringify(out));
"""


def _hatch() -> dict:
    return _node(
        _HATCH_SCRIPT.format(
            stub=json.dumps(str(_STUB)),
            affordance=_slice(_CORE, *_AFFORDANCE),
        )
    )


@_NEEDS_NODE
def test_a_host_who_never_joined_as_a_player_does_not_arm_the_recap_hatch() -> None:
    """The screenshot in #834: three phones, a full-width red Reset game button
    under "Hang tight — the host continues the game", and the host looking at
    the same recap in the next room."""
    assert _hatch()["recapHostNotAPlayer"] is False


@_NEEDS_NODE
def test_a_host_who_never_joined_as_a_player_does_not_arm_the_hot_seat_hatch() -> None:
    assert _hatch()["hotSeatHostNotAPlayer"] is False


@_NEEDS_NODE
def test_roster_churn_during_the_stage_does_not_arm_it_either() -> None:
    """A guest joining or dropping is a roster frame, and refreshStageReset
    re-decides on every one of them. It must reach the same answer."""
    assert _hatch()["recapRosterChurn"] is False


@_NEEDS_NODE
def test_the_hatch_still_arms_when_the_host_is_actually_gone() -> None:
    """#803's rescue, which the live test exercised end to end. Narrowing the
    arming condition must not touch this."""
    result = _hatch()

    assert result["hotSeatHostGone"] is True
    assert result["recapAfterHostDied"] is True
    assert result["recapWhileHostHere"] is False


@_NEEDS_NODE
def test_the_hatch_stays_armed_once_the_dead_host_leaves_the_roster() -> None:
    """The disconnect grace period removes the row. Read naively that is a
    roster with no host in it — the same shape as a host who was never a
    player — and the hatch would vanish at the moment it is needed."""
    assert _hatch()["recapAfterHostRemoved"] is True


def test_the_three_way_reading_is_what_the_hatch_asks_for() -> None:
    """Guards the shape, not just the behaviour: a later edit that goes back to
    a boolean would pass every case above that happens to name a host."""
    source = _without_comments(_CORE.read_text("utf-8"))

    assert "_hostIsConnected" not in source
    assert "return (named || _hostSeenInRoster) ? 'gone' : 'unknown';" in source
    assert "state.isAdmin || _hostPresence() !== 'gone'" in source


# ---------------------------------------------------------------------------
# #838 — nothing from the game survives onto the join screen
# ---------------------------------------------------------------------------


_CHROME_SCRIPT = """
require({stub});

QZ.els(['reaction-bar', 'admin-control-bar', 'error-toast']);
document.getElementById('reaction-bar').classList.remove('hidden');
document.getElementById('admin-control-bar').classList.remove('hidden');

window.QuizifyClientCore = {{
    saveSession: function () {{}},
    getSession: function () {{ return {{}}; }},
    clearSession: function () {{}}
}};
QZ.load({utils});
var pu = window.QuizifyPlayerUtils;

var cleared = 0;
var game = {{ clearTimeAnnouncement: function () {{ cleared++; }} }};

var _lastRoster = [{{ name: 'Host', is_admin: true, connected: false }}];
var _hostSeenInRoster = true;
var stages = [];
function setResetStage(stage) {{ stages.push(stage); }}

{chrome}

pu.showToast('3 in a row!', 2500);
var toast = document.getElementById('error-toast');
var before = {{
    reactionBar: !document.getElementById('reaction-bar').classList.contains('hidden'),
    toastText: toast.textContent,
    toastOpacity: toast.style.opacity
}};

clearGameChrome();

console.log(JSON.stringify({{
    before: before,
    reactionBar: !document.getElementById('reaction-bar').classList.contains('hidden'),
    adminBar: !document.getElementById('admin-control-bar').classList.contains('hidden'),
    toastText: toast.textContent,
    toastOpacity: toast.style.opacity,
    announcementsCleared: cleared,
    stages: stages,
    roster: _lastRoster.length,
    hostSeen: _hostSeenInRoster
}}));
"""


def _chrome() -> dict:
    return _node(
        _CHROME_SCRIPT.format(
            stub=json.dumps(str(_STUB)),
            utils=json.dumps(str(_UTILS)),
            chrome=_slice(
                _CORE,
                "    function clearGameChrome()",
                "    // ============================================\n    // Finale",
            ),
        )
    )


@_NEEDS_NODE
def test_the_game_view_paints_the_leftovers_first() -> None:
    """Guards the guard: without a visible bar and a live toast the teardown
    below would prove nothing."""
    result = _chrome()

    assert result["before"]["reactionBar"] is True
    assert result["before"]["toastText"] == "3 in a row!"
    assert result["before"]["toastOpacity"] == "1"


@_NEEDS_NODE
def test_the_reaction_bar_and_the_admin_bar_go_with_the_game() -> None:
    """The reported symptom: five reaction buttons between Join and the
    version line, on a screen with no game to react to."""
    result = _chrome()

    assert result["reactionBar"] is False
    assert result["adminBar"] is False


@_NEEDS_NODE
def test_the_last_toast_goes_with_it() -> None:
    """Hidden AND emptied — a faded-out toast is still a line in the
    accessibility tree."""
    result = _chrome()

    assert result["toastOpacity"] == "0"
    assert result["toastText"] == ""


@_NEEDS_NODE
def test_the_wiped_game_takes_its_hatch_and_its_roster_with_it() -> None:
    """A hatch armed on the screen we just left would otherwise fire a minute
    later on the join screen, judged against a game that no longer exists."""
    result = _chrome()

    assert result["stages"] == [None]
    assert result["roster"] == 0
    assert result["hostSeen"] is False
    assert result["announcementsCleared"] == 1


def test_both_ways_out_of_a_game_run_the_teardown() -> None:
    """`game_reset` is the one in the issue; `kicked` is the same page
    furniture on the same kind of screen, and was equally uncleared."""
    source = _without_comments(_CORE.read_text("utf-8"))

    for case in ("game_reset", "kicked"):
        body = source.split(f"case '{case}':", 1)[1].split("break;", 1)[0]
        assert "clearGameChrome()" in body, case


# ---------------------------------------------------------------------------
# #839 — the timer's polite region
# ---------------------------------------------------------------------------


_ANNOUNCE_SCRIPT = """
require({stub});
QZ.serveI18n({i18n});
QZ.load({i18njs});
QZ.el('timer-sr-announce');

{announce}

function read() {{
    var region = document.getElementById('timer-sr-announce');
    return {{ text: region.textContent, key: region.getAttribute('data-i18n') }};
}}

(async function () {{
    await window.QuizifyI18n.init('en');

    // An English game reaches five seconds left.
    announceTimeLeft(43);
    announceTimeLeft(5);
    var english = read();

    // The room is German next time. Same sweep every other string gets.
    await window.QuizifyI18n.setLanguage('de');
    window.QuizifyI18n.initPageTranslations();
    var afterSwitch = read();

    // The next question starts (startCountdown's first act is stopCountdown).
    clearTimeAnnouncement();
    var afterClear = read();

    // …and the sweep must not write the sentence back into an empty region.
    window.QuizifyI18n.initPageTranslations();
    var afterClearAndSweep = read();

    // The dedupe still works after a clear: 5s of the next question announces.
    announceTimeLeft(5);
    var nextQuestion = read();

    console.log(JSON.stringify({{
        english: english,
        afterSwitch: afterSwitch,
        afterClear: afterClear,
        afterClearAndSweep: afterClearAndSweep,
        nextQuestion: nextQuestion
    }}));
}})();
"""


def _announce() -> dict:
    return _node(
        _ANNOUNCE_SCRIPT.format(
            stub=json.dumps(str(_STUB)),
            i18n=json.dumps(str(_I18N)),
            i18njs=json.dumps(str(_JS / "i18n.js")),
            announce=_slice(
                _GAME,
                "    var lastAnnouncedSecond = null;",
                "    /**\n     * Restart the timer's one-shot pulse",
            ),
        )
    )


@_NEEDS_NODE
def test_the_announcement_is_written_in_the_language_of_the_moment() -> None:
    """Guards the guard — the writer was never the broken half."""
    assert _announce()["english"]["text"] == "5 seconds left"


@_NEEDS_NODE
def test_a_language_change_re_renders_the_announcement() -> None:
    """The half of #839 that was measured live: a German game reading
    "5 seconds left" because the region carried no key for the sweep to find."""
    result = _announce()

    assert result["afterSwitch"]["key"] == "game.timerFiveLeft"
    assert result["afterSwitch"]["text"] == "Noch 5 Sekunden"


@_NEEDS_NODE
def test_a_new_question_empties_the_region() -> None:
    """The other half: 43 seconds on the visible clock and "5 seconds left" in
    the live region, left over from the question before."""
    result = _announce()

    assert result["afterClear"] == {"text": "", "key": None}
    assert result["afterClearAndSweep"] == {"text": "", "key": None}


@_NEEDS_NODE
def test_clearing_does_not_cost_the_next_question_its_announcement() -> None:
    """`lastAnnouncedSecond` dedupes repeated ticks at the same second; a clear
    that left it at 5 would silence the next question's warning entirely."""
    assert _announce()["nextQuestion"]["text"] == "Noch 5 Sekunden"


def test_every_end_of_a_question_clears_the_region() -> None:
    """stopCountdown is the reveal, the finale and the clock reaching zero —
    and startCountdown's own first statement, so it is a question starting
    too."""
    source = _without_comments(_GAME.read_text("utf-8"))
    body = source.split("function stopCountdown()", 1)[1].split("\n    }", 1)[0]

    assert "clearTimeAnnouncement()" in body
    assert source.split("function startCountdown(", 1)[1].lstrip().startswith(
        "deadline) {\n        stopCountdown();"
    )
