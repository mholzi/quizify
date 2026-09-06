"""The tab has to stop claiming the game is still on question five (#782).

Observed live on v1.15.0-RC2, on all three phones in the same game::

    document.title -> "🟢 Quizify — Question 5"
    visible page   -> "Game Over! / 5 rounds · 3 players / WINNER Anna"

``updatePageTitle`` has known the FINALE case since it was written, and the
lobby, the question and the reveal all call it. The end screen is reached two
ways — the live ``finale`` event and a ``FINALE`` game_state — and both routed
straight into ``handleFinale``, which called it from neither. So the title kept
the last question for the rest of the evening.

That matters more on a phone than the wording suggests: the title is what a
player reads in the tab strip and in the app switcher, and it was the one place
left that still said a round was running.
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
_CORE = _JS / "player-core.js"

_NEEDS_NODE = pytest.mark.skipif(
    shutil.which("node") is None, reason="node not installed"
)


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


def test_the_end_screen_sets_the_title() -> None:
    """Read at the one place both routes to the end screen pass through."""
    source = _CORE.read_text("utf-8")
    body = _js_function(source, "function handleFinale(msg)")

    assert "updatePageTitle('FINALE', msg)" in body


def test_both_routes_to_the_end_screen_go_through_it() -> None:
    """The live event and the snapshot. Fixing one and not the other would
    leave the stale title on exactly the phones that reconnected."""
    source = re.sub(r"^\s*//.*$", "", _CORE.read_text("utf-8"), flags=re.M)

    assert "case 'finale':\n                handleFinale(msg);" in source
    assert "case 'END':\n                handleFinale(msg);" in source


# ---------------------------------------------------------------------------
# … and what it actually writes
# ---------------------------------------------------------------------------


_SCRIPT = """
require({stub});
QZ.serveI18n({i18n});
QZ.load({i18njs});

var shown = null;
var state = {{ playerName: 'Anna', currentPhase: 'QUESTION_ACTIVE', isAdmin: false }};
var game = {{ stopCountdown: function () {{}} }};
var pu = {{ showView: function (v) {{ shown = v; }} }};
var end = {{ updateEndView: function () {{}}, setupNewGameButton: function () {{}} }};
function _clearFinaleCountdown() {{}}
function setResetStage() {{}}
var _lastTitlePhase = null;
var _lastTitleMsg = null;

{update_page_title}
{handle_finale}

(async function () {{
    await window.QuizifyI18n.init('en');

    // Where the phone actually is when the last question ends.
    updatePageTitle('QUESTION_ACTIVE', {{ round: 5 }});
    var duringQuestion = document.title;

    handleFinale({{ round: 5, total_rounds: 5 }});
    var atTheEnd = document.title;

    await window.QuizifyI18n.setLanguage('de');
    handleFinale({{ round: 5, total_rounds: 5 }});
    var german = document.title;

    console.log(JSON.stringify({{
        duringQuestion: duringQuestion,
        atTheEnd: atTheEnd,
        german: german,
        view: shown
    }}));
}})();
"""


def _run() -> dict:
    source = _CORE.read_text("utf-8")
    script = _SCRIPT.format(
        stub=json.dumps(str(_STUB)),
        i18n=json.dumps(str(_I18N)),
        i18njs=json.dumps(str(_JS / "i18n.js")),
        update_page_title=_js_function(source, "function updatePageTitle(phase, msg)"),
        handle_finale=_js_function(source, "function handleFinale(msg)"),
    )
    out = subprocess.run(
        ["node", "-e", script], capture_output=True, text=True, check=True
    )
    return json.loads(out.stdout)


@_NEEDS_NODE
def test_the_title_follows_the_phase_off_the_last_question() -> None:
    result = _run()

    assert result["duringQuestion"] == "Quizify — Question 5"
    assert result["atTheEnd"] == "Quizify — Final Results"


@_NEEDS_NODE
def test_the_end_title_speaks_the_game_s_language() -> None:
    """Written the same way as every other phase, so it inherits the language
    handling rather than needing its own."""
    result = _run()

    assert result["german"] == "Quizify — Endergebnis"


@_NEEDS_NODE
def test_the_end_view_is_still_the_one_that_opens() -> None:
    """Guards the guard: a handleFinale that threw before showView would give
    a passing title and a blank screen."""
    result = _run()

    assert result["view"] == "end-view"
