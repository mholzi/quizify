"""A phone that reloads mid-question gets the whole question screen back (#870).

Measured on real hardware during the v1.16.0-RC5 live test, two phones side by
side on the same question at the same moment, 390x844 DPR 2::

    p3  (not reloaded)    #answer-buttons  top 506  bottom 756
    p2  (reloaded)        #answer-buttons  top 442  bottom 692

The 64 px is the roster chip row — six players, wrapped to two lines — which
the reloaded phone never got back. On the final round of a second game the
FINAL ROUND! pill was missing from the reloaded screens too::

    p1  (not reloaded)    "ROUND 20 OF 20  FINAL ROUND!  ANIMALS & NATURE"
    p3  (reloaded)        "ROUND 20 OF 20  ANIMALS & NATURE"

Both pieces were only ever built by frames a reloading phone cannot receive.
The chips come from ``answer_progress``, which is sent when somebody answers —
so the next one arrives only if somebody still has to, and if everyone already
has, none is sent at all. The pill was raised by the wager window, a one-shot
frame that is long gone by the time the question is live.

That is the same shape as #864 and #858 before it: a snapshot path that does
not rebuild what the live path builds. Both pieces are informational — the
guest can still answer — but the guest who reloads because something looked
stuck is exactly the guest who then cannot tell whether the room is waiting
for them.

The snapshot has carried everything needed all along: ``players`` with
``name`` / ``colour`` / ``connected`` / ``submitted`` for the row, and
``round`` / ``total_rounds`` for the pill.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO))

from custom_components.quizify.game.state import (  # noqa: E402
    GamePhase,
    QuizifyGameState,
)
from custom_components.quizify.server.serializers import (  # noqa: E402
    serialize_state_snapshot,
)

_CC = _REPO / "custom_components" / "quizify"
_WWW = _CC / "www"
_JS = _WWW / "js"
_I18N = _WWW / "i18n"
_CORE = _JS / "player-core.js"
_STUB = Path(__file__).resolve().parent / "fixtures" / "dom_stub.js"

_NEEDS_NODE = pytest.mark.skipif(
    shutil.which("node") is None, reason="node not installed"
)


def _slice(path: Path, start: str, end: str) -> str:
    source = path.read_text("utf-8")
    a = source.index(start)
    return source[a : source.index(end, a)]


def _node(script: str) -> dict:
    out = subprocess.run(
        ["node", "-e", script], capture_output=True, text=True, check=True
    )
    return json.loads(out.stdout)


# ---------------------------------------------------------------------------
# The room, as the server describes it
# ---------------------------------------------------------------------------

#: The live-test room: six players, two of whom (Anna, Dan) are already in.
ROOM = [
    {"name": "Anna", "submitted": True, "connected": True, "color": "#E88A7F"},
    {"name": "Ben", "submitted": False, "connected": True, "color": "#7FA897"},
    {"name": "Cleo", "submitted": False, "connected": True, "color": "#A855F7"},
    {"name": "Dan", "submitted": True, "connected": True, "color": "#E8C07F"},
    {"name": "Eve", "submitted": False, "connected": False, "color": "#7FA8C0"},
    {"name": "Finn", "submitted": False, "connected": True, "color": "#C07FA8"},
]


def test_the_snapshot_has_carried_the_row_all_along() -> None:
    """Before anything is asserted about the phone: the fix is a client one,
    and it is only a client one if the server was already sending this."""
    game = QuizifyGameState(runtime=None, entry_id="test")
    for row in ROOM:
        game.add_player(row["name"], MagicMock(closed=False))
    game.start_game(language="en", num_rounds=20, difficulty="easy")
    assert game.start_next_question() is not None
    assert game.phase == GamePhase.QUESTION_ACTIVE
    game.submit_answer("Anna", 0)

    snapshot = serialize_state_snapshot(game)
    players = {p["name"]: p for p in snapshot["players"]}

    assert set(players) == {r["name"] for r in ROOM}
    assert players["Anna"]["submitted"] is True
    assert players["Ben"]["submitted"] is False
    assert all("connected" in p and "color" in p for p in snapshot["players"])
    assert snapshot["round"] == 1 and snapshot["total_rounds"] == 20


# ---------------------------------------------------------------------------
# …and the phone that reads it
# ---------------------------------------------------------------------------

_CORE_SLICE = (
    "    function handleGameState(msg) {",
    "    function handleRoundSummary(msg) {",
)

_SCRIPT = """
require({stub});
QZ.serveI18n({i18n});
QZ.load({i18njs});

