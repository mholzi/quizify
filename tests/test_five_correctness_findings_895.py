"""Five small correctness findings from the weekly code review (#895).

Each is a few lines of code and none of them is cosmetic except the last, so
they are grouped here rather than lost in five files. What they share is a
shape the repository has paid for repeatedly: **a snapshot path that does not
do what the live path does**, and **a teardown that one caller forgot**.

* **(a) the pause did not stop the phone's clock.** ``startCountdown`` is a
  local ``setInterval`` seeded at question start; the pause happens on the
  server. ``player-core.js``'s ``PAUSED`` case showed the pause screen and
  left the interval running, so behind it the soft ticks kept playing, the
  10s/5s VoiceOver announcements kept firing, and the visible clock kept
  draining a round nobody was allowed to answer.

* **(b) the lightning snapshot carried no answered state.** A solo player who
  reloaded mid-question came back to three live buttons. ``record_answer``
  refuses the second tap ("one answer per question — the base rule") and the
  handler returns without a reply, so the phone marked itself "Answered" for
  an answer the room never received.

* **(c) the deferred host-disconnect pause only watched the player socket.**
  ``_schedule_admin_pause`` is armed when the host's PLAYER socket closes
  during a live question. Both cancels — ``_handle_join`` and the session
  reconnect — look for a returning player, so a host who leaves the game to
  open ``/quizify/admin`` came back on an ADMIN socket, nothing cancelled the
  timer, and four seconds later every phone in the room was shown the "host
  disconnected" overlay while the host sat on the admin page watching.

* **(d) a television reveal rebuilt from a snapshot had no distribution
  bars,** because the snapshot never carried ``answer_distribution`` and the
  snapshot renderer never drew the markup for it — so the board showed the
  correct tile and every bar at 0 %, which reads as "nobody picked anything"
  rather than "this board does not know". The estimate branch of the same
  case additionally rebuilt the number line inside the previous round's
  frame: stale round counter, stale category, and the fun fact of the round
  being revealed never appeared at all.

* **(e) ``lockBetUi`` left the bet controls live after a restore.** Cosmetic
  — ``_state.betPlaced`` already blocks the send — but the screen offered a
  second bet and swallowed it.
"""

from __future__ import annotations

import asyncio
import json
import shutil
import subprocess
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO))

from custom_components.quizify.game.lightning import LightningRound  # noqa: E402
from custom_components.quizify.game.state import (  # noqa: E402
    GamePhase,
    QuizifyGameState,
)
from custom_components.quizify.server.connection import ConnectionManager  # noqa: E402
from custom_components.quizify.server.round_message_builder import (  # noqa: E402
    RoundMessageBuilder,
)
from custom_components.quizify.server.serializers import (  # noqa: E402
    serialize_state_snapshot,
)
from custom_components.quizify.server.websocket import (  # noqa: E402
    QuizifyWebSocketHandler,
)

_CC = _REPO / "custom_components" / "quizify"
_WWW = _CC / "www"
_JS = _WWW / "js"
_I18N = _WWW / "i18n"
_CORE = _JS / "player-core.js"
_DASH = _WWW / "dashboard.html"
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
# The phone, driven for real
# ---------------------------------------------------------------------------

#: ``handleGameState`` down to (but not including) ``handleRoundSummary``:
#: the snapshot router, the question renderer it delegates to, and the
#: final-round helpers between them. Lifted whole so the behaviour measured
#: below is the client's, not a copy of it.
_CORE_SLICE = (
    "    function handleGameState(msg) {",
    "    function handleRoundSummary(msg) {",
)

