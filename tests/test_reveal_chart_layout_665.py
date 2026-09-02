"""#665 — the reveal chart must cost no layout while it is invisible.

``dashboard.html`` keeps its CSS inline, so these are text-level guards (same
shape as ``test_ux_dashboard_w3a.py`` and ``test_reveal_dim_666.py``).

What went wrong: ``.dashboard-answer-distribution`` was ``opacity: 0`` but kept
its box, and its box had ``min-width: clamp(140px, 22%, 280px)``. That number
was chosen when the answers were one full-width row; the grid has been three
columns for a long time. A 140px reservation inside a ~170px tile is more than
the tile holds, and because grid items cannot shrink below their min-content,
the whole answers grid overflowed its column — 330px past it at 1280x720, on
top of the leaderboard — while every answer wrapped to two or three lines
beside an empty right half.

Measured on the fixed page, question phase: grid width equals grid scrollWidth
at 1280x720 and 1920x1080, and a 1920x1080 answer went from 100px of text
across two lines to 250px on one.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
DASHBOARD = REPO / "custom_components" / "quizify" / "www" / "dashboard.html"


def _css() -> str:
    """The inline ``<style>`` blocks with CSS comments removed.

    The comments carry commas and braces of their own, so a selector list read
    straight off the file arrives with half a sentence glued to it.
    """
    html = DASHBOARD.read_text(encoding="utf-8")
    blocks = re.findall(r"<style[^>]*>(.*?)</style>", html, re.DOTALL)
    assert blocks, "dashboard.html is expected to carry its CSS inline"
    return re.sub(r"/\*.*?\*/", "", "\n".join(blocks), flags=re.DOTALL)


def _rules_for(css: str, selector: str) -> list[str]:
    """Every declaration block whose selector list contains ``selector``."""
    out = []
    for match in re.finditer(r"([^{}]+)\{([^{}]*)\}", css):
        if selector in [s.strip() for s in match.group(1).split(",")]:
            out.append(match.group(2))
    return out


def _one(css: str, selector: str) -> str:
    blocks = _rules_for(css, selector)
    assert len(blocks) == 1, f"{selector}: expected one rule, found {len(blocks)}"
    return blocks[0]


def test_chart_is_out_of_layout_until_revealed() -> None:
    """``display: none``, not ``opacity: 0`` — an invisible box still pushes."""
    base = _one(_css(), ".dashboard-answer-distribution")
    assert "display: none" in base
    assert "opacity" not in base


def test_chart_reappears_only_on_the_revealed_tile() -> None:
    revealed = _one(_css(), ".dashboard-answer.revealed .dashboard-answer-distribution")
    assert "display: flex" in revealed


def test_the_fade_is_an_animation_because_a_transition_cannot_cross_display() -> None:
    """A ``transition`` never runs across a ``display`` change, so the delayed
    fade-in from #151 has to be a keyframe animation or it silently stops
    happening — the chart would pop in at the same instant as the colour."""
    css = _css()
    revealed = _one(css, ".dashboard-answer.revealed .dashboard-answer-distribution")
    assert "animation:" in revealed
    assert "@keyframes dashboard-distribution-in" in css
    # `both` holds opacity 0 through the delay; without it the chart is visible
    # during the wait and the sequencing is gone.
    assert "both" in revealed


def test_reservation_floor_is_below_the_old_140px() -> None:
    base = _one(_css(), ".dashboard-answer-distribution")
    floor = re.search(r"min-width:\s*clamp\(\s*(\d+)px", base)
    assert floor, base
    assert int(floor.group(1)) < 140


def test_tile_wraps_so_the_chart_can_take_its_own_line() -> None:
    """Wrapping is load-bearing twice over: it gives the chart a second line on
    a narrow tile, and it caps the tile's min-content at its widest single item
    instead of the sum — which is what stops the grid from overflowing.

    ``.dashboard-answer`` is declared twice (the base rule and the narrow-screen
    media query), so this reads the base rule rather than asserting there is
    only one.
    """
    base = _rules_for(_css(), ".dashboard-answer")[0]
    assert "flex-wrap: wrap" in base


def test_answer_text_outgrows_the_chart_on_a_shared_line() -> None:
    """The lopsided grow factor is the mechanism, not a stray number.

    Both items grow, so on a shared line 100:1 leaves the chart at its
    ``min-width`` floor and hands the text everything else; on a wrapped line
    the chart is alone and fills it. Equal factors put a 158px bar next to a
    two-line answer at 1920x1080; no growth at all leaves a 10px stub of a bar
    on the wrapped line at 1280x720.
    """
    css = _css()
    grow = re.search(r"flex-grow:\s*(\d+)", _one(css, ".dashboard-answer .answer-text"))
    assert grow, "the answer text needs an explicit grow factor"
    revealed = _one(css, ".dashboard-answer.revealed .dashboard-answer-distribution")
    chart = re.search(r"flex:\s*(\d+)", revealed)
    assert chart, "the chart has to grow too, or a wrapped line leaves a stub"
    assert int(grow.group(1)) > int(chart.group(1)) * 10
