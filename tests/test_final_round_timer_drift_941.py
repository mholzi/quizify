"""The final round's clock starts when the server's does (#941).

On the final round ``question_started`` is held for the 3·2·1 flourish
(``playFinaleCountdown``, 700 + 3·600 = 2500 ms) before
``handleQuestionStarted`` stamped ``deadline = Date.now() + timer_duration``.
The server's clock started at send time, and its ``timer_tick`` reaches every
phone once a second. ``startCountdown``'s interval and ``updateTimer`` both
write ``#timer``, so in the closing seconds of the last question the digit
alternated between two values ~3 s apart, and the ticks and the critical pulse
followed the wrong one.

These tests run the real ``question_started`` case, ``playFinaleCountdown`` and
``handleQuestionStarted`` out of ``player-core.js`` with the real
``player-game.js``, on a fake clock, and read the deadline and the digit back.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parent.parent
_WWW = _REPO / "custom_components" / "quizify" / "www"
_JS = _WWW / "js"
_I18N = _WWW / "i18n"
_CORE = _JS / "player-core.js"
_STUB = Path(__file__).resolve().parent / "fixtures" / "dom_stub.js"

_NEEDS_NODE = pytest.mark.skipif(
    shutil.which("node") is None, reason="node not installed"
)

#: Same slice as test_reload_restores_the_question_screen_870: it holds
#: handleQuestionStarted, isFinalRound and playFinaleCountdown.
_CORE_SLICE = (
    "    function handleGameState(msg) {",
    "    function handleRoundSummary(msg) {",
)
#: The dispatcher's own case, so the stamp is tested where it is taken.
_CASE_SLICE = ("            case 'question_started':", "            case 'timer_tick':")

T0 = 1700000000000
DURATION = 30


def _slice(path: Path, start: str, end: str) -> str:
    source = path.read_text("utf-8")
    a = source.index(start)
    b = source.index(end, a)
    return source[a:b]


_SCRIPT = """
require({stub});
QZ.serveI18n({i18n});
QZ.load({i18njs});

QZ.els(['current-round', 'total-rounds', 'last-round-banner', 'submission-tracker',
        'submitted-players', 'question-text', 'question-category', 'answer-buttons',
        'answers-container', 'timer', 'timer-sr-announce', 'leaderboard-list',
        'finale-countdown-overlay', 'finale-countdown-number']);

var NOW = {t0};
Date.now = function () {{ return NOW; }};
global.setInterval = function () {{ return 1; }};
global.clearInterval = function () {{}};
window.QuizifyPlayerSound = {{
    resetTick: function () {{}}, tickFromRemaining: function () {{}}
}};

QZ.load({utils});
QZ.load({render_shared});
QZ.load({player_utils});
QZ.load({player_game});

var game = window.QuizifyPlayerGame;
var deadlines = [];
var realStart = game.startCountdown;
game.startCountdown = function (deadline) {{
    deadlines.push(deadline);
    realStart(deadline);
}};

var pu = window.QuizifyPlayerUtils;
var state = pu.state;
var lightning = null;
var team = null;
var hotSeat = null;
var lobby = {{ handlePlayerJoined: function () {{}}, renderLobby: function () {{}} }};
var reveal = {{ updateRevealView: function () {{}} }};
var myPowerUp = null;
var currentQuestion = null;
var _finaleCountdownTimers = [];
var STAGE_RESET_AFFORDANCES = {{}};
function _acquireWakeLock() {{}}
function _releaseWakeLock() {{}}
function _rememberHostFlag() {{}}
function _rememberRoster() {{}}
function _syncServerLanguage() {{ return true; }}
function updatePageTitle() {{}}
function disarmResetAffordance() {{}}
function setResetStage() {{}}
function clearStalePanelsForPhase() {{}}
function handleFinale() {{}}
function updatePausedView() {{}}
function handleWagerWindow() {{}}

{core}

function dispatch(msg) {{
    switch (msg.type) {{
{case_block}
    }}
}}

function question(round, total) {{
    return {{
        type: 'question_started', question_text: 'Last one', answers: ['a', 'b', 'c'],
        timer_duration: {duration}, round_num: round, total_rounds: total
    }};
}}

function digit() {{ return document.getElementById('timer').textContent; }}