_PHONE = """
require({stub});
QZ.serveI18n({i18n});
QZ.load({i18njs});

QZ.els(['current-round', 'total-rounds', 'last-round-banner', 'submission-tracker',
        'submitted-players', 'question-text', 'question-category', 'answer-buttons',
        'answers-container', 'timer', 'timer-sr-announce', 'leaderboard-list',
        'lightning-progress', 'lightning-category', 'lightning-question-text',
        'lightning-answered', 'lightning-timer', 'lightning-answer-text-0',
        'lightning-answer-text-1', 'lightning-answer-text-2']);
QZ.el('last-round-banner').classList.add('hidden');

// The three lightning buttons the module finds by attribute.
var LBTN = [0, 1, 2].map(function (i) {{
    var b = QZ.el('lightning-answer-' + i);
    b.setAttribute('data-lightning-answer', String(i));
    return b;
}});

// A clock we advance by hand: one second per tick, so the countdown really
// counts down rather than sitting on whatever Date.now() happened to be.
var NOW = 1700000000000;
Date.now = function () {{ return NOW; }};
var intervals = [];
global.setInterval = function (fn) {{
    intervals.push({{ fn: fn, live: true }});
    return intervals.length;
}};
global.clearInterval = function (h) {{ if (h) intervals[h - 1].live = false; }};
function tick(n) {{
    for (var k = 0; k < (n || 1); k++) {{
        NOW += 1000;
        intervals.forEach(function (t) {{ if (t.live) t.fn(); }});
    }}
}}

var ticks = [];
window.QuizifyPlayerSound = {{
    resetTick: function () {{}},
    tickFromRemaining: function (r) {{ ticks.push(r); }}
}};

QZ.load({utils});
QZ.load({render_shared});
QZ.load({player_utils});
QZ.load({player_game});
QZ.load({player_lightning});

var game = window.QuizifyPlayerGame;
var pu = window.QuizifyPlayerUtils;
var state = pu.state;
var lightning = window.QuizifyPlayerLightning;
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

function LROW() {{ return document.getElementById('lightning-answered'); }}

function clock() {{
    return {{
        running: intervals.filter(function (t) {{ return t.live; }}).length,
        shown: document.getElementById('timer').textContent,
        announced: document.getElementById('timer-sr-announce').textContent,
        ticks: ticks.length
    }};
}}

(async function () {{
    await window.QuizifyI18n.init('en');
    state.playerName = 'Cleo';

    // A live question with eight seconds left on it.
    handleQuestionStarted({{
        question_text: 'Which country?', answers: ['a', 'b', 'c'],
        timer_duration: 8, round_num: 3, total_rounds: 20
    }});
    tick(2);
    var running = clock();

    // The host pauses. The server sends the paused snapshot; the phone's own
    // interval is the only clock in the room that nothing else can stop.
    handleGameState({{ phase: 'PAUSED', round: 3, total_rounds: 20, players: [] }});
    var atPause = clock();
    tick(8);
    var afterPause = clock();

    // The lightning round, rebuilt from a snapshot for a player whose
    // entrant has already answered question 3 of 5.
    handleGameState({{
        phase: 'LIGHTNING', round: 3, total_rounds: 20, players: [],
        lightning: {{
            index: 2, num_questions: 5, time_remaining: 9,
            seconds_per_question: 15,
            question: {{ text: 'Which one?', answers: ['x', 'y', 'z'] }},
            you_answered: true, you_answer_index: 1
        }}
    }});
    var answeredRestore = {{
        disabled: LBTN.map(function (b) {{ return !!b.disabled; }}),
        selected: LBTN.map(function (b) {{ return b.classList.contains('selected'); }}),
        rowShown: !LROW().classList.contains('hidden')
    }};

    // …and for one who has not.
    handleGameState({{
        phase: 'LIGHTNING', round: 3, total_rounds: 20, players: [],
        lightning: {{
            index: 3, num_questions: 5, time_remaining: 12,
            seconds_per_question: 15,
            question: {{ text: 'And this one?', answers: ['x', 'y', 'z'] }},
            you_answered: false, you_answer_index: null
        }}
    }});
    var openRestore = {{
        disabled: LBTN.map(function (b) {{ return !!b.disabled; }}),
        rowShown: !LROW().classList.contains('hidden')
    }};

    console.log(JSON.stringify({{
        running: running, atPause: atPause, afterPause: afterPause,
        answeredRestore: answeredRestore, openRestore: openRestore
    }}));
}})();
"""


def _phone() -> dict:
    return _node(
        _PHONE.format(
            stub=json.dumps(str(_STUB)),
            i18n=json.dumps(str(_I18N)),
            i18njs=json.dumps(str(_JS / "i18n.js")),
            utils=json.dumps(str(_JS / "utils.js")),
            render_shared=json.dumps(str(_JS / "render-shared.js")),
            player_utils=json.dumps(str(_JS / "player-utils.js")),
            player_game=json.dumps(str(_JS / "player-game.js")),
            player_lightning=json.dumps(str(_JS / "player-lightning.js")),
            core=_slice(_CORE, *_CORE_SLICE),
        )
    )


