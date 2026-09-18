"""A tied estimate names every winner, not the first one (#966).

Found in the live test of v1.20.0-RC3: two teams guessed the same number,
both were marked with a star and both got +75, but every phone read
"Sofa wins the round" — Couch's phones included. ``renderEstimateReveal``
took the first rank-1 row as the winner and ignored the rest.

The tests run the real ``player-reveal.js`` against the DOM stub.
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
    'reveal-hero', 'reveal-estimate', 'reveal-answer-strip', 'reveal-standings',
    'header-round-num', 'header-round-total', 'fun-fact-container', 'fun-fact',
    'reveal-admin-controls', 'next-round-btn', 'flag-question-btn'
]);
QZ.load({utils_js});
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

// The live-test round: both teams guessed 100 on an answer of 280.
var SAME_GUESS = [
    {{ player_name: 'Sofa', team_id: 't-sofa', members: ['Anna', 'Bert'], guess: 100,
       distance: 180, points: 75, rank: 1, exact: false, no_guess: false }},
    {{ player_name: 'Couch', team_id: 't-couch', members: ['Cleo', 'Dora'], guess: 100,
       distance: 180, points: 75, rank: 1, exact: false, no_guess: false }}
];
// Same distance from opposite sides of the answer.
var OPPOSITE_SIDES = [
    {{ player_name: 'Sofa', team_id: 't-sofa', members: ['Anna', 'Bert'], guess: 100,
       distance: 180, points: 75, rank: 1, exact: false, no_guess: false }},
    {{ player_name: 'Couch', team_id: 't-couch', members: ['Cleo', 'Dora'], guess: 460,
       distance: 180, points: 75, rank: 1, exact: false, no_guess: false }}
];
var TEAM_ROWS = SAME_GUESS;

function reveal() {{
    return {{
        question_type: 'estimate', question_id: 'pregnancy', round: 2, total_rounds: 10,
        correct_answer: '280 days', all_answers: [],
        players: [
            {{ name: 'Anna', score: 0, streak: 0, connected: true }},
            {{ name: 'Bert', score: 0, streak: 0, connected: true }},
            {{ name: 'Cleo', score: 0, streak: 0, connected: true }},
            {{ name: 'Dora', score: 0, streak: 0, connected: true }}
        ],
        leaderboard: [],
        estimate: {{ answer: 280, answer_text: '280 days', min: 0, max: 600,
                     unit: 'days', step: 1, winner: 'Sofa', guesses: TEAM_ROWS }}
    }};
}}

function as(name) {{
    pu.state.playerName = name;
    window.QuizifyPlayerReveal.updateRevealView(reveal());
    return {{
        hero: document.getElementById('reveal-hero').textContent,
        estimate: document.getElementById('reveal-estimate').innerHTML
    }};
}}

(async function () {{
    await window.QuizifyI18n.init('en');
    window.QuizifyPlayerTeam.handleTeamsUpdate({{ teams: [
        {{ team_id: 't-sofa', name: 'Sofa', members: ['Anna', 'Bert'] }},
        {{ team_id: 't-couch', name: 'Couch', members: ['Cleo', 'Dora'] }}
    ] }});
    var out = {{}};
    out.bert = as('Bert');
    out.dora = as('Dora');
    TEAM_ROWS = OPPOSITE_SIDES;
    out.opposite = as('Dora');
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
        player_team=json.dumps(str(_JS / "player-team.js")),
        player_reveal=json.dumps(str(_JS / "player-reveal.js")),
    )
    out = subprocess.run(
        ["node", "-e", script], capture_output=True, text=True, check=True
    )
    return json.loads(out.stdout.strip().splitlines()[-1])


@pytest.fixture(scope="module")
def phones() -> dict:
    return _run()


def _en(*path: str) -> str:
    node = json.loads((_I18N / "en.json").read_text("utf-8"))
    for key in path:
        node = node[key]
    return node


def _shared() -> str:
    return _en("estimate", "sharedWin").replace("{names}", "Sofa &amp; Couch")


@_NEEDS_NODE
def test_the_losing_side_of_a_tie_is_not_told_the_other_team_won(
    phones: dict,
) -> None:
    dora = phones["dora"]["estimate"]
    assert _en("estimate", "playerWon").replace("{name}", "Sofa") not in dora
    assert _shared() in dora


@_NEEDS_NODE
def test_both_teams_read_the_same_shared_banner(phones: dict) -> None:
    for name in ("bert", "dora"):
        estimate = phones[name]["estimate"]
        assert _shared() in estimate
        assert _en("estimate", "youWon") not in estimate
        assert "+75" in estimate


@_NEEDS_NODE
def test_a_shared_guess_keeps_its_sub_line(phones: dict) -> None:
    assert "est-winner-sub" in phones["dora"]["estimate"]


@_NEEDS_NODE
def test_tied_guesses_on_opposite_sides_do_not_claim_one_guess(
    phones: dict,
) -> None:
    """100 and 460 are both 180 off 280; "Guess 100" would be wrong for Couch."""
    opposite = phones["opposite"]["estimate"]
    assert _shared() in opposite
    assert "est-winner-sub" not in opposite


@_NEEDS_NODE
def test_every_language_carries_the_shared_banner() -> None:
    for lang in ("de", "en", "es"):
        node = json.loads((_I18N / f"{lang}.json").read_text("utf-8"))
        assert "{names}" in node["estimate"]["sharedWin"]
