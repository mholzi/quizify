"""#832 — the host page follows the Hot Seat's events, not only its snapshot.

Found on real hardware during the v1.16.0-RC1 live test, and the reason RC1
was not cut. Hot Seat is on by default, and a host running the evening from
``/quizify/admin`` without joining as a player was left on "The chair goes to
the highest bid" after the chair had been settled, with the reset icon and the
red End Game as the only controls. The server was in ``HOT_SEAT_REVEAL`` and
accepting ``next_question``; reloading the tab repaired the screen instantly.

The mechanism: the detour broadcasts one full ``game_state`` when the auction
opens and none afterwards. Everything after that — the chair being won, the
seat question, the settlement — is announced by a one-shot ``hot_seat_*``
frame. #699 taught ``handleGameState`` about the three detour phases, so the
host page was correct for exactly as long as that first snapshot was, and a
reload was the only way to get another one.

These tests run the real ``admin.js`` message router against the real i18n
bundles under node (``tests/fixtures/dom_stub.js``), so they assert what the
host reads off the screen rather than what the source looks like. The frames
are the ones ``server/protocol.py`` declares and ``websocket.py`` sends.

#830 lists ten frames the host page had no case for. Seven of them are the
``hot_seat_*`` broadcasts, and all seven are wired here; ``answer_progress``,
``evening_tally`` and ``head_to_head`` are a separate change and stay recorded
as gaps in ``tests/test_frame_surface_coverage_787.py``.
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
_ADMIN_JS = _JS / "admin.js"
_ADMIN_HTML = _WWW / "admin.html"
_I18N = _WWW / "i18n"
_STUB = Path(__file__).resolve().parent / "fixtures" / "dom_stub.js"

_NEEDS_NODE = pytest.mark.skipif(
    shutil.which("node") is None, reason="node not installed"
)

#: Every Hot Seat frame the server broadcasts to everybody. Unicasts
#: (``hot_seat_auction_you``, ``hot_seat_bid_accepted``, …) belong to one
#: player's phone and are not the host's business.
BROADCASTS = (
    "hot_seat_auction",
    "hot_seat_bid_count",
    "hot_seat_no_bids",
    "hot_seat_awarded",
    "hot_seat_question",
    "hot_seat_tick",
    "hot_seat_result",
)

#: The ids the in-game view actually ships, so the harness below is furnished
#: like the real page instead of like the test's idea of it.
ELEMENT_IDS = (
    "admin-round",
    "admin-question",
    "admin-detour-note",
    "admin-correct",
    "game-leaderboard",
    "next-question-btn",
    "end-game-btn",
)


def _admin() -> str:
    return _ADMIN_JS.read_text(encoding="utf-8")


def _js_function(source: str, signature: str, required: bool = True) -> str:
    """One function declaration, taken by brace balance.

    ``required=False`` returns nothing for a function that is not there, so
    the harness below still runs against a host page that has none of this —
    and the tests then fail on what the host reads, which is the complaint,
    rather than on an exception in the fixture.
    """
    if signature not in source:
        if required:
            raise AssertionError(f"admin.js no longer has `{signature}`")
        return ""
    start = source.index(signature)
    depth = 0
    seen = False
    for i in range(start, len(source)):
        if source[i] == "{":
            depth += 1
            seen = True
        elif source[i] == "}":
            depth -= 1
            if seen and depth == 0:
                return source[start : i + 1]
    raise AssertionError(f"unbalanced braces after {signature!r}")


def _element_classes() -> dict[str, list[str]]:
    """The class list each element ships with in ``admin.html``.

    Read rather than invented: whether Next Question starts visible is the
    whole subject here, and a harness that guessed would prove nothing.
    """
    html = _ADMIN_HTML.read_text(encoding="utf-8")
    out: dict[str, list[str]] = {}
    for element_id in ELEMENT_IDS:
        m = re.search(rf'id="{element_id}"(?:\s+class="([^"]*)")?', html)
        # #admin-detour-note is the element this change adds, and its markup
        # is the subject of the test below. Not demanding it here is what
        # lets the harness run against a host page that has none of this, so
        # the tests fail on what the host reads instead of on the fixture.
        assert m or element_id == "admin-detour-note", (
            f"#{element_id} is missing from admin.html"
        )
        out[element_id] = (m.group(1) or "").split() if m else []
    return out


# ---------------------------------------------------------------------------
# The router has a case at all
# ---------------------------------------------------------------------------


def test_every_hot_seat_broadcast_reaches_the_host_router() -> None:
    """The shape of the omission: seven frames, no case for any of them."""
    router = _js_function(_admin(), "function handleMessage(msg) {")

    missing = [f for f in BROADCASTS if f"case '{f}':" not in router]
    assert not missing, f"the host page still drops {missing}"


def test_the_second_line_is_not_the_success_slot() -> None:
    """``.admin-correct-answer`` is green with a glow — it is the "Correct: …"
    line. A lost stake printed there would read as a win, so the detour's
    second line is its own element."""
    html = _ADMIN_HTML.read_text(encoding="utf-8")
    lines = [line for line in html.splitlines() if 'id="admin-detour-note"' in line]
    assert lines, "the in-game view has no second line for the detour"
    note = lines[0]

    assert "admin-correct-answer" not in note
    assert "display:none" in note.replace(" ", ""), (
        "the second line has to start hidden; an empty gap under every "
        "question is the cost of getting this wrong"
    )


# ---------------------------------------------------------------------------
# … and what the host actually reads
# ---------------------------------------------------------------------------


_SCRIPT = """
require({stub});
QZ.serveI18n({i18n});
QZ.load({i18njs});

