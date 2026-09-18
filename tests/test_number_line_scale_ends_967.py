"""The estimate number line prints round scale ends inside the range (#967).

Found in the live test of v1.20.0-RC3: the ends of the number line printed the
raw zoomed window — "17.84 ... 34.16" for the chessboard question (answer 32,
guesses 20 / 30) and "-1.56 ... 102.04" for the piano question (answer 88,
guesses 10 / 40, range 0..200). The clamp to the question's range let 2% of
the span through, which is where the negative number came from. On the phone
the label of the lowest guess also sat on the scale-end line and overprinted
the left number.

The window is computed once in ``render-shared.js`` and both the phone and
the television use it. The phone test runs the real ``player-reveal.js``
against the DOM stub.
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
_I18N = _WWW / "i18n"
_STUB = Path(__file__).resolve().parent / "fixtures" / "dom_stub.js"
_PLAYER_CSS = _WWW / "css" / "src" / "07-player.css"

_NEEDS_NODE = pytest.mark.skipif(
    shutil.which("node") is None, reason="node not installed"
)

# (answer, guesses, min, max, step) as the live test saw them, plus edge cases.
_CASES = {
    "chessboard": (32, [20, 30], 0, 100, 1),
    "piano": (88, [10, 40], 0, 200, 1),
    "dracula": (1897, [1850, 1840], 1700, 2000, 1),
    "decimal": (2.5, [1.2, 3.3], 0, 10, 0.1),
    "one_value": (5, [5], 0, 10, 1),
    "at_the_max": (100, [95, 100], 0, 100, 1),
}

_SCRIPT = """
require({stub});
QZ.serveI18n({i18n});
QZ.load({i18njs});
QZ.els(['reveal-hero', 'reveal-estimate', 'reveal-answer-strip', 'reveal-standings',
        'header-round-num', 'header-round-total', 'fun-fact-container', 'fun-fact',
        'reveal-admin-controls', 'next-round-btn', 'flag-question-btn']);
QZ.load({utils_js});
QZ.load({render_shared});
QZ.load({player_utils});
QZ.load({player_game});
QZ.load({player_team});
QZ.load({player_reveal});
window.QuizifyPlayerSound = {{
    playCorrect: function () {{}}, playWrong: function () {{}}
}};

var CASES = {cases};
var NAMES = ['Anna', 'Bert', 'Cleo'];

function phoneEnds(c) {{
    var guesses = c[1].map(function (g, i) {{
        return {{ player_name: NAMES[i], guess: g, distance: Math.abs(g - c[0]),
                  points: 10, rank: i + 1, exact: false, no_guess: false }};
    }});
    window.QuizifyPlayerUtils.state.playerName = 'Anna';
    window.QuizifyPlayerReveal.updateRevealView({{
        question_type: 'estimate', question_id: 'q', round: 1, total_rounds: 5,
        correct_answer: String(c[0]), all_answers: [],
        players: NAMES.map(function (n) {{
            return {{ name: n, score: 0, streak: 0, connected: true }};
        }}),
        leaderboard: [],
        estimate: {{ answer: c[0], answer_text: String(c[0]), min: c[2], max: c[3],
                     unit: '', step: c[4], winner: 'Anna', guesses: guesses }}
    }});
    var html = document.getElementById('reveal-estimate').innerHTML;
    var m = html.match(/nl-scale-ends"><span>([^<]*)<\\/span><span>([^<]*)<\\/span>/);
    return m ? [m[1], m[2]] : null;
}}

(async function () {{
    await window.QuizifyI18n.init('en');
    var out = {{ range: {{}}, phone: {{}} }};
    Object.keys(CASES).forEach(function (k) {{
        var c = CASES[k];
        out.range[k] = window.QuizifyRenderShared.numberLineRange(
            c[1].concat([c[0]]), c[2], c[3], c[4]);
        out.phone[k] = phoneEnds(c);
    }});
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
        cases=json.dumps(_CASES),
    )
    out = subprocess.run(
        ["node", "-e", script], capture_output=True, text=True, check=True
    )
    return json.loads(out.stdout.strip().splitlines()[-1])


@pytest.fixture(scope="module")
def result() -> dict:
    return _run()


@_NEEDS_NODE
def test_the_live_test_s_two_questions_print_round_ends(result: dict) -> None:
    assert result["phone"]["chessboard"] == ["16", "36"]
    assert result["phone"]["piano"] == ["0", "110"]


@_NEEDS_NODE
@pytest.mark.parametrize("case", sorted(_CASES))
def test_ends_stay_inside_the_declared_range(result: dict, case: str) -> None:
    answer, guesses, q_min, q_max, _step = _CASES[case]
    rng = result["range"][case]
    assert q_min <= rng["lo"] < rng["hi"] <= q_max
    # Every guess and the answer are still on the line.
    for v in [*guesses, answer]:
        assert rng["lo"] <= v <= rng["hi"]


@_NEEDS_NODE
@pytest.mark.parametrize("case", sorted(_CASES))
def test_ends_are_multiples_of_the_step(result: dict, case: str) -> None:
    _answer, _guesses, _min, _max, step = _CASES[case]
    for end in (result["range"][case]["lo"], result["range"][case]["hi"]):
        ratio = end / step
        assert abs(ratio - round(ratio)) < 1e-9, (case, end)


@_NEEDS_NODE
@pytest.mark.parametrize("case", sorted(_CASES))
def test_phone_prints_no_long_decimals(result: dict, case: str) -> None:
    ends = result["phone"][case]
    assert ends is not None
    for text in ends:
        assert not text.startswith("-")
        assert re.fullmatch(r"\d+(\.\d)?", text), (case, text)


def test_television_uses_the_shared_window() -> None:
    src = (_JS / "dashboard.js").read_text("utf-8")
    assert "numberLineRange(" in src
    assert "lo -= pad" not in src
    assert "span * 0.02" not in src


def _px(rule: str, prop: str) -> float:
    m = re.search(rf"{re.escape(prop)}\s*:\s*(\d+(?:\.\d+)?)px", rule)
    assert m, (prop, rule)
    return float(m.group(1))


def _rule(css: str, selector: str) -> str:
    m = re.search(rf"(?m)^{re.escape(selector)}\s*\{{([^}}]*)\}}", css)
    assert m, selector
    return m.group(1)


def test_phone_scale_ends_sit_below_the_lowest_label() -> None:
    """The below-label reaches ~(top offset + one 16px line) under the dot; the
    scale-end band must start after that, not on the same line."""
    css = _PLAYER_CSS.read_text("utf-8")
    below_top = _px(_rule(css, ".nl-lbl--below"), "top")
    dot = _px(_rule(css, ".nl-dot"), "height")
    axis = _px(_rule(css, ".nl-axis"), "height")
    # Tick box is centred on the axis: its top sits (dot - axis) / 2 above it.
    label_bottom_below_axis = below_top - (dot - axis) / 2 + 16 - axis
    gap = _px(_rule(css, ".nl-scale-ends"), "margin-top")
    assert gap >= label_bottom_below_axis, (gap, label_bottom_below_axis)
