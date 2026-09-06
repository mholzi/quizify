"""Three screens that showed the game before this one (v1.16.0-RC2 live test).

**#849 — the host page's leaderboard was a round behind at every reveal.**
``handleRoundSummary`` set the correct-answer line, relabelled Next Question,
and never touched ``#game-leaderboard``. The frame it is handed declares
``leaderboard`` in ``server/protocol.py`` and the television renders exactly
that field at exactly that moment, so with three players in round 3 the room
read ``Anna 26 · Ben 26 · Cleo 0`` off the TV while the host, one metre away,
read ``Anna 12 · Ben 12 · Cleo 0`` off the page they were calling the scores
from. The host page caught up on the next ``question_started``, i.e. always
one round after it mattered. Same class as #833 one surface over: the
settlement had happened, only the board had not been told.

**#851 — the phone lobby's difficulty badge, twice wrong.** The host applied
``5 Runden · Einfach · 30 s · 🇩🇪``; the badge read ``🎯 Medium``.

*Stale*, because ``GameState.difficulty`` is written by ``start_game`` and by
nothing else, so for the whole life of the lobby it described the PREVIOUS
game — the constructor default on the first game of the evening, the last
game's pick after that. The state snapshot carries that field to every phone
and to the television, so the value was not wrong by a rendering: it was the
last game's, faithfully rendered. That is exactly the hole ``set_language``
closed for the language in #776, and it is closed here the same way.

*Untranslated*, because the badge was written with ``textContent``, which
leaves the ``initPageTranslations`` sweep nothing to find. The rest of the
phone was German (``Spiel-Lobby``, ``Warte, bis der Host…``) around the
English word. Same shape as #776's ``renderAllTime`` and #809's meta line.

**#852 — the Hot Seat's seat question did not clear the timer's polite
region.** #839 gave ``stopCountdown()`` the job of emptying
``#timer-sr-announce``, and on every ordinary question it holds. The chair's
question never goes through ``startCountdown`` — its clock arrives as
``hot_seat_tick`` — so the auction's last sentence was still standing when it
opened: ``"10 seconds left"`` in the live region with 24 on the visible clock,
read off a phone during the live test.
"""

from __future__ import annotations

import asyncio
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO))

from custom_components.quizify.game.state import (  # noqa: E402
    GamePhase,
    QuizifyGameState,
)
from custom_components.quizify.server import protocol  # noqa: E402
from custom_components.quizify.server.connection import ConnectionManager  # noqa: E402
from custom_components.quizify.server.serializers import (  # noqa: E402
    serialize_state_snapshot,
)
from custom_components.quizify.server.websocket import (  # noqa: E402
    MSG_SET_DIFFICULTY,
    QuizifyWebSocketHandler,
)

_CC = _REPO / "custom_components" / "quizify"
_WWW = _CC / "www"
_JS = _WWW / "js"
_I18N = _WWW / "i18n"
_ADMIN = _JS / "admin.js"
_CORE = _JS / "player-core.js"
_GAME = _JS / "player-game.js"
_LOBBY = _JS / "player-lobby.js"
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
# #849 — the host page's leaderboard at the reveal
# ---------------------------------------------------------------------------


