"""#874 — a television that reloads during the Hot Seat settlement.

``renderHotSeatFromSnapshot`` in ``js/dashboard.js`` branched on two of the
server's three stages. ``auction`` and ``question`` had a case; ``result`` —
emitted for the whole of ``HOT_SEAT_REVEAL`` (``serializers.py``) — fell
through to ``handleHotSeatAwarded``, the handler for the moment the chair is
sold. So a board that reconnected inside the settlement printed the auction
price ("Anna — 50%") with no correct answer and no outcome, and kept printing
it: ``HOT_SEAT_REVEAL`` lasts until the host taps Next, so the whole window is
reconnect territory, at the biggest single points swing of the game.

The snapshot already carried the cure. ``hot_seat.summary`` is the same dict
the live ``hot_seat_result`` frame spreads, so the restored board can render
the identical frame rather than a second, weaker version of it.

The behavioural tests below build a real game, drive it to ``HOT_SEAT_REVEAL``,
serialize the real snapshot, and run the real ``renderHotSeatFromSnapshot``
over it under node (``tests/fixtures/dom_stub.js``, the #803/#826 precedent) —
so what is asserted is what the television puts on screen, not the shape of a
source file. Same family as #832, #848, #858 and #870: a screen must not behave
differently depending on whether it lived through the round or was rebuilt from
a snapshot.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO))

from custom_components.quizify.game.phase_controller import GamePhase  # noqa: E402
from custom_components.quizify.game.state import QuizifyGameState  # noqa: E402
from custom_components.quizify.server.serializers import (  # noqa: E402
    serialize_state_snapshot,
)

# #829: the television's script is its own file now.
_JS = _REPO / "custom_components" / "quizify" / "www" / "js"
_DASHBOARD = _JS / "dashboard.js"
_STUB = Path(__file__).resolve().parent / "fixtures" / "dom_stub.js"

_NEEDS_NODE = pytest.mark.skipif(
    shutil.which("node") is None, reason="node not installed"
)


# ---------------------------------------------------------------------------
# A real game, settled
# ---------------------------------------------------------------------------


def _ws() -> MagicMock:
    ws = MagicMock()
    ws.closed = False
    return ws


class _Runtime:
    def __init__(self, tmp_path: Path) -> None:
        self.data_dir = tmp_path


@pytest.fixture
def settled(tmp_path: Path) -> dict:
    """The snapshot a television gets when it reloads during the settlement."""
    st = QuizifyGameState(runtime=_Runtime(tmp_path), entry_id="test")
    for name in ("Anna", "Ben", "Mira"):
        st.add_player(name, _ws())
    st.start_game(
        category="picture-round-en", difficulty="easy", num_rounds=5, language="en"
    )
    for player in st.get_players():
        player.score = 40
    st.phase = GamePhase.ANSWER_REVEAL
    assert st.start_hot_seat_auction() is True
    assert st.hot_seat is not None
    assert st.hot_seat.record_bid("Anna", 50) is True
    st.close_hot_seat_auction()
    # Nobody answers: the seat holder runs out of time, which is the outcome
    # the room is waiting to read and the one the auction line hides.
    st.finish_hot_seat()
    snapshot = serialize_state_snapshot(st)
    assert snapshot["phase"] == GamePhase.HOT_SEAT_REVEAL.value
    return snapshot


def test_the_snapshot_carries_the_settlement(settled: dict) -> None:
    """The premise of the fix: nothing new has to reach the wire."""
    block = settled["hot_seat"]
    assert block["stage"] == "result"
    summary = block["summary"]
    assert summary["entrant"] == "Anna"
    assert summary["correct_answer"]
    assert summary["winner_delta"] < 0, "a timed-out 50% stake costs points"


# ---------------------------------------------------------------------------
# The television, run for real
# ---------------------------------------------------------------------------


def _without_comments(source: str) -> str:
    source = re.sub(r"/\*.*?\*/", "", source, flags=re.S)
    return re.sub(r"^\s*//.*$", "", source, flags=re.M)


def _js_function(source: str, signature: str) -> str:
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


_HOT_SEAT_FUNCTIONS = (
    "function hotSeatT(key, vars, fallback)",
    "function hotSeatFrame(round, total)",
    "function handleHotSeatAuction(msg)",
    "function handleHotSeatBidCount(msg)",
    "function handleHotSeatAwarded(msg)",
    "function handleHotSeatQuestion(msg)",
    "function handleHotSeatResult(msg)",
    "function renderHotSeatFromSnapshot(hs, round, total)",
)


def _render(snapshot: dict) -> dict:
    """Run the page's own hot-seat renderers over a real snapshot.

    Everything the detour draws with is the shipped source. The four
    collaborators that are *not* — ``showView``, ``renderQuestionImage``,
    ``renderLeaderboard``, ``handleQuestionStarted`` — are recorded instead of
    executed, because they belong to other frames of the page and would drag
    half the television in with them. ``escapeHtml`` is stubbed only because
    dom_stub has no innerHTML serializer for text nodes.
    """
    source = _without_comments(_DASHBOARD.read_text(encoding="utf-8"))
    els_block = source[source.index("var els = {") :]
    els_block = els_block[: els_block.index("};") + 2]
    ids = sorted(set(re.findall(r"getElementById\('([^']+)'\)", els_block)))
    bodies = "\n".join(_js_function(source, sig) for sig in _HOT_SEAT_FUNCTIONS)

    script = f"""
