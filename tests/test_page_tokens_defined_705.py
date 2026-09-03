"""Every design token a page uses has to exist (#705).

`analytics.html` still referenced seven tokens from the pre-Soft-Parlor
palette. An undefined `var()` invalidates the whole declaration at
computed-value time, so each one silently fell back to `initial`: the
"Games Over Time" bars lost their gradient and rendered transparent, the cards
lost their borders, the stat values and the "← Back" link lost their colour —
and the link has no underline, so it read as plain text.

Nothing failed loudly, which is the point of this test: a token that goes away
during a palette change now breaks a test instead of a page.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

_WWW = Path(__file__).resolve().parent.parent / "custom_components/quizify/www"
_STYLES = _WWW / "css/styles.css"

# ``var(--x)`` with no fallback — those are the ones that fail silently.
_USE = re.compile(r"var\(\s*(--[\w-]+)\s*\)")
_DEF = re.compile(r"(--[\w-]+)\s*:")


def _pages() -> list[Path]:
    return sorted(_WWW.glob("*.html"))


def test_there_are_pages_to_check() -> None:
    assert _pages(), "no www/*.html found — the glob is wrong, not the pages"


@pytest.mark.parametrize("page", _pages(), ids=lambda p: p.name)
def test_every_token_a_page_uses_is_defined(page: Path) -> None:
    html = page.read_text(encoding="utf-8")
    # A page may define its own tokens inline (dashboard.html does, with its
    # --dash-* set), so both sources count as a definition.
    defined = set(_DEF.findall(_STYLES.read_text(encoding="utf-8")))
    defined |= set(_DEF.findall(html))
    missing = sorted(t for t in set(_USE.findall(html)) if t not in defined)
    assert not missing, (
        f"{page.name} uses undefined tokens: {missing}. "
        "An undefined var() invalidates the declaration — the rule is dropped, "
        "not merely mis-coloured."
    )


def test_no_broadcast_living_room_purple_is_left() -> None:
    """The old palette's purple, hard-coded past the token layer.

    `.period-btn.active` kept `rgba(108, 92, 231, 0.2)` — the only purple on
    any Soft Parlor surface, and invisible to the token check above because it
    never went through a token.
    """
    for page in _pages():
        html = page.read_text(encoding="utf-8")
        assert "108, 92, 231" not in html, f"{page.name} still paints the old purple"
