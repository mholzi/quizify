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
    # A page may define its own tokens inline, so both sources count as a
    # definition. (The launcher takes its tokens from css/tokens.css, which
    # is the same generated :root block styles.css starts with.)
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


# ---------------------------------------------------------------------------
# #1178: the same check for the CSS modules and the scripts' style strings.
#
# The page test above never saw the stylesheets themselves. The design-system
# review behind #1178 found 16 `var()` references in css/src/ to tokens that
# do not exist — mostly DESIGN.md spellings (`--hairline`, `--fs-sm`,
# `--color-text`, `--font-sans`) — four of them without a fallback, so the
# declaration was dropped and the element inherited its colour by accident.
# Here a reference *with* a fallback counts too: the fallback hides the typo,
# it does not make the token exist.
# ---------------------------------------------------------------------------

_SRC = _WWW / "css/src"
_JS_DIR = _WWW / "js"
#: Generated copies of modules that are checked themselves.
_JS_GENERATED = {"player.bundle.js", "common.bundle.js"}
# ``var(--x`` with or without a fallback.
_ANY_USE = re.compile(r"var\(\s*(--[\w-]+)")
_SET_PROPERTY = re.compile(r"setProperty\(\s*['\"](--[\w-]+)")

#: Known undefined references that cannot be repointed without a visible
#: change. Each entry is (file, token). This list may only shrink.
#:
#: ``player-reveal.js`` paints an estimate dot / swatch with
#: ``g.color || 'var(--sky)'``. ``--sky`` does not exist, so in the (rare) case
#: of a player without a colour the inline background is invalid and the dot
#: is transparent. Pointing it at ``--color-accent-blue`` would make it sky
#: blue, which is the intent but a visible change; it belongs to the
#: player-colour step of #1178, not to the no-visual-change step that added
#: this check.
_KNOWN_UNDEFINED = {("js/player-reveal.js", "--sky")}


def _module_files() -> list[Path]:
    files = sorted(_SRC.glob("*.css"))
    files += sorted(p for p in _JS_DIR.glob("*.js") if p.name not in _JS_GENERATED)
    return files


def _all_definitions() -> set[str]:
    defined: set[str] = set()
    for path in _module_files() + _pages():
        text = path.read_text(encoding="utf-8")
        defined |= set(_DEF.findall(text))
        defined |= set(_SET_PROPERTY.findall(text))
    return defined


def test_there_are_modules_to_check() -> None:
    names = {p.name for p in _module_files()}
    assert {"00-tokens.css", "07-player.css", "admin.js"} <= names, names


@pytest.mark.parametrize("module", _module_files(), ids=lambda p: p.name)
def test_every_token_a_module_uses_is_defined(module: Path) -> None:
    rel = module.relative_to(_WWW).as_posix()
    defined = _all_definitions()
    used = set(_ANY_USE.findall(module.read_text(encoding="utf-8")))
    missing = sorted(
        t for t in used if t not in defined and (rel, t) not in _KNOWN_UNDEFINED
    )
    assert not missing, (
        f"{rel} uses undefined tokens: {missing}. Without a fallback the "
        "declaration is dropped; with one, the fallback hides a name that "
        "exists nowhere. Use the token from css/src/00-tokens.css "
        "(docs/DESIGN.md lists them)."
    )


def test_known_undefined_references_are_still_there() -> None:
    """An allow-list entry that no longer matches must be removed."""
    defined = _all_definitions()
    for rel, token in sorted(_KNOWN_UNDEFINED):
        text = (_WWW / rel).read_text(encoding="utf-8")
        assert token in _ANY_USE.findall(text) and token not in defined, (
            f"({rel}, {token}) is fixed — drop it from _KNOWN_UNDEFINED"
        )
