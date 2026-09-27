"""The first question after a reset shows the new game's room (#1032).

Found in the v1.21.0-RC4 live test: game 1 had Alice and Carol as a team and
Bob alone. After the host reset and all three rejoined solo, question 1 of
game 2 showed the dissolved team and Bob in ``#submitted-players`` until the
first ``answer_progress``.

#953 made a new question repaint the cached roster with the marks cleared. The
cache was only ever written by ``renderSubmissionTracker`` and never emptied, so
a phone that lived through a reset repainted the previous game's entrants.

These tests run the real ``handleGameState`` / ``handleQuestionStarted`` and the
real ``clearGameChrome`` out of ``player-core.js`` with the real
``player-game.js`` against the DOM stub.
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

_CORE_SLICES = (
    (
        "    function clearGameChrome() {",
        "    // ============================================\n    // Finale",
    ),
    ("    function handleGameState(msg) {", "    function handleRoundSummary(msg) {"),
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
function endJoinPending() {{}}
function _clearFinaleCountdown() {{}}
function handleFinale() {{}}
function updatePausedView() {{}}

{core}

function read() {{
    var row = document.getElementById('submitted-players').innerHTML;
    return {{
        chips: (row.match(/class="player-indicator/g) || []).length,
        answered: (row.match(/is-submitted/g) || []).length,
        me: (row.match(/is-current-player/g) || []).length,
        allSubmitted: document.getElementById('submission-tracker').classList.contains('all-submitted'),
        html: row
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
    state.playerName = 'Bob';
    var out = {{}};

    // Game 1 ends: Alice & Carol as a team, Bob alone, both answered.
    game.renderSubmissionTracker([
        {{ entrant_id: 't-sofa', name: 'A&B Sofa', submitted: true, connected: true }},
        {{ entrant_id: 'Bob', name: 'Bob', submitted: true, connected: true }}
    ]);
    out.endOfGame1 = read();

    // The host resets: game_reset runs clearGameChrome.
    clearGameChrome();
    out.afterReset = read();

    // Everyone rejoins; game 2 starts from the lobby, question 1 opens.
    handleGameState({{ phase: 'LOBBY', players: [] }});
    handleQuestionStarted({{
        question_text: 'Q1', answers: ['a', 'b', 'c', 'd'], question_type: 'multiple_choice',
        timer_duration: 30, round_num: 1, total_rounds: 5
    }});
    out.game2Q1 = read();

    // A finished game that returns to the lobby without a reset (New game).
    game.renderSubmissionTracker([
        {{ entrant_id: 't-sofa', name: 'A&B Sofa', submitted: true, connected: true }}
    ]);
    handleGameState({{ phase: 'LOBBY', players: [] }});
    handleQuestionStarted({{
        question_text: 'Q1', answers: ['a', 'b', 'c', 'd'], question_type: 'multiple_choice',
        timer_duration: 30, round_num: 1, total_rounds: 5
    }});
    out.viaLobby = read();

    // The first answer_progress of game 2 still draws the new room.
    game.renderSubmissionTracker([
        {{ entrant_id: 'Alice', name: 'Alice', submitted: true, connected: true }},
        {{ entrant_id: 'Bob', name: 'Bob', submitted: false, connected: true }},
        {{ entrant_id: 'Carol', name: 'Carol', submitted: false, connected: true }}
    ]);
    out.firstProgress = read();

    console.log(JSON.stringify(out));
}})().catch(function (e) {{ console.error(e); process.exit(1); }});
"""


def _run() -> dict:
    core = "var _lastRoster = []; var _hostSeenInRoster = false; var _hostConnectedFlag = null;\n"
    core += "\n".join(_slice(_CORE, a, b) for a, b in _CORE_SLICES)
    script = _SCRIPT.format(
        stub=json.dumps(str(_STUB)),
        i18n=json.dumps(str(_I18N)),
        i18njs=json.dumps(str(_JS / "i18n.js")),
        utils=json.dumps(str(_JS / "utils.js")),
        render_shared=json.dumps(str(_JS / "render-shared.js")),
        player_utils=json.dumps(str(_JS / "player-utils.js")),
        player_game=json.dumps(str(_JS / "player-game.js")),
        player_team=json.dumps(str(_JS / "player-team.js")),
        core=core,
    )
    out = subprocess.run(
        ["node", "-e", script], capture_output=True, text=True, check=True
    )
    return json.loads(out.stdout.strip().splitlines()[-1])


@pytest.fixture(scope="module")
def result() -> dict:
    return _run()


@_NEEDS_NODE
def test_the_premise_game_one_left_a_roster(result: dict) -> None:
    assert result["endOfGame1"]["chips"] == 2


@_NEEDS_NODE
def test_a_reset_empties_the_answered_row(result: dict) -> None:
    assert result["afterReset"]["chips"] == 0


@_NEEDS_NODE
def test_the_first_question_after_a_reset_does_not_repaint_the_old_room(
    result: dict,
) -> None:
    """The reported DOM: the dissolved team back in the row at question 1."""
    assert "Sofa" not in result["game2Q1"]["html"]
    assert result["game2Q1"]["chips"] == 0


@_NEEDS_NODE
def test_a_new_game_from_the_lobby_starts_without_the_old_room(result: dict) -> None:
    assert "Sofa" not in result["viaLobby"]["html"]
    assert result["viaLobby"]["chips"] == 0


@_NEEDS_NODE
def test_the_first_answer_progress_draws_the_new_room(result: dict) -> None:
    assert result["firstProgress"]["chips"] == 3
    assert result["firstProgress"]["answered"] == 1
