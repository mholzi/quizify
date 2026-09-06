"""The seat holder is shown the answers they are being charged for (#847).

Found on hardware in the v1.16.0-RC2 live test, in the first auction of the
evening that reached a settlement. Team Sofa bought the chair for 80 % of its
points. On the seat holder's phone the chair was announced correctly, the seat
question's text was at the top and its clock was running — and the panel under
it was the *previous* round's estimate slider, still showing their own
submitted guess. There were no answers on the phone at all. The television had
the three options up the whole time. The clock ran out and the settlement took
60 points, the largest single swing of the game, for a question the player was
never shown an answer to.

The cause is one section swap that only ever ran in one direction.
``renderQuestion`` branches on the round type (#275): an estimate round hides
``#answers-container`` and shows ``#estimate-container``. ``hot_seat.py`` keeps
estimate questions out of the CHAIR's question only — not out of the rounds the
auction interrupts — so the detour regularly opens on top of a slider, and
``renderSeatAnswers`` filled a grid inside a section that was still hidden.

Same family as #698 (the previous round's question text), #802 (its picture)
and #696 (the grid's markup): everything the detour borrows from the normal
round has to be taken over, not assumed clean. This one was the expensive one,
because the phone that could not answer was the phone with points at stake.
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


# ---------------------------------------------------------------------------
# The premise: the server did its half
# ---------------------------------------------------------------------------


def test_the_server_sends_the_seat_holder_their_answers() -> None:
    """Worth pinning before blaming the phone. The seat holder's frame carries
    the shuffled options and ``you_are_seated``; the spectators' carries an
    empty list. Nothing was missing on the wire."""
    source = (_CC / "server" / "websocket.py").read_text("utf-8")
    body = source.split("async def _broadcast_hot_seat_question(", 1)[1].split(
        "\n    async def ", 1
    )[0]

    assert '"answers": hs.shuffled_answers(),' in body
    assert '"you_are_seated": True,' in body


def test_the_round_the_auction_interrupts_may_be_an_estimate() -> None:
    """The other half of the premise. If the detour could never follow an
    estimate round the sections would never be swapped when it opens."""
    source = (_CC / "game" / "hot_seat.py").read_text("utf-8")

    assert "is_estimate" in source, (
        "hot_seat.py filters estimate questions out of the CHAIR's question; "
        "if that ever became a filter on the whole round, this bug's premise "
        "would be gone and this file should say so"
    )
    body = re.sub(r"^\s*#.*$", "", source, flags=re.M)
    assert "def start(" in body


# ---------------------------------------------------------------------------
# …and what the phone does with it
# ---------------------------------------------------------------------------


_SCRIPT = """
require({stub});
QZ.els([
    'hotseat-panel', 'hotseat-title', 'hotseat-hint', 'hotseat-bank',
    'hotseat-bid-stage', 'hotseat-bet-stage', 'hotseat-bid-btn',
    'hotseat-bid-count', 'hotseat-slider', 'hotseat-value',
    'hotseat-bet-slider', 'hotseat-bet-value',
    'question-text', 'question-category', 'answer-buttons',
    'answers-container', 'estimate-container'
]);

window.QuizifyI18n = {{ t: function (k) {{ return k; }} }};
window.QuizifyPlayer = {{ send: function () {{}} }};
window.QuizifyPlayerUtils = {{ state: {{ playerName: 'Anna' }} }};
window.QuizifyPlayerGame = {{
    updateTimer: function () {{}},
    renderQuestionImageBanner: function () {{}}
}};

QZ.load({hotseat});
var hs = window.QuizifyPlayerHotSeat;

// The state renderQuestion leaves behind on an estimate round (#275): the
// slider is up, the answer grid is not.
function estimateRound() {{
    document.getElementById('estimate-container').classList.remove('hidden');
    document.getElementById('answers-container').classList.add('hidden');
}}
function shot() {{
    return {{
        answers: document.getElementById('answers-container').classList.contains('hidden'),
        estimate: document.getElementById('estimate-container').classList.contains('hidden')
    }};
}}

estimateRound();
hs.handleQuestion({{
    question: 'Why do your fingertips wrinkle after a long bath?',
    you_are_seated: true,
    answers: ['Osmosis', 'A nerve reflex', 'Skin swelling']
}});
var seated = shot();

estimateRound();
hs.handleQuestion({{
    question: 'Why do your fingertips wrinkle after a long bath?',
    you_are_seated: false,
    winner: 'Bea',
    score: 40
}});
var spectator = shot();

// A normal multiple-choice round: the grid was already up and must stay up.
document.getElementById('answers-container').classList.remove('hidden');
document.getElementById('estimate-container').classList.add('hidden');
hs.handleQuestion({{
    question: 'Text question',
    you_are_seated: true,
    answers: ['a', 'b', 'c']
}});
var afterChoiceRound = shot();

console.log(JSON.stringify({{
    seated: seated,
    spectator: spectator,
    afterChoiceRound: afterChoiceRound
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
def test_the_seat_holder_gets_the_answer_grid_back() -> None:
    """The reported symptom, run rather than read: the chair's question with
    nothing to answer it with."""
    result = _run()

    assert result["seated"]["answers"] is False, (
        "#answers-container is still hidden, so the grid renderSeatAnswers "
        "fills is off screen and the seat holder has nothing to tap"
    )


@_NEEDS_NODE
def test_the_previous_round_s_slider_is_gone_for_the_seat_holder() -> None:
    """It is not only useless, it is misleading: it still reads '✓ Submitted'
    from the round before, which is what made the phone look like it had
    already answered."""
    result = _run()

    assert result["seated"]["estimate"] is True


@_NEEDS_NODE
def test_the_spectators_lose_it_too() -> None:
    """They are staking on the chair, not answering, so they get no grid — but
    the slider belongs to a round that is over for them as well."""
    result = _run()

    assert result["spectator"]["estimate"] is True
    assert result["spectator"]["answers"] is True, (
        "a spectator must not be handed an answer grid; the server sends them "
        "an empty answers list on purpose"
    )


@_NEEDS_NODE
def test_a_choice_round_is_left_exactly_as_it_was() -> None:
    """The common case, and the one that already worked. A fix that swapped
    sections unconditionally would be indistinguishable here, so this pins
    that it stays a no-op."""
    result = _run()

    assert result["afterChoiceRound"] == {"answers": False, "estimate": True}
