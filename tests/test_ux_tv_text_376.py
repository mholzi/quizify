"""Guard the #376 TV-legibility fix for the dashboard reveal/standings text.

The dashboard is a self-contained ``www/dashboard.html`` with an inline
``<style>`` block (it does NOT load styles.css). Several reveal/standings
elements used to be pinned at small, non-scaling sizes (~1rem / 13-14px /
0.85rem) while the question already scaled with ``clamp(..., vw, ...)`` — so
they were unreadable from a couch 2-3m away. This test locks in that those
selectors now use a ``clamp()``/``vw`` fluid font-size so a later edit can't
silently regress them back to a bare small px/rem.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
DASHBOARD = REPO / "custom_components" / "quizify" / "www" / "dashboard.html"


def _rule(css: str, selector: str) -> str:
    """Declaration block of the first rule whose selector list *starts* with
    ``selector``.

    The anchor matters. A plain ``css.index(selector)`` matches the selector
    anywhere, including as the tail of a more specific one: #680 added
    ``#question-view .dashboard-funfact { display: none }`` inside a media
    query that sits earlier in the file, and this helper happily returned that
    block instead of the base rule — reporting that ``.dashboard-funfact`` had
    lost the font-size it still has. The base rule is the one that carries the
    #376 sizes, so the selector list has to match exactly — not start with
    the name (that finds ``.dashboard-funfact.visible``) and not merely
    contain it (that finds the media-query override). Comments go first for
    the same reason: they carry no braces, so a selector list read raw arrives
    with the paragraph above it attached.
    """
    css = re.sub(r"/\*.*?\*/", "", css, flags=re.DOTALL)
    for match in re.finditer(r"(?:^|[{};])\s*([^{};]*?)\s*\{", css, re.MULTILINE):
        if match.group(1).strip() == selector.rstrip(" {").strip():
            start = css.index("{", match.end() - 1)
            return css[start + 1 : css.index("}", start)]
    raise AssertionError(f"no rule found for {selector}")


# Selectors that must scale for TV couch legibility (#376).
SCALED_SELECTORS = (
    ".dashboard-funfact {",
    ".dashboard-leaderboard .leaderboard-row {",
    ".dashboard-leaderboard .leaderboard-score {",
    ".award-detail {",
    ".podium-score {",
    ".podium-other-name {",
)


def test_reveal_standings_text_scales_with_clamp() -> None:
    html = DASHBOARD.read_text("utf-8")
    for selector in SCALED_SELECTORS:
        block = _rule(html, selector)
        assert "font-size:" in block, f"{selector} lost its font-size (#376)"
        assert "clamp(" in block and "vw" in block, (
            f"{selector} must use a clamp()/vw fluid font-size for TV legibility (#376), "
            f"not a bare small px/rem"
        )


def test_reveal_standings_font_floors_are_legible() -> None:
    """The clamp() lower bound must be a >=15px pixel floor (readable on TV)."""
    import re

    html = DASHBOARD.read_text("utf-8")
    floor_re = re.compile(r"font-size:\s*clamp\(\s*([0-9.]+)px")
    for selector in SCALED_SELECTORS:
        block = _rule(html, selector)
        m = floor_re.search(block)
        assert m is not None, f"{selector} clamp() floor must be an explicit px value (#376)"
        assert float(m.group(1)) >= 15.0, (
            f"{selector} clamp() floor is below 15px — too small for a TV (#376)"
        )
