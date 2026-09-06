"""The seat holder has to see the picture they are being charged for (#802).

``hot_seat.py:start`` filters *estimate* questions out of the detour and
nothing else, so a picture question can be the chair's question, and
``_broadcast_hot_seat_question`` sends ``image_url`` to every phone.

``handleQuestion`` in player-hotseat.js read the question text, the category
and the answer grid — and not that field. Nothing in the hot-seat panel ever
touched ``#question-media``. The banner is written only by
``renderQuestionImageBanner`` on ``question_started`` and cleared only by the
wager handler; the reveal deliberately leaves it alone. So the seat holder,
alone on the clock with a percentage of their score at stake, answered "Which
animal is this?" under the PREVIOUS round's photo, or under nothing, while the
television showed the right one. The spectators staking on them saw the same
stale banner.

Lightning walked this path first and grew its own ``#lightning-image``. Hot
Seat did not. #730 fixed the missing question *text* on reload; the image was
one field over and stayed missing on every path.
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

_CC = _REPO / "custom_components" / "quizify"
_JS = _CC / "www" / "js"
_STUB = Path(__file__).resolve().parent / "fixtures" / "dom_stub.js"

_NEEDS_NODE = pytest.mark.skipif(
    shutil.which("node") is None, reason="node not installed"
)


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


# ---------------------------------------------------------------------------
# The wiring
# ---------------------------------------------------------------------------


def test_the_banner_renderer_is_reachable_from_outside_player_game() -> None:
    """A second copy of the URL sanitiser is the wrong fix.

    ``renderQuestionImageBanner`` owns the ``safeImageUrl`` check (#536/#540),
    the localized alt text (#467) and the progressive-reveal reset (#434). The
    hot seat needs all three, so it borrows the function rather than a subset
    of its behaviour.
    """
    source = (_JS / "player-game.js").read_text("utf-8")
    exports = source.split("window.QuizifyPlayerGame = {", 1)[1]

    assert "renderQuestionImageBanner: renderQuestionImageBanner," in exports


def test_the_hot_seat_question_reads_the_field_the_server_sends() -> None:
    source = _without_comments((_JS / "player-hotseat.js").read_text("utf-8"))
    body = _js_function(source, "function handleQuestion(msg)")

    assert "msg.image_url" in body, (
        "the server sends image_url to every phone; handleQuestion must read it"
    )


def test_the_auction_clears_the_previous_round_s_picture() -> None:
    """Two reasons, and the second one is the sharper.

    The banner on screen when the auction opens belongs to a question that is
    over. It is also, on a picture round, a free look at what the chair is
    about to be — and the whole point of a sealed bid is that it is a bet on
    yourself, not on a question you have already read.
    """
    source = _without_comments((_JS / "player-hotseat.js").read_text("utf-8"))
    body = _js_function(source, "function handleAuctionYou(msg)")

    assert "paintQuestionImage('')" in body


def test_the_restore_path_forwards_the_image_too() -> None:
    """#730 made this a frame copy rather than a hand-written field list.

    The point of that change was that the next field would ride along for free.
    This is the next field: the snapshot's hot_seat.question block carries
    image_url, and questionMessageFromSnapshot copies every key it finds.
    """
    source = _without_comments((_JS / "player-hotseat.js").read_text("utf-8"))
    body = _js_function(source, "function questionMessageFromSnapshot(hs)")

    assert "for (var k in q)" in body
    assert "msg[k] = q[k]" in body

    state_source = (_CC / "game" / "state.py").read_text("utf-8")
    hot_seat_block = state_source.split('block["question"] = {', 1)[1].split("}", 1)[0]
    assert '"image_url"' in hot_seat_block


# ---------------------------------------------------------------------------
# … and what it actually does
# ---------------------------------------------------------------------------


_SCRIPT = """
require({stub});
QZ.els([
    'hotseat-panel', 'hotseat-title', 'hotseat-hint', 'hotseat-bank',
    'hotseat-bid-stage', 'hotseat-bet-stage', 'hotseat-bid-btn',
    'hotseat-bid-count', 'hotseat-slider', 'hotseat-value',
    'hotseat-bet-slider', 'hotseat-bet-value',
    'question-text', 'question-category', 'answer-buttons'
]);

var seen = [];
window.QuizifyI18n = {{ t: function (k) {{ return k; }} }};
window.QuizifyPlayer = {{ send: function () {{}} }};
window.QuizifyPlayerUtils = {{ state: {{ playerName: 'Anna' }} }};
window.QuizifyPlayerGame = {{
    updateTimer: function () {{}},
    renderQuestionImageBanner: function (url, style, duration) {{
        seen.push({{ url: url, style: style, duration: duration }});
    }}
}};

QZ.load({hotseat});
var hs = window.QuizifyPlayerHotSeat;

hs.handleAuctionYou({{ score: 100 }});
var afterAuction = seen[seen.length - 1];

hs.handleQuestion({{
    question: 'Which animal is this?',
    image_url: 'https://example.invalid/lynx.png',
    you_are_seated: true,
    answers: ['a', 'b', 'c']
}});
var seated = seen[seen.length - 1];

hs.handleQuestion({{
    question: 'Which animal is this?',
    image_url: 'https://example.invalid/lynx.png',
    you_are_seated: false,
    winner: 'Bea',
    score: 80
}});
var spectator = seen[seen.length - 1];

hs.handleQuestion({{ question: 'No picture here', you_are_seated: true, answers: [] }});
var textOnly = seen[seen.length - 1];

console.log(JSON.stringify({{
    calls: seen.length,
    afterAuction: afterAuction,
    seated: seated,
    spectator: spectator,
    textOnly: textOnly
}}));
"""


def _run() -> dict:
    script = _SCRIPT.format(
        stub=json.dumps(str(_STUB)),
        hotseat=json.dumps(str(_JS / "player-hotseat.js")),
    )
    out = subprocess.run(
        ["node", "-e", script], capture_output=True, text=True, check=True
    )
    return json.loads(out.stdout)


@_NEEDS_NODE
def test_the_seat_holder_gets_the_picture() -> None:
    """The reported symptom, run rather than read."""
    result = _run()

    assert result["seated"]["url"] == "https://example.invalid/lynx.png"


@_NEEDS_NODE
def test_so_do_the_people_betting_on_them() -> None:
    """The spectators' bet stage shares the same banner element, and had the
    same stale picture above it."""
    result = _run()

    assert result["spectator"]["url"] == "https://example.invalid/lynx.png"


@_NEEDS_NODE
def test_a_text_question_takes_the_banner_down() -> None:
    """Most hot seat questions carry no picture. Leaving the previous one up is
    the other half of the bug, and the one a text round would show."""
    result = _run()

    assert result["textOnly"]["url"] == ""


@_NEEDS_NODE
def test_the_auction_starts_with_a_clean_banner() -> None:
    result = _run()

    assert result["afterAuction"]["url"] == ""


@_NEEDS_NODE
def test_the_hot_seat_never_asks_for_a_progressive_reveal() -> None:
    """The blur is a fraction of the round's clock (#434/#731). The detour runs
    its own clock and has no reveal style, so handing the banner a duration
    would blur a picture nobody asked to have blurred."""
    result = _run()

    for call in ("afterAuction", "seated", "spectator", "textOnly"):
        assert result[call]["style"] is None, call
        assert result[call]["duration"] == 0, call
