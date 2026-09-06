"""#850 — the television kept the last game's DID YOU KNOW panel.

Found on real hardware during the v1.16.0-RC2 live test. A German game had
just started — round 1, a German music question — and the panel in the lower
right still held the closing fact of the English game before it, under a
``WUSSTEST DU?`` label that *had* followed the room into German because it is
a static ``data-i18n`` string. It is replaced the moment that round's own
reveal lands, so the window is one question long — the first question of the
evening, when the room is looking hardest at the screen.

``#fun-fact`` is the third panel on this board with a writer and no clearer,
after #706's answer tally and #807's evening leaders, and it fails the same
way for the same reason. ``handleRoundSummary`` fills it;
``question_started`` was the only thing that ever took the class off again,
and nothing ever emptied the text. So the fact survived the finale, the reset
and the whole next lobby — and every *other* door into the question view put
it back on screen: a ``QUESTION_ACTIVE`` snapshot with no ``question`` block,
a ``WAGER_ACTIVE`` or ``HOT_SEAT`` snapshot with no detour block, and the
``ANSWER_REVEAL`` rebuild all call ``showView('question')`` without touching
it.

The fix is #706's and #807's: clear it on every view change, so a new path
cannot forget. The reveal is unaffected — it writes the fact without a view
change, and the reconnect path re-renders it from the snapshot immediately
after the view is shown.

Measured in Chrome at 1280x720 with the socket stubbed, replaying
game_ended → game_reset → LOBBY(de) → question_started: before the fix
``#fun-fact`` carried ``visible`` and the previous game's 165px of text right
through the reset; after it, the panel is empty and 68px from the finale
onwards.
"""

from __future__ import annotations

import re
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
_DASHBOARD = _REPO_ROOT / "custom_components" / "quizify" / "www" / "dashboard.html"

SOURCE = _DASHBOARD.read_text(encoding="utf-8")


def _fn_body(name: str) -> str:
    """The body of a top-level (8-space indented) function in dashboard.html."""
    marker = f"function {name}("
    assert marker in SOURCE, f"{name}() is gone"
    return SOURCE.split(marker, 1)[1].split("\n        }", 1)[0]


def test_every_view_change_takes_the_fun_fact_down() -> None:
    body = _fn_body("showView")
    assert "els.funFact" in body, (
        "showView never touches #fun-fact — the last game's fact will hang in "
        "the right column through the finale, the reset and the next lobby"
    )


def test_the_text_is_emptied_and_not_merely_hidden() -> None:
    """Removing the class alone leaves the previous game's sentence in the
    DOM, still sized into the 549.8px right column and one missed
    ``question_started`` away from being read out to the room."""
    body = _fn_body("showView")
    block = re.search(r"if \(els\.funFact\) \{(.*?)\n            \}", body, re.S)
    assert block, "the fun-fact clear is not a block of showView's own"
    assert "classList.remove('visible')" in block.group(1)
    assert re.search(
        r"els\.funFactText\.textContent = ''", block.group(1)
    ), "the panel keeps its text and its height"


def test_the_clear_is_not_scoped_to_one_view() -> None:
    """#706 and #807 could name the one view that keeps their line. This one
    cannot: the fact belongs to a *reveal*, not to a view, and the reveal
    arrives without a view change at all."""
    body = _fn_body("showView")
    block = re.search(r"(if \([^)]*els\.funFact[^)]*\) \{)", body)
    assert block, "showView no longer guards the fun-fact clear"
    assert "name !==" not in block.group(1), (
        "gating on a view name lets the reveal-reconnect and hot-seat paths "
        "back into the question view with a stale fact"
    )


def test_the_reveal_still_writes_the_fact_without_a_view_change() -> None:
    """The clear is only safe because handleRoundSummary shows no view."""
    body = _fn_body("handleRoundSummary")
    assert "showView(" not in body, (
        "handleRoundSummary now changes view, so showView would wipe the fact "
        "it is about to write"
    )
    assert "els.funFactText.textContent" in body


def test_a_television_reconnecting_at_the_reveal_gets_its_fact_back() -> None:
    """``ANSWER_REVEAL`` calls showView('question') — which now clears — and
    must re-render the fact from the snapshot afterwards."""
    branch = SOURCE.split("case 'ANSWER_REVEAL':", 1)[1].split("case '", 1)[0]
    assert "showView('question')" in branch
    assert "renderRevealFromSnapshot" in branch
    assert "rs.fun_fact" in _fn_body("renderRevealFromSnapshot")


def test_all_three_header_and_panel_lines_are_cleared_the_same_way() -> None:
    """#706 fixed the answer progress, #807 the line below it, and #850 the
    panel in the other column. None may drift back to a single writer."""
    body = _fn_body("showView")
    for element in ("els.answerProgress", "els.eveningTally", "els.funFact"):
        assert element in body, f"{element} lost its clearer in showView"
