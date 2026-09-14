"""The phone's result hero agrees with the estimate reveal under it (#951).

An estimate round ships ``all_answers: []`` — the guesses travel in
``data.estimate.guesses`` — so ``updateRevealView`` handed the hero the plain
``players`` row. That row never carries ``answer_index``, and the hero's
"missed" test was exactly ``answer_index == null``: every estimate reveal
opened with "Time's up · 0 Points", directly above the card saying "You win
the round +75".

Team mode adds a second trap: a team's guess is carried into the ranking by
one member (#602), so the other members' guess rows read ``no_guess`` although
their team scored.

These tests run the real ``player-reveal.js`` (and the real team module)
against the DOM stub and read the hero back.
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
var cues = [];
window.QuizifyPlayerSound = {{
    playCorrect: function () {{ cues.push('correct'); }},
    playWrong: function () {{ cues.push('wrong'); }}
}};

// Round 7 of the live test: Anna guessed 1850 for Sofa, Cleo 1850 for Couch —
// but the reveal ranks one guess per entrant, and here Anna's is the winner.
function reveal(guesses) {{
    return {{
        question_type: 'estimate',
        question_id: 'dracula',
        round: 7, total_rounds: 10,
        correct_answer: '1897 year',
        all_answers: [],
        players: [
            {{ name: 'Anna', score: 125, streak: 0, connected: true }},
            {{ name: 'Bert', score: 0, streak: 0, connected: true }},
            {{ name: 'Cleo', score: 50, streak: 0, connected: true }}
        ],
        leaderboard: [],
        estimate: {{
            answer: 1897, answer_text: '1897 year', min: 1700, max: 2000,
            unit: 'year', step: 1, winner: 'Anna', guesses: guesses
        }}
    }};
}}

var ANNA_WINS = [
    {{ player_name: 'Anna', guess: 1850, distance: 47, points: 75, rank: 1, exact: false, no_guess: false }},
    {{ player_name: 'Bert', guess: null, distance: null, points: 0, rank: null, exact: false, no_guess: true }},
    {{ player_name: 'Cleo', guess: 1840, distance: 57, points: 50, rank: 2, exact: false, no_guess: false }}
];

// The stub's classList is not tied to className, so the reset the module does
// with ``hero.className = 'pl-result-hero'`` has to be mirrored here — or one
// scenario's state class would leak into the next.
var STATES = ['pl-result-hero--missed', 'pl-result-hero--wrong',
              'pl-result-hero--streak', 'pl-result-hero--big'];
function hero() {{
    var el = document.getElementById('reveal-hero');
    return {{ cls: el.classList.list().join(' '), text: el.textContent }};
}}

function as(name, guesses) {{
    var el = document.getElementById('reveal-hero');
    STATES.forEach(function (c) {{ el.classList.remove(c); }});
    pu.state.playerName = name;
    cues = [];
    window.QuizifyPlayerReveal.updateRevealView(reveal(guesses));
    var h = hero();
    h.cues = cues.slice();
    return h;
}}

(async function () {{
    await window.QuizifyI18n.init('en');
    var out = {{}};

    // Solo: the guesser who won the round.
    out.annaSolo = as('Anna', ANNA_WINS);
    // Solo: a guesser who did not win still scored.
    out.cleoSolo = as('Cleo', ANNA_WINS);
    // Solo: no guess at all — the one case that IS "Time's up".
    out.bertSolo = as('Bert', ANNA_WINS);

    // Team mode: Bert is on Sofa with Anna, who carried the team's guess.
    pu.state.playerName = 'Bert';
    window.QuizifyPlayerTeam.handleTeamsUpdate({{ teams: [
        {{ team_id: 't-sofa', name: 'Sofa', members: ['Anna', 'Bert'] }},
        {{ team_id: 't-couch', name: 'Couch', members: ['Cleo'] }}
    ] }});
    out.bertTeam = as('Bert', ANNA_WINS);

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
def result() -> dict:
    return _run()


def _time_up() -> str:
    """The "Time's up" headline as the hero writes it: HTML-escaped, which the
    stub's textContent does not decode."""
    text = json.loads((_I18N / "en.json").read_text("utf-8"))["reveal"]["timeUp"]
    return text.replace("'", "&#39;")


@_NEEDS_NODE
def test_the_round_winner_is_not_told_time_is_up(result: dict) -> None:
    """The reported screen: +75 below, "Time's up · 0" above."""
    anna = result["annaSolo"]
    assert "pl-result-hero--missed" not in anna["cls"], anna
    assert _time_up() not in anna["text"]
    assert "pl-result-hero--big" in anna["cls"]
    assert "+75" in anna["text"]
    assert "Base score" not in anna["text"], "estimate scoring has no base score"


@_NEEDS_NODE
def test_a_guess_that_did_not_win_still_shows_its_points(result: dict) -> None:
    cleo = result["cleoSolo"]
    assert "pl-result-hero--missed" not in cleo["cls"], cleo
    assert "+50" in cleo["text"]


@_NEEDS_NODE
def test_no_guess_still_reads_time_s_up(result: dict) -> None:
    """Guards the fix from overshooting: the missed state has to survive."""
    bert = result["bertSolo"]
    assert "pl-result-hero--missed" in bert["cls"], bert
    assert _time_up() in bert["text"]
    assert bert["cues"] == [], "a missed round plays no cue"


@_NEEDS_NODE
def test_the_teammate_who_did_not_carry_the_guess_sees_the_team_result(
    result: dict,
) -> None:
    """#602 settles a team's guess onto one member; the others' rows say
    no_guess although their team won the round."""
    bert = result["bertTeam"]
    assert "pl-result-hero--missed" not in bert["cls"], bert
    assert "+75" in bert["text"]


@_NEEDS_NODE
def test_a_scored_guess_plays_the_positive_cue(result: dict) -> None:
    assert result["annaSolo"]["cues"] == ["correct"]
