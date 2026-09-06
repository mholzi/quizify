"""The non-playing host gets controls at the reveal (issue #618).

The lobby offers starting a game without joining as a player
(``doStartGameNoJoin``); that host stays on ``/quizify/admin``. But
``handleQuestionStarted`` hides ``#next-question-btn`` and ``#end-game-btn`` at
question start, and before this fix **nothing anywhere un-hid them** — the old
admin reveal view was removed in v1.1.16 on the premise that "the host always
redirects to /quizify/player on game start", which stopped being true when
start-without-joining arrived.

So that host sat in front of a stale question with no controls at every reveal.
With nobody holding the crown, the guests' only way forward was the 60-second
"host gone" reset hatch — which wipes the running game. A quiz night ended in a
reset instead of a winner.

These follow the project's existing frontend-test pattern (read the shipped
source, assert on structure) because the suite has no JS runtime. They are
therefore structural, not behavioural: they pin that the un-hiding happens in
the reveal handler and that the string it renders exists in every language
bundle. A browser check of the actual screen is still worth doing before the
release — noted in the PR rather than implied away.
"""

from __future__ import annotations

import json
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
_WWW = _REPO_ROOT / "custom_components" / "quizify" / "www"
_ADMIN_JS = _WWW / "js" / "admin.js"


def _function_body(source: str, signature: str) -> str:
    """The text of one top-level function, brace-matched.

    Slicing to the next ``\\n    }`` would stop at the first nested block, so
    this counts braces — the assertions below are only meaningful if they see
    the whole function and nothing of its neighbours.
    """
    start = source.index(signature)
    depth = 0
    for i in range(start, len(source)):
        if source[i] == "{":
            depth += 1
        elif source[i] == "}":
            depth -= 1
            if depth == 0:
                return source[start : i + 1]
    raise AssertionError(f"unbalanced braces after {signature!r}")


def test_question_start_still_hides_the_controls() -> None:
    """Guards the premise. Without this the rest proves nothing."""
    body = _function_body(
        _ADMIN_JS.read_text("utf-8"), "function handleQuestionStarted("
    )

    assert "nextQuestionBtn.classList.add('hidden')" in body
    assert "endGameBtn.classList.add('hidden')" in body


def test_the_reveal_brings_both_controls_back() -> None:
    """The defect itself: hidden at question start, never shown again."""
    body = _function_body(_ADMIN_JS.read_text("utf-8"), "function handleRoundSummary(")

    assert "nextQuestionBtn.classList.remove('hidden')" in body
    assert "endGameBtn.classList.remove('hidden')" in body


def test_the_last_round_still_offers_the_forward_button() -> None:
    """This used to hide Next Question on the last round (#806), leaving the
    admin-tab host only the red End Game. See
    ``tests/test_last_round_host_controls_806.py`` for the full argument; kept
    here so the #618 file cannot quietly grow the old gate back."""
    body = _function_body(_ADMIN_JS.read_text("utf-8"), "function handleRoundSummary(")

    hide_lines = [
        line
        for line in body.splitlines()
        if "nextQuestionBtn" in line and "add('hidden')" in line
    ]
    assert not hide_lines, "Next Question must never be hidden at the reveal"
    end_lines = [line for line in body.splitlines() if "endGameBtn" in line]
    assert not any("last_round" in line for line in end_lines), (
        "End Game must stay available on the last round"
    )


def test_the_reveal_shows_the_correct_answer() -> None:
    """The host is the one person who may see it — that is the whole point of
    the admin tab at the reveal."""
    body = _function_body(_ADMIN_JS.read_text("utf-8"), "function handleRoundSummary(")

    assert "admin.correctLabel" in body
    assert "msg.correct_answer" in body


def test_every_language_bundle_carries_the_label() -> None:
    """A missing key renders the raw key on screen, in a place only the host
    ever sees — exactly where nobody would notice it for months."""
    for code in ("de", "en", "es"):
        bundle = json.loads((_WWW / "i18n" / f"{code}.json").read_text("utf-8"))
        label = bundle["admin"]["correctLabel"]
        assert "{answer}" in label, f"{code}: correctLabel must interpolate the answer"
