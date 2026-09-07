"""The phones are shown the Hot Seat settlement (#859).

Found on real hardware during the v1.16.0-RC3 live test. Ben bought the chair
for 90 % (80 pts) and answered correctly. The host page read *"The chair is
settled"* over ``Ben 169 | Cleo 164 | Anna 152 | Dan 62``; the television read
*"Ben answered it — +80 points"* over the same board. All four phones read:

    ROUND 3 OF 5 | Which country is this pair of swords from? | 4 | 5 seconds left
    Leaderboard  Cleo: 137 + 3 more

— the seat question, a stopped clock, the countdown's last screen-reader line,
and the standings from *before* the chair. The largest single swing of the
evening was invisible to the people it happened to, and the spectators who
staked on the outcome were never told whether their bet came in.

``player-hotseat.js`` was one line and a wrong premise::

    function handleResult() {
        // The reveal screen owns the outcome; the panel's job is done.
        reset();
    }

No reveal follows. ``hot_seat_result`` is the only frame the settlement ever
produces (``send_hot_seat_result``), the room stays in HOT_SEAT_REVEAL until
the host taps Next Question, and no ``round_summary`` arrives — so the frame
carrying ``winner``, ``answered``, ``correct_answer``, every ``delta`` and,
since #833, the full post-settlement ``leaderboard`` was received and dropped.
Unchanged since the mode landed in #654; not a regression from RC3.

The fix keeps the outcome in the panel that already narrates the auction, the
award and the seat question, rather than inventing a fourth full-screen stage:
the phone is already looking at that card, and the #803 escape hatch below it
keeps working because it sits outside the panel.

These tests run the real ``player-hotseat.js`` against the real i18n bundles
under ``tests/fixtures/dom_stub.js`` (the #803/#826 precedent), and the payload
they feed it is built by the real ``HotSeatRound.summary()`` — so the fields
are the server's, not the test's idea of them.
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
_HOTSEAT = _JS / "player-hotseat.js"
_CORE = _JS / "player-core.js"
_STUB = Path(__file__).resolve().parent / "fixtures" / "dom_stub.js"

_NEEDS_NODE = pytest.mark.skipif(
    shutil.which("node") is None, reason="node not installed"
)

#: The settlement's own elements, plus the ones it has to put away.
SETTLEMENT_IDS = (
    "hotseat-result-stage",
    "hotseat-result-answer",
    "hotseat-result-you",
    "hotseat-result-you-label",
    "hotseat-result-you-delta",
    "hotseat-result-standings",
)


def _without_comments(source: str) -> str:
    source = re.sub(r"/\*.*?\*/", "", source, flags=re.S)
    return re.sub(r"^\s*//.*$", "", source, flags=re.M)


# ---------------------------------------------------------------------------
# The markup, and where it sits
# ---------------------------------------------------------------------------


def test_the_settlement_has_somewhere_to_go() -> None:
    html = _HTML.read_text("utf-8")
    for element_id in SETTLEMENT_IDS:
        assert f'id="{element_id}"' in html, element_id


def test_it_starts_hidden() -> None:
    """The panel opens on an auction. A settlement standing under the bid
    slider would be last round's chair."""
    html = _HTML.read_text("utf-8")
    m = re.search(r'id="hotseat-result-stage" class="([^"]*)"', html)
    assert m and "hidden" in m.group(1).split()


def test_it_lives_inside_the_panel_that_narrates_the_rest_of_the_detour() -> None:
    """No fourth full-screen stage: the auction, the award and the seat
    question are all this one card, and so is the end of it."""
    html = _HTML.read_text("utf-8")
    panel_start = html.index('id="hotseat-panel"')
    panel_end = html.index("</section>", panel_start)

    assert panel_start < html.index('id="hotseat-result-stage"') < panel_end


def test_the_escape_hatch_still_sits_outside_it() -> None:
    """#803's hatch is armed on exactly this screen. It must not be inside a
    panel whose stages get toggled — that is the shape #859 replaces, not one
    to inherit."""
    html = _HTML.read_text("utf-8")
    panel_end = html.index("</section>", html.index('id="hotseat-panel"'))

    assert html.index('id="hotseat-reset-controls"') > panel_end


