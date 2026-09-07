"""A panel that can be shown must have a frame that takes it away (#864).

Found on real hardware during the v1.16.0-RC4 live test, one release after
#858 built the *arming* half of exactly this guard.

#862 fixed #859 by replacing ``handleResult``'s ``reset()`` with a render of
the Hot Seat settlement. Nothing ever tore the panel down again: the only two
things that hide it — ``hotSeat.reset()`` and the next auction's bid stage —
are called by the NEXT auction, and the Hot Seat fires once per game
(``_hot_seat_fired``). So from the settlement to the podium every remaining
question was played behind the previous chair's result card, measured out of
the DOM on a 390x844 phone one round and five rounds after the settlement::

    hotseat-panel    top  448   bottom  920
    answer-buttons   top 1017   bottom 1225      window.innerHeight  844

The three answers start 173 px below the fold. That is not a cosmetic
leftover, and the reason is *where* the panel sits: every one of these blocks
is a direct child of ``.game-container`` ABOVE ``#answers-container``, so a
visible one does not overlap the answer grid, it pushes it down the page.

**Why RC4's own guard missed it.** ``tests/test_stage_entry_parity_858.py``
asserts that every stage the snapshot can select is reachable from a live
frame. It guards the arming and says nothing about the teardown. The same
asymmetry is recorded five times over in ``server/websocket.py``'s task
registry (#362, #407, #656, #671, #746): a thing is added, and one teardown
path is forgotten.

So this file is the counterpart, built from the same table the client uses.
``GAME_VIEW_PANELS`` in ``player-core.js`` carries one row per block that can
stand between the question and the answer grid — the frames that raise it, the
snapshot phases that own it, and either the teardown this table runs or the
module that already owns one. Two halves check it:

* **Static, and general.** Every block ``player.html`` ships hidden above the
  answer grid must have a row; every row must name a teardown or an owner;
  every ``raisedBy`` must be a frame the server declares and the phone routes.
  A sixth panel added to the page with no row here is a red test.
* **Behavioural, and narrower.** For the row this table actually tears down,
  the real ``player-hotseat.js`` is driven under ``tests/fixtures/dom_stub.js``
  and the answer grid is measured after each frame that opens the next screen.

What it does NOT catch is written out in
``test_the_guard_says_what_it_does_not_cover`` at the bottom, because a guard
that overpromises is worse than none.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO))

from custom_components.quizify.server.protocol import SERVER_FRAMES  # noqa: E402

_CC = _REPO / "custom_components" / "quizify"
_WWW = _CC / "www"
_JS = _WWW / "js"
_I18N = _WWW / "i18n"
_HTML = _WWW / "player.html"
_CORE = _JS / "player-core.js"
_HOTSEAT = _JS / "player-hotseat.js"
_STUB = Path(__file__).resolve().parent / "fixtures" / "dom_stub.js"

_NEEDS_NODE = pytest.mark.skipif(
    shutil.which("node") is None, reason="node not installed"
)

# ---------------------------------------------------------------------------
# The phone, as the live test measured it
# ---------------------------------------------------------------------------

#: iPhone 14 portrait — Markus' test device, and the viewport of every reading
#: in the issue.
VIEWPORT_H = 844

#: Top of ``#hotseat-panel`` with the panel up: everything above it (header,
#: question card, timer, submission tracker) with no optional block showing.
PRELUDE_H = 448

#: ``#hotseat-panel`` bottom 920 → ``#answer-buttons`` top 1017. The section
#: chrome above the grid: heading, team chip, padding.
GRID_CHROME_H = 97

#: ``#answer-buttons`` 1017 → 1225.
GRID_H = 208

#: The one panel height taken off real hardware: 448 → 920.
MEASURED_HEIGHTS = {"hotseat-panel": 472}

#: What any *other* block is charged in the model. Not a measurement of
#: anything: with no optional block up the grid ends at
#: 448 + 97 + 208 = 753, i.e. 91 px above the fold, so 92 is the height at
#: which the measured baseline breaks. It is deliberately punitive, which is
#: why the geometry assertions below are made only for the panels this table
#: tears down — a genuinely small block (the final-round pill) would be
#: charged more than it costs, and asserting on that would be inventing a bug.
BREAKS_THE_FOLD_H = VIEWPORT_H - (PRELUDE_H + GRID_CHROME_H + GRID_H) + 1


def _without_comments(source: str) -> str:
    source = re.sub(r"/\*.*?\*/", "", source, flags=re.S)
    return re.sub(r"^\s*//.*$", "", source, flags=re.M)


# ---------------------------------------------------------------------------
# The two tables, read from the code that ships
# ---------------------------------------------------------------------------


#: What the harness runs when ``player-core.js`` has no teardown table at all
#: — the RC4 client, and any future one that loses it. Deliberately a working
#: no-op rather than a crash: the behavioural half then measures a phone with
#: nothing tearing the panel down, which is the live test's own reading, so the
#: geometry tests fail on the 1017 px the issue measured instead of on a
#: missing substring. The static tests are what say the table is gone.
_NO_TABLE = """
var GAME_VIEW_PANELS = {};
var MOVED_PAST_DETOUR = [];
function clearStalePanels() {}
function clearStalePanelsForPhase() {}
"""


def _panel_table_source() -> str:
    """``GAME_VIEW_PANELS`` … ``clearStalePanelsForPhase``, verbatim.

    The behavioural half runs this slice rather than a copy of it, so a table
    edited in the client is the table the geometry is measured against.
    """
    source = _CORE.read_text("utf-8")
    marker = "    var GAME_VIEW_PANELS = {"
    if marker not in source:
        return _NO_TABLE
    start = source.index(marker)
    end = source.index("    // The roster from the most recent frame")
    return source[start:end]


def _panel_rows() -> dict[str, dict[str, list[str] | bool]]:
    """One entry per row: its frames, phases, and whether it has a teardown."""
    body = _without_comments(_panel_table_source())
    table = body.split("var GAME_VIEW_PANELS = {", 1)[1].split("\n    };", 1)[0]
    rows: dict[str, dict] = {}
    for block in re.finditer(
        r"'([a-z-]+)': \{(.*?)\n        \}", table, flags=re.S
    ):
        panel, spec = block.group(1), block.group(2)
        rows[panel] = {
            "raisedBy": re.findall(r"'([a-z_]+)'", _field(spec, "raisedBy")),
            "phases": re.findall(r"'([A-Z_]+)'", _field(spec, "phases")),
            "clear": "clear: null" not in spec,
            "owner": "owner: null" not in spec,
        }
    return rows


def _field(spec: str, name: str) -> str:
    m = re.search(rf"{name}: \[(.*?)\]", spec, flags=re.S)
    return m.group(1) if m else ""


def _moves_on() -> list[str]:
    body = _without_comments(_panel_table_source())
    listed = body.split("var MOVED_PAST_DETOUR = [", 1)[1].split("]", 1)[0]
    return re.findall(r"'([a-z_]+)'", listed)


def _handle_message_body() -> str:
    source = _without_comments(_CORE.read_text("utf-8"))
    return source.split("function handleMessage(msg) {", 1)[1].split(
        "function handleReactionBonus", 1
    )[0]


# ---------------------------------------------------------------------------
# …and the page they are about
# ---------------------------------------------------------------------------


def _stack_above_the_grid() -> list[tuple[str, bool]]:
    """The blocks between the question and the answer grid, in page order.

    Direct children of ``.game-container`` (one indent level, 16 spaces) from
    the top of ``#game-view`` down to ``#answers-container``. The second value
    is whether the page ships the block hidden — which is the same thing as
    "something can make it appear later", and therefore the same thing as
    "something has to make it go away again".
    """
    html = _HTML.read_text("utf-8")
    view = html[html.index('<div id="game-view"') :]
    view = view[: view.index('<section id="answers-container"')]

    out: list[tuple[str, bool]] = []
    for m in re.finditer(r'^                <(?:div|section)([^>]*)>', view, re.M):
        attrs = m.group(1)
        ident = re.search(r'id="([^"]+)"', attrs)
        classes = re.search(r'class="([^"]*)"', attrs)
        name = ident.group(1) if ident else "(anonymous)"
        out.append((name, bool(classes) and "hidden" in classes.group(1).split()))
    assert out, "the game view is unreadable"
    return out


def _optional_blocks() -> list[str]:
    return [name for name, hidden in _stack_above_the_grid() if hidden]


# ---------------------------------------------------------------------------
# The invariant
# ---------------------------------------------------------------------------


def test_every_block_that_can_appear_above_the_grid_has_a_teardown_row() -> None:
    """The #864 invariant, and the only test here that had to be written.

    ``player.html`` ships a block hidden exactly when something is expected to
    show it later. Every one of those sits above ``#answers-container``, so
    showing one and never hiding it again pushes the answer grid down the page
    — which is the bug. A row in ``GAME_VIEW_PANELS`` is the declaration that
    somebody owns taking it back down.
    """
    rows = _panel_rows()

    for panel in _optional_blocks():
        assert panel in rows, (
            f"#{panel} is shipped hidden above #answers-container, so some "
            "frame can raise it over the answer grid — and no row in "
            "GAME_VIEW_PANELS says what takes it down again. That is #864: "
            "#862 raised #hotseat-panel and nothing lowered it, for every "
            "remaining question of the game."
        )


def test_every_row_names_a_teardown_or_the_module_that_owns_one() -> None:
    """A row is a claim that somebody hides this panel. Exactly one of the two
    ways of being true, so "owned elsewhere" cannot quietly mean "nowhere"."""
    for panel, row in _panel_rows().items():
        assert row["clear"] != row["owner"], (
            f"#{panel} must name either a clear() this table runs or the "
            "module that already owns the teardown — not both, and not neither"
        )


def test_the_table_covers_the_page_and_nothing_else() -> None:
    """A row for a block that is not there is a teardown that never runs."""
    optional = set(_optional_blocks())

    for panel in _panel_rows():
        assert panel in optional, (
            f"#{panel} has a teardown row but player.html does not ship it "
            "hidden above the answer grid"
        )


def test_every_raiser_is_a_frame_this_phone_actually_receives() -> None:
    """Same check #858 makes on the arming table: a row pointing at a frame
    the server never sends, or one the router has no case for, is dead."""
    body = _handle_message_body()

    for panel, row in _panel_rows().items():
        for frame in row["raisedBy"]:
            assert frame in SERVER_FRAMES, f"{panel}: {frame} is not a server frame"
            assert f"case '{frame}':" in body, f"{panel}: no case for {frame}"


def test_the_frames_that_move_the_game_on_are_frames_too() -> None:
    body = _handle_message_body()

    for frame in _moves_on():
        assert frame in SERVER_FRAMES, frame
        assert f"case '{frame}':" in body, frame


def test_the_teardown_is_applied_once_and_not_from_inside_the_switch() -> None:
    """The lesson #803/#858 already paid for: a per-case teardown is a case
    somebody will forget. One call after the switch is what makes a frame
    added later get it for free."""
    body = _handle_message_body()

    assert body.count("clearStalePanels(msg)") == 1
    assert body.index("clearStalePanels(msg)") > body.rindex("break;"), (
        "the teardown has to run after the switch, beside enterStageFor"
    )


def test_the_snapshot_half_tears_down_too() -> None:
    """The parity #858 is about, in the other direction: a phone that
    reconnects into a phase that no longer owns the panel must find it gone.
    ``restoreFromSnapshot`` returns at its first line when the snapshot has no
    ``hot_seat`` block, so without this a reload did not clear it either."""
    source = _without_comments(_CORE.read_text("utf-8"))
    body = source.split("function handleGameState(msg)", 1)[1][:3000]

    assert "clearStalePanelsForPhase(msg.phase)" in body


def test_the_hot_seat_row_is_the_one_the_live_test_found() -> None:
    """The regression itself, spelled out so a table edit cannot lose it."""
    rows = _panel_rows()

    assert "hotseat-panel" in rows
    row = rows["hotseat-panel"]
    assert row["clear"] is True, (
        "the Hot Seat fires once per game, so 'the next auction clears it' "
        "means 'nothing clears it' — this row owns its own teardown"
    )
    assert "hot_seat_result" in row["raisedBy"]
    assert "question_started" in _moves_on(), (
        "the frame that ends the settlement is the one that opens the next "
        "question — the room leaves HOT_SEAT_REVEAL when the host taps Next "
        "Question and every phone receives question_started"
    )


# ---------------------------------------------------------------------------
# …and what the phone actually shows
# ---------------------------------------------------------------------------


_SCRIPT = """
require({stub});
QZ.serveI18n({i18n});
QZ.load({i18njs});

