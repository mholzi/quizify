"""#1007 — the television kept the last question's "2/2" through the wager.

Found in the v1.21.0-RC1 live test on real hardware at 1920x1080. Question 9
ended with both players answered, so the header tally read ``2/2`` in its
``is-complete`` colour. Question 10 opened the final-round betting window and
the headline read "Placing bets · Science — 0 of 2 have bet" — under a tally
that still said everyone was done.

``#answer-progress`` sits in the header, outside every view. #706 made
``showView`` clear it on every change *away from* the question view, and
``question_started`` clears it before a new question. The wager goes through
neither: ``handleWagerProgress`` renders into the question view itself, so
``showView('question')`` deliberately leaves the tally alone, and the question
has not been sent yet.

The fix clears the existing tally in ``handleWagerProgress`` — no new element;
the headline already carries the betting count. The tests run the page's own
``showView``, ``handleAnswerProgress`` and ``handleWagerProgress`` under node
(``tests/fixtures/dom_stub.js``), so they assert what the room reads off the
header rather than the shape of the source.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parent.parent
_JS = _REPO / "custom_components" / "quizify" / "www" / "js"
_DASHBOARD = _JS / "dashboard.js"
_STUB = Path(__file__).resolve().parent / "fixtures" / "dom_stub.js"

_NEEDS_NODE = pytest.mark.skipif(
    shutil.which("node") is None, reason="node not installed"
)

_FUNCTIONS = (
    "function showView(name)",
    "function handleAnswerProgress(msg)",
    "function handleWagerProgress(msg)",
)


def _without_comments(source: str) -> str:
    source = re.sub(r"/\*.*?\*/", "", source, flags=re.S)
    return re.sub(r"^\s*//.*$", "", source, flags=re.M)


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


def _block(source: str, opener: str) -> str:
    block = source[source.index(opener) :]
    return block[: block.index("};") + 2]


def _run(frames: list[dict]) -> dict:
    """Feed frames through the real handlers; report the header tally."""
    source = _without_comments(_DASHBOARD.read_text(encoding="utf-8"))
    views_block = _block(source, "var views = {")
    els_block = _block(source, "var els = {")
    ids = sorted(
        set(re.findall(r"getElementById\('([^']+)'\)", views_block + els_block))
    )
    bodies = "\n".join(_js_function(source, sig) for sig in _FUNCTIONS)

    script = f"""
'use strict';
require({json.dumps(str(_STUB))});
QZ.els({json.dumps(ids)});

function renderQuestionImage() {{}}
var currentPhase = 'UNSET';
var timerDuration = 0;
var timerRemaining = 0;
window.QuizifyI18n = {{
    t: function (key, vars) {{ return key + '|' + JSON.stringify(vars || {{}}); }}
}};

{views_block}
{els_block}
{bodies}

var frames = {json.dumps(frames)};
frames.forEach(function (f) {{
    if (f.type === 'answer_progress') handleAnswerProgress(f);
    else if (f.type === 'wager_progress') handleWagerProgress(f);
    else if (f.type === 'view') showView(f.name);
}});

var tally = els.answerProgress;
console.log(JSON.stringify({{
    text: tally.textContent,
    hidden: tally.classList.contains('hidden'),
    complete: tally.classList.contains('is-complete'),
    headline: els.questionText.textContent,
    phase: currentPhase
}}));
"""
    out = subprocess.run(
        ["node", "-e", script], capture_output=True, text=True, check=True
    )
    return json.loads(out.stdout)


_Q9_DONE = {"type": "answer_progress", "submitted": 2, "total": 2, "players": []}
_WAGER_OPENS = {
    "type": "wager_progress",
    "round_num": 10,
    "total_rounds": 10,
    "category": "Science",
    "locked_in": 0,
    "player_count": 2,
    "window_duration": 20,
}


@_NEEDS_NODE
def test_the_premise_the_question_view_keeps_its_tally() -> None:
    """Question 9 ends with ``2/2`` on the board — the state the wager inherits."""
    result = _run([{"type": "view", "name": "question"}, _Q9_DONE])
    assert result["text"] == "2/2"
    assert not result["hidden"]
    assert result["complete"]


@_NEEDS_NODE
def test_the_wager_window_takes_the_previous_tally_down() -> None:
    """The bug: "0 of 2 have bet" under a header that says "2/2"."""
    result = _run([{"type": "view", "name": "question"}, _Q9_DONE, _WAGER_OPENS])
    assert result["phase"] == "WAGER_ACTIVE"
    assert result["headline"].startswith("wager.hostWindowTitle")
    assert result["hidden"], "the wager window still shows question 9's answer tally"
    assert result["text"] == "", "the stale '2/2' is still in the header"
    assert not result["complete"], "the tally keeps its 'everyone answered' colour"


@_NEEDS_NODE
def test_a_bet_arriving_mid_window_keeps_it_down() -> None:
    """Every ``wager_progress`` re-renders the frame; none may bring it back."""
    bet = dict(_WAGER_OPENS, locked_in=1)
    del bet["window_duration"]
    result = _run([_Q9_DONE, _WAGER_OPENS, bet])
    assert result["hidden"]
    assert result["text"] == ""
