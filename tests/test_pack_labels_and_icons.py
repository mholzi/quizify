"""Packs must be named and marked the way the picker names and marks them.

Two small things a full browser pass turned up, both cosmetic and both the
kind that only shows up when you look at the screen:

* the shareable result card printed the pack **slug** (``picture-round-en``),
  while every other surface shows the display name ("Picture Round"). The card
  exists to be pasted into a group chat, so it reads like a filename there.
* the four packs that ship ``theme: trivia`` (the picture rounds from #537 and
  the estimation packs from #275) had no icon of their own, so they fell back
  to the ``mixed`` glyph — the same mark "Gemischt" uses, which means every
  pack at once, and the same 🎲 an *unknown* theme gets.

The icon test is written against the packs actually shipped rather than a
hard-coded theme list: a new pack with a new theme should fail here, not paint
a fallback glyph in the picker.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
QUESTIONS = REPO / "custom_components" / "quizify" / "questions"
ICONS_JS = REPO / "custom_components" / "quizify" / "www" / "js" / "icons.js"
VIEWS_PY = REPO / "custom_components" / "quizify" / "server" / "views.py"
WS_PY = REPO / "custom_components" / "quizify" / "server" / "websocket.py"


def _shipped_themes() -> set[str]:
    themes = set()
    for path in QUESTIONS.glob("*.json"):
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            theme = data.get("theme")
            if isinstance(theme, str) and theme:
                themes.add(theme)
    return themes


def _js_map_keys(name: str) -> set[str]:
    """Keys of an object literal in icons.js.

    Matches ``key: '…'`` anywhere, not just at the start of a line — the tint
    map packs several entries per line, and a line-anchored pattern silently
    saw only the first of each, which read as "nine themes have no tint".
    """
    src = ICONS_JS.read_text(encoding="utf-8")
    match = re.search(rf"{name}\s*=\s*\{{(.*?)\n\s*\}};", src, re.S)
    assert match, f"could not find {name} in icons.js"
    return set(re.findall(r"([a-z]+)\s*:\s*'", match.group(1)))


def test_every_shipped_theme_has_a_line_icon() -> None:
    """No shipped pack may fall through to the ``mixed`` glyph."""
    missing = sorted(_shipped_themes() - _js_map_keys("CATEGORY_ICON_SVG"))
    assert not missing, (
        f"themes {missing} ship in a pack but have no icon in icons.js, so the "
        "picker draws the 'mixed' glyph — the mark that means *all* packs."
    )


def test_every_shipped_theme_has_a_tint() -> None:
    """A themed icon without a tint gets the neutral mixed disc."""
    missing = sorted(_shipped_themes() - _js_map_keys("CATEGORY_TINT"))
    assert not missing, f"themes {missing} have an icon but no tint in icons.js"


def test_every_shipped_theme_has_a_server_emoji() -> None:
    """The server-rendered chips must not fall back to the unknown-theme 🎲."""
    src = VIEWS_PY.read_text(encoding="utf-8")
    match = re.search(r"_THEME_ICONS\s*=\s*\{(.*?)\n\}", src, re.S)
    assert match, "could not find _THEME_ICONS in views.py"
    keys = set(re.findall(r'"([a-z]+)"\s*:', match.group(1)))
    missing = sorted(_shipped_themes() - keys)
    assert not missing, (
        f"themes {missing} have no entry in _THEME_ICONS, so a shipped pack "
        "is drawn with the same 🎲 an unknown/broken theme gets."
    )


def test_share_card_gets_display_names_not_slugs() -> None:
    """The finale maps pack slugs through the bank before sending them.

    Asserted on the source because building a finale payload needs a live
    game state; what matters is that the mapping happens at all, and that it
    keeps the slug when a pack is unknown rather than dropping the line.
    """
    src = WS_PY.read_text(encoding="utf-8")
    assert "get_pack_versions()" in src, (
        "the finale no longer resolves pack display names — the share card "
        "will print slugs like 'picture-round-en' again."
    )
    assert 'or slug for slug in packs' in src, (
        "the slug fallback is gone; an unknown pack would vanish from the "
        "card's Packs line instead of appearing under its slug."
    )