_REVEAL_SCRIPT = """
require({stub});
QZ.load({utils});
QZ.load({shared});
QZ.els(['game-leaderboard', 'admin-correct-answer', 'next-question-btn',
        'end-game-btn']);

var els = {{
    gameLeaderboard: document.getElementById('game-leaderboard'),
    adminCorrect: document.getElementById('admin-correct-answer'),
    nextQuestionBtn: document.getElementById('next-question-btn'),
    endGameBtn: document.getElementById('end-game-btn')
}};
var _redirecting = false;
var currentPhase = 'QUESTION_ACTIVE';
var adminTimer = {{ stop: function () {{}} }};
var _lastGameLeaderboard = null;
function _t(key) {{ return key; }}
function scoreDeltaHtml() {{ return ''; }}

{render}
{reveal}

// The board reads names and scores the way the live test read it: out of the
// DOM, not off the pixels.
function board() {{
    var html = els.gameLeaderboard.innerHTML;
    var names = [];
    var scores = [];
    var re = /leaderboard-name">([^<]*)<[\\s\\S]*?leaderboard-score">(\\d+)</g;
    var m;
    while ((m = re.exec(html)) !== null) {{
        names.push(m[1]);
        scores.push(Number(m[2]));
    }}
    return {{ names: names, scores: scores }};
}}

// Round 3 of 5. This is the board question_started painted, i.e. the scores
// as they stood BEFORE the round now being revealed.
var BEFORE = [
    {{ name: 'Anna', score: 12 }},
    {{ name: 'Ben', score: 12 }},
    {{ name: 'Cleo', score: 0 }}
];
// …and what the settlement made of them, which is what the television shows.
var AFTER = [
    {{ name: 'Anna', score: 26 }},
    {{ name: 'Ben', score: 26 }},
    {{ name: 'Cleo', score: 0 }}
];

renderLeaderboard(els.gameLeaderboard, BEFORE);
var before = board();

handleRoundSummary({{
    correct_answer: 'Lisbon', leaderboard: AFTER, round: 3, total_rounds: 5
}});
var atReveal = board();

// A frame that carries no leaderboard must leave the board alone rather than
// blank it — the reveal is the one screen the host reads the scores from.
handleRoundSummary({{ correct_answer: 'Lisbon', round: 4, total_rounds: 5 }});
var withoutTheField = board();

// The last round relabels the button; the board still has to move.
renderLeaderboard(els.gameLeaderboard, BEFORE);
handleRoundSummary({{
    correct_answer: 'Lisbon', leaderboard: AFTER, last_round: true
}});
var lastRound = {{
    board: board(),
    nextKey: els.nextQuestionBtn.getAttribute('data-i18n')
}};

console.log(JSON.stringify({{
    before: before,
    atReveal: atReveal,
    withoutTheField: withoutTheField,
    lastRound: lastRound
}}));
"""


def _reveal() -> dict:
    return _node(
        _REVEAL_SCRIPT.format(
            stub=json.dumps(str(_STUB)),
            utils=json.dumps(str(_JS / "utils.js")),
            shared=json.dumps(str(_JS / "render-shared.js")),
            render=_slice(
                _ADMIN,
                "    function renderLeaderboard(container, players) {",
                "    function renderPodium(container, podium)",
            ),
            reveal=_slice(
                _ADMIN,
                "    function handleRoundSummary(msg) {",
                "    function handleFinale(msg) {",
            ),
        )
    )


@_NEEDS_NODE
def test_the_board_starts_the_reveal_showing_the_previous_standings() -> None:
    """Guards the guard: without the pre-round board painted first, the
    assertion below would pass on an empty element."""
    assert _reveal()["before"]["scores"] == [12, 12, 0]


@_NEEDS_NODE
def test_the_reveal_moves_the_host_page_to_the_scores_the_room_can_see() -> None:
    """The issue's two DOM reads, one moment apart on two screens:

        admin:     "1 Anna 12 | 1 Ben 12 | 3 Cleo 0"
        dashboard: "1 Anna 26 | 1 Ben 26 | 3 Cleo 0"
    """
    result = _reveal()

    assert result["atReveal"]["scores"] == [26, 26, 0]
    assert result["atReveal"]["names"] == ["Anna", "Ben", "Cleo"]


@_NEEDS_NODE
def test_a_summary_without_a_leaderboard_leaves_the_board_standing() -> None:
    """Every builder of this frame carries the field, but a blanked board on a
    frame that did not is a worse failure than a stale one."""
    assert _reveal()["withoutTheField"]["scores"] == [26, 26, 0]


@_NEEDS_NODE
def test_the_last_round_updates_the_board_and_the_button() -> None:
    """#806 put the Final Results relabel in this handler; the board update
    shares it and must not have displaced it."""
    result = _reveal()

    assert result["lastRound"]["board"]["scores"] == [26, 26, 0]
    assert result["lastRound"]["nextKey"] == "reveal.finalResults"


def test_the_reveal_renders_the_field_the_frame_declares() -> None:
    """``round_summary`` declares ``leaderboard`` in protocol.py, which is why
    the television could render it all along."""
    assert "leaderboard" in protocol.SERVER_FRAMES["round_summary"].required

    body = _without_comments(
        _slice(
            _ADMIN,
            "    function handleRoundSummary(msg) {",
            "    function handleFinale(msg) {",
        )
    )
    assert "renderLeaderboard(els.gameLeaderboard, msg.leaderboard)" in body


# ---------------------------------------------------------------------------
# #851 — the lobby knows the difficulty of the game about to be played
# ---------------------------------------------------------------------------


class _FakeRuntime:
    def __init__(self, tmp_path: Path) -> None:
        self.data_dir = tmp_path

    def create_task(self, coro):  # noqa: ANN001, ANN202
        return asyncio.ensure_future(coro)

    async def run_in_executor(self, func, *args):  # noqa: ANN001, ANN002
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, func, *args)


