"""#680 — the question view must fit a 720p television.

`.dashboard-left` centres its children with nowhere to put the excess. At
1280x720 the question view needed 823.3px of a 549.8px column, and plain
`center` splits an overflow across *both* ends — so the top of it climbed into
the fixed header and the category label sat 106px behind the timer bar.

Two halves to the fix, and they are not interchangeable:

* `safe center` stops the upward climb. It is a floor, not a solution: it only
  decides which end the overflow comes out of.
* The short-screen block makes the content actually fit, so there is no
  overflow to place. Measured after: 536.3px of a 549.8px column at reveal,
  461.6px in the question phase, overlap 0 at both 1280x720 and 1920x1080.

These are text-level guards; `dashboard.html` keeps its CSS inline.
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


def _balanced(css: str, header: str) -> str:
    start = css.index(header)
    start = css.index("{", start)
    depth, j = 0, start
    while True:
        if css[j] == "{":
            depth += 1
        elif css[j] == "}":
            depth -= 1
            if depth == 0:
                return css[start + 1 : j]
        j += 1


def _rule(css: str, selector: str) -> str:
    blocks = [
        m.group(2)
        for m in re.finditer(r"([^{}]+)\{([^{}]*)\}", css)
        if selector in [s.strip() for s in m.group(1).split(",")]
    ]
    assert blocks, f"{selector}: no rule found"
    return blocks[0]


def test_the_column_cannot_centre_content_into_the_header() -> None:
    assert "justify-content: safe center" in _rule(_css(), ".dashboard-left")


def test_the_safe_keyword_keeps_a_plain_center_beneath_it() -> None:
    """A renderer that does not know `safe` drops the whole declaration, and
    the initial value is `flex-start`, not `center`. Without the fallback an
    old browser would top-align every question rather than behave as it does
    today."""
    column = _rule(_css(), ".dashboard-left")
    plain = column.index("justify-content: center")
    safe = column.index("justify-content: safe center")
    assert plain < safe, "the fallback has to come first to be overridden"


def test_short_screens_get_two_answer_columns() -> None:
    """Three columns are why the grid was 383.5px tall: a ~221px tile cannot
    hold an answer and its chart side by side, so both wrap. Two columns give
    ~341px and the row collapses back."""
    short = _balanced(_css(), "@media (max-height: 850px)")
    grid = _rule(short, "#question-view .dashboard-answers")
    assert "grid-template-columns: 1fr 1fr" in grid


def test_short_screens_spend_spacing_not_type() -> None:
    """#376 sized this type for a room. The height comes out of margins,
    padding and gaps — if a font-size ever appears in here, that trade was
    made silently."""
    short = _balanced(_css(), "@media (max-height: 850px)")
    question_view = "".join(
        m.group(2)
        for m in re.finditer(r"([^{}]+)\{([^{}]*)\}", short)
        if "#question-view" in m.group(1)
    )
    assert "font-size" not in question_view, question_view


def test_the_unreachable_fun_fact_stops_holding_the_column() -> None:
    """At 1280x720 the fun fact's top edge measured 726.5px of a 720px
    viewport — below the fold before this change and after it. All it did there
    was push the last answer row towards the same fold."""
    short = _balanced(_css(), "@media (max-height: 850px)")
    assert "display: none" in _rule(short, "#question-view .dashboard-funfact")