QZ.els({ids});

// The real class list each block ships with, so a block the page hides is
// hidden here too — otherwise every optional block would read as "showing".
var CLASSES = {classes};
Object.keys(CLASSES).forEach(function (id) {{
    CLASSES[id].forEach(function (c) {{ QZ.el(id).classList.add(c); }});
}});

QZ.load({utils_js});
QZ.load({render_shared});
QZ.load({player_utils});
QZ.load({player_game});
QZ.load({hotseat});

var hotSeat = window.QuizifyPlayerHotSeat;
var S = window.QuizifyPlayerUtils.state;

{table}

// The stack, as player.html orders it. A visible block pushes everything
// below it down the page; a hidden one costs nothing.
var STACK = {stack};
var HEIGHTS = {heights};
var GEOM = {geom};

function shown(id) {{
    var node = document.getElementById(id);
    return !!node && !node.classList.contains('hidden');
}}

function grid() {{
    var top = GEOM.prelude;
    STACK.forEach(function (id) {{
        if (shown(id)) top += (HEIGHTS[id] || GEOM.breaksTheFold);
    }});
    top += GEOM.chrome;
    var bottom = top + GEOM.grid;
    return {{ top: top, bottom: bottom, fits: bottom <= GEOM.viewport }};
}}

function reading() {{
    return {{
        panel: shown('hotseat-panel'),
        resultStage: shown('hotseat-result-stage'),
        title: document.getElementById('hotseat-title').textContent,
        grid: grid()
    }};
}}

