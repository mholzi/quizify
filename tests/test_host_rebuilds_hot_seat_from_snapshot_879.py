"""The host page rebuilds the whole Hot Seat out of the snapshot (#879).

The other half of #832, from the same weekly review. #832 taught the host page
to follow the detour's ``hot_seat_*`` broadcasts; this is what happens when the
page arrives in the middle of one — a tab reopened, a reload, a second host
device — and the only thing the server has for it is ``game_state``.

That snapshot carries the round, the bid count, the seat question and the
settlement (``server/serializers.py``, the ``hot_seat`` block). The page read
one field of it, ``winner``, handed it to ``setDetourNotice`` and drew nothing
else: no round number, no bid count, no question, no settlement.

The clock is the part that could not repair itself later. ``adminTimer.start``
never ran, so ``adminTimerDuration`` stayed 0 — and ``adminTimer.update``
guards the bar with ``adminTimerDuration > 0``. Every ``hot_seat_tick`` for the
rest of the detour then moved the seconds and left the bar exactly where it
was. A frozen bar under a running number is worse than no bar at all, because
the bar is the thing a host reads across the room.

These tests run the real ``admin.js`` — the real ``handleGameState``, the real
detour handlers and the real ``adminTimer`` against real elements — under node
(``tests/fixtures/dom_stub.js``), so they assert what the host reads off the
screen and how wide the bar actually is. A source-text assertion would have
passed on a fix that called ``start`` with the wrong number.
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

#: Every element the code under test touches, so the harness is furnished like
#: the real page rather than like the test's idea of it.
ELEMENT_IDS = (
    "setup-screen",
    "lobby-screen",
    "game-view",
    "admin-finale-view",
    "admin-lightning-view",
    "admin-lightning-recap-view",
    "admin-round",
    "admin-question",
    "admin-detour-note",
    "admin-correct",
    "game-leaderboard",
    "next-question-btn",
    "end-game-btn",
    "reset-game-btn",
    "admin-timer-bar",
    "admin-timer-bar-text",
)


def _admin() -> str:
    return _ADMIN_JS.read_text(encoding="utf-8")


def _js_block(source: str, signature: str, required: bool = True) -> str:
    """One brace-balanced declaration — a function or an object literal.

    ``required=False`` returns nothing for a declaration that is not there, so
    the harness still runs against a host page that has none of this fix. The
    tests then fail on what the host reads, which is the complaint, rather
    than on an exception in the fixture.
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

    Read rather than invented: whether the game view starts active and whether
    Next Question starts hidden are load-bearing here, and a harness that
    guessed would prove nothing.
    """
    html = _ADMIN_HTML.read_text(encoding="utf-8")
    out: dict[str, list[str]] = {}
    for element_id in ELEMENT_IDS:
        m = re.search(rf'id="{element_id}"(?:\s+class="([^"]*)")?', html)
        assert m, f"#{element_id} is missing from admin.html"
        out[element_id] = (m.group(1) or "").split()
    return out


# ---------------------------------------------------------------------------
# The premise: the snapshot carries the whole detour
# ---------------------------------------------------------------------------


def test_the_snapshot_carries_what_the_host_page_was_missing() -> None:
    """Nothing was absent on the wire, which is why nothing looked broken."""
    source = (
        _REPO / "custom_components" / "quizify" / "server" / "serializers.py"
    ).read_text("utf-8")
    block = source.split('block: dict[str, Any] = {', 1)[1].split(
        'snapshot["hot_seat"] = block', 1
    )[0]

    for field in (
        '"stage"',
        '"time_remaining"',
        '"auction_seconds"',
        '"answer_seconds"',
        '"bid_count"',
        '"bidder_count"',
        '"winner"',
        '"entrant"',
        '"question"',
        '"summary"',
    ):
        assert field in block, f"the snapshot no longer carries {field}"


# ---------------------------------------------------------------------------
# …and what the host actually reads
# ---------------------------------------------------------------------------


_SCRIPT = """
require({stub});
QZ.serveI18n({i18n});