def test_the_settlement_uses_strings_that_already_exist() -> None:
    """Every line is one of the strings the television already prints, or one
    the phone already prints elsewhere. A settlement is a bad moment to
    discover an untranslated key on two of three languages."""
    # #787: the three result keys are picked in render-shared.js now, because
    # all three screens have to tell the room the same story; the rest of the
    # settlement's strings are still the phone's own.
    source = _without_comments(
        _HOTSEAT.read_text("utf-8")
        + (_HOTSEAT.parent / "render-shared.js").read_text("utf-8")
    )
    settlement_keys = {
        "hotSeat.resultRight",
        "hotSeat.resultWrong",
        "hotSeat.resultTimeout",
        "hotSeat.hostSettled",
        "lightning.recapWaitHint",
        "reveal.correctAnswerWas",
        "reveal.ptsUnit",
        "lobby.you",
    }
    for key in settlement_keys:
        assert f"'{key}'" in source, key

    for bundle in ("en", "de", "es"):
        data = json.loads((_I18N / f"{bundle}.json").read_text("utf-8"))
        for key in settlement_keys:
            section, leaf = key.split(".", 1)
            assert leaf in data.get(section, {}), f"{bundle}: {key}"


def test_the_frame_still_declares_what_the_phone_reads() -> None:
    """A field renamed on the server is a blank line on the phone."""
    spec = SERVER_FRAMES["hot_seat_result"]

    assert "leaderboard" in spec.required
    # ``summary()`` is spread into the frame, which is why the spec allows
    # dynamic keys — the fields the phone reads come from there.
    assert spec.dynamic_keys


# ---------------------------------------------------------------------------
# …and what it puts on the screen
# ---------------------------------------------------------------------------


_SCRIPT = """
require({stub});
QZ.serveI18n({i18n});
QZ.load({i18njs});

QZ.els({ids});
QZ.load({utils_js});
QZ.load({render_shared});
QZ.load({player_utils});
QZ.load({player_game});
QZ.load({hotseat});

var HS = window.QuizifyPlayerHotSeat;
var S = window.QuizifyPlayerUtils.state;

function screen() {{
    var standings = document.getElementById('hotseat-result-standings').innerHTML;
    var names = [];
    var scores = [];
    var re = /mstand-name">([^<]*)[\\s\\S]*?mstand-score">(-?\\d+)</g;
    var m;
    while ((m = re.exec(standings)) !== null) {{
        names.push(m[1]);
        scores.push(parseInt(m[2], 10));
    }}
    return {{
        panelHidden: document.getElementById('hotseat-panel')
            .classList.contains('hidden'),
        stageHidden: document.getElementById('hotseat-result-stage')
            .classList.contains('hidden'),
        bidHidden: document.getElementById('hotseat-bid-stage')
            .classList.contains('hidden'),
        betHidden: document.getElementById('hotseat-bet-stage')
            .classList.contains('hidden'),
        title: document.getElementById('hotseat-title').textContent,
        hint: document.getElementById('hotseat-hint').textContent,
        answer: document.getElementById('hotseat-result-answer').textContent,
        youHidden: document.getElementById('hotseat-result-you')
            .classList.contains('hidden'),
        you: document.getElementById('hotseat-result-you-label').textContent
            + ' ' + document.getElementById('hotseat-result-you-delta').textContent,
        boardNames: names,
        boardScores: scores,
        mine: /mstand-row--me/.test(standings),
        collapsedBoard: document.getElementById('leaderboard-list').innerHTML,
        srAnnounce: document.getElementById('timer-sr-announce').textContent
    }};
}}

var FRAME = {frame};

(async function () {{
    await window.QuizifyI18n.init('en');
    var out = {{}};

    // The seat holder's own phone.
    S.playerName = 'Ben';
    S.isAdmin = false;
    document.getElementById('timer-sr-announce').textContent = '5 seconds left';
    HS.handleResult(JSON.parse(JSON.stringify(FRAME)));
    out.seatHolder = screen();

    // A spectator who staked against them.
    S.playerName = 'Cleo';
    HS.handleResult(JSON.parse(JSON.stringify(FRAME)));
    out.bettor = screen();

    // Somebody who neither bought the chair nor bet on it.
    S.playerName = 'Anna';
    HS.handleResult(JSON.parse(JSON.stringify(FRAME)));
    out.bystander = screen();

    // The host, who has Next Question on their own bar.
    S.playerName = 'Ben';
    S.isAdmin = true;
    HS.handleResult(JSON.parse(JSON.stringify(FRAME)));
    out.host = screen();
    S.isAdmin = false;

    // Wrong, and never answered — since #653 they cost the same points, so
    // the sentence is the only thing that tells them apart.
    S.playerName = 'Ben';
    var wrong = JSON.parse(JSON.stringify(FRAME));
    wrong.answered = false;
    wrong.winner_delta = -80;
    HS.handleResult(wrong);
    out.wrong = screen();

    var timeout = JSON.parse(JSON.stringify(FRAME));
    timeout.answered = null;
    timeout.winner_delta = -80;
    HS.handleResult(timeout);
    out.timeout = screen();

    // The room's language, not the browser's.
    await window.QuizifyI18n.setLanguage('de');
    HS.handleResult(JSON.parse(JSON.stringify(FRAME)));
    out.german = screen();
    await window.QuizifyI18n.setLanguage('en');

    // The next chair opens over a clean panel.
    HS.handleResult(JSON.parse(JSON.stringify(FRAME)));
    HS.handleAuctionYou({{ score: 169 }});
    out.nextAuction = screen();

    // …and so does the next question, via reset().
    HS.handleResult(JSON.parse(JSON.stringify(FRAME)));
    HS.reset();
    out.afterReset = screen();

    // A phone that reloads on the settled chair: the snapshot carries the
    // whole settlement in hot_seat.summary and the board at the top level.
    S.playerName = 'Cleo';
    HS.reset();
    HS.restoreFromSnapshot(
        {{ stage: 'result', winner: FRAME.winner, entrant: FRAME.entrant,
           pct: FRAME.winner_pct, stake: FRAME.winner_stake,
           summary: {summary} }},
        {{ leaderboard: FRAME.leaderboard }}
    );
    out.reconnected = screen();

    console.log(JSON.stringify(out));
}})();
"""


