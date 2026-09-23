"""The reveal breakdown named the Double power-up and the difficulty a streak (#1005).

``ScoringEngine`` made the breakdown add up (#308) by booking everything past
base and speed as ``streak_bonus = points - BASE_POINTS - speed_bonus``. The
difficulty multiplier and the Double power-up both live in that remainder, so
the live test of v1.21.0-RC1 read:

* Round 1, first correct answer on Medium: "1-streak bonus! +9 · Difficulty
  1.5x" — the +9 was mostly the 1.5x.
* Round 3 with Double: "1-streak bonus! +31" — the doubling appeared nowhere,
  and the player who spent the power-up could not see that it worked.

The engine now books each factor as the points it added (``difficulty_bonus``,
``streak_bonus``, ``double_bonus``), the parts still sum to the score, and the
phone labels them with the words it already has (``reveal.difficulty``,
``reveal.streakBonus``, ``powerups.double_points``).
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO))

from custom_components.quizify.game.scoring_engine import (  # noqa: E402
    ScoreComputation,
    ScoringEngine,
)
from custom_components.quizify.game.types import Difficulty  # noqa: E402

_WWW = _REPO / "custom_components" / "quizify" / "www"
_JS = _WWW / "js"
_I18N = _WWW / "i18n"
_STUB = Path(__file__).resolve().parent / "fixtures" / "dom_stub.js"

_NEEDS_NODE = pytest.mark.skipif(
    shutil.which("node") is None, reason="node not installed"
)


def _score(*, streak: int, double: bool, elapsed: float = 16.0) -> ScoreComputation:
    return ScoringEngine().score_submission(
        correct=True,
        elapsed=elapsed,
        round_duration=30.0,
        difficulty=Difficulty.MEDIUM,
        streak=streak,
        double_points_active=double,
        is_final_round=False,
        wager=None,
        score_before_wager=0,
    )


# ---------------------------------------------------------------------------
# The engine: each factor under its own name
# ---------------------------------------------------------------------------


def test_the_difficulty_uplift_is_not_a_streak_bonus() -> None:
    """First correct answer on Medium, 16 s of 30: 10 + 2.33 speed, x1.5, x1.1."""
    c = _score(streak=1, double=False)
    assert c.points == 20
    assert c.speed_bonus == 2
    assert c.difficulty_bonus == 6  # int(18.5) - int(12.33)
    assert c.streak_bonus == 2  # int(20.35) - int(18.5): the 10 % a 1-streak pays
    assert c.double_bonus == 0


def test_the_double_power_up_shows_up_as_itself() -> None:
    plain = _score(streak=1, double=False)
    doubled = _score(streak=1, double=True)
    assert doubled.points == 40
    assert doubled.double_bonus == doubled.points - plain.points
    # The streak did not grow because a power-up was spent.
    assert doubled.streak_bonus == plain.streak_bonus
    assert doubled.difficulty_bonus == plain.difficulty_bonus


def test_no_streak_pays_no_streak_bonus() -> None:
    assert _score(streak=0, double=True).streak_bonus == 0


def test_the_parts_travel_in_the_breakdown() -> None:
    bd = _score(streak=1, double=True).breakdown
    assert bd["difficulty_bonus"] == 6
    assert bd["double_bonus"] == 20


def test_a_wager_clears_every_part() -> None:
    c = ScoringEngine().score_submission(
        correct=True,
        elapsed=5.0,
        round_duration=30.0,
        difficulty=Difficulty.HARD,
        streak=2,
        double_points_active=True,
        is_final_round=True,
        wager=50,
        score_before_wager=100,
    )
    assert c.wager_used == 50
    assert (c.speed_bonus, c.difficulty_bonus, c.streak_bonus, c.double_bonus) == (
        0,
        0,
        0,
        0,
    )


# ---------------------------------------------------------------------------
# The phone: the words it shows
# ---------------------------------------------------------------------------

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
pu.state.playerName = 'Anna';

function breakdown(row) {{
    window.QuizifyPlayerReveal.updateRevealView({{
        question_id: 'q1', round: 1, total_rounds: 10,
        correct_answer: 'Paris',
        players: [{{
            name: 'Anna', score: row.points_earned,
            streak: row.streak, connected: true
        }}],
        leaderboard: [],
        all_answers: [row]
    }});
    var el = document.getElementById('reveal-hero');
    var m = /<div class="pl-result-breakdown">([\\s\\S]*?)<\\/div>/.exec(el.innerHTML);
    return m ? m[1].replace(/<[^>]*>/g, '') : '';
}}

(async function () {{
    await window.QuizifyI18n.init('en');
    var rows = {rows};
    var out = {{}};
    Object.keys(rows).forEach(function (k) {{ out[k] = breakdown(rows[k]); }});
    console.log(JSON.stringify(out));
}})().catch(function (e) {{ console.error(e); process.exit(1); }});
"""


def _row(c: ScoreComputation, streak: int) -> dict:
    """The all_answers row the server sends for this player (round_message_builder)."""
    bd = c.breakdown
    return {
        "player_name": "Anna",
        "answer_index": 0,
        "answer_text": "Paris",
        "correct": True,
        "correct_button_index": 0,
        "points_earned": c.points,
        "speed_bonus": bd["speed_bonus"],
        "streak_bonus": bd["streak_bonus"],
        "difficulty_multiplier": bd["difficulty_multiplier"],
        "difficulty_bonus": bd["difficulty_bonus"],
        "double_points": bd["double_points"],
        "double_bonus": bd["double_bonus"],
        "streak": streak,
    }


@pytest.fixture(scope="module")
def shown() -> dict:
    rows = {
        "first": _row(_score(streak=1, double=False), 1),
        "doubled": _row(_score(streak=1, double=True), 1),
        "no_streak": _row(_score(streak=0, double=False), 0),
    }
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
        rows=json.dumps(rows),
    )
    out = subprocess.run(
        ["node", "-e", script], capture_output=True, text=True, check=True
    )
    return json.loads(out.stdout.strip().splitlines()[-1])


def _en(section: str, key: str) -> str:
    return json.loads((_I18N / "en.json").read_text("utf-8"))[section][key]


@_NEEDS_NODE
def test_the_phone_names_the_difficulty_points(shown: dict) -> None:
    line = shown["first"]
    assert f"{_en('reveal', 'difficulty')} +6" in line
    assert "1-streak bonus! +2" in line
    assert "+9" not in line and "+8" not in line


@_NEEDS_NODE
def test_the_phone_shows_the_double_it_paid_for(shown: dict) -> None:
    line = shown["doubled"]
    assert f"{_en('powerups', 'double_points')} +20" in line
    # The doubling is not dressed up as a streak any more.
    assert "1-streak bonus! +2" in line


@_NEEDS_NODE
def test_no_streak_no_streak_line(shown: dict) -> None:
    assert "streak" not in shown["no_streak"].lower()
