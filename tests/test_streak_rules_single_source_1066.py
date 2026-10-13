"""The streak rules have one owner, ``game/scoring.py`` (#1066).

They used to live twice, and the two copies had drifted:

* **Milestones.** ``STREAK_MILESTONES`` pays a one-off bonus at 3, 5, 10, 15,
  20 and 25. The phone picked its toast from a map of its own, ``{3, 5, 7}``,
  matched against ``new_streak`` — so a 7-streak was celebrated although it
  paid nothing.
* **"Is this a streak yet?"** The multiplier starts at
  ``STREAK_MULTIPLIER_MIN_STREAK`` (3 since #1062). The reveal promoted itself
  to the streak headline from 2 (``STREAK_HERO_THRESHOLD``) and both
  leaderboard rows printed their badge from ``streak > 1`` — a streak announced
  one answer before it scored.

Now the server says both things on the wire and the client only renders them:
``milestone_streak`` on ``answer_result`` fires the toast, and ``streak_active``
rides beside every ``streak`` a client paints.

What this file deliberately does NOT decide, because the owner has not:
whether 7 becomes a real milestone or ``game.streakToast7`` goes, and what the
toasts for 10/15/20/25 say. A milestone without a string stays silent, exactly
as before; the unused string for 7 stays in the locale files.

The last section is the guard: it fails when the JS grows a streak threshold
or a milestone list of its own again, which is how the drift started.
"""

from __future__ import annotations

import ast
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

_REPO = Path(__file__).resolve().parent.parent
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from custom_components.quizify.game.phase_controller import GamePhase  # noqa: E402
from custom_components.quizify.game.player import PlayerSession  # noqa: E402
from custom_components.quizify.game.scoring import (  # noqa: E402
    STREAK_MILESTONES,
    STREAK_MULTIPLIER_MIN_STREAK,
    get_streak_multiplier,
    is_streak_active,
)
from custom_components.quizify.game.state import (  # noqa: E402
    AnswerResult,
    QuizifyGameState,
)
from custom_components.quizify.server.connection import ConnectionManager  # noqa: E402
from custom_components.quizify.server.serializers import (  # noqa: E402
    serialize_leaderboard,
    serialize_player_list,
)
from custom_components.quizify.server.websocket import (  # noqa: E402
    QuizifyWebSocketHandler,
)

_PKG = _REPO / "custom_components" / "quizify"
_WWW = _PKG / "www"
_JS = _WWW / "js"
_I18N = _WWW / "i18n"
_STUB = Path(__file__).resolve().parent / "fixtures" / "dom_stub.js"

_NEEDS_NODE = pytest.mark.skipif(
    shutil.which("node") is None, reason="node not installed"
)

#: Streaks on both sides of every rule: below the multiplier, on it, each
#: milestone, and the 7 the phone used to celebrate on its own.
_STREAKS = sorted({0, 1, 2, 3, 4, 7, *STREAK_MILESTONES})

_EN = json.loads((_I18N / "en.json").read_text("utf-8"))


def _toast_string(level: int) -> str | None:
    """The English toast for a streak level, if the locale has one."""
    return _EN["game"].get(f"streakToast{level}")


# ---------------------------------------------------------------------------
# The server: one predicate, sent wherever a streak is painted
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("streak", range(0, 30))
def test_a_streak_is_active_exactly_when_the_multiplier_pays(streak: int) -> None:
    """Measured against the multiplier itself rather than against the constant,
    so a second threshold inside ``is_streak_active`` could not pass."""
    assert is_streak_active(streak) is (get_streak_multiplier(streak) > 1.0)


def _players() -> list[PlayerSession]:
    # Distinct scores keep the leaderboard order (and so the row order) fixed.
    return [
        PlayerSession(name=f"P{streak}", streak=streak, score=1000 - streak)
        for streak in _STREAKS
    ]


def test_every_leaderboard_row_carries_the_verdict() -> None:
    rows = serialize_leaderboard(_players())

    assert [row["streak"] for row in rows] == _STREAKS
    for row in rows:
        assert row["streak_active"] is (get_streak_multiplier(row["streak"]) > 1.0)


def test_the_roster_row_carries_it_too() -> None:
    """Same ``streak`` key, same reader on the host page, so the same flag."""
    for row in serialize_player_list(_players()):
        assert row["streak_active"] is (get_streak_multiplier(row["streak"]) > 1.0)