var CLASSES = {classes};
Object.keys(CLASSES).forEach(function (id) {{
    var el = QZ.el(id);
    CLASSES[id].forEach(function (c) {{ el.classList.add(c); }});
}});
// The bar's fill is a class-only child of #admin-timer-bar, which is how
// admin.js finds it — and the element whose width this whole issue is about.
var fill = document.createElement('div');
fill.setAttribute('class', 'timer-bar-fill');
QZ.el('admin-timer-bar').appendChild(fill);

QZ.load({i18njs});

// The page around the snapshot handler. Everything the detour touches is
// real; the arms of the phase switch that belong to the ordinary round are
// stubbed, because the subject here is the detour.
var _redirecting = false;
var _adminJoinedAs = null;
var _presetsLoaded = true;
var _ttsEls = null;
var _houseEls = null;
var _ttsEntitiesLoaded = true;
var _houseEntitiesLoaded = true;
var _lobbyTeams = null;
var currentPhase = 'LOBBY';
var QuizifyUtils = {{ writeAdminToken: function () {{}} }};
var sessionStorage = {{
    getItem: function () {{ return null; }},
    removeItem: function () {{}},
    setItem: function () {{}}
}};
function _loadCustomPresets() {{}}
function renderLobbyPlayers() {{}}
function redirectToPlayer() {{}}
function handleQuestionStarted() {{}}
function handleRoundSummary() {{}}
function handleFinale() {{}}
function handleAdminLightningSplash() {{}}
function handleAdminLightningQuestion() {{}}
function handleAdminLightningRecap() {{}}
var leaderboard = null;
function renderLeaderboard(container, players) {{ leaderboard = players; }}

{admin}

function snap() {{
    return {{
        phase: currentPhase,
        gameActive: document.getElementById('game-view').classList.contains('active'),
        round: document.getElementById('admin-round').textContent,
        question: document.getElementById('admin-question').textContent,
        note: document.getElementById('admin-detour-note').textContent,
        noteShown:
            document.getElementById('admin-detour-note').style.display !== 'none',
        nextHidden:
            document.getElementById('next-question-btn').classList.contains('hidden'),
        seconds: document.getElementById('admin-timer-bar-text').textContent,
        barWidth: fill.style.width,
        leaderboard: leaderboard
    }};
}}

function snapshot(phase, hotSeat) {{
    return {{
        type: 'game_state', phase: phase, round: 4, total_rounds: 10,
        players: [], leaderboard: [{{ name: 'Sofa', score: 340 }}],
        hot_seat: hotSeat
    }};
}}

var AUCTION = {{
    stage: 'auction', time_remaining: 12, auction_seconds: 20,
    answer_seconds: 30, banks: {{}}, bid_count: 2, bidder_count: 5,
    winner: null, entrant: null
}};
var SEAT = {{
    stage: 'question', time_remaining: 9, auction_seconds: 20,
    answer_seconds: 30, banks: {{}}, bid_count: 3, bidder_count: 5,
    winner: 'Anna', entrant: 'Sofa', pct: 100, stake: 120, bids: [],
    question: {{
        text: 'Which planet is closest to the sun?',
        answers: ['Mercury', 'Venus', 'Mars', 'Earth'],
        category: 'Science', difficulty: 'medium', image_url: null
    }}
}};
var SETTLED = {{
    stage: 'result', time_remaining: 0, auction_seconds: 20,
    answer_seconds: 30, banks: {{}}, bid_count: 3, bidder_count: 5,
    winner: 'Anna', entrant: 'Sofa', pct: 100, stake: 120, bids: [],
    question: SEAT.question,
    summary: {{
        winner: 'Anna', entrant: 'Sofa', winner_pct: 100, winner_stake: 120,
        winner_delta: -120, answered: false, bids: [], bets: []
    }}
}};