# ---------------------------------------------------------------------------
# (a) the pause stops the phone's clock
# ---------------------------------------------------------------------------


@_NEEDS_NODE
def test_the_clock_is_running_before_the_pause() -> None:
    """The premise. Without this the test below could pass on a phone whose
    countdown never started at all."""
    result = _phone()

    assert result["running"]["running"] == 1
    assert result["running"]["shown"] == "6"
    assert result["running"]["ticks"] > 0


@_NEEDS_NODE
def test_the_pause_stops_the_phones_own_countdown() -> None:
    """#895a. Eight seconds pass behind the pause screen; nothing may move.

    Without the fix the interval survives the phase change, so the visible
    clock drains to 0, the last five seconds each play a tick, and the polite
    region reads "5 seconds left" and then "time's up" to anyone using
    VoiceOver — over a screen that says the game is paused.
    """
    result = _phone()
    at_pause, after = result["atPause"], result["afterPause"]

    assert at_pause["running"] == 0, "the pause left the interval running"
    assert after["running"] == 0
    assert after["shown"] == at_pause["shown"], (
        f"the clock kept counting behind the pause screen: "
        f"{at_pause['shown']} -> {after['shown']}"
    )
    assert after["ticks"] == at_pause["ticks"], "the soft ticks kept playing"
    assert after["announced"] == "", (
        "the timer's polite region is still announcing seconds left on a "
        "round nobody is allowed to answer"
    )


# ---------------------------------------------------------------------------
# (b) the lightning snapshot carries the answered state
# ---------------------------------------------------------------------------


def _lightning_state(tmp_path: Path) -> tuple[QuizifyGameState, LightningRound]:
    game = QuizifyGameState(runtime=None, entry_id="test")
    game.add_player("Anna", MagicMock(closed=False))
    game.add_player("Cleo", MagicMock(closed=False))
    game.start_game(language="en", num_rounds=3, difficulty="easy")
    assert game.start_lightning_round() is not None
    lr = game.lightning
    assert lr is not None
    return game, lr


def test_the_lightning_snapshot_says_whether_this_phone_has_answered(
    tmp_path: Path,
) -> None:
    """#895b, on the wire.

    The canonical block is built for the television, which has no "you" — so
    the per-player projection is the only place that can say it. The index is
    reported in the recipient's OWN button order, the same frame the answers
    beside it ride in, because that is the only order the phone can highlight.
    """
    game, lr = _lightning_state(tmp_path)
    builder = RoundMessageBuilder()

    cleo = game.get_player("Cleo")
    anna = game.get_player("Anna")
    assert cleo is not None and anna is not None

    # Nobody has answered yet.
    snapshot = serialize_state_snapshot(game)
    before = builder.project_snapshot_for_player(
        game, snapshot=snapshot, player=cleo
    )
    assert before["lightning"]["you_answered"] is False
    assert before["lightning"]["you_answer_index"] is None

    # Cleo taps her second button.
    shuffle = lr.ensure_shuffle("Cleo")
    assert lr.record_answer("Cleo", 1) is not None

    snapshot = serialize_state_snapshot(game)
    after = builder.project_snapshot_for_player(game, snapshot=snapshot, player=cleo)
    assert after["lightning"]["you_answered"] is True, (
        "a reloaded player is handed live buttons for a question they have "
        "already answered; the second tap is dropped without a reply"
    )
    assert after["lightning"]["you_answer_index"] == 1

    # …and it is per recipient, not per room.
    for_anna = builder.project_snapshot_for_player(
        game, snapshot=snapshot, player=anna
    )
    assert for_anna["lightning"]["you_answered"] is False
    assert shuffle == lr.ensure_shuffle("Cleo"), "the projection reshuffled her"


@_NEEDS_NODE
def test_a_reloaded_lightning_phone_comes_back_locked() -> None:
    """#895b, on the phone. The same end state ``submitAnswer`` leaves behind:
    three dead buttons with the one that stands marked."""
    result = _phone()

    assert result["answeredRestore"]["disabled"] == [True, True, True]
    assert result["answeredRestore"]["selected"] == [False, True, False]
    assert result["answeredRestore"]["rowShown"] is True

    # And a phone that has NOT answered still gets to.
    assert result["openRestore"]["disabled"] == [False, False, False]
    assert result["openRestore"]["rowShown"] is False