def _server_dict_literals() -> list[tuple[str, int, set[str]]]:
    """Every dict literal under ``server/``, as (file, line, literal keys)."""
    found: list[tuple[str, int, set[str]]] = []
    for path in sorted((_PKG / "server").glob("*.py")):
        for node in ast.walk(ast.parse(path.read_text("utf-8"))):
            if not isinstance(node, ast.Dict):
                continue
            keys = {
                k.value
                for k in node.keys
                if isinstance(k, ast.Constant) and isinstance(k.value, str)
            }
            found.append((path.name, node.lineno, keys))
    return found


def test_no_frame_sends_a_streak_without_saying_whether_it_scores() -> None:
    """A row built with ``streak`` and no ``streak_active`` is where a client
    would have to guess again. Read from the source, so the next builder that
    adds a row is covered without being listed here."""
    bare = [
        f"{name}:{line}"
        for name, line, keys in _server_dict_literals()
        if "streak" in keys and "streak_active" not in keys
    ]
    assert not bare, (
        "these rows carry `streak` but not `streak_active`; add "
        f"is_streak_active(...) beside it (#1066): {bare}"
    )


def test_the_guard_above_actually_sees_the_rows() -> None:
    """A walker that found nothing would pass the test above for ever."""
    carrying = [
        name for name, _line, keys in _server_dict_literals() if "streak" in keys
    ]
    assert carrying.count("serializers.py") >= 2
    assert carrying.count("round_message_builder.py") >= 2


@pytest.fixture
def handler() -> QuizifyWebSocketHandler:
    hass = MagicMock()
    hass.data = {}
    h = QuizifyWebSocketHandler(hass, QuizifyGameState())
    h._conn = MagicMock(spec=ConnectionManager)
    h._conn.send = AsyncMock()
    h._conn.send_error = AsyncMock()
    h._conn.broadcast = AsyncMock()
    return h


async def _answer_result_frame(
    handler: QuizifyWebSocketHandler, result: AnswerResult
) -> dict:
    """Run the real submit path and return the ``answer_result`` it sent."""
    ws = MagicMock()
    player = MagicMock()
    player.name = result.player_id
    gs = MagicMock()
    gs.phase = GamePhase.QUESTION_ACTIVE
    gs.get_player_by_connection.return_value = player
    question = MagicMock()
    question.is_estimate = False
    gs.get_current_question.return_value = question
    gs.get_player_shuffle.return_value = [0, 1, 2, 3]
    gs.submit_answer.return_value = result
    handler._tts_announcer = MagicMock()
    handler._event_emitter = MagicMock()

    await handler._handle_submit_answer(ws, {"answer_index": 0}, gs)

    frames = [
        call.args[1]
        for call in handler._conn.send.await_args_list
        if len(call.args) > 1
        and isinstance(call.args[1], dict)
        and call.args[1].get("type") == "answer_result"
    ]
    assert len(frames) == 1, handler._conn.send.await_args_list
    return frames[0]


@pytest.mark.asyncio
@pytest.mark.parametrize("streak", _STREAKS)
async def test_answer_result_says_what_the_streak_is_worth(
    handler: QuizifyWebSocketHandler, streak: int
) -> None:
    bonus = STREAK_MILESTONES.get(streak, 0)
    frame = await _answer_result_frame(
        handler,
        AnswerResult(
            player_id="Ann",
            correct=streak > 0,
            points_earned=20,
            new_streak=streak,
            new_total=200,
            milestone_bonus=bonus,
            milestone_streak=streak if bonus else 0,
        ),
    )

    assert frame["streak_active"] is (get_streak_multiplier(streak) > 1.0)
    assert frame["milestone_streak"] == (streak if bonus else 0)
    assert frame["milestone_bonus"] == bonus


# ---------------------------------------------------------------------------
# The phone toast: fired by the server's milestone, not by a list of its own
# ---------------------------------------------------------------------------


def _node(script: str) -> dict:
    out = subprocess.run(
        ["node", "-e", script], capture_output=True, text=True, check=False
    )
    assert out.returncode == 0, f"node harness failed:\n{out.stderr}"
    return json.loads(out.stdout.strip().splitlines()[-1])


def _answer_result_case() -> str:
    """The real ``case 'answer_result':`` body, lifted out of player-core.js."""
    source = (_JS / "player-core.js").read_text("utf-8")
    start = source.index("case 'answer_result':") + len("case 'answer_result':")
    return source[start : source.index("break;", start)]


