"""The Hot Seat auction is not played over the last round's question (#1033).

Live test of v1.21.0-RC4: while the bidding window ran, every phone still
showed the previous round's category and question, its answered row and the
whole answer grid (power-up button included) under the bid card. On the
winner's phone the grid survived the award as well, so a quick tap in the
seconds before the chair's question arrived answered a question that was over.

#802 took the picture away and #940 the reveal marks; this is the rest of the
same leftover. These tests run the real ``player-game.js`` and
``player-hotseat.js`` against the DOM stub.
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
    'answers-container', 'estimate-container', 'submitted-confirmation',
    'submission-tracker'
]);
QZ.load({utils_js});
QZ.load({render_shared});
QZ.load({player_utils});
QZ.load({player_game});
QZ.load({hotseat});

var HS = window.QuizifyPlayerHotSeat;
window.QuizifyPlayerUtils.state.playerName = 'Anna';

var grid = document.getElementById('answer-buttons');
for (var i = 0; i < 3; i++) {{
    var btn = QZ.el('answer-btn-' + i);
    btn.tagName = 'BUTTON';
    btn.className = 'answer-btn';
    var text = QZ.el('answer-text-' + i);
    text.tagName = 'SPAN';
    text.className = 'answer-text';
    btn.appendChild(text);
    grid.appendChild(btn);
}}

// What round 3's reveal leaves on the game view.
function paintLastRound() {{
    document.getElementById('question-category').textContent = 'Geography';
    document.getElementById('question-text').textContent =
        'The Philippines was named in honour of whom?';
    ['answers-container', 'submission-tracker'].forEach(function (id) {{
        document.getElementById(id).classList.remove('hidden');
    }});
}}

function view() {{
    function hidden(id) {{
        return document.getElementById(id).classList.contains('hidden');
    }}
    return {{
        category: document.getElementById('question-category').textContent,
        question: document.getElementById('question-text').textContent,
        gridHidden: hidden('answers-container'),
        trackerHidden: hidden('submission-tracker'),
        panelHidden: hidden('hotseat-panel')
    }};
}}

(async function () {{
    await window.QuizifyI18n.init('en');
    var out = {{}};

    paintLastRound();
    HS.handleAuctionYou({{ score: 48 }});
    out.auction = view();

    HS.handleAwarded({{ winner: 'Anna', pct: 50, stake: 24, bids: [] }});
    out.awarded = view();

    HS.handleQuestion({{
        question: 'Which city is Times Square in?', you_are_seated: true,
        answers: ['New York', 'Chicago', 'Boston']
    }});
    out.seated = view();

    // A reload between award and question never runs the auction.
    paintLastRound();
    HS.restoreFromSnapshot({{
        stage: 'awarded', winner: 'Anna', pct: 50, stake: 24, bids: []
    }}, {{}});
    out.restoredAwarded = view();

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
    )
    out = subprocess.run(
        ["node", "-e", script], capture_output=True, text=True, check=True
    )
    return json.loads(out.stdout.strip().splitlines()[-1])


@pytest.fixture(scope="module")
def result() -> dict:
    return _run()


@_NEEDS_NODE
def test_the_auction_hides_the_last_round_s_question(result: dict) -> None:
    """The reported screen: the bid card over round 3's question and grid."""
    auction = result["auction"]
    assert auction["panelHidden"] is False
    assert auction["category"] == ""
    assert auction["question"] == ""
    assert auction["gridHidden"] is True, "old answer grid under the bid card"
    assert auction["trackerHidden"] is True, "old answered row still showing"


@_NEEDS_NODE
def test_the_winner_has_no_old_grid_to_tap_after_the_award(result: dict) -> None:
    assert result["awarded"]["gridHidden"] is True


@_NEEDS_NODE
def test_the_chair_s_question_brings_the_grid_back(result: dict) -> None:
    """Guards the fix from overshooting: the seat holder answers on the grid."""
    seated = result["seated"]
    assert seated["gridHidden"] is False
    assert seated["question"] == "Which city is Times Square in?"


@_NEEDS_NODE
def test_a_reload_onto_the_award_is_clean_too(result: dict) -> None:
    restored = result["restoredAwarded"]
    assert restored["gridHidden"] is True
    assert restored["question"] == ""
