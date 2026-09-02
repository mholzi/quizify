"""#681 — the distribution bar must keep a floor of its own.

#665 lowered the chart container's reservation to `clamp(80px, 30%, 200px)`
without checking it against `.dashboard-answer-percent`, whose own floor
resolves to 72px at 1920x1080. Minus a 12.8px gap that leaves the bar nothing:
measured 1.4px on `main`, next to a full-size percentage.

The container's `min-width` was quietly doing two jobs — reserving the
side-by-side space and guaranteeing a readable bar. Only the first was
reconsidered. So the bar states its own floor now, and the container's
min-content follows it up.
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


def _clamp_bounds(declaration: str, prop: str) -> tuple[int, int]:
    """The first and last px values of ``prop: clamp(min, …, max)``."""
    match = re.search(rf"{prop}:\s*clamp\(\s*(\d+)px[^)]*?(\d+)px\s*\)", declaration)
    assert match, f"{prop} is expected to be a clamp: {declaration}"
    return int(match.group(1)), int(match.group(2))


def test_the_bar_has_a_floor_of_its_own() -> None:
    low, _ = _clamp_bounds(_rule(_css(), ".dashboard-answer-bar"), "min-width")
    assert low >= 40, "a bar narrower than this is a sliver at TV distance"


def test_the_bar_floor_survives_the_percentage_beside_it() -> None:
    """The regression in one assertion: whatever the chart reserves, the
    percentage takes its share first. If the bar had no floor of its own, the
    two floors could pass each other again without anything failing."""
    css = _css()
    bar_low, bar_high = _clamp_bounds(_rule(css, ".dashboard-answer-bar"), "min-width")
    pct_low, pct_high = _clamp_bounds(
        _rule(css, ".dashboard-answer-percent"), "min-width"
    )
    chart_low, _ = _clamp_bounds(
        _rule(css, ".dashboard-answer-distribution"), "min-width"
    )
    # The chart's own floor is no longer what guarantees the bar — the bar's is.
    assert bar_low + pct_low > chart_low, (
        "the container floor alone would decide the bar's width again"
    )
    # And the widest case still leaves the bar the larger half.
    assert bar_high >= pct_high - 10
