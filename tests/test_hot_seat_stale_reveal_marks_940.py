"""The chair's question is not answered on the last round's reveal (#940).

The reveal paints ``#answer-buttons`` with ``correct`` / ``wrong`` /
``dimmed`` (★ and × via CSS). The only code that strips them is
``resetSubmissionState``, which runs when a round or a wager window opens. The
Hot Seat opens between rounds, and ``renderSeatAnswers`` reused the grid while
removing only the ``is-*`` classes — so the seat holder answered the chair's
question with a star on one option and a cross on another.

Same family as #698 (question text), #802 (picture) and #847 (the section
swap): everything the detour borrows from the normal round has to be taken
over, not assumed clean. These tests run the real ``player-game.js`` and
``player-hotseat.js`` against the DOM stub and read the class lists back.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parent.parent
_WWW = _REPO / "custom_components" / "quizify" / "www"
_JS = _WWW / "js"
_I18N = _WWW / "i18n"
_STUB = Path(__file__).resolve().parent / "fixtures" / "dom_stub.js"

_NEEDS_NODE = pytest.mark.skipif(
    shutil.which("node") is None, reason="node not installed"
)

_MARKS = ["correct", "wrong", "dimmed"]

_SCRIPT = """
require({stub});
QZ.serveI18n({i18n});
QZ.load({i18njs});

QZ.els([
    'hotseat-panel', 'hotseat-title', 'hotseat-hint', 'hotseat-bank',
    'hotseat-bid-stage', 'hotseat-bet-stage', 'hotseat-bid-btn',
    'hotseat-bid-count', 'hotseat-slider', 'hotseat-value',
    'hotseat-bet-slider', 'hotseat-bet-value', 'hotseat-result-stage',
    'question-text', 'question-category', 'answer-buttons',
    'answers-container', 'estimate-container', 'submitted-confirmation'
]);
QZ.load({utils_js});
QZ.load({render_shared});
QZ.load({player_utils});
QZ.load({player_game});
QZ.load({hotseat});

var HS = window.QuizifyPlayerHotSeat;
window.QuizifyPlayerUtils.state.playerName = 'Anna';

// The real grid: three .answer-btn buttons, each with an .answer-text child,
// which is what renderQuestion and renderSeatAnswers both reuse.
var grid = document.getElementById('answer-buttons');
var buttons = [];
for (var i = 0; i < 3; i++) {{
    var btn = QZ.el('answer-btn-' + i);
    btn.tagName = 'BUTTON';
    btn.className = 'answer-btn';
    var text = QZ.el('answer-text-' + i);
    text.tagName = 'SPAN';
    text.className = 'answer-text';
    btn.appendChild(text);
    grid.appendChild(btn);
    buttons.push(btn);
}}

// What player-reveal.js leaves on the grid at the end of a round.
function paintReveal() {{
    buttons[0].classList.add('correct');
    buttons[1].classList.add('wrong');
    buttons[2].classList.add('dimmed');
    document.getElementById('answers-container').classList.remove('hidden');
}}
function marks() {{
    return buttons.map(function (b) {{
        return {marks}.filter(function (c) {{ return b.classList.contains(c); }});
    }});
}}

var SEATED = {{
    question: 'Why do your fingertips wrinkle after a long bath?',
    you_are_seated: true,
    answers: ['Osmosis', 'A nerve reflex', 'Skin swelling']
}};

(async function () {{
    await window.QuizifyI18n.init('en');
    var out = {{}};

    // Live: the auction opens straight over the reveal.
    paintReveal();
    HS.handleAuctionYou({{ score: 100 }});
    out.afterAuction = marks();
    HS.handleQuestion(JSON.parse(JSON.stringify(SEATED)));
    out.seatedLive = marks();

    // Reload into the chair's question: restoreFromSnapshot never runs the
    // auction, so renderSeatAnswers is the only thing between the marks and
    // the seat holder.
    paintReveal();
    HS.handleQuestion(JSON.parse(JSON.stringify(SEATED)));
    out.seatedRestore = marks();
    out.seatedGridHidden = document.getElementById('answers-container')
        .classList.contains('hidden');

    // A spectator: no answers to give, so no grid — least of all this one.
    paintReveal();
    HS.handleQuestion({{
        question: SEATED.question, you_are_seated: false, winner: 'Bea', score: 40
    }});
    out.spectatorGridHidden = document.getElementById('answers-container')
        .classList.contains('hidden');

    console.log(JSON.stringify(out));
}})().catch(function (e) {{ console.error(e); process.exit(1); }});
"""


def _run() -> dict:
    script = _SCRIPT.format(
        stub=json.dumps(str(_STUB)),
        i18n=json.dumps(str(_I18N)),
        i18njs=json.dumps(str(_JS / "i18n.js")),
        utils_js=json.dumps(str(_JS / "utils.js")),
        render_shared=json.dumps(str(_JS / "render-shared.js")),
        player_utils=json.dumps(str(_JS / "player-utils.js")),
        player_game=json.dumps(str(_JS / "player-game.js")),
        hotseat=json.dumps(str(_JS / "player-hotseat.js")),
        marks=json.dumps(_MARKS),
    )
    out = subprocess.run(
        ["node", "-e", script], capture_output=True, text=True, check=True
    )
    return json.loads(out.stdout.strip().splitlines()[-1])


@_NEEDS_NODE
def test_the_premise_the_reveal_marks_are_on_the_grid() -> None:
    """Guards the test itself: if painting did not stick, every assertion
    below would pass on a grid that never had a mark."""
    source = (_JS / "player-reveal.js").read_text("utf-8")
    for mark in _MARKS:
        assert f"btn.classList.add('{mark}')" in source


@_NEEDS_NODE
def test_the_auction_clears_the_last_round_s_marks() -> None:
    result = _run()

    assert result["afterAuction"] == [[], [], []], (
        "the auction opened over the reveal and left ★/× on the grid the "
        "seat holder is about to answer on"
    )


@_NEEDS_NODE
def test_the_seat_holder_answers_on_a_clean_grid() -> None:
    """The reported symptom, run rather than read."""
    result = _run()

    assert result["seatedLive"] == [[], [], []]


@_NEEDS_NODE
def test_a_reload_into_the_chair_s_question_is_clean_too() -> None:
    """The restore path skips the auction; renderSeatAnswers has to strip the
    marks itself."""
    result = _run()

    assert result["seatedRestore"] == [[], [], []]
    assert result["seatedGridHidden"] is False


@_NEEDS_NODE
def test_a_spectator_is_not_left_looking_at_the_old_grid() -> None:
    result = _run()

    assert result["spectatorGridHidden"] is True, (
        "a phone staking on the chair still shows the previous round's "
        "answer grid, reveal marks and all"
    )