(async function () {{
    await window.QuizifyI18n.init('en');
    var out = {{}};

    // A host page that opens during the auction, and the clock that then has
    // to keep running off the ticks alone.
    handleGameState(snapshot('HOT_SEAT_AUCTION', AUCTION));
    out.auction = snap();
    handleMessage({{ type: 'hot_seat_tick', phase: 'auction', remaining: 5 }});
    out.auctionTicked = snap();

    // …during the seat holder's question.
    handleGameState(snapshot('HOT_SEAT', SEAT));
    out.seat = snap();
    handleMessage({{ type: 'hot_seat_tick', phase: 'answer', remaining: 3 }});
    out.seatTicked = snap();

    // …and after the chair has been settled, which is the one phase of the
    // detour where the server accepts next_question.
    handleGameState(snapshot('HOT_SEAT_REVEAL', SETTLED));
    out.settled = snap();

    // The wager window has no block of its own and must keep its own notice.
    handleGameState({{
        type: 'game_state', phase: 'WAGER_ACTIVE', round: 4, total_rounds: 10,
        players: [], leaderboard: []
    }});
    out.wager = snap();

    // German, because the host reads the room in their own language.
    await window.QuizifyI18n.setLanguage('de');
    handleGameState(snapshot('HOT_SEAT', SEAT));
    out.seatDe = snap();

    console.log(JSON.stringify(out));
}})();
"""


def _run() -> dict:
    source = _admin()
    parts = [
        # The page's own furniture, taken from the source so the harness
        # cannot drift from the real element wiring.
        _js_block(source, "const views = {"),
        _js_block(source, "const els = {"),
        _js_block(source, "var adminTimer = {"),
        _js_block(source, "function _adminTimerText() {"),
        _js_block(source, "function _adminTimerFill() {"),
        _js_block(source, "function showView(name) {"),
        _js_block(source, "function _t(key, params) {"),
        _js_block(source, "function _tOr(key, params, fallback) {"),
        _js_block(source, "function setDetourNotice(phase, seatHolder) {"),
        _js_block(source, "function setDetourDetail(text) {"),
        _js_block(source, "function _setDetourRound(msg) {"),
        _js_block(source, "function handleHotSeatAuction(msg) {"),
        _js_block(source, "function handleHotSeatBidCount(msg) {"),
        _js_block(source, "function handleHotSeatAwarded(msg) {"),
        _js_block(source, "function handleHotSeatQuestion(msg) {"),
        _js_block(source, "function handleHotSeatTick(msg) {"),
        _js_block(source, "function handleHotSeatResult(msg) {"),
        _js_block(source, "function handleMessage(msg) {"),
        _js_block(source, "function handleGameState(msg) {"),
    ]
    # What the fix adds. Absent, the harness still runs and the assertions
    # below fail on the screen the host is left with.
    parts += [
        _js_block(source, signature, required=False)
        for signature in (
            "function renderHotSeatFromSnapshot(hs, round, totalRounds) {",
            "function _detourWindow(full, hs) {",
            "function _resumeDetourClock(hs) {",
            "function clearAnswerProgress() {",
        )
    ]
    # The bar's four module-level variables live outside any function.
    preamble = (
        "var adminTimerTextEl = null;\n"
        "var adminTimerFillEl = null;\n"
        "var adminTimerDuration = 0;\n"
        "var adminTimerInterval = null;\n"
    )
    script = _SCRIPT.format(
        stub=json.dumps(str(_STUB)),
        i18n=json.dumps(str(_I18N)),
        i18njs=json.dumps(str(_JS / "i18n.js")),
        classes=json.dumps(_element_classes()),
        admin=preamble + "\n\n".join(p for p in parts if p),
    )
    out = subprocess.run(
        ["node", "-e", script], capture_output=True, text=True, check=True
    )
    return json.loads(out.stdout)


@_NEEDS_NODE
def test_the_clock_bar_runs_again_after_a_snapshot_rebuild() -> None:
    """The frozen bar, which is the half of this issue no reload-free fix can
    fake: ``update`` refuses to touch the fill while ``adminTimerDuration`` is
    0, and only ``start`` ever sets it. The width is asserted rather than the
    call, because a fix that started the clock with the wrong number would
    leave a bar that runs and lies."""
    out = _run()

    # 12 seconds left of a 20-second auction, then a tick at 5.
    assert out["auction"]["barWidth"] == "60%"
    assert out["auctionTicked"]["barWidth"] == "25%", (
        "the bar is frozen for the rest of the detour — every tick hits the "
        "`adminTimerDuration > 0` guard because start() never ran"
    )
    assert out["auctionTicked"]["seconds"] == "5s"

    # 9 seconds left of the seat holder's 30, then a tick at 3.
    assert out["seat"]["barWidth"] == "30%"
    assert out["seatTicked"]["barWidth"] == "10%"


@_NEEDS_NODE
def test_the_bar_shows_the_window_that_is_left_not_a_full_one() -> None:
    """Restarting the bar full at the moment somebody reconnects would be the
    same lie in the other direction: the room has been bidding for eight
    seconds and the host would be shown an untouched clock."""
    auction = _run()["auction"]

    assert auction["barWidth"] != "100%"
    assert auction["seconds"] == "12s"


@_NEEDS_NODE
def test_the_auction_snapshot_shows_the_bids_landing() -> None:
    """A blind auction: the count is the public half, and it is the only thing
    that moves on the host's screen for the whole window."""
    auction = _run()["auction"]

    assert auction["phase"] == "HOT_SEAT_AUCTION"
    assert auction["gameActive"] is True
    assert auction["round"] == "Question 4 / 10"
    assert auction["question"] == "The chair goes to the highest bid"
    assert auction["note"] == "2 of 5 have bid"
    assert auction["noteShown"] is True
    assert auction["nextHidden"] is True, (
        "next_question is refused for the whole auction; offering it is the "
        "ERR_INVALID_ACTION #699 was about"
    )