var CLASSES = {classes};
Object.keys(CLASSES).forEach(function (id) {{
    var el = QZ.el(id);
    CLASSES[id].forEach(function (c) {{ el.classList.add(c); }});
}});
var byId = document.getElementById.bind(document);

// The page around the router. Everything the Hot Seat frames touch is real;
// the rest of admin.js is not loaded, because the point is the routing.
var _redirecting = false;
var currentPhase = 'LOBBY';
var shownView = null;
function showView(name) {{ shownView = name; }}
var leaderboard = null;
function renderLeaderboard(container, players) {{ leaderboard = players; }}
var timer = {{ started: null, remaining: null, stopped: 0 }};
var adminTimer = {{
    start: function (d) {{ timer.started = d; }},
    update: function (r) {{ timer.remaining = r; }},
    stop: function () {{ timer.stopped += 1; }}
}};
var els = {{
    adminRound: byId('admin-round'),
    adminQuestion: byId('admin-question'),
    adminDetourNote: byId('admin-detour-note'),
    adminCorrect: byId('admin-correct'),
    gameLeaderboard: byId('game-leaderboard'),
    nextQuestionBtn: byId('next-question-btn'),
    endGameBtn: byId('end-game-btn')
}};

{admin}

function snap() {{
    return {{
        phase: currentPhase,
        view: shownView,
        round: byId('admin-round').textContent,
        question: byId('admin-question').textContent,
        note: byId('admin-detour-note').textContent,
        noteShown: byId('admin-detour-note').style.display !== 'none',
        nextHidden: byId('next-question-btn').classList.contains('hidden'),
        endHidden: byId('end-game-btn').classList.contains('hidden'),
        timer: {{
            started: timer.started, remaining: timer.remaining,
            stopped: timer.stopped
        }},
        leaderboard: leaderboard
    }};
}}