_TOAST_SCRIPT = """
require({stub});
QZ.serveI18n({i18n});
QZ.load({i18njs});
QZ.load({utils_js});
QZ.load({client_core});
QZ.load({player_utils});

// showToast makes this node on first use in a browser; the stub only finds
// elements it was told about, so it is declared the way the page would have it.
var toast = QZ.el('error-toast');
var pu = window.QuizifyPlayerUtils;
var t = window.QuizifyI18n.t;
var game = {{ handleAnswerResult: function () {{}} }};

// What the guest reads: the toast's text while it is up, '' when none fired.
function toastAfter(msg) {{
    pu.clearToast();
    {case_body}
    if (toast.style.opacity !== '1') return '';
    return String(toast.textContent);
}}

(async function () {{
    await window.QuizifyI18n.init('en');
    var frames = {frames};
    var out = {{}};
    Object.keys(frames).forEach(function (k) {{ out[k] = toastAfter(frames[k]); }});
    console.log(JSON.stringify(out));
}})().catch(function (e) {{ console.error(e); process.exit(1); }});
"""


def _frame(streak: int, *, correct: bool = True) -> dict:
    """The answer_result the server sends for a player now on ``streak``."""
    bonus = STREAK_MILESTONES.get(streak, 0)
    return {
        "type": "answer_result",
        "correct": correct,
        "new_streak": streak,
        "streak_active": is_streak_active(streak),
        "milestone_bonus": bonus,
        "milestone_streak": streak if bonus else 0,
    }


@pytest.fixture(scope="module")
def toasts() -> dict:
    frames = {str(streak): _frame(streak) for streak in _STREAKS}
    # A frame that names a milestone but is not a correct answer: never sent
    # today, and a toast on it would be a celebration of a wrong answer.
    frames["wrong"] = {**_frame(3), "correct": False}
    return _node(
        _TOAST_SCRIPT.format(
            stub=json.dumps(str(_STUB)),
            i18n=json.dumps(str(_I18N)),
            i18njs=json.dumps(str(_JS / "i18n.js")),
            utils_js=json.dumps(str(_JS / "utils.js")),
            client_core=json.dumps(str(_JS / "client-core.js")),
            player_utils=json.dumps(str(_JS / "player-utils.js")),
            case_body=_answer_result_case(),
            frames=json.dumps(frames),
        )
    )


@_NEEDS_NODE
@pytest.mark.parametrize("streak", sorted(STREAK_MILESTONES))
def test_a_paid_milestone_shows_its_toast_when_it_has_a_string(
    toasts: dict, streak: int
) -> None:
    """3 and 5 have strings today; 10/15/20/25 have none and stay silent until
    the owner writes them — no raw key, no borrowed text."""
    assert toasts[str(streak)] == (_toast_string(streak) or "")


@_NEEDS_NODE
def test_the_milestones_that_have_strings_do_fire(toasts: dict) -> None:
    """So the test above cannot go green on a toast that never shows at all."""
    assert toasts["3"] == _EN["game"]["streakToast3"]
    assert toasts["5"] == _EN["game"]["streakToast5"]


@_NEEDS_NODE
@pytest.mark.parametrize(
    "streak", [s for s in _STREAKS if s not in STREAK_MILESTONES]
)
def test_a_streak_that_pays_no_bonus_shows_no_toast(toasts: dict, streak: int) -> None:
    """The reported case is 7: "7 in a row! On fire!" over a round that paid
    no bonus. The string still exists; nothing the server sends asks for it."""
    assert toasts[str(streak)] == ""


@_NEEDS_NODE
def test_a_wrong_answer_is_never_toasted(toasts: dict) -> None:
    assert toasts["wrong"] == ""


# ---------------------------------------------------------------------------
# The reveal headline and the two leaderboard rows
# ---------------------------------------------------------------------------

