"""A lost final-round wager shows on the phone's reveal (#1031).

Live test of v1.21.0-RC4: a player with 39 points bet 25 % (9 points) and
answered wrong. The standings on the phone said 30, the hero above them said
"0 points" — the wrong (Z) and time-up (W) states printed a literal ``0``.

Two halves:

* the server: an unanswered row sent ``points_earned: 0`` although the
  timeout had already booked the lost stake into ``round_score`` (#653);
* the phone: both states now print the round's points, signed.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from custom_components.quizify.game.state import QuizifyGameState
from custom_components.quizify.server.round_message_builder import (
    RoundMessageBuilder,
)

_REPO = Path(__file__).resolve().parent.parent
_WWW = _REPO / "custom_components" / "quizify" / "www"
_JS = _WWW / "js"
_I18N = _WWW / "i18n"
_STUB = Path(__file__).resolve().parent / "fixtures" / "dom_stub.js"

_NEEDS_NODE = pytest.mark.skipif(
    shutil.which("node") is None, reason="node not installed"
)


# ----------------------------------------------------------------------
# Server: the reveal row carries the round's points, not a literal 0
# ----------------------------------------------------------------------


class _Runtime:
    def __init__(self, tmp_path: Path) -> None:
        self.data_dir = tmp_path


@pytest.fixture
def state(tmp_path: Path) -> QuizifyGameState:
    gs = QuizifyGameState(runtime=_Runtime(tmp_path), entry_id="test")
    gs.add_player("Alice")
    gs.add_player("Bob")
    gs.start_game(language="de", num_rounds=2, difficulty="easy")
    gs.start_next_question()
    gs.evaluate_round()
    gs.start_next_question()
    gs.arm_round_timers()
    assert gs.round == gs.total_rounds
    return gs


def _row(gs: QuizifyGameState, name: str) -> dict:
    msg = RoundMessageBuilder().build_round_summary(gs)
    assert msg is not None
    return next(r for r in msg["all_answers"] if r["player_name"] == name)


def _answer(gs: QuizifyGameState, name: str, *, correct: bool) -> None:
    question = gs._current_question
    idx = next(i for i, a in enumerate(question.answers) if a.correct is correct)
    gs.submit_answer(name, idx)


def test_a_timeout_row_reports_the_lost_stake(state: QuizifyGameState) -> None:
    alice = state.get_player("Alice")
    alice.score = 39
    alice.wager = 25
    _answer(state, "Bob", correct=True)
    state.evaluate_round()
    row = _row(state, "Alice")
    assert row["no_answer"] is True
    assert row["points_earned"] == -9
    assert alice.score == 30


def test_a_wrong_answer_row_reports_the_lost_stake(state: QuizifyGameState) -> None:
    alice = state.get_player("Alice")
    alice.score = 39
    alice.wager = 25
    _answer(state, "Alice", correct=False)
    _answer(state, "Bob", correct=True)
    state.evaluate_round()
    assert _row(state, "Alice")["points_earned"] == -9


def test_a_timeout_without_a_bet_still_reports_zero(state: QuizifyGameState) -> None:
    alice = state.get_player("Alice")
    alice.score = 39
    alice.wager = None
    _answer(state, "Bob", correct=True)
    state.evaluate_round()
    assert _row(state, "Alice")["points_earned"] == 0


# ----------------------------------------------------------------------
# Phone: the hero prints the signed round points
# ----------------------------------------------------------------------

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
    playCorrect: function () {{}}, playWrong: function () {{}}
}};

function reveal(row) {{
    return {{
        question_id: 'philippines', round: 5, total_rounds: 5,
        correct_answer: 'Philip II',
        correct_answer_index: 0,
        all_answers: [row],
        players: [{{ name: 'Anna', score: 30, streak: 0, connected: true }}],
        leaderboard: []
    }};
}}

var STATES = ['pl-result-hero--missed', 'pl-result-hero--wrong',
              'pl-result-hero--streak', 'pl-result-hero--big'];
function show(row) {{
    var el = document.getElementById('reveal-hero');
    STATES.forEach(function (c) {{ el.classList.remove(c); }});
    pu.state.playerName = 'Anna';
    window.QuizifyPlayerReveal.updateRevealView(reveal(row));
    return {{ cls: el.classList.list().join(' '), text: el.textContent }};
}}

(async function () {{
    await window.QuizifyI18n.init('en');
    var out = {{}};
    out.wrongLost = show({{ player_name: 'Anna', answer_index: 2,
        answer_text: 'Magellan', correct: false,
        correct_button_index: 0, points_earned: -9, streak: 0 }});
    out.missedLost = show({{ player_name: 'Anna', answer_index: null,
        answer_text: '—', correct: false,
        correct_button_index: 0, points_earned: -9, no_answer: true }});
    out.wrongPlain = show({{ player_name: 'Anna', answer_index: 2,
        answer_text: 'Magellan', correct: false,
        correct_button_index: 0, points_earned: 0, streak: 0 }});
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
def hero() -> dict:
    return _run()


@_NEEDS_NODE
def test_a_wrong_answer_shows_the_lost_wager(hero: dict) -> None:
    """The reported screen: "0 points" above standings that dropped by 9."""
    wrong = hero["wrongLost"]
    assert "pl-result-hero--wrong" in wrong["cls"], wrong
    assert "−9" in wrong["text"], wrong
    assert ">0<" not in wrong["text"]


@_NEEDS_NODE
def test_a_missed_round_shows_the_lost_wager(hero: dict) -> None:
    """#653: no answer costs the stake too, so time-up needs the same figure."""
    missed = hero["missedLost"]
    assert "pl-result-hero--missed" in missed["cls"], missed
    assert "−9" in missed["text"], missed


@_NEEDS_NODE
def test_a_plain_wrong_answer_still_shows_zero(hero: dict) -> None:
    plain = hero["wrongPlain"]
    assert "pl-result-hero--wrong" in plain["cls"], plain
    assert "Philip II0Points" in plain["text"], plain