var AUCTION = {{
    type: 'hot_seat_auction', round_num: 4, total_rounds: 10,
    seconds: 20, players: 3
}};
var AWARDED = {{
    type: 'hot_seat_awarded', winner: 'Anna', entrant: 'Sofa',
    pct: 100, stake: 120, bids: []
}};
var QUESTION = {{
    type: 'hot_seat_question', round_num: 4, total_rounds: 10,
    winner: 'Anna', entrant: 'Sofa', seconds: 30,
    question: 'Which planet is closest to the sun?'
}};
function result(answered, delta) {{
    return {{
        type: 'hot_seat_result', round_num: 4, total_rounds: 10,
        winner: 'Anna', entrant: 'Sofa', winner_pct: 100, winner_stake: 120,
        winner_delta: delta, answered: answered,
        scores: {{ 'Sofa': 340, 'Sessel': 210 }}
    }};
}}

(async function () {{
    await window.QuizifyI18n.init('en');
    var out = {{}};

    // The evening as the host sees it, in the order the server sends it.
    handleMessage(AUCTION);
    out.auction = snap();
    handleMessage({{ type: 'hot_seat_tick', phase: 'auction', remaining: 12 }});
    handleMessage({{ type: 'hot_seat_bid_count', count: 2, total: 3 }});
    out.bidding = snap();
    handleMessage(AWARDED);
    out.awarded = snap();
    handleMessage(QUESTION);
    out.seatQuestion = snap();
    handleMessage(result(true, 120));
    out.settled = snap();

    // The host presses Next Question and the ordinary game resumes.
    handleQuestionStarted({{
        question_text: 'Round five', round_num: 5, total_rounds: 10,
        timer_duration: 30
    }});
    out.afterNext = snap();

    // The three outcomes of the chair. `answered` is tri-state.
    handleMessage(result(false, -120));
    out.wrong = snap();
    handleMessage(result(null, -120));
    out.timeout = snap();

    // Nobody bid: not a failure, a round that does not happen.
    handleMessage(AUCTION);
    handleMessage({{ type: 'hot_seat_no_bids' }});
    out.noBids = snap();

    // German, because the host reads the room in their own language.
    await window.QuizifyI18n.setLanguage('de');
    handleMessage(AUCTION);
    handleMessage(result(true, 120));
    out.settledDe = snap();

    console.log(JSON.stringify(out));
}})();
"""


def _run() -> dict:
    source = _admin()
    # The router, the notice and the question renderer are load-bearing for
    # every one of these tests; the per-frame handlers are what the fix adds,
    # so a missing one is a failed assertion below rather than a broken
    # fixture here.
    parts = [
        _js_function(source, "function _t(key, params) {"),
        _js_function(source, "function setDetourNotice(phase, seatHolder) {"),
        _js_function(source, "function handleQuestionStarted(msg) {"),
        _js_function(source, "function handleMessage(msg) {"),
    ]
    parts += [
        _js_function(source, signature, required=False)
        for signature in (
            "function _tOr(key, params, fallback) {",
            "function setDetourDetail(text) {",
            "function _setDetourRound(msg) {",
            "function handleHotSeatAuction(msg) {",
            "function handleHotSeatBidCount(msg) {",
            "function handleHotSeatNoBids() {",
            "function handleHotSeatAwarded(msg) {",
            "function handleHotSeatQuestion(msg) {",
            "function handleHotSeatTick(msg) {",
            "function handleHotSeatResult(msg) {",
        )
    ]
    script = _SCRIPT.format(
        stub=json.dumps(str(_STUB)),
        i18n=json.dumps(str(_I18N)),
        i18njs=json.dumps(str(_JS / "i18n.js")),
        classes=json.dumps(_element_classes()),
        admin="\n\n".join(parts),
    )
    out = subprocess.run(
        ["node", "-e", script], capture_output=True, text=True, check=True
    )
    return json.loads(out.stdout)


@_NEEDS_NODE
def test_the_settled_chair_offers_next_question() -> None:
    """The blocker, in one assertion: no reload, and a way on."""
    settled = _run()["settled"]

    assert settled["phase"] == "HOT_SEAT_REVEAL"
    assert settled["nextHidden"] is False, (
        "the host is left with the reset icon and End Game — both throw away "
        "the rest of the evening"
    )
    assert settled["endHidden"] is False
    assert settled["question"] == (
        "The chair is settled — Next Question continues the game."
    )


@_NEEDS_NODE
def test_the_auction_notice_does_not_outlive_the_auction() -> None:
    """What the live test photographed: the auction line, long after it."""
    out = _run()

    assert out["auction"]["question"] == "The chair goes to the highest bid"
    assert "highest bid" not in out["awarded"]["question"]
    assert "highest bid" not in out["seatQuestion"]["question"]
    assert "highest bid" not in out["settled"]["question"]


@_NEEDS_NODE
def test_the_host_watches_the_auction_fill_up() -> None:
    """#830: a host driving the evening from the admin tab watched the whole
    auction with nothing on screen. The count is the public half of a blind
    auction — never the amounts."""
    bidding = _run()["bidding"]

    assert bidding["note"] == "2 of 3 have bid"
    assert bidding["noteShown"] is True
    assert bidding["timer"] == {"started": 20, "remaining": 12, "stopped": 0}
    assert bidding["nextHidden"] is True, (
        "next_question is refused for the whole auction; offering it is the "
        "ERR_INVALID_ACTION #699 was about"
    )


@_NEEDS_NODE
def test_the_notice_names_who_took_the_chair_and_what_it_cost() -> None:
    """``winner`` is the person in the chair, ``entrant`` is who pays (#804)."""
    awarded = _run()["awarded"]

    assert awarded["phase"] == "HOT_SEAT"
    assert awarded["question"] == (
        "Anna has the chair and is answering alone — nobody else can answer."
    )
    assert awarded["note"] == "Sofa took the chair for 100% (120 pts)."


@_NEEDS_NODE
def test_the_host_can_read_the_seat_question() -> None:
    """The host is running the evening; for ninety seconds the only question
    in play was one they could not see."""
    seat = _run()["seatQuestion"]

    assert seat["question"] == "Which planet is closest to the sun?"
    assert seat["round"] == "Question 4 / 10"
    assert seat["timer"]["started"] == 30
    assert seat["note"] == "Sofa took the chair for 100% (120 pts)."


@_NEEDS_NODE
def test_the_settlement_says_which_way_it_went() -> None:
    """``answered`` is tri-state — true / false / null. A bare falsy check
    collapses a wrong answer into the timeout branch, and since #653 both cost
    the same points, so the number would not give it away."""
    out = _run()

    assert out["settled"]["note"] == "Sofa answered it — +120 points"
    assert out["wrong"]["note"] == "Sofa got it wrong — −120 points"
    assert out["timeout"]["note"] == "Sofa ran out of time — −120 points"


@_NEEDS_NODE
def test_the_leaderboard_follows_the_stake() -> None:
    """The settlement moves the standings the host is looking at."""
    settled = _run()["settled"]

    assert settled["leaderboard"] == [
        {"name": "Sofa", "score": 340},
        {"name": "Sessel", "score": 210},
    ]
    assert settled["timer"]["stopped"] == 1


@_NEEDS_NODE
def test_an_auction_nobody_bid_on_says_so() -> None:
    out = _run()["noBids"]

    assert out["question"] == "Nobody bid — carrying on as usual."
    assert out["noteShown"] is False
    assert out["nextHidden"] is True, (
        "the server resumes the normal question straight after; there is "
        "nothing here to advance"
    )


@_NEEDS_NODE
def test_the_detour_leaves_nothing_behind() -> None:
    """The second line belongs to a round that is over."""
    after = _run()["afterNext"]

    assert after["question"] == "Round five"
    assert after["note"] == ""
    assert after["noteShown"] is False


@_NEEDS_NODE
def test_the_host_reads_it_in_their_own_language() -> None:
    """The notice goes through the bundles, not through the fallbacks."""
    settled = _run()["settledDe"]

    assert settled["nextHidden"] is False
    assert settled["question"] != (
        "The chair is settled — Next Question continues the game."
    )
    assert "hotSeat." not in settled["question"]
    assert "hotSeat." not in settled["note"]