def test_a_team_member_may_still_change_the_teams_lightning_answer(
    tmp_path: Path,
) -> None:
    """The exception, pinned so the lock cannot spread to it.

    In team mode the answer belongs to the team and any member may change it
    until the clock stops (#552). Coming back to locked buttons would take
    that away, so ``you_answered`` stays false there and the live
    ``lightning_team_answer`` frame keeps its job.
    """
    game = QuizifyGameState(runtime=None, entry_id="test")
    game.add_player("Anna", MagicMock(closed=False))
    game.add_player("Cleo", MagicMock(closed=False))
    team = game.team_registry.create("Sofa", "Anna")
    game.team_registry.join(team.team_id, "Cleo")
    game.start_game(language="en", num_rounds=3, difficulty="easy")
    assert game.start_lightning_round() is not None
    lr = game.lightning
    assert lr is not None
    assert lr.entrant_for("Cleo") != "Cleo", "team mode did not take"

    assert lr.record_answer("Anna", 0) is not None

    cleo = game.get_player("Cleo")
    assert cleo is not None
    projected = RoundMessageBuilder().project_snapshot_for_player(
        game, snapshot=serialize_state_snapshot(game), player=cleo
    )
    assert projected["lightning"]["you_answered"] is False


# ---------------------------------------------------------------------------
# (c) the host who walks to the admin page does not pause the room
# ---------------------------------------------------------------------------


class _FakeRuntime:
    def __init__(self, tmp_path: Path) -> None:
        self.data_dir = tmp_path


def _handler(game: QuizifyGameState, tmp_path: Path) -> QuizifyWebSocketHandler:
    runtime = _FakeRuntime(tmp_path)
    handler = QuizifyWebSocketHandler(
        runtime=runtime, game_state_provider=lambda: game
    )
    handler._conn = ConnectionManager(runtime, lambda: game)
    handler._conn.broadcast = AsyncMock()
    handler.ADMIN_REDIRECT_GRACE = 0.2
    return handler


def _ws() -> MagicMock:
    ws = MagicMock()
    ws.closed = False
    ws.send_json = AsyncMock()
    return ws


async def _host_walks_out(
    game: QuizifyGameState, handler: QuizifyWebSocketHandler
) -> MagicMock:
    """A host playing along, whose phone/tab leaves the game mid-question."""
    host_ws = _ws()
    game.add_player("Host", host_ws)
    host = game.get_player("Host")
    assert host is not None
    host.is_admin = True
    game.add_player("Cleo", _ws())
    game.start_game(language="en", num_rounds=3, difficulty="easy")
    assert game.start_next_question() is not None
    assert game.phase == GamePhase.QUESTION_ACTIVE

    host_ws.closed = True
    await handler._handle_disconnect(host_ws)
    assert handler._admin_pause_task is not None, "no pause was armed to cancel"
    return host_ws


@pytest.mark.asyncio
async def test_a_host_who_opens_the_admin_page_does_not_pause_the_room(
    tmp_path: Path,
) -> None:
    """#895c, the reading from the review.

    The host leaves the game to open ``/quizify/admin``. That is an ADMIN
    socket, so neither ``_handle_join`` nor the session reconnect ever runs —
    and those were the only two places that cancelled the deferred pause. Four
    seconds later every phone in the room got the "host disconnected" overlay
    while the host was looking at the same game from the admin page.
    """
    game = QuizifyGameState(runtime=None, entry_id="test")
    handler = _handler(game, tmp_path)
    await _host_walks_out(game, handler)

    await handler._handle_admin_connect(_ws(), game)
    assert handler._admin_pause_task is None, (
        "the admin socket is the host arriving; the pause armed by their "
        "player socket closing has to be cancelled here"
    )

    await asyncio.sleep(handler.ADMIN_REDIRECT_GRACE * 2)

    assert game.phase == GamePhase.QUESTION_ACTIVE
    sent = [c.args[0] for c in handler._conn.broadcast.await_args_list]
    paused = [
        m
        for m in sent
        if m.get("type") == "game_state" and m.get("phase") == "PAUSED"
    ]
    assert not paused, f"the room was paused anyway: {paused}"