var FRAME = {frame};

function settle() {{
    hotSeat.reset();
    hotSeat.handleResult(JSON.parse(JSON.stringify(FRAME)));
}}

function auction() {{
    hotSeat.reset();
    hotSeat.handleAuctionYou({{ score: 137 }});
}}

function seatQuestion() {{
    hotSeat.reset();
    hotSeat.handleAwarded({{ winner: 'Ben', entrant: 'Ben', pct: 90, stake: 80 }});
    hotSeat.handleQuestion({{
        question: 'Which country is this pair of swords from?',
        answers: ['Japan', 'China', 'Korea'],
        winner: 'Ben',
        you_are_seated: false,
        score: 137
    }});
}}

var RAISERS = {{
    hot_seat_auction_you: auction,
    hot_seat_question: seatQuestion,
    hot_seat_result: settle
}};

(async function () {{
    await window.QuizifyI18n.init('en');
    S.playerName = 'Cleo';
    S.isAdmin = false;

    var out = {{
        raised: {{}}, afterQuestion: {{}}, afterFrame: {{}},
        afterPhase: {{}}, noise: {{}}
    }};

    // Nothing up: the baseline the measurements were taken against.
    hotSeat.reset();
    out.empty = reading();

    // The live-test reading itself: the settlement on screen.
    settle();
    out.settlement = reading();

    Object.keys(RAISERS).forEach(function (raiser) {{
        RAISERS[raiser]();
        out.raised[raiser] = reading();

        // The frame the issue names, driven whether or not the client's own
        // table lists it — a guard that only checks the rows the client
        // happens to declare passes an empty table, which is the shape of the
        // bug it exists to catch.
        RAISERS[raiser]();
        clearStalePanels({{ type: 'question_started' }});
        out.afterQuestion[raiser] = reading();

        // …and every frame the table declares.
        out.afterFrame[raiser] = {{}};
        MOVED_PAST_DETOUR.forEach(function (frame) {{
            RAISERS[raiser]();
            clearStalePanels({{ type: frame }});
            out.afterFrame[raiser][frame] = reading();
        }});

        // …and every snapshot phase that does not own the panel: the phone
        // that reconnects rather than lives through it.
        out.afterPhase[raiser] = {{}};
        {phases}.forEach(function (phase) {{
            RAISERS[raiser]();
            clearStalePanelsForPhase(phase);
            out.afterPhase[raiser][phase] = reading();
        }});
    }});

    // A settlement must survive everything that is NOT the game moving on —
    // otherwise #864's fix eats #859. These arrive in their dozens.
    ['hot_seat_tick', 'timer_tick', 'answer_progress', 'reaction',
     'round_summary', 'player_left'].forEach(function (frame) {{
        settle();
        clearStalePanels({{ type: frame }});
        out.noise[frame] = reading();
    }});

    // …and the phases that own it.
    out.ownPhase = {{}};
    ['HOT_SEAT_AUCTION', 'HOT_SEAT', 'HOT_SEAT_REVEAL'].forEach(function (phase) {{
        settle();
        clearStalePanelsForPhase(phase);
        out.ownPhase[phase] = reading();
    }});

    // The headline: a sweep must not put the auction's title back over the
    // settlement it is standing on.
    settle();
    window.QuizifyI18n.initPageTranslations();
    out.afterSweep = reading();
    await window.QuizifyI18n.setLanguage('de');
    window.QuizifyI18n.initPageTranslations();
    out.afterGerman = reading();
    await window.QuizifyI18n.setLanguage('en');
    window.QuizifyI18n.initPageTranslations();

    // The settlement rebuilt from a snapshot (#859) still goes away.
    hotSeat.reset();
    hotSeat.restoreFromSnapshot(
        {{ stage: 'result', winner: FRAME.winner, summary: {summary} }},
        {{ leaderboard: FRAME.leaderboard }}
    );
    out.fromSnapshot = reading();
    clearStalePanels({{ type: 'question_started' }});
    out.fromSnapshotThenQuestion = reading();

    console.log(JSON.stringify(out));
}})();
"""


def _frame() -> dict:
    """The settlement, in the shape ``hot_seat_result`` carries.

    The live test's numbers: Ben buys the chair at 90 % (80 pts) and gets it
    right, Cleo stakes 15 % against him.
    """
    return {
        "type": "hot_seat_result",
        "winner": "Ben",
        "entrant": "Ben",
        "answered": True,
        "correct_answer": "Japan",
        "winner_delta": 80,
        "winner_pct": 90,
        "winner_stake": 80,
        "deltas": {"Ben": 80, "Cleo": -20},
        "leaderboard": [
            {"rank": 1, "name": "Ben", "score": 169, "entrant_id": "Ben"},
            {"rank": 2, "name": "Anna", "score": 152, "entrant_id": "Anna"},
            {"rank": 3, "name": "Cleo", "score": 117, "entrant_id": "Cleo"},
            {"rank": 4, "name": "Dan", "score": 62, "entrant_id": "Dan"},
        ],
    }


def _element_classes() -> dict[str, list[str]]:
    """The class attribute ``player.html`` gives each block above the grid.

    Without this the stub would start every optional block visible, and the
    baseline the geometry is measured against would be a fiction.
    """
    html = _HTML.read_text("utf-8")
    out: dict[str, list[str]] = {}
    for element_id in _optional_blocks():
        m = re.search(rf'id="{element_id}" class="([^"]*)"', html)
        assert m, f"#{element_id} is missing from player.html"
        out[element_id] = m.group(1).split()
    return out


def _ids() -> list[str]:
    return sorted(
        {
            *(name for name, _ in _stack_above_the_grid() if name != "(anonymous)"),
            "answer-buttons",
            "answers-container",
            "hotseat-panel",
            "hotseat-title",
            "hotseat-hint",
            "hotseat-bid-stage",
            "hotseat-bet-stage",
            "hotseat-bid-btn",
            "hotseat-bid-count",
            "hotseat-slider",
            "hotseat-value",
            "hotseat-bank",
            "hotseat-bet-slider",
            "hotseat-bet-value",
            "hotseat-bet-will",
            "hotseat-bet-wont",
            "hotseat-result-stage",
            "hotseat-result-answer",
            "hotseat-result-you",
            "hotseat-result-you-label",
            "hotseat-result-you-delta",
            "hotseat-result-standings",
            "leaderboard-list",
            "leaderboard-summary",
            "question-category",
            "question-media",
            "question-text",
            "timer-sr-announce",
        }
    )


def _run() -> dict:
    frame = _frame()
    script = _SCRIPT.format(
        stub=json.dumps(str(_STUB)),
        i18n=json.dumps(str(_I18N)),
        i18njs=json.dumps(str(_JS / "i18n.js")),
        ids=json.dumps(_ids()),
        classes=json.dumps(_element_classes()),
        utils_js=json.dumps(str(_JS / "utils.js")),
        render_shared=json.dumps(str(_JS / "render-shared.js")),
        player_utils=json.dumps(str(_JS / "player-utils.js")),
        player_game=json.dumps(str(_JS / "player-game.js")),
        hotseat=json.dumps(str(_HOTSEAT)),
        table=_panel_table_source(),
        stack=json.dumps(_optional_blocks()),
        heights=json.dumps(MEASURED_HEIGHTS),
        geom=json.dumps(
            {
                "viewport": VIEWPORT_H,
                "prelude": PRELUDE_H,
                "chrome": GRID_CHROME_H,
                "grid": GRID_H,
                "breaksTheFold": BREAKS_THE_FOLD_H,
            }
        ),
        phases=json.dumps(["QUESTION_ACTIVE", "PLAYING", "WAGER_ACTIVE", "LIGHTNING"]),
        frame=json.dumps(frame),
        summary=json.dumps({k: v for k, v in frame.items() if k != "leaderboard"}),
    )
    out = subprocess.run(
        ["node", "-e", script], capture_output=True, text=True, check=True
    )
    return json.loads(out.stdout)


@_NEEDS_NODE
def test_the_model_reproduces_both_readings_from_the_live_test() -> None:
    """Before anything is asserted about the fix: the stack model has to
    produce the numbers the issue measured out of a real phone, or the
    geometry below is arithmetic about nothing.

    With nothing up the grid ends 91 px above the fold; with the settlement up
    it starts at 1017 and ends at 1225, against a viewport of 844.
    """
    result = _run()

    assert result["empty"]["grid"]["fits"] is True
    assert result["empty"]["grid"]["bottom"] == 753

    assert result["settlement"]["panel"] is True
    assert result["settlement"]["grid"]["top"] == 1017
    assert result["settlement"]["grid"]["bottom"] == 1225
    assert result["settlement"]["grid"]["fits"] is False


@_NEEDS_NODE
def test_the_next_question_leaves_the_answer_grid_reachable() -> None:
    """The bug, and the fix. Whichever way the panel got on screen, the frame
    that opens the next screen takes it off — and the three answers are back
    above the fold on a 390x844 phone."""
    result = _run()
    raisers = {"hot_seat_auction_you", "hot_seat_question", "hot_seat_result"}

    # The reading from the issue, pinned rather than read off the table: an
    # empty MOVED_PAST_DETOUR would otherwise make the loop below vacuous.
    assert set(result["afterQuestion"]) == raisers
    for raiser, reading in result["afterQuestion"].items():
        assert reading["panel"] is False, raiser
        assert reading["grid"]["top"] == 545, raiser
        assert reading["grid"]["fits"] is True, (raiser, reading["grid"])

    # …and every other frame the table says moves the game on.
    assert set(result["afterFrame"]) == raisers
    for raiser, frames in result["afterFrame"].items():
        assert set(frames) == {"question_started", "wager_window", "lightning_splash"}
        for frame, reading in frames.items():
            assert reading["panel"] is False, (raiser, frame)
            assert reading["grid"]["fits"] is True, (raiser, frame, reading["grid"])


@_NEEDS_NODE
def test_a_reconnect_into_a_later_phase_finds_it_gone_too() -> None:
    """The snapshot half of the parity: a phone that reloads onto round 9 must
    not rebuild the round-4 chair."""
    result = _run()

    assert set(result["afterPhase"]) == {
        "hot_seat_auction_you",
        "hot_seat_question",
        "hot_seat_result",
    }
    for raiser, phases in result["afterPhase"].items():
        assert set(phases) == {
            "QUESTION_ACTIVE",
            "PLAYING",
            "WAGER_ACTIVE",
            "LIGHTNING",
        }
        for phase, reading in phases.items():
            assert reading["panel"] is False, (raiser, phase)
            assert reading["grid"]["fits"] is True, (raiser, phase)


@_NEEDS_NODE
def test_the_settlement_still_stands_until_the_game_moves_on() -> None:
    """#859, which this must not undo. ``hot_seat_tick``, ``timer_tick`` and
    ``answer_progress`` arrive in their dozens while the settlement is the
    screen the room is reading; ``round_summary`` and ``player_left`` are the
    between-round frames that are not the next question."""
    result = _run()

    for frame, reading in result["noise"].items():
        assert reading["panel"] is True, frame
        assert reading["resultStage"] is True, frame
        assert reading["title"] == "Ben answered it — +80 points", frame

    for phase, reading in result["ownPhase"].items():
        assert reading["panel"] is True, phase
        assert reading["resultStage"] is True, phase


@_NEEDS_NODE
def test_the_settlement_from_a_snapshot_is_rendered_and_then_taken_away() -> None:
    """Both halves of #859 in one line: a phone that reloads onto the settled
    chair renders it from ``hot_seat.summary``, and the next question still
    clears it."""
    result = _run()

    assert result["fromSnapshot"]["panel"] is True
    assert result["fromSnapshot"]["resultStage"] is True
    assert result["fromSnapshot"]["title"] == "Ben answered it — +80 points"

    assert result["fromSnapshotThenQuestion"]["panel"] is False
    assert result["fromSnapshotThenQuestion"]["grid"]["fits"] is True


@_NEEDS_NODE
def test_the_headline_says_what_the_card_is_showing() -> None:
    """The second wrongness riding along on the stale card: ``hotseat-title``
    carried ``data-i18n="hotSeat.auctionTitle"``, so the next translation
    sweep announced an auction over the settlement's own result."""
    result = _run()

    assert result["afterSweep"]["title"] == "Ben answered it — +80 points"
    assert result["afterGerman"]["title"] == "Ben hat sie gewusst — +80 Punkte"


