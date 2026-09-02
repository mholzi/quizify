"""Four small UI fixes from the 2026-09-02 review (#667).

Each is a couple of lines, and each is the kind of thing that survives
because nothing asserts it. These tests do assert it.

1. The custom-settings questionnaire numbered two different steps "7" — the
   Hot Seat step (#616) was inserted without renumbering what followed.
2. The TV lobby rendered a guest's name chip at 12px and "Scan to join the
   game" at 14px, while the couch-legibility pass (#376) had moved
   comparable text to a clamp. Those two are the first things a guest looks
   up at after scanning.
3. Newer features shipped German literals as the markup fallback on pages
   that declare ``lang="en"``, so an English or Spanish party saw German
   until the i18n bundle finished loading.
4. "Wagerunde" is not a German word.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

_WWW = (
    Path(__file__).resolve().parent.parent
    / "custom_components" / "quizify" / "www"
)


def _read(name: str) -> str:
    return (_WWW / name).read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# 1. One number per step
# ---------------------------------------------------------------------------


def test_the_custom_settings_steps_are_numbered_once_each() -> None:
    nums = re.findall(r'<span class="num">(\d+)</span>', _read("admin.html"))
    assert nums, "the questionnaire lost its numbers entirely"
    assert len(nums) == len(set(nums)), f"duplicate step number in {nums}"
    assert nums == sorted(nums, key=int), f"steps out of order: {nums}"


# ---------------------------------------------------------------------------
# 2. The television is read from a sofa
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "selector",
    [".dashboard-player-chip", ".dashboard-waiting-text"],
)
def test_tv_lobby_text_is_sized_for_the_room(selector: str) -> None:
    """No fixed pixel font on the two strings a guest looks up to check.

    A clamp is what #376 settled on for this screen. The point is not the
    exact number, it is that these two stopped being phone-sized.
    """
    src = _read("dashboard.html")
    # Anchor on the rule, not on the first mention: the selector also appears
    # inside a comment further up, and a test that reads a comment is a test
    # that stops testing.
    start = src.index(selector + " {")
    block = src[start:src.index("}", start)]
    fixed = re.search(r"font-size:\s*(\d+)px", block)
    assert fixed is None, (
        f"{selector} is back to a fixed {fixed.group(1)}px on a screen "
        "nobody sits close to"
    )
    assert "clamp(" in block, f"{selector} has no responsive font-size"


def test_the_name_chip_is_not_shouted() -> None:
    """Capitals cost legibility at distance and buy nothing on a name."""
    src = _read("dashboard.html")
    start = src.index(".dashboard-player-chip {")
    block = src[start:src.index("}", start)]
    assert "text-transform: uppercase" not in block


# ---------------------------------------------------------------------------
# 3. The markup fallback matches the page's own lang
# ---------------------------------------------------------------------------


_GERMAN_GIVEAWAYS = (
    "Der Stuhl",
    "Wagerunde",
    "Runde",
    "Spieler",
    "Punkte",
    "Frage",
)


@pytest.mark.parametrize("page", ["player.html", "admin.html", "dashboard.html"])
def test_markup_fallbacks_are_english_like_the_lang_attribute(page: str) -> None:
    """The fallback is what shows until the bundle lands, and on a slow
    instance that is a visible flash on every load."""
    src = _read(page)
    assert 'lang="en"' in src, f"{page} no longer declares English"
    for element in re.findall(r"<[^>]*data-i18n=\"[^\"]+\"[^>]*>([^<]+)<", src):
        text = element.strip()
        for word in _GERMAN_GIVEAWAYS:
            assert word not in text, (
                f"{page}: German fallback {text!r} on a lang=\"en\" page"
            )


# ---------------------------------------------------------------------------
# 4. German that is actually German
# ---------------------------------------------------------------------------


def test_the_german_bundle_has_no_english_german_hybrid() -> None:
    de = json.loads((_WWW / "i18n" / "de.json").read_text(encoding="utf-8"))

    def walk(node, path=""):
        if isinstance(node, dict):
            for k, v in node.items():
                yield from walk(v, f"{path}.{k}" if path else k)
        elif isinstance(node, str):
            yield path, node

    for path, value in walk(de):
        assert "Wagerunde" not in value, (
            f"{path}: 'Wagerunde' is a hybrid of 'wager' and 'Runde'; "
            "the German word is 'Einsatz' or 'Wette'"
        )
