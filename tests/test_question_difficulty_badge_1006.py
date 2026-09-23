"""The phone's question header drew an empty purple pill (#1006).

``#game-difficulty-badge`` sits next to "Round 1 of 10" in ``player.html``
and carries ``.stat-badge--difficulty`` — a background and a border. Since the
player.html port nothing ever wrote into it: the only difficulty-badge code
lived in ``player-lobby.js`` and targeted ``#lobby-difficulty-badge``. So every
question showed a hollow capsule.

``renderQuestion`` now fills it with the question's difficulty (icon plus the
existing ``difficulties.*`` word, as a data-i18n span so a later language
change re-renders it) and hides it when there is nothing to show.
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

_NEEDS_NODE = pytest.mark.skipif(
    shutil.which("node") is None, reason="node not installed"
)

_SCRIPT = """
require({stub});
QZ.serveI18n({i18n});
QZ.load({i18njs});

QZ.els([
    'question-text', 'question-category', 'answer-buttons',
    'estimate-container', 'answers-container', 'submitted-confirmation',
    'game-difficulty-badge'
]);
QZ.load({utils_js});
QZ.load({render_shared});
QZ.load({player_utils});
QZ.load({player_game});

var game = window.QuizifyPlayerGame;
var badge = document.getElementById('game-difficulty-badge');

function snap() {{
    return {{
        text: badge.textContent,
        hidden: badge.classList.contains('hidden'),
        keys: badge.querySelectorAll('[data-i18n]').map(function (n) {{
            return n.getAttribute('data-i18n');
        }})
    }};
}}

function ask(difficulty) {{
    game.renderQuestion({{
        question_text: 'Q?', category: 'Science', answers: ['a', 'b', 'c'],
        difficulty: difficulty, question_type: 'multiple_choice'
    }});
    return snap();
}}

(async function () {{
    await window.QuizifyI18n.init('de');
    var out = {{}};
    out.medium = ask('medium');
    out.hard = ask('hard');
    out.missing = ask(undefined);
    out.unknown = ask('mixed-up');
    out.easyAgain = ask('easy');
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
    )
    out = subprocess.run(
        ["node", "-e", script], capture_output=True, text=True, check=True
    )
    return json.loads(out.stdout.strip().splitlines()[-1])


@pytest.fixture(scope="module")
def result() -> dict:
    return _run()


def _de(key: str) -> str:
    return json.loads((_I18N / "de.json").read_text("utf-8"))["difficulties"][key]


@_NEEDS_NODE
def test_the_pill_shows_the_question_s_difficulty(result: dict) -> None:
    medium = result["medium"]
    assert not medium["hidden"]
    assert _de("medium") in medium["text"]
    assert medium["keys"] == ["difficulties.medium"], (
        "the word must be a data-i18n span so a language change re-renders it"
    )


@_NEEDS_NODE
def test_the_next_question_repaints_it(result: dict) -> None:
    assert _de("hard") in result["hard"]["text"]
    assert _de("medium") not in result["hard"]["text"]
    assert _de("easy") in result["easyAgain"]["text"]
    assert not result["easyAgain"]["hidden"]


@_NEEDS_NODE
def test_nothing_to_show_hides_the_pill_instead_of_drawing_it_hollow(
    result: dict,
) -> None:
    for case in ("missing", "unknown"):
        assert result[case]["hidden"], case
        assert result[case]["text"] == "", case


def test_the_pill_starts_hidden_in_the_markup() -> None:
    """Before the first question arrives nothing has filled it yet."""
    html = (_WWW / "player.html").read_text("utf-8")
    tag = re.search(r'<span id="game-difficulty-badge"[^>]*>', html)
    assert tag is not None
    assert re.search(r'class="[^"]*\bhidden\b', tag.group(0))