def _ws() -> MagicMock:
    ws = MagicMock()
    ws.closed = False
    ws.send_json = AsyncMock()
    ws.close = AsyncMock()
    return ws


@pytest.fixture
def game(tmp_path: Path) -> QuizifyGameState:
    return QuizifyGameState(runtime=_FakeRuntime(tmp_path), entry_id="test")


@pytest.fixture
def handler(game, tmp_path: Path):
    h = QuizifyWebSocketHandler(
        runtime=_FakeRuntime(tmp_path), game_state_provider=lambda: game
    )
    h._conn = ConnectionManager(_FakeRuntime(tmp_path), lambda: game)
    h._conn.send_error = AsyncMock()
    h._broadcast_state_projected = AsyncMock()
    return h


def test_a_fresh_lobby_still_reports_the_default_difficulty() -> None:
    """The default is not the bug. The bug is that nothing could change it
    until start_game, half an hour of lobby later — so the first game of the
    evening advertised Medium whatever the host had picked."""
    source = (_CC / "game" / "state.py").read_text("utf-8")
    assert "self.difficulty: str = DIFFICULTY_DEFAULT" in source


@pytest.mark.asyncio
async def test_the_host_difficulty_reaches_the_lobby_snapshot(handler, game) -> None:
    """The whole issue: the snapshot said "medium" until start_game, so the
    phone that joined an Easy lobby was handed the previous game's word."""
    assert game.phase == GamePhase.LOBBY
    assert serialize_state_snapshot(game)["difficulty"] == "medium"

    await handler._handle_set_difficulty(_ws(), {"difficulty": "easy"}, game)

    assert game.difficulty == "easy"
    assert serialize_state_snapshot(game)["difficulty"] == "easy"


@pytest.mark.asyncio
async def test_the_previous_games_difficulty_is_overwritten(handler, game) -> None:
    """The live test's own sequence: one game on Medium, then a new game set to
    Einfach. The field survives the finished game, so "unset" is not a state
    the lobby can be in — only "the last one's" or "this one's"."""
    game.difficulty = "hard"

    await handler._handle_set_difficulty(_ws(), {"difficulty": "easy"}, game)

    assert serialize_state_snapshot(game)["difficulty"] == "easy"


@pytest.mark.asyncio
async def test_the_change_reaches_phones_already_in_the_lobby(handler, game) -> None:
    """A guest who joined before the host reached the difficulty chip is
    exactly the guest reading the stale badge."""
    await handler._handle_set_difficulty(_ws(), {"difficulty": "hard"}, game)

    handler._broadcast_state_projected.assert_awaited_once()
    assert handler._broadcast_state_projected.await_args.args[0] is game


@pytest.mark.asyncio
async def test_an_unchanged_difficulty_costs_no_broadcast(handler, game) -> None:
    """admin.js pushes on every socket open, and the socket reopens on every HA
    restart and network blip."""
    await handler._handle_set_difficulty(_ws(), {"difficulty": "medium"}, game)

    assert game.difficulty == "medium"
    handler._broadcast_state_projected.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("value", ["easy", "medium", "hard", "auto"])
async def test_every_chip_the_setup_screen_offers_is_accepted(
    handler, game, value
) -> None:
    """The four chips in admin.html. "auto" is a real pick (#40), not a
    placeholder, and refusing it would leave the lobby on the last game's."""
    game.difficulty = "hard" if value != "hard" else "easy"

    await handler._handle_set_difficulty(_ws(), {"difficulty": value}, game)

    assert game.difficulty == value


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "payload",
    [{}, {"difficulty": None}, {"difficulty": ""}, {"difficulty": 7},
     {"difficulty": "impossible"}],
)
async def test_a_junk_payload_leaves_the_difficulty_alone(
    handler, game, payload
) -> None:
    """Untyped client JSON. A word no bundle can translate would paint a badge
    nothing in the room can read."""
    await handler._handle_set_difficulty(_ws(), payload, game)

    assert game.difficulty == "medium"
    handler._broadcast_state_projected.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "phase",
    [GamePhase.QUESTION_ACTIVE, GamePhase.ANSWER_REVEAL, GamePhase.FINALE],
)
async def test_it_is_refused_once_the_game_is_running(handler, game, phase) -> None:
    """Relabelling a running game underneath the players is worse than the bug
    — the questions would not change with the label."""
    game.phase = phase

    await handler._handle_set_difficulty(_ws(), {"difficulty": "easy"}, game)

    assert game.difficulty == "medium"
    handler._broadcast_state_projected.assert_not_awaited()