_RENDER_SCRIPT = """
require({stub});
QZ.serveI18n({i18n});
QZ.load({i18njs});

QZ.els([
    'reveal-hero', 'reveal-estimate', 'reveal-answer-strip', 'reveal-standings',
    'header-round-num', 'header-round-total', 'fun-fact-container', 'fun-fact',
    'reveal-admin-controls', 'next-round-btn', 'flag-question-btn'
]);
QZ.load({utils_js});
QZ.load({client_core});
QZ.load({render_shared});
QZ.load({player_utils});
QZ.load({player_game});
QZ.load({player_team});
QZ.load({player_reveal});

var pu = window.QuizifyPlayerUtils;
window.QuizifyPlayerSound = {{
    playCorrect: function () {{}},
    playWrong: function () {{}}
}};
pu.state.playerName = 'Anna';

function hero(row) {{
    window.QuizifyPlayerReveal.updateRevealView({{
        question_id: 'q1', round: 1, total_rounds: 10,
        correct_answer: 'Paris',
        players: [], leaderboard: [],
        all_answers: [{{
            player_name: 'Anna', answer_index: 0, answer_text: 'Paris',
            correct: true, correct_button_index: 0, points_earned: 15,
            streak: row.streak, streak_active: row.streak_active
        }}]
    }});
    // Read from the markup, which the renderer replaces on every call; the
    // stub's classList outlives `className = ...` and would stay "streak".
    var html = document.getElementById('reveal-hero').innerHTML;
    return html.indexOf('pl-result-headline') !== -1;
}}

function badge(html) {{
    var m = /<span class="leaderboard-streak">([^<]*)<\\/span>/.exec(html);
    return m ? m[1] : '';
}}

(async function () {{
    await window.QuizifyI18n.init('en');
    var rows = {rows};
    var out = {{ hero: {{}}, board: {{}}, phone: {{}} }};
    rows.forEach(function (row) {{
        out.hero[row.streak] = hero(row);
        out.board[row.streak] = badge(
            window.QuizifyRenderShared.leaderboardRowsHtml([row]));
        out.phone[row.streak] = badge(
            window.QuizifyPlayerGame.renderLeaderboardEntry(row));
    }});
    console.log(JSON.stringify(out));
}})().catch(function (e) {{ console.error(e); process.exit(1); }});
"""


@pytest.fixture(scope="module")
def painted() -> dict:
    """Rows the real serializer built, painted by the real renderers."""
    return _node(
        _RENDER_SCRIPT.format(
            stub=json.dumps(str(_STUB)),
            i18n=json.dumps(str(_I18N)),
            i18njs=json.dumps(str(_JS / "i18n.js")),
            utils_js=json.dumps(str(_JS / "utils.js")),
            client_core=json.dumps(str(_JS / "client-core.js")),
            render_shared=json.dumps(str(_JS / "render-shared.js")),
            player_utils=json.dumps(str(_JS / "player-utils.js")),
            player_game=json.dumps(str(_JS / "player-game.js")),
            player_team=json.dumps(str(_JS / "player-team.js")),
            player_reveal=json.dumps(str(_JS / "player-reveal.js")),
            rows=json.dumps(serialize_leaderboard(_players())),
        )
    )


@_NEEDS_NODE
@pytest.mark.parametrize("streak", _STREAKS)
def test_the_reveal_headline_follows_the_multiplier(painted: dict, streak: int) -> None:
    """Streak 2 is the reported case: the flame headline over a round the
    multiplier did not touch."""
    assert painted["hero"][str(streak)] is (get_streak_multiplier(streak) > 1.0)


@_NEEDS_NODE
@pytest.mark.parametrize("surface", ["board", "phone"])
@pytest.mark.parametrize("streak", _STREAKS)
def test_the_leaderboard_badge_follows_the_multiplier(
    painted: dict, surface: str, streak: int
) -> None:
    """``board`` is the row the television and the host page share
    (render-shared.js); ``phone`` is the in-game list in player-game.js."""
    expected = f"{streak}x" if get_streak_multiplier(streak) > 1.0 else ""
    assert painted[surface][str(streak)] == expected


# ---------------------------------------------------------------------------
# The guard: the JS keeps no streak rule of its own
# ---------------------------------------------------------------------------

#: The generated bundles are copies of the modules; counting them would report
#: every finding twice and let a stale build hide one.
_GENERATED = ("player.bundle.js", "common.bundle.js")

#: A streak-named value compared with a number, in either order —
#: ``entry.streak > 1``, ``prevStreak >= 2``, ``3 <= msg.new_streak``.
_COMPARED = re.compile(
    r"(?i)(?:[\w.$]*streak[\w$]*\s*(?:[<>]=?|[!=]==?)\s*\d"
    r"|\d\s*(?:[<>]=?|[!=]==?)\s*[\w.$]*streak)"
)
#: A streak-named constant holding a number — ``STREAK_HERO_THRESHOLD = 2``.
_CONSTANT = re.compile(r"(?i)\b[\w$]*streak[\w$]*\s*=\s*\d")
#: A streak level mapped to its toast — ``3: 'game.streakToast3'`` — or a bare
#: list of levels next to the word.
_LEVEL_MAP = re.compile(r"\d+\s*:\s*['\"]game\.streakToast")

