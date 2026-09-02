"""#666 — the reveal must not dim the distribution chart along with the tile.

``dashboard.html`` is a self-contained page with an inline ``<style>`` block,
so these are text-level guards (same shape as ``test_ux_dashboard_w3a.py``).

The regression being locked out: ``.dashboard-answer.wrong { opacity: .32 }``.
CSS opacity multiplies down the subtree, so a rule on the tile took the
answer-distribution bar and its percentage (#151) with it — the number the room
is arguing about, at a third of its contrast, on a screen watched from the
couch. The dimming has to sit on the *text* children instead.

The selector lookups anchor on ``selector + " {"`` on purpose: the file
mentions these class names in comments too, and an ``index(selector)`` would
happily return a comment.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
DASHBOARD = REPO / "custom_components" / "quizify" / "www" / "dashboard.html"


def _text() -> str:
    return DASHBOARD.read_text(encoding="utf-8")


def _css() -> str:
    """The inline ``<style>`` blocks with CSS comments removed.

    Both matter. Comments carry commas and braces of their own, so a selector
    list read straight off the file arrives with half a sentence glued to it.
    """
    blocks = re.findall(r"<style[^>]*>(.*?)</style>", _text(), re.DOTALL)
    assert blocks, "dashboard.html is expected to carry its CSS inline"
    return re.sub(r"/\*.*?\*/", "", "\n".join(blocks), flags=re.DOTALL)


def _rule(css: str, selector: str) -> str:
    """Declaration block of the first rule whose selector list ends in ``selector``."""
    idx = css.index(selector + " {")
    start = css.index("{", idx)
    end = css.index("}", start)
    return css[start + 1 : end]


def _rules_for(css: str, selector: str) -> list[str]:
    """Every declaration block whose selector list contains ``selector``."""
    out = []
    for match in re.finditer(r"([^{}]+)\{([^{}]*)\}", css):
        selectors = [s.strip() for s in match.group(1).split(",")]
        if selector in selectors:
            out.append(match.group(2))
    return out


def test_wrong_tile_itself_is_not_dimmed() -> None:
    """No opacity on the tile — that is what reached the bar and the percent."""
    for block in _rules_for(_css(), ".dashboard-answer.wrong"):
        assert "opacity" not in block, block


def test_wrong_answer_text_is_dimmed_instead() -> None:
    css = _css()
    blocks = _rules_for(css, ".dashboard-answer.wrong .answer-text")
    assert blocks, "wrong answers must still recede — dim the text"
    assert any("opacity" in block for block in blocks)


def test_wrong_answer_label_recedes_with_its_text() -> None:
    """The A/B/C/D letter is accent-coloured; left bright it would out-shout
    the answer it labels."""
    blocks = _rules_for(_css(), ".dashboard-answer.wrong .answer-label")
    assert any("opacity" in block for block in blocks)


def test_distribution_children_keep_full_contrast() -> None:
    """The bar, its fill and the percentage carry no opacity of their own, so
    with the tile rule gone they render at full strength on wrong answers."""
    css = _css()
    for selector in (
        ".dashboard-answer-bar",
        ".dashboard-answer-bar-fill",
        ".dashboard-answer-percent",
    ):
        assert "opacity" not in _rule(css, selector), selector


def _balanced(css: str, header: str) -> str:
    """Body of an at-rule, read with a brace counter (it nests)."""
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


def test_the_chart_ends_at_full_opacity_once_it_is_shown() -> None:
    """#665 replaced the opacity fade with a keyframe animation, because a
    transition cannot run across the `display` change it introduced. What this
    issue cares about is unchanged: once the chart is on screen, nothing holds
    it below full contrast."""
    css = _css()
    frames = _balanced(css, "@keyframes dashboard-distribution-in")
    assert re.search(r"to\s*\{[^}]*opacity:\s*1", frames), frames
    # And the tile it sits in must not scale that back down again.
    for block in _rules_for(css, ".dashboard-answer.wrong"):
        assert "opacity" not in block
