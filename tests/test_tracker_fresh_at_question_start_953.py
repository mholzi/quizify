"""A new question opens with nobody marked as answered (#953).

Found in the v1.19.0-RC1 live test: after the Lightning recap, round 7 opened on
both phones with Sofa and Couch already carrying ``is-submitted``, while the
admin page and the television showed no counter. The first guess corrected it.

The phone's tracker is drawn only by ``answer_progress``, and the server sends
none when a question starts — the television clears its own counter on
``question_started`` (#619), the phone never did. So the row kept the last
frame it was given. Before a Lightning Round that frame is the previous
round's "everyone answered"; the Lightning Round sends no progress at all, so
nothing overwrote it on the way back.

These tests run the real ``handleQuestionStarted`` / ``handleGameState`` out of
``player-core.js`` with the real ``player-game.js`` and ``player-team.js``
against the DOM stub, and count the marks in ``#submitted-players``.
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

#: Same slice as test_reload_restores_the_question_screen_870.
_CORE_SLICE = (
    "    function handleGameState(msg) {",
    "    function handleRoundSummary(msg) {",
)


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
        'answers-container', 'timer', 'timer-sr-announce', 'leaderboard-list']);

var NOW = 1700000000000;
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
QZ.load({player_team});

var game = window.QuizifyPlayerGame;
var pu = window.QuizifyPlayerUtils;
var state = pu.state;
var lightning = null;
var team = null;
var hotSeat = null;
var lobby = {{ handlePlayerJoined: function () {{}}, renderLobby: function () {{}} }};
var reveal = {{ updateRevealView: function () {{}} }};
var myPowerUp = null;
var currentQuestion = null;
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

{core}

function read() {{
    var row = document.getElementById('submitted-players').innerHTML;
    return {{
        chips: (row.match(/class="player-indicator/g) || []).length,
        answered: (row.match(/is-submitted/g) || []).length,
        me: (row.match(/is-current-player/g) || []).length,
        allSubmitted: document.getElementById('submission-tracker').classList.contains('all-submitted')
    }};
}}

function progress(sofa, couch) {{
    return [
        {{ entrant_id: 't-sofa', name: 'Sofa', submitted: sofa, connected: true }},
        {{ entrant_id: 't-couch', name: 'Couch', submitted: couch, connected: true }}
    ];
}}

(async function () {{
    await window.QuizifyI18n.init('en');
    state.playerName = 'Anna';
    window.QuizifyPlayerTeam.handleTeamsUpdate({{ teams: [
        {{ team_id: 't-sofa', name: 'Sofa', members: ['Anna', 'Bert'] }},
        {{ team_id: 't-couch', name: 'Couch', members: ['Cleo'] }}
    ] }});

    var out = {{}};

    // Round 6 ends with both teams in. Then the Lightning Round and its
    // recap: no answer_progress on the way.
    game.renderSubmissionTracker(progress(true, true));
    out.endOfRound6 = read();

    // The host taps Continue: round 7, an estimate question.
    handleQuestionStarted({{
        question_text: "In which year was Bram Stoker's 'Dracula' first published?",
        answers: [], question_type: 'estimate', timer_duration: 30,
        round_num: 7, total_rounds: 10
    }});
    out.round7Start = read();

    // Cleo's guess: the first frame of the round.
    game.renderSubmissionTracker(progress(false, true));
    out.afterCleo = read();

    // A reload mid-round still gets the real answered row from the snapshot
    // (#870) — the reset runs inside handleQuestionStarted, before the repaint.
    handleGameState({{
        phase: 'QUESTION_ACTIVE', round: 7, total_rounds: 10,
        question: {{ text: 'Dracula', answers: [], time_limit: 30, time_remaining: 18,
                     question_type: 'estimate' }},
        players: progress(false, true),
        leaderboard: []
    }});
    out.reload = read();

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
        player_team=json.dumps(str(_JS / "player-team.js")),
        core=_slice(_CORE, *_CORE_SLICE),
    )
    out = subprocess.run(
        ["node", "-e", script], capture_output=True, text=True, check=True
    )
    return json.loads(out.stdout.strip().splitlines()[-1])


@pytest.fixture(scope="module")
def result() -> dict:
    return _run()


@_NEEDS_NODE
def test_the_premise_the_row_ended_the_last_round_full(result: dict) -> None:
    """Guards the test: a row that was never lit proves nothing when it is
    found unlit."""
    assert result["endOfRound6"]["answered"] == 2
    assert result["endOfRound6"]["allSubmitted"] is True


@_NEEDS_NODE
def test_a_new_question_opens_with_nobody_answered(result: dict) -> None:
    """The reported DOM: both indicators carrying is-submitted at round 7."""
    start = result["round7Start"]
    assert start["answered"] == 0, (
        "the new question opened with the previous frame's answered marks"
    )
    assert start["allSubmitted"] is False


@_NEEDS_NODE
def test_the_row_itself_survives_the_reset(result: dict) -> None:
    """Clearing the marks must not empty the room or lose the "you" mark."""
    start = result["round7Start"]
    assert start["chips"] == 2
    assert start["me"] == 1


@_NEEDS_NODE
def test_the_first_answer_of_the_round_still_lights_its_row(result: dict) -> None:
    assert result["afterCleo"]["answered"] == 1


@_NEEDS_NODE
def test_a_reload_still_gets_the_real_row_back(result: dict) -> None:
    """#870, which a reset in the wrong place would undo."""
    assert result["reload"]["answered"] == 1
    assert result["reload"]["chips"] == 2