def test_the_message_is_admin_gated() -> None:
    handler_fn, admin_required = QuizifyWebSocketHandler._DISPATCH[MSG_SET_DIFFICULTY]
    assert admin_required is True
    assert handler_fn is not None


def test_the_wire_contract_declares_it() -> None:
    assert MSG_SET_DIFFICULTY == "set_difficulty"
    assert MSG_SET_DIFFICULTY in protocol.CLIENT_MESSAGE_TYPES


def test_start_game_still_carries_the_difficulty() -> None:
    """The lobby push is an addition, not a replacement: a game started from
    the HA service, or by a host who reloaded the admin page, must still
    arrive with one."""
    ws_source = (_CC / "server" / "websocket.py").read_text("utf-8")
    assert 'difficulty = data.get("difficulty")' in ws_source

    payload_line = (
        "difficulty: selectedDifficulty === 'mixed' ? null : selectedDifficulty,"
    )
    assert payload_line in _ADMIN.read_text("utf-8")


def test_the_admin_pushes_the_difficulty_on_connect() -> None:
    """What makes the FIRST phone to join correct — a host who never touches
    the chip is a host serving the previous game's setting."""
    source = _without_comments(_ADMIN.read_text("utf-8"))
    onopen = source.split("onOpen: function ()", 1)[1].split("onMessage:", 1)[0]

    assert "_pushDifficulty()" in onopen
    assert "send('set_difficulty'" in source


def test_the_admin_pushes_the_difficulty_when_the_chip_changes() -> None:
    source = _without_comments(_ADMIN.read_text("utf-8"))
    callback = source.split("setupChips(els.difficultyChips", 1)[1][:600]

    assert "selectedDifficulty = v" in callback
    assert "_pushDifficulty()" in callback
    assert callback.index("selectedDifficulty = v") < callback.index(
        "_pushDifficulty()"
    ), "push the new value, not the old one"


def test_the_admin_pushes_the_difficulty_when_a_preset_sets_it() -> None:
    """"Schnellrunde" sets easy without the chip handler ever running, and a
    preset is how the live test's host set the game up."""
    source = _without_comments(_ADMIN.read_text("utf-8"))
    body = source.split("function _applyPreset(", 1)[1].split(
        "function _activateChip(", 1
    )[0]

    assert "_pushDifficulty()" in body


# ---- …and paints it in the room's language --------------------------------


_BADGE_SCRIPT = """
require({stub});
QZ.serveI18n({i18n});
QZ.load({i18njs});

QZ.els(['lobby-difficulty-badge', 'player-count-badge', 'players-summary',
        'players-empty', 'player-list', 'pl-also', 'pl-meta']);

window.QuizifyPlayerUtils = {{
    state: {{ playerName: 'Anna', playerColor: '#A89E89', isAdmin: false }},
    escapeHtml: function (s) {{ return String(s); }},
    generateQR: function () {{}},
    showToast: function () {{}}
}};
window.QuizifyPlayer = {{ send: function () {{}} }};
QZ.load({lobby});

function badge() {{
    var el = document.getElementById('lobby-difficulty-badge');
    return {{
        text: el.textContent,
        hidden: el.classList.contains('hidden')
    }};
}}

var ROOM = {{ players: [{{ name: 'Anna', color: '#A89E89' }}], difficulty: 'easy' }};

(async function () {{
    // The phone paints its first frame in the language its browser reports…
    await window.QuizifyI18n.init('en');
    window.QuizifyPlayerLobby.renderLobby(ROOM);
    var english = badge();

    // …and one heartbeat later the room turns out to be German. This is the
    // same sweep every other translated string on the page gets.
    await window.QuizifyI18n.setLanguage('de');
    window.QuizifyI18n.initPageTranslations();
    var german = badge();

    await window.QuizifyI18n.setLanguage('es');
    window.QuizifyI18n.initPageTranslations();
    var spanish = badge();

    // A frame with no difficulty in it hides the pill rather than leaving the
    // last one standing.
    window.QuizifyPlayerLobby.renderLobby({{ players: ROOM.players }});
    var none = badge();

    // …and the sweep must not write the word back into an emptied badge.
    window.QuizifyI18n.initPageTranslations();
    var noneAfterSweep = badge();

    // Repainting with a difficulty brings it back, in the current language.
    window.QuizifyPlayerLobby.renderLobby({{
        players: ROOM.players, difficulty: 'hard'
    }});
    var hardSpanish = badge();

    console.log(JSON.stringify({{
        english: english, german: german, spanish: spanish,
        none: none, noneAfterSweep: noneAfterSweep, hardSpanish: hardSpanish
    }}));
}})();
"""


