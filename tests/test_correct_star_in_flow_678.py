"""#678 — the correct tile's star must not be painted over anything.

It used to be pinned with `position: absolute; right: 16px`, which put it on
top of the distribution percentage: measured 3.0px of overlap at 1920x1080,
5.4px at 1280x720 and 10.5px at 1024x640. The overlap grew as the screen
shrank, because the star's offset is a fixed 16px while the tile's padding is
a clamp — two numbers moving at different rates towards each other.

In flow it cannot collide with anything at any width. `order` places it right
after the answer text and before the chart, so it reads as "Konrad Adenauer ★"
and stays on the text's line when the chart wraps to its own (#665) instead of
being stranded on a third line.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
DASHBOARD = REPO / "custom_components" / "quizify" / "www" / "dashboard.html"


def _css() -> str:
    html = DASHBOARD.read_text(encoding="utf-8")
    blocks = re.findall(r"<style[^>]*>(.*?)</style>", html, re.DOTALL)
    assert blocks, "dashboard.html is expected to carry its CSS inline"
    return re.sub(r"/\*.*?\*/", "", "\n".join(blocks), flags=re.DOTALL)


def _rule(css: str, selector: str) -> str:
    blocks = [
        m.group(2)
        for m in re.finditer(r"([^{}]+)\{([^{}]*)\}", css)
        if selector in [s.strip() for s in m.group(1).split(",")]
    ]
    assert len(blocks) == 1, f"{selector}: expected one rule, found {len(blocks)}"
    return blocks[0]


def test_the_star_is_still_there() -> None:
    """#521 made the reveal readable without relying on colour. The star is the
    only non-colour marker on the correct tile, so it may move but not go."""
    assert '"★"' in _rule(_css(), ".dashboard-answer.correct::after")


def test_the_star_is_in_flow_not_pinned_to_the_corner() -> None:
    star = _rule(_css(), ".dashboard-answer.correct::after")
    for pinned in ("position:", "right:", "top:", "transform:"):
        assert pinned not in star, f"{pinned} puts the star back over the chart"


def test_the_tile_dropped_the_anchor_the_star_needed() -> None:
    """`position: relative` on the tile existed only to anchor that star. Left
    behind it reads as a constraint someone still depends on."""
    assert "position: relative" not in _rule(_css(), ".dashboard-answer.correct")


def test_the_star_comes_before_the_chart() -> None:
    """Ordering is the difference between the star sharing the answer's line
    and being stranded alone on a third one once the chart wraps."""
    css = _css()
    star = re.search(r"order:\s*(\d+)", _rule(css, ".dashboard-answer.correct::after"))
    chart = re.search(
        r"order:\s*(\d+)",
        _rule(css, ".dashboard-answer.revealed .dashboard-answer-distribution"),
    )
    assert star and chart, "both need an explicit order or the source order wins"
    assert int(star.group(1)) < int(chart.group(1))
