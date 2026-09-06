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
guest reset while a live ``?role=admin`` socket exists). The phone now gets
that same answer from the server (#842, ``host_presence`` / ``host_connected``)
and reads it first; where a server does not send it, the roster reading is the
fallback, and it grew a third answer — "unknown" — so a roster that names no
host at all no longer reads as a death.

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
    _hostConnectedFlag = null;
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

// ---- #842: the server now says it outright. -------------------------------

// The live test's own room: an admin-only host, so no roster all evening
// names one — and the tab is closed, which nothing but the flag can say.
reset();
_rememberRoster({{ players: GUESTS_ONLY }});
_rememberHostFlag({{ host_connected: false }});
setResetStage('LIGHTNING_RECAP');
tick();
out.flagSaysGoneNoHostRow = visible('lightning-recap-reset-controls');

// The same room with the host at the admin page. This is #834's screenshot.
reset();
_rememberRoster({{ players: GUESTS_ONLY }});
_rememberHostFlag({{ host_connected: true }});
setResetStage('HOT_SEAT_REVEAL');
tick();
out.flagSaysHereNoHostRow = visible('hotseat-reset-controls');

// The host closes the tab while the recap is on screen: one frame, nothing
// else, exactly as an admin-only host's departure arrives.
reset();
_rememberRoster({{ players: GUESTS_ONLY }});
_rememberHostFlag({{ host_connected: true }});
setResetStage('LIGHTNING_RECAP');
tick();
out.beforeHostClosedTheTab = visible('lightning-recap-reset-controls');
_rememberHostFlag({{ host_connected: false }});
refreshStageReset();
tick();
out.afterHostClosedTheTab = visible('lightning-recap-reset-controls');

// …and comes back before the timer fires. The hatch never appears.
reset();
_rememberRoster({{ players: GUESTS_ONLY }});
_rememberHostFlag({{ host_connected: false }});
setResetStage('LIGHTNING_RECAP');
_rememberHostFlag({{ host_connected: true }});
refreshStageReset();
tick();
out.hostCameBack = visible('lightning-recap-reset-controls');

// The flag outranks the roster: an admin tab is live, so the server would
// refuse the reset however dead the host's PLAYER row looks.
reset();
_rememberRoster({{ players: HOST_GONE }});
_rememberHostFlag({{ host_connected: true }});
setResetStage('LIGHTNING_RECAP');
tick();
out.flagOutranksTheRoster = visible('lightning-recap-reset-controls');

// A frame without the key changes nothing — the leaderboard-refresh
// game_state is built by another builder and carries no host key.
reset();
_rememberRoster({{ players: GUESTS_ONLY }});
_rememberHostFlag({{ host_connected: false }});
_rememberHostFlag({{ phase: 'LIGHTNING_RECAP' }});
setResetStage('LIGHTNING_RECAP');
tick();
out.frameWithoutTheKey = visible('lightning-recap-reset-controls');

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


@_NEEDS_NODE
def test_the_flag_arms_the_hatch_for_a_host_who_was_never_a_player() -> None:
    """#842, and the capability #834 alone would have cost: the live test's own
    room. An admin-only host closed the tab on the Hot Seat result and a guest
    pulled the whole room out — nothing in any roster could say so."""
    result = _hatch()

    assert result["flagSaysGoneNoHostRow"] is True
    assert result["beforeHostClosedTheTab"] is False
    assert result["afterHostClosedTheTab"] is True


@_NEEDS_NODE
def test_the_flag_keeps_it_hidden_while_that_same_host_is_present() -> None:
    """The #834 screenshot, decided by the server instead of guessed at."""
    result = _hatch()

    assert result["flagSaysHereNoHostRow"] is False
    assert result["hostCameBack"] is False


@_NEEDS_NODE
def test_the_flag_outranks_the_roster() -> None:
    """`not host_connected` is exactly `_is_reset_authorized`'s guest clause, so
    a phone that armed against a live admin socket would offer a button the
    server refuses."""
    assert _hatch()["flagOutranksTheRoster"] is False


@_NEEDS_NODE
def test_a_frame_without_the_key_does_not_wipe_the_answer() -> None:
    """The leaderboard-refresh `game_state` (#221) comes from a different
    builder and carries no host key at all."""
    assert _hatch()["frameWithoutTheKey"] is True


def test_the_roster_reading_stays_as_the_fallback() -> None:
    """A phone on this version against a server on the last one still behaves
    the way it did — every roster case above runs with no flag set."""
    source = _without_comments(_CORE.read_text("utf-8"))

    assert "if (_hostConnectedFlag !== null) {" in source
    assert "typeof msg.host_connected === 'boolean'" in source


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
var _hostConnectedFlag = true;
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
    hostSeen: _hostSeenInRoster,
    hostFlag: _hostConnectedFlag
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
    assert result["hostFlag"] is None
    assert result["announcementsCleared"] == 1


def test_the_phone_re_decides_the_hatch_on_the_host_presence_frame() -> None:
    """The frame is the whole point of #842: it is the only thing that ever
    reports an admin-only host arriving or leaving."""
    source = _without_comments(_CORE.read_text("utf-8"))
    body = source.split("case 'host_presence':", 1)[1].split("break;", 1)[0]

    assert "_rememberHostFlag(msg)" in body
    assert "refreshStageReset()" in body


def test_the_snapshot_carries_the_flag_to_a_phone_that_missed_the_frame() -> None:
    source = _without_comments(_CORE.read_text("utf-8"))
    body = source.split("function handleGameState(msg)", 1)[1][:2500]

    assert "_rememberHostFlag(msg)" in body


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