@pytest.mark.asyncio
async def test_a_host_who_really_leaves_still_pauses_the_room(
    tmp_path: Path,
) -> None:
    """The capability the cancel must not eat. A closed tab or a dead wifi is
    still a host who is gone, and the room still has to be told."""
    game = QuizifyGameState(runtime=None, entry_id="test")
    handler = _handler(game, tmp_path)
    await _host_walks_out(game, handler)

    await asyncio.sleep(handler.ADMIN_REDIRECT_GRACE + 0.3)

    assert game.phase == GamePhase.PAUSED


# ---------------------------------------------------------------------------
# (d) the television's reveal, rebuilt from a snapshot
# ---------------------------------------------------------------------------


def _revealed_game() -> QuizifyGameState:
    """A finished round with a canonical shuffle and three votes in it.

    ``submit_answer`` takes the ORIGINAL question index — the per-player
    shuffle is applied by the WS layer, one level above this — so the taps are
    written in that space, and the round shuffle is set the way
    ``_start_next_question`` sets it, because the whole point of the
    distribution is that it hangs off the tiles the television drew.
    """
    game = QuizifyGameState(runtime=None, entry_id="test")
    for name in ("Anna", "Ben", "Cleo", "Dan"):
        game.add_player(name, MagicMock(closed=False))
    game.start_game(language="en", num_rounds=3, difficulty="easy")
    question = game.start_next_question()
    assert question is not None
    correct = next(i for i, a in enumerate(question.answers) if a.correct)
    wrong = next(i for i, a in enumerate(question.answers) if not a.correct)

    # A canonical shuffle that is not the identity, so a distribution built in
    # question-JSON order would hang the bars off the wrong tiles.
    order = [2, 0, 1]
    game.set_round_shuffle(order, [question.answers[i].text for i in order])

    for name, original in (("Anna", correct), ("Ben", correct), ("Cleo", wrong)):
        game.submit_answer(name, original)
    # Dan never answers.
    game.evaluate_round()
    assert game.phase == GamePhase.ANSWER_REVEAL
    return game


def test_the_snapshot_reveal_carries_the_answer_distribution() -> None:
    """#895d, on the wire.

    ``answer_distribution`` has ridden the live ``round_summary`` since #151
    and the snapshot never carried it, so a television that reconnected during
    a reveal — or was cast to the room after the question closed — had nothing
    to draw the bars from.
    """
    game = _revealed_game()
    snapshot = serialize_state_snapshot(game)

    distribution = snapshot["round_summary"]["answer_distribution"]
    assert distribution, "the reveal snapshot still has no bars to draw"

    by_index = {d["index"]: d for d in distribution}
    correct = snapshot["round_summary"]["correct_answer_index"]
    assert correct != snapshot["round_summary"]["correct_answer_index_original"], (
        "the round shuffle is the identity here, so this test could not tell "
        "canonical space from question-JSON space"
    )
    assert by_index[correct]["count"] == 2, (
        "two of four picked the right answer; the bar has to hang off the "
        "tile the board drew it on"
    )
    assert by_index[correct]["percent"] == 50

    # Four voters, one of whom never answered: the no-answer bucket is part of
    # the tally, exactly as it is in the live payload.
    assert sum(d["count"] for d in distribution) == 4
    assert any(d.get("no_answer") for d in distribution)


def test_the_distribution_counts_one_vote_per_team() -> None:
    """#853's rule, in the snapshot too — the two builders have to agree or a
    reconnect changes the chart the room is reading."""
    game = QuizifyGameState(runtime=None, entry_id="test")
    for name in ("Anna", "Ben", "Cleo"):
        game.add_player(name, MagicMock(closed=False))
    sofa = game.team_registry.create("Sofa", "Anna")
    game.team_registry.join(sofa.team_id, "Ben")
    couch = game.team_registry.create("Couch", "Cleo")
    assert couch.team_id != sofa.team_id
    game.start_game(language="en", num_rounds=3, difficulty="easy")
    question = game.start_next_question()
    assert question is not None
    correct = next(i for i, a in enumerate(question.answers) if a.correct)
    wrong = next(i for i, a in enumerate(question.answers) if not a.correct)
    game.submit_answer("Anna", correct)
    game.submit_answer("Ben", correct)
    game.submit_answer("Cleo", wrong)
    game.evaluate_round()

    distribution = serialize_state_snapshot(game)["round_summary"][
        "answer_distribution"
    ]
    assert sum(d["count"] for d in distribution) == 2, (
        "a team of two counted as two votes — the reading #853 removed from "
        "the live path"
    )