def _frame() -> dict:
    """The live frame, built by the real settlement code.

    ``summary()`` is what ``send_hot_seat_result`` spreads into the broadcast,
    so the phone is handed the server's field names and the server's
    arithmetic rather than the test's idea of either. The numbers are the live
    test's: Ben holds 89, buys the chair at 90 % (80 pts) and gets it right;
    Cleo holds 137 and stakes 15 % against him.
    """
    from custom_components.quizify.game.hot_seat import BET_WONT, HotSeatRound
    from custom_components.quizify.game.questions import Answer, Question

    class _Bank:
        """Minimal QuestionBank stand-in — the auction only needs a pool."""

        def __init__(self, questions: list[Question]) -> None:
            self._questions = questions

        def load_all_categories(self) -> None:
            pass

        def build_pool(self, **_kwargs) -> list[Question]:
            return list(self._questions)

        def shown_this_game_ids(self) -> set[str]:
            return set()

        def remaining_queue_ids(self) -> set[str]:
            return set()

        def record_shown(self, qid: str) -> None:
            pass

        def drop_from_queue(self, ids: set[str]) -> None:
            pass

    question = Question(
        id="q-swords",
        question="Which country is this pair of swords from?",
        answers=[
            Answer(text="Japan", correct=True),
            Answer(text="China", correct=False),
            Answer(text="Korea", correct=False),
        ],
        difficulty="medium",
    )
    scores = {"Ben": 89, "Cleo": 137, "Anna": 152, "Dan": 62}

    hs = HotSeatRound(_Bank([question]), scores)
    assert hs.start()
    hs.record_bid("Ben", 90)
    hs.record_bid("Cleo", 10)
    assert hs.resolve_auction() == "Ben"
    hs.start_answer_clock()
    assert hs.record_bet("Cleo", BET_WONT, 15)
    assert hs.record_answer("Ben", hs.shuffled_answers().index("Japan")) is True

    summary = hs.summary()
    settled = {name: scores[name] + summary["deltas"].get(name, 0) for name in scores}
    ranked = sorted(settled.items(), key=lambda kv: -kv[1])
    return {
        "type": "hot_seat_result",
        "round_num": 3,
        "total_rounds": 5,
        **summary,
        "leaderboard": [
            {"rank": i + 1, "name": name, "score": score, "entrant_id": name}
            for i, (name, score) in enumerate(ranked)
        ],
    }


