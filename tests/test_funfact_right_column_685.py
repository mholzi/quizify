"""#685 — the fun fact has to live where there is room for it.

Last in the left column it never fit a 720p television: measured with the
reveal showing and `.visible` set, its top edge landed at 726.5px of a 720px
viewport. A 720p room had never seen one. #680 stopped it from holding 83.6px
of an overflowing column, which protected the answers but left it invisible.

The right column stands empty beside the question and has the height. The
leaderboard above it already scrolls (`flex: 1; overflow-y: auto`), so it is
the thing that yields. Measured after the move at 1280x720: the fact spans
565.4px to 681.6px — fully on screen — and the leaderboard gives up 132px and
scrolls. The answer grid does not move (bottom stays 674.9px).
"""

from __future__ import annotations

import re
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
DASHBOARD = REPO / "custom_components" / "quizify" / "www" / "dashboard.html"


def _html() -> str:
    return DASHBOARD.read_text(encoding="utf-8")


def _css() -> str:
    blocks = re.findall(r"<style[^>]*>(.*?)</style>", _html(), re.DOTALL)
    assert blocks, "dashboard.html is expected to carry its CSS inline"
    return re.sub(r"/\*.*?\*/", "", "\n".join(blocks), flags=re.DOTALL)


def _rule(css: str, selector: str) -> str:
    blocks = [
        m.group(2)
        for m in re.finditer(r"([^{}]+)\{([^{}]*)\}", css)
        if selector in [s.strip() for s in m.group(1).split(",")]
    ]
    assert blocks, f"{selector}: no rule found"
    return blocks[0]


def _question_view() -> str:
    """The question view's markup, bounded by the next view's banner comment."""
    html = _html()
    start = html.index("<!-- QUESTION VIEW -->")
    return html[start : html.index("<!-- FINALE VIEW -->", start)]


def test_the_fun_fact_sits_in_the_right_column() -> None:
    """Ordering is the assertion: it has to come after the leaderboard opens
    and before the right column closes."""
    view = _question_view()
    right = view.index('class="dashboard-right"')
    fact = view.index('id="fun-fact"')
    body_end = view.index('id="dashboard-estimate"')
    assert fact > right, "the fun fact is back under the answers"
    assert fact > body_end


def test_the_fun_fact_follows_the_leaderboard() -> None:
    view = _question_view()
    assert view.index('id="leaderboard"') < view.index('id="fun-fact"')


def test_the_leaderboard_yields_the_space_not_the_fact() -> None:
    """Both share a flex column. Without this the layout would shrink the fact
    — the wrong one, since the list already knows how to scroll."""
    assert "flex-shrink: 0" in _rule(_css(), ".dashboard-funfact")
    assert "overflow-y: auto" in _rule(_css(), ".dashboard-leaderboard")


def test_short_screens_show_it_again() -> None:
    """#680 hid it at this height because under the answers it was unreachable
    anyway. Out of that column there is no reason left to hide it."""
    css = _css()
    start = css.index("@media (max-height: 850px)")
    start = css.index("{", start)
    depth, j = 0, start
    while True:
        if css[j] == "{":
            depth += 1
        elif css[j] == "}":
            depth -= 1
            if depth == 0:
                break
        j += 1
    short = css[start + 1 : j]
    assert "display: none" not in _rule(short, "#question-view .dashboard-funfact")