'use strict';
require({json.dumps(str(_STUB))});
QZ.els({json.dumps(ids)});
// #787: the detour's three sentences are the shared renderers' now, and the
// page loads common.bundle.js ahead of its own script for exactly this reason.
QZ.load({json.dumps(str(_JS / "utils.js"))});
QZ.load({json.dumps(str(_JS / "render-shared.js"))});

var seen = {{ views: [], leaderboards: [], questions: [] }};
function showView(name) {{ seen.views.push(name); }}
function renderQuestionImage() {{}}
function renderLeaderboard(container, players) {{
    seen.leaderboards.push((players || []).length);
}}
function handleQuestionStarted(msg) {{ seen.questions.push(msg.question_text); }}
function escapeHtml(text) {{ return String(text == null ? '' : text); }}
var currentPhase = 'UNSET';
var timerDuration = 0;
var timerRemaining = 0;

// A recognisable i18n so the assertions can name the string the board picked
// rather than guess at a rendered sentence.
window.QuizifyI18n = {{
    t: function (key, vars) {{ return key + '|' + JSON.stringify(vars || {{}}); }}
}};

{els_block}
{bodies}

var snap = {json.dumps(snapshot)};
renderHotSeatFromSnapshot(snap.hot_seat, snap.round, snap.total_rounds);

console.log(JSON.stringify({{
    headline: els.questionText.textContent,
    grid: els.answersGrid.innerHTML,
    round: els.roundIndicator.textContent,
    phase: currentPhase,
    seen: seen
}}));
"""
    out = subprocess.run(
        ["node", "-e", script], capture_output=True, text=True, check=True
    )
    return json.loads(out.stdout)


@_NEEDS_NODE
def test_the_restored_board_shows_the_outcome_not_the_price(settled: dict) -> None:
    """The bug, in the one line the room reads.

    Without the ``result`` branch this is ``hotSeat.lost`` — the auction line,
    naming a percentage — while the settlement it is standing in front of
    never appears.
    """
    result = _render(settled)
    key, _, raw = result["headline"].partition("|")
    variables = json.loads(raw)

    assert key == "hotSeat.resultTimeout", (
        "a board rebuilt inside HOT_SEAT_REVEAL rendered "
        f"{key!r} — the settlement is the frame the room is waiting for"
    )
    assert "pct" not in variables, "the auction price is over; the points moved"
    assert variables["name"] == "Anna"
    assert variables["pts"] == abs(settled["hot_seat"]["summary"]["winner_delta"])


@_NEEDS_NODE
def test_the_restored_board_spells_out_the_correct_answer(settled: dict) -> None:
    """#833's second finding, on the reload path.

    Without it the left column is one line over an empty screen for as long as
    the host takes to tap Next.
    """
    result = _render(settled)
    assert settled["hot_seat"]["summary"]["correct_answer"] in result["grid"]
    assert "correct" in result["grid"]


@_NEEDS_NODE
def test_the_restored_board_knows_which_phase_it_is_in(settled: dict) -> None:
    """``currentPhase`` drives what the next live frame is allowed to do."""
    result = _render(settled)
    assert result["phase"] == "HOT_SEAT_REVEAL"


@_NEEDS_NODE
def test_the_other_two_stages_still_route_where_they_did(tmp_path: Path) -> None:
    """The new branch sits in front of the awarded fallback, not in the road.

    An auction snapshot must still draw the auction line, and an awarded one —
    the stage the fallback exists for — must still name the payer and the
    price.
    """
    auction = {
        "hot_seat": {"stage": "auction", "time_remaining": 12.0, "bid_count": 2,
                     "bidder_count": 3},
        "round": 3,
        "total_rounds": 8,
    }
    assert _render(auction)["headline"].startswith("hotSeat.auctionTitle")

    awarded = {
        "hot_seat": {"stage": "awarded", "winner": "Anna", "entrant": "Sofa",
                     "pct": 50, "stake": 20},
        "round": 3,
        "total_rounds": 8,
    }
    key, _, raw = _render(awarded)["headline"].partition("|")
    assert key == "hotSeat.lost"
    assert json.loads(raw) == {"name": "Sofa", "pct": 50, "pts": 20}