def test_the_guard_says_what_it_does_not_cover() -> None:
    """Written down rather than implied, because the gap in #858's guard was
    never stated either.

    **Covered.** Every block ``player.html`` ships hidden between the question
    and the answer grid has to declare who takes it down (static, general);
    the frames in the table have to exist on the wire and in the router
    (static, general); and for the panel this table tears down, the real
    module is driven and the grid measured after every frame and phase that
    means "the game moved on" (behavioural, one panel).

    **Not covered, and deliberately.**

    * *Layout.* ``dom_stub.js`` has no layout engine. The geometry is a stack
      model: page order from ``player.html``, one measured height, and the
      baseline reproduced against the live test in the first test above. A CSS
      change that shrinks a panel, or makes one ``position: fixed``, is
      invisible here — that is what the pre-release mobile-width verify in
      CLAUDE.md is for.
    * *The other four rows.* Their teardown lives in another module
      (``renderQuestion``, ``handleQuestionStarted``, ``setResetStage``) and is
      asserted as a declaration, not driven. A wrong ``owner:`` string would
      pass. Only ``BREAKS_THE_FOLD_H`` would let them be measured, and it is a
      punitive floor rather than their real height.
    * *Content.* A panel correctly hidden while holding the wrong text still
      passes. The stale leaderboard inside the card was a symptom of the same
      bug and is not separately guarded.
    * *Overlays.* Blocks outside ``.game-container``, and anything on
      ``#reveal-view`` / ``#lightning-view`` / ``#end-view``. The issue notes
      the reveal is unaffected precisely because the panel lives in
      ``#game-view``; a panel added to another view would need its own row
      shape here.
    * *The server.* This is entirely a client guard. A phase the server can
      enter that no client table names is #858's problem, not this one's.
    """
    rows = _panel_rows()

    driven = {panel for panel, row in rows.items() if row["clear"]}
    declared = set(rows) - driven

    assert driven == {"hotseat-panel"}, (
        "the behavioural half drives the rows this table tears down; if that "
        "set grew, give the new panel a measured height in MEASURED_HEIGHTS "
        "and a raiser in the node harness rather than leaving it asserted "
        "only as a declaration"
    )
    assert declared, "every row claiming an external owner is unmeasured here"