#: The snapshot reveal renderers, lifted whole out of the television page.
_TV_SLICE = (
    "        // #296: rebuild the reveal question view from the snapshot.",
    "        // ---- Lightning (#296)",
)

#: …and the ``ANSWER_REVEAL`` case that chooses between them, wrapped so it
#: can be called. Sliced rather than paraphrased: the estimate branch's bug
#: was in the case body, not in a function.
_TV_CASE = (
    "                case 'ANSWER_REVEAL':",
    "                case 'HOT_SEAT_AUCTION':",
)

_TV = """
require({stub});

['roundIndicator', 'questionCategory', 'questionText', 'answersGrid',
 'funFact', 'funFactText', 'timerFill'].forEach(function (name) {{
    QZ.el(name);
}});

var els = {{
    roundIndicator: QZ.el('roundIndicator'),
    questionCategory: QZ.el('questionCategory'),
    questionText: QZ.el('questionText'),
    answersGrid: QZ.el('answersGrid'),
    funFact: QZ.el('funFact'),
    funFactText: QZ.el('funFactText'),
    timerFill: QZ.el('timerFill')
}};

function t(key, fallback) {{ return fallback; }}
function escapeHtml(s) {{ return String(s === undefined || s === null ? '' : s); }}
function showView() {{}}
function renderQuestionImage() {{}}
var estimateRendered = null;
function renderDashboardEstimateReveal(block) {{ estimateRendered = block; }}
global.setTimeout = function (fn) {{ fn(); return 0; }};

{renderers}

function revealCase(msg) {{
    switch ('ANSWER_REVEAL') {{
{case}
    }}
}}

function read() {{
    return {{
        round: els.roundIndicator.textContent,
        category: els.questionCategory.textContent,
        question: els.questionText.textContent,
        grid: els.answersGrid.innerHTML,
        funFact: els.funFactText.textContent,
        estimate: estimateRendered
    }};
}}

var out = {{}};

revealCase({snapshot});
out.multipleChoice = read();

els.roundIndicator.textContent = 'Question 6 / 20';
els.questionCategory.textContent = 'Animals & Nature';
els.funFactText.textContent = '';
revealCase({estimate_snapshot});
out.estimate = read();

console.log(JSON.stringify(out));
"""

_TV_SNAPSHOT = {
    "round": 7,
    "total_rounds": 20,
    "round_summary": {
        "question_text": "Which country?",
        "category": "Geography",
        "answers": ["Japan", "China", "Korea"],
        "correct_answer_index": 0,
        "correct_answer_index_original": 0,
        "fun_fact": "Swords, mostly.",
        "answer_distribution": [
            {"index": 0, "count": 2, "percent": 50},
            {"index": 1, "count": 1, "percent": 25},
            {"index": 2, "count": 0, "percent": 0},
            {"no_answer": True, "count": 1, "percent": 25},
        ],
    },
}

_TV_ESTIMATE_SNAPSHOT = {
    "round": 7,
    "total_rounds": 20,
    "round_summary": {
        "question_text": "How many islands?",
        "category": "Geography",
        "question_type": "estimate",
        "fun_fact": "Nobody agrees on the count.",
        "estimate": {"value": 6852, "min": 0, "max": 10000, "guesses": []},
    },
}


def _television() -> dict:
    return _node(
        _TV.format(
            stub=json.dumps(str(_STUB)),
            renderers=_slice(_DASH, *_TV_SLICE),
            case=_slice(_DASH, *_TV_CASE),
            snapshot=json.dumps(_TV_SNAPSHOT),
            estimate_snapshot=json.dumps(_TV_ESTIMATE_SNAPSHOT),
        )
    )