(async function () {{
    await window.QuizifyI18n.init('en');

    // A fake clock the flourish's setTimeouts run on.
    var pending = [];
    global.setTimeout = function (fn, ms) {{
        pending.push({{ fn: fn, at: NOW + (ms || 0), live: true }});
        return pending.length;
    }};
    global.clearTimeout = function (h) {{ if (h && pending[h - 1]) pending[h - 1].live = false; }};
    function advance(ms) {{
        var until = NOW + ms;
        for (;;) {{
            var next = null;
            pending.forEach(function (p) {{
                if (p.live && p.at <= until && (!next || p.at < next.at)) next = p;
            }});
            if (!next) break;
            NOW = next.at;
            next.live = false;
            next.fn();
        }}
        NOW = until;
    }}

    var out = {{}};

    // The final round: the flourish holds the question back.
    NOW = {t0}; deadlines = []; pending = [];
    dispatch(question(10, 10));
    out.finalBeforeReveal = deadlines.length;
    advance(2500);
    out.finalRevealedAt = NOW;
    out.finalDeadline = deadlines[0];
    out.finalDigit = digit();
    // What the server's timer_tick says at the same instant.
    game.updateTimer({duration} - (NOW - {t0}) / 1000);
    out.serverDigit = digit();

    // An ordinary round has no flourish and was already right.
    NOW = {t0}; deadlines = []; pending = [];
    dispatch(question(3, 10));
    out.normalDeadline = deadlines[0];

    // A snapshot's timer_duration is what is left: no arrival stamp, now.
    NOW = {t0} + 5000; deadlines = [];
    handleQuestionStarted({{
        question_text: 'Restored', answers: ['a', 'b', 'c'],
        timer_duration: 12, round_num: 4, total_rounds: 10
    }});
    out.snapshotDeadline = deadlines[0];

    console.log(JSON.stringify(out));
}})().catch(function (e) {{ console.error(e); process.exit(1); }});
"""


def _run() -> dict:
    script = _SCRIPT.format(
        stub=json.dumps(str(_STUB)),
        i18n=json.dumps(str(_I18N)),
        i18njs=json.dumps(str(_JS / "i18n.js")),
        utils=json.dumps(str(_JS / "utils.js")),
        render_shared=json.dumps(str(_JS / "render-shared.js")),
        player_utils=json.dumps(str(_JS / "player-utils.js")),
        player_game=json.dumps(str(_JS / "player-game.js")),
        core=_slice(_CORE, *_CORE_SLICE),
        case_block=_slice(_CORE, *_CASE_SLICE),
        t0=T0,
        duration=DURATION,
    )
    out = subprocess.run(
        ["node", "-e", script], capture_output=True, text=True, check=True
    )
    return json.loads(out.stdout.strip().splitlines()[-1])


@_NEEDS_NODE
def test_the_premise_the_flourish_holds_the_question_back() -> None:
    """Guards the test itself: if the question were revealed at once there
    would be no delay for the clock to drift by."""
    result = _run()

    assert result["finalBeforeReveal"] == 0
    assert result["finalRevealedAt"] == T0 + 2500


@_NEEDS_NODE
def test_the_final_round_deadline_counts_from_the_frame_not_the_reveal() -> None:
    result = _run()

    assert result["finalDeadline"] == T0 + DURATION * 1000, (
        "the final round's countdown started after the 3·2·1 flourish, "
        f"{(result['finalDeadline'] - T0) / 1000 - DURATION:.1f} s behind the "
        "server's clock"
    )


@_NEEDS_NODE
def test_the_phone_and_the_server_write_the_same_digit() -> None:
    """The reported symptom: two writers, one element, two values."""
    result = _run()

    assert result["finalDigit"] == result["serverDigit"]


@_NEEDS_NODE
def test_an_ordinary_round_is_unchanged() -> None:
    result = _run()

    assert result["normalDeadline"] == T0 + DURATION * 1000


@_NEEDS_NODE
def test_a_restored_question_still_counts_from_now() -> None:
    """questionStartedFromSnapshot hands over time_remaining; stamping it
    with anything but the moment of the restore would shorten the round."""
    result = _run()

    assert result["snapshotDeadline"] == T0 + 5000 + 12 * 1000