def _badge() -> dict:
    return _node(
        _BADGE_SCRIPT.format(
            stub=json.dumps(str(_STUB)),
            i18n=json.dumps(str(_I18N)),
            i18njs=json.dumps(str(_JS / "i18n.js")),
            lobby=json.dumps(str(_LOBBY)),
        )
    )


@_NEEDS_NODE
def test_the_badge_is_painted_in_the_language_of_the_moment() -> None:
    """Guards the guard — the writer was never the broken half."""
    result = _badge()

    assert result["english"] == {"text": "🌱 Easy", "hidden": False}


@_NEEDS_NODE
def test_a_language_change_re_renders_the_badge() -> None:
    """The measured symptom: `Spiel-Lobby`, `Warte, bis der Host das Spiel
    startet…` and, between them, the English word."""
    result = _badge()

    assert result["german"]["text"] == "🌱 Einfach"
    assert result["spanish"]["text"] == "🌱 Fácil"


@_NEEDS_NODE
def test_a_frame_without_a_difficulty_empties_the_badge() -> None:
    """And keeps it empty: a hollow pill that the next sweep re-fills with the
    last game's word would be the same bug wearing the fix."""
    result = _badge()

    assert result["none"] == {"text": "", "hidden": True}
    assert result["noneAfterSweep"] == {"text": "", "hidden": True}


@_NEEDS_NODE
def test_a_later_frame_repaints_the_badge_in_the_current_language() -> None:
    """The host flips the chip mid-lobby; the phone is on its third language.
    Both halves of the fix meet here."""
    assert _badge()["hardSpanish"] == {"text": "🔥 Difícil", "hidden": False}


# ---------------------------------------------------------------------------
# #852 — the Hot Seat's seat question clears the polite region
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

    // The auction's clock crosses ten seconds and says so.
    announceTimeLeft(10);
    var auction = read();

    // The chair is won and the seat question opens with a full clock. Its
    // ticks arrive as hot_seat_tick -> updateTimer -> announceTimeLeft, never
    // through startCountdown, so this is the only place that can notice.
    announceTimeLeft(24);
    var seatQuestion = read();

    // …and the sweep must not write the leftover sentence back.
    window.QuizifyI18n.initPageTranslations();
    var afterSweep = read();

    // The seat question's own countdown still announces.
    announceTimeLeft(10);
    var seatQuestionAtTen = read();

    console.log(JSON.stringify({{
        auction: auction, seatQuestion: seatQuestion,
        afterSweep: afterSweep, seatQuestionAtTen: seatQuestionAtTen
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
def test_the_auction_still_announces_its_last_ten_seconds() -> None:
    """Guards the guard, and the capability: the bid slider has a clock too,
    and blind bidders need it."""
    assert _announce()["auction"]["text"] == "10 seconds left"


@_NEEDS_NODE
def test_a_clock_above_ten_seconds_empties_the_region() -> None:
    """The reported reading, off a phone with the seat question live:

        { text: "10 seconds left", "data-i18n": "game.timerTenLeft" }
        visible countdown at the same moment: 24
    """
    result = _announce()

    assert result["seatQuestion"] == {"text": "", "key": None}
    assert result["afterSweep"] == {"text": "", "key": None}


@_NEEDS_NODE
def test_clearing_does_not_cost_the_seat_question_its_own_warning() -> None:
    """`lastAnnouncedSecond` dedupes ticks at the same whole second; a clear
    that left it at 10 would silence the chair's own countdown."""
    assert _announce()["seatQuestionAtTen"]["text"] == "10 seconds left"


def test_the_seat_question_clears_the_region_as_it_opens() -> None:
    """Not only on the first tick after it: a screen-reader user who tabs back
    in as the chair's question appears must not be told there are ten seconds
    left. Cleared from the dispatch, which is where every other question
    clears it from, so player-hotseat.js keeps its one job."""
    source = _without_comments(_CORE.read_text("utf-8"))
    body = source.split("case 'hot_seat_question':", 1)[1].split("break;", 1)[0]

    assert "game.clearTimeAnnouncement()" in body
    assert "hotSeat.handleQuestion(msg)" in body


def test_the_ordinary_question_path_is_untouched() -> None:
    """#839's fix is what this one extends, not what it replaces."""
    source = _without_comments(_GAME.read_text("utf-8"))
    body = source.split("function stopCountdown()", 1)[1].split("\n    }", 1)[0]

    assert "clearTimeAnnouncement()" in body