@_NEEDS_NODE
def test_a_television_rebuilt_from_a_snapshot_draws_the_bars() -> None:
    """#895d, on the board. The snapshot renderer emitted bare tiles with no
    distribution markup in them at all, so there was nowhere to put a bar even
    once the server started sending one."""
    grid = _television()["multipleChoice"]["grid"]

    assert "dashboard-answer-bar-fill" in grid, (
        "the rebuilt grid has no bars — the live grid embeds this markup at "
        "question time (#151) and this path never did"
    )
    assert "width: 50%" in grid and "width: 25%" in grid
    assert grid.count("dashboard-answer-percent") == 3
    assert ">50%<" in grid and ">25%<" in grid and ">0%<" in grid


@_NEEDS_NODE
def test_the_estimate_reveal_gets_its_own_frame_back() -> None:
    """#895d's second half: the number line was rebuilt inside the previous
    round's furniture — stale counter, stale category, no fun fact."""
    result = _television()["estimate"]

    assert result["estimate"] is not None, "the number line was not rendered"
    assert result["round"] == "Question 7 / 20", (
        f"the board is still showing the previous round: {result['round']!r}"
    )
    assert result["category"] == "Geography"
    assert result["question"] == "How many islands?"
    assert result["funFact"] == "Nobody agrees on the count."


# ---------------------------------------------------------------------------
# (e) the bet controls after a restore
# ---------------------------------------------------------------------------


_BET = """
require({stub});
QZ.serveI18n({i18n});
QZ.load({i18njs});

QZ.els(['hotseat-panel', 'hotseat-title', 'hotseat-hint', 'hotseat-bid-stage',
        'hotseat-bet-stage', 'hotseat-result-stage', 'hotseat-bet-slider',
        'hotseat-bet-value', 'hotseat-bet-will', 'hotseat-bet-wont',
        'hotseat-slider', 'hotseat-value', 'hotseat-bank', 'hotseat-bid-btn',
        'hotseat-bid-count', 'question-text', 'question-category',
        'answer-buttons', 'answers-container']);
QZ.el('hotseat-bet-slider').value = '30';

QZ.load({utils});
QZ.load({render_shared});
QZ.load({player_utils});
QZ.load({player_game});
QZ.load({hotseat});

var hotSeat = window.QuizifyPlayerHotSeat;
var S = window.QuizifyPlayerUtils.state;

(async function () {{
    await window.QuizifyI18n.init('en');
    S.playerName = 'Cleo';

    // A spectator who already staked against the chair, coming back from a
    // reload while the seat holder is still answering.
    hotSeat.reset();
    hotSeat.restoreFromSnapshot({{
        stage: 'question', winner: 'Ben', entrant: 'Ben', pct: 90, stake: 80,
        own_bank: 137, time_remaining: 18, you_are_seated: false,
        you_bet: {{ side: 'wont', pct: 15 }},
        question: {{ text: 'Which country?', answers: ['Japan', 'China', 'Korea'] }}
    }}, {{ leaderboard: [] }});

    console.log(JSON.stringify({{
        slider: !!document.getElementById('hotseat-bet-slider').disabled,
        will: !!document.getElementById('hotseat-bet-will').disabled,
        wont: !!document.getElementById('hotseat-bet-wont').disabled,
        hint: document.getElementById('hotseat-hint').textContent
    }}));
}})();
"""


@_NEEDS_NODE
def test_a_restored_bet_leaves_the_controls_dead() -> None:
    """#895e. Cosmetic — ``_state.betPlaced`` already refuses the send — but a
    live slider over a bet that cannot be changed is an offer the server will
    not honour."""
    result = _node(
        _BET.format(
            stub=json.dumps(str(_STUB)),
            i18n=json.dumps(str(_I18N)),
            i18njs=json.dumps(str(_JS / "i18n.js")),
            utils=json.dumps(str(_JS / "utils.js")),
            render_shared=json.dumps(str(_JS / "render-shared.js")),
            player_utils=json.dumps(str(_JS / "player-utils.js")),
            player_game=json.dumps(str(_JS / "player-game.js")),
            hotseat=json.dumps(str(_JS / "player-hotseat.js")),
        )
    )

    assert result["slider"] is True, "the bet slider is still live after a reload"
    assert result["will"] is True and result["wont"] is True
    # …and the side is named the way the tap path names it, not as the wire
    # spells it.
    assert "wont" not in result["hint"], (
        f"the raw wire value leaked into the hint: {result['hint']!r}"
    )