#: Findings that are known and not a second copy of a live rule. Every entry
#: says why; nothing goes on this list without a reason, and
#: ``test_every_allowed_finding_still_exists`` drops it again when the code
#: goes.
_ALLOWED = {
    ("player-reveal.js", "prevStreak >= 2"): (
        "the 'streak lost' line reads `previous_streak`, which no frame "
        "carries (it is always 0 here), so this number decides nothing today. "
        "Wiring it up means sending the verdict from the server like "
        "`streak_active`, not reviving the literal."
    ),
}


def _without_comments(source: str) -> str:
    source = re.sub(r"/\*.*?\*/", "", source, flags=re.S)
    return re.sub(r"//.*$", "", source, flags=re.M)


def _front_end_sources() -> list[Path]:
    modules = sorted(p for p in _JS.rglob("*.js") if p.name not in _GENERATED)
    return modules + sorted(_WWW.glob("*.html"))


def _findings() -> list[tuple[str, str]]:
    found: list[tuple[str, str]] = []
    for path in _front_end_sources():
        code = _without_comments(path.read_text("utf-8"))
        for pattern in (_COMPARED, _CONSTANT, _LEVEL_MAP):
            for match in pattern.finditer(code):
                found.append((path.name, match.group(0).strip()))
    return found


def _is_allowed(finding: tuple[str, str]) -> bool:
    name, text = finding
    return any(
        name == allowed_name and allowed_text.startswith(text.rstrip("0123456789"))
        for allowed_name, allowed_text in _ALLOWED
    )


def test_the_front_end_keeps_no_streak_threshold_of_its_own() -> None:
    """A number next to a streak in the JS is a second copy of a rule in
    ``scoring.py`` — and the two copies had already disagreed twice."""
    stray = [f for f in _findings() if not _is_allowed(f)]
    assert not stray, (
        "streak rules belong to game/scoring.py (#1066). Read `streak_active` "
        "or `milestone_streak` from the frame instead of comparing against a "
        f"number here: {stray}"
    )


def test_every_allowed_finding_still_exists() -> None:
    """An exemption left behind after its code is gone would silently widen
    the net for the next literal in that file."""
    present = {
        (name, text)
        for name, text in _ALLOWED
        if text in _without_comments((_JS / name).read_text("utf-8"))
    }
    assert present == set(_ALLOWED)


def test_only_the_icon_table_names_a_toast_level() -> None:
    """``game.streakToast<N>`` is assembled from the server's milestone. The
    one place that may still spell a level out is the key → glyph table in
    player-utils.js, which decides an icon and not whether a toast fires."""
    spelled = sorted(
        path.name
        for path in _front_end_sources()
        if "streakToast" in _without_comments(path.read_text("utf-8"))
    )
    assert spelled == ["player-utils.js"]


def test_the_toast_is_not_chosen_from_the_running_streak() -> None:
    """``new_streak`` says how long the streak is, not whether it paid; the
    old code matched it against its own list. The handler reads the
    milestone."""
    body = _without_comments(_answer_result_case())
    assert "milestone_streak" in body
    assert "new_streak" not in body


@pytest.mark.parametrize(
    "snippet",
    [
        "var streakBadge = entry.streak > 1 ? a : b;",
        "(p.streak > 1 ? x : '')",
        "var STREAK_HERO_THRESHOLD = 2;",
        "var streakKeys = { 3: 'game.streakToast3', 5: 'game.streakToast5' };",
        "if (3 <= msg.new_streak) {}",
        "if (msg.new_streak === 7) {}",
    ],
)
def test_the_guard_catches_the_shapes_the_drift_took(snippet: str) -> None:
    """The four lines #1066 removed, plus the two obvious ways to write them
    again. A guard is only worth its patterns."""
    assert any(p.search(snippet) for p in (_COMPARED, _CONSTANT, _LEVEL_MAP))


def test_the_multiplier_threshold_is_still_the_one_the_tests_were_written_for() -> None:
    """Not a rule of this file — a tripwire. If the threshold moves, the
    parametrised streaks above (0–4 around it) need another look."""
    assert STREAK_MULTIPLIER_MIN_STREAK in _STREAKS
    assert STREAK_MULTIPLIER_MIN_STREAK - 1 in _STREAKS