def _ids() -> list[str]:
    """Every id the harness furnishes — the ones the real page ships."""
    return sorted(
        {
            *SETTLEMENT_IDS,
            "hotseat-panel",
            "hotseat-title",
            "hotseat-hint",
            "hotseat-bid-stage",
            "hotseat-bet-stage",
            "hotseat-slider",
            "hotseat-value",
            "hotseat-bank",
            "hotseat-bid-btn",
            "hotseat-bid-count",
            "answer-buttons",
            "leaderboard-list",
            "leaderboard-summary",
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
        utils_js=json.dumps(str(_JS / "utils.js")),
        render_shared=json.dumps(str(_JS / "render-shared.js")),
        player_utils=json.dumps(str(_JS / "player-utils.js")),
        player_game=json.dumps(str(_JS / "player-game.js")),
        hotseat=json.dumps(str(_HOTSEAT)),
        frame=json.dumps(frame),
        summary=json.dumps({k: v for k, v in frame.items() if k != "leaderboard"}),
    )
    out = subprocess.run(
        ["node", "-e", script], capture_output=True, text=True, check=True
    )
    return json.loads(out.stdout)


@_NEEDS_NODE
def test_the_phone_is_told_who_took_the_chair_and_what_it_paid() -> None:
    """The headline the television printed and the phones did not."""
    result = _run()

    assert result["seatHolder"]["title"] == "Ben answered it — +80 points"
    assert result["seatHolder"]["panelHidden"] is False
    assert result["seatHolder"]["stageHidden"] is False


@_NEEDS_NODE
def test_wrong_and_out_of_time_are_told_apart() -> None:
    """#653 charges both the same points, so the sentence is the only thing
    that says which one happened."""
    result = _run()

    assert result["wrong"]["title"] == "Ben got it wrong — −80 points"
    assert result["timeout"]["title"] == "Ben ran out of time — −80 points"


@_NEEDS_NODE
def test_the_answer_is_spelled_out() -> None:
    """The seat holder answered a question nobody ever told them the answer
    to. ``correct_index`` is canonical and they were shown a shuffle, so the
    string (#833) is the only usable field."""
    result = _run()

    assert result["seatHolder"]["answer"] == "Correct answer was: Japan"


@_NEEDS_NODE
def test_each_phone_is_told_what_it_did_to_its_own_points() -> None:
    """The part no other screen can show: the chair moved 80 of Ben's points
    and 20 of Cleo's, and Cleo's bet was against him."""
    result = _run()

    assert result["seatHolder"]["youHidden"] is False
    assert result["seatHolder"]["you"] == "You +80 pts"

    assert result["bettor"]["youHidden"] is False
    assert result["bettor"]["you"] == "You −20 pts"


@_NEEDS_NODE
def test_a_phone_the_chair_did_not_touch_gets_no_line() -> None:
    """Anna neither bought it nor bet on it. A decorative "0" would be worse
    than nothing."""
    result = _run()

    assert result["bystander"]["youHidden"] is True


@_NEEDS_NODE
def test_the_board_underneath_is_the_one_the_settlement_produced() -> None:
    """The stale leaderboard from the issue: a player reading their own phone
    saw the standings the chair was supposed to change."""
    result = _run()

    assert result["seatHolder"]["boardNames"] == ["Ben", "Anna", "Cleo", "Dan"]
    assert result["seatHolder"]["boardScores"] == [169, 152, 117, 62]
    assert result["bettor"]["mine"] is True
    # …and the phone's own collapsed board, further down the same screen, does
    # not sit there disagreeing with it.
    assert "169" in result["seatHolder"]["collapsedBoard"]


@_NEEDS_NODE
def test_the_bidding_controls_are_put_away() -> None:
    result = _run()

    assert result["seatHolder"]["bidHidden"] is True
    assert result["seatHolder"]["betHidden"] is True


@_NEEDS_NODE
def test_the_room_is_told_what_it_is_waiting_for() -> None:
    """The guests wait on a tap they cannot make; the host is the one holding
    the button."""
    result = _run()

    assert result["seatHolder"]["hint"] == "Hang tight — the host continues the game"
    assert result["host"]["hint"] == (
        "The chair is settled — Next Question continues the game."
    )


@_NEEDS_NODE
def test_the_stopped_clock_stops_talking_too() -> None:
    """#839/#852: the countdown's polite region was still holding "5 seconds
    left" over a question that had been settled."""
    result = _run()

    assert result["seatHolder"]["srAnnounce"] == ""


@_NEEDS_NODE
def test_it_speaks_the_rooms_language() -> None:
    result = _run()

    assert result["german"]["title"] == "Ben hat sie gewusst — +80 Punkte"
    assert result["german"]["answer"] == "Richtige Antwort: Japan"


@_NEEDS_NODE
def test_the_next_chair_opens_on_a_clean_panel() -> None:
    """Both ways out of the settlement: the next auction, and reset()."""
    result = _run()

    assert result["nextAuction"]["stageHidden"] is True
    assert result["nextAuction"]["bidHidden"] is False
    assert result["afterReset"]["stageHidden"] is True
    assert result["afterReset"]["panelHidden"] is True


@_NEEDS_NODE
def test_a_phone_that_reloads_on_the_settled_chair_sees_the_same_thing() -> None:
    """#858's rule, one surface over: the snapshot carries the whole
    settlement in ``hot_seat.summary`` and the board at the top level, and it
    has since #664 — the restore path just showed who had bought the chair and
    stopped there."""
    result = _run()

    assert result["reconnected"]["title"] == result["bettor"]["title"]
    assert result["reconnected"]["answer"] == result["bettor"]["answer"]
    assert result["reconnected"]["you"] == result["bettor"]["you"]
    assert result["reconnected"]["boardScores"] == result["bettor"]["boardScores"]