QZ.els(['current-round', 'total-rounds', 'last-round-banner', 'submission-tracker',
        'submitted-players', 'question-text', 'question-category', 'answer-buttons',
        'answers-container', 'timer', 'timer-sr-announce', 'leaderboard-list']);
// The page ships the pill hidden; something has to raise it.
QZ.el('last-round-banner').classList.add('hidden');

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

var ROOM = {room};

function snapshot(round, totalRounds) {{
    return {{
        phase: 'QUESTION_ACTIVE',
        round: round,
        total_rounds: totalRounds,
        question: {{
            text: 'Which country is this pair of swords from?',
            answers: ['Japan', 'China', 'Korea'],
            time_limit: 30, time_remaining: 18, question_type: 'multiple_choice'
        }},
        players: JSON.parse(JSON.stringify(ROOM)),
        leaderboard: ROOM.map(function (p) {{ return {{ name: p.name, score: 0 }}; }})
    }};
}}

function pillEl() {{ return document.getElementById('last-round-banner'); }}

function read() {{
    var row = document.getElementById('submitted-players').innerHTML;
    return {{
        chips: (row.match(/class="player-indicator/g) || []).length,
        answered: (row.match(/is-submitted/g) || []).length,
        me: (row.match(/is-current-player/g) || []).length,
        dropped: (row.match(/player-indicator--disconnected/g) || []).length,
        names: (row.match(/player-name">([^<]*)</g) || []).map(function (m) {{
            return m.slice('player-name">'.length, -1);
        }}),
        pill: !pillEl().classList.contains('hidden'),
        round: document.getElementById('current-round').textContent,
        total: document.getElementById('total-rounds').textContent
    }};
}}

(async function () {{
    await window.QuizifyI18n.init('en');
    state.playerName = 'Cleo';

    var out = {{}};

    // The reload the live test performed: round 2 of 5, two players in.
    handleGameState(snapshot(2, 5));
    out.midQuestion = read();

    // …and the second game's final round, where the pill goes missing too.
    handleGameState(snapshot(20, 20));
    out.finalRound = read();

    // The live path must still take the pill down on the round after it —
    // #706, which a one-way toggle would undo.
    handleQuestionStarted({{
        question_text: 'Next game, round 1', answers: ['a', 'b', 'c'],
        timer_duration: 30, round_num: 1, total_rounds: 20
    }});
    out.nextGame = read();

    console.log(JSON.stringify(out));
}})();
"""


def _phone() -> dict:
    return _node(
        _SCRIPT.format(
            stub=json.dumps(str(_STUB)),
            i18n=json.dumps(str(_I18N)),
            i18njs=json.dumps(str(_JS / "i18n.js")),
            utils=json.dumps(str(_JS / "utils.js")),
            render_shared=json.dumps(str(_JS / "render-shared.js")),
            player_utils=json.dumps(str(_JS / "player-utils.js")),
            player_game=json.dumps(str(_JS / "player-game.js")),
            core=_slice(_CORE, *_CORE_SLICE),
            room=json.dumps(ROOM),
        )
    )


@_NEEDS_NODE
def test_the_reloaded_phone_gets_the_answered_row_back() -> None:
    """The 64 px, and what it says. Six chips, two of them lit, the reader's
    own marked, and the guest who dropped shown as dropped — the row the
    neighbouring phone had and this one did not."""
    row = _phone()["midQuestion"]

    assert row["chips"] == 6, (
        "the reloaded phone is still playing the question without the row "
        "that says who else is in the room"
    )
    assert row["answered"] == 2, "Anna and Dan are already in"
    assert row["me"] == 1, "the reader's own chip is not marked"
    assert row["dropped"] == 1, "Eve is gone and the row does not say so"
    assert row["names"] == ["Anna", "Ben", "Cleo", "Dan", "Eve", "Finn"]


@_NEEDS_NODE
def test_the_reloaded_phone_still_knows_it_is_the_final_round() -> None:
    """The pill was raised by the wager window and by nothing else, so a
    reload took it off the screen for the rest of the game."""
    result = _phone()

    assert result["finalRound"]["pill"] is True, (
        "round 20 of 20 rebuilt from a snapshot, and the phone no longer "
        "says it is the final round"
    )
    assert result["finalRound"]["round"] == "20"
    assert result["finalRound"]["total"] == "20"
    assert result["midQuestion"]["pill"] is False, "round 2 of 5 is not the final"


@_NEEDS_NODE
def test_the_pill_still_comes_down_on_the_round_after() -> None:
    """#706, which this must not undo: play again keeps the phones on this
    page, so a pill that only goes up is a pill worn for a whole second game.
    """
    assert _phone()["nextGame"]["pill"] is False