@_NEEDS_NODE
def test_the_seat_question_snapshot_shows_the_question_and_the_price() -> None:
    """The host is running the evening. For ninety seconds the only question
    in play was one the snapshot was carrying and the page threw away."""
    seat = _run()["seat"]

    assert seat["phase"] == "HOT_SEAT"
    assert seat["question"] == "Which planet is closest to the sun?"
    assert seat["round"] == "Question 4 / 10"
    # #804: `winner` is the person in the chair, `entrant` is who pays.
    assert seat["note"] == "Sofa took the chair for 100% (120 pts)."


@_NEEDS_NODE
def test_the_settled_snapshot_says_how_it_went_and_offers_next_question() -> None:
    """``summary`` is in the snapshot for exactly this, and a host who
    reopened the tab here used to get the auction's notice and no way on."""
    settled = _run()["settled"]

    assert settled["phase"] == "HOT_SEAT_REVEAL"
    assert settled["question"] == (
        "The chair is settled — Next Question continues the game."
    )
    assert settled["note"] == "Sofa got it wrong — −120 points"
    assert settled["nextHidden"] is False
    assert settled["leaderboard"] == [{"name": "Sofa", "score": 340}]


@_NEEDS_NODE
def test_the_wager_window_keeps_its_own_notice() -> None:
    """Guards the fix against overreach: WAGER_ACTIVE shares the arm and has
    no ``hot_seat`` block, so it must still land on its own line."""
    wager = _run()["wager"]

    assert wager["phase"] == "WAGER_ACTIVE"
    assert wager["question"] == "Placing bets"
    assert wager["nextHidden"] is True


@_NEEDS_NODE
def test_the_rebuilt_screen_speaks_the_room_language() -> None:
    """The snapshot path goes through the same renderers as the live one, so
    it cannot end up with one screen translated and the other not."""
    seat = _run()["seatDe"]

    assert seat["round"] == "Frage 4 / 10"
    assert seat["note"] == "Sofa hat den Stuhl für 100% (120 Pkt.)."
