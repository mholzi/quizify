"""Accessibility mode (#372) and the invariants it silently depends on.

Two separate things ship under this issue and they have different rules:

* The **reveal glyphs** on the answer buttons are unconditional. DESIGN.md's
  Accessibility section states *"Color is never the sole signal —
  correct/incorrect always paired with glyph (★ / ×)"*; the dashboard already
  honoured it, the player's buttons did not. A regression here is invisible to
  anyone with full colour vision, which is exactly why it needs a test.

* The **comfort mode** (larger type, motion held still) is opt-in, persisted
  in ``localStorage`` and applied as ``a11y`` on ``<html>``.

The quiet dependencies pinned here, each of which fails silently rather than
loudly if it drifts:

1. The storage key exists **twice** — in the no-flash inline script in
   ``player.html``'s ``<head>`` and in ``js/player-a11y.js``. A one-sided
   rename would not throw; the page would simply paint at normal size and then
   jump on every load. So the two are compared to each other.

2. ``09-a11y.css`` must be **last** in ``build_css.py``'s cascade. It
   redefines the ``--font-size-*`` tokens that ``00-tokens.css`` sets, and
   ``.a11y`` and ``:root`` have equal specificity on the same element — only
   source order decides. Move the module earlier and the type scale stops
   working while every other rule in the file keeps working.

3. The player reveal applies ``.correct`` / ``.wrong`` (see
   ``player-reveal.js``), *not* the ``.is-correct`` / ``.is-wrong`` pair that
   also exists in ``07-player.css``. Styling the wrong vocabulary would look
   right in a CSS grep and do nothing on screen.

Pure text parsing — no Home Assistant imports, so this runs everywhere.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
_WWW = _REPO / "custom_components" / "quizify" / "www"
_PLAYER_HTML = _WWW / "player.html"
_CSS_SRC = _WWW / "css" / "src"
_A11Y_CSS = _CSS_SRC / "09-a11y.css"
_QUESTION_CSS = _CSS_SRC / "03-question.css"
_A11Y_JS = _WWW / "js" / "player-a11y.js"
_CORE_JS = _WWW / "js" / "player-core.js"
_REVEAL_JS = _WWW / "js" / "player-reveal.js"
_BUNDLE = _WWW / "js" / "player.bundle.js"
_STYLES = _WWW / "css" / "styles.css"
_I18N = _WWW / "i18n"
_BUILD_CSS = _REPO / "scripts" / "build_css.py"
_BUILD_BUNDLE = _REPO / "scripts" / "build_bundle.py"

_LANGS = ("de", "en", "es")


# --------------------------------------------------------------------------
# 1. Reveal glyphs — unconditional, and on the vocabulary actually applied
# --------------------------------------------------------------------------


def test_answer_buttons_carry_a_glyph_for_both_states() -> None:
    """Correct and wrong each get a ::after glyph, not just a hue."""
    css = _QUESTION_CSS.read_text("utf-8")

    correct = re.search(
        r"\.answer-btn\.correct::after\s*\{[^}]*content:\s*\"([^\"]+)\"", css
    )
    wrong = re.search(
        r"\.answer-btn\.wrong::after\s*\{[^}]*content:\s*\"([^\"]+)\"", css
    )

    assert correct, "no ::after glyph on .answer-btn.correct — reveal is hue-only again"
    assert wrong, "no ::after glyph on .answer-btn.wrong — reveal is hue-only again"
    assert correct.group(1) != wrong.group(1), "both states show the same glyph"


def test_glyphs_match_the_dashboard_vocabulary() -> None:
    """A player glancing at the TV and back should not learn two languages."""
    css = _QUESTION_CSS.read_text("utf-8")
    dashboard = (_WWW / "dashboard.html").read_text("utf-8")

    dash_glyph = re.search(
        r"\.dashboard-answer\.correct::after\s*\{[^}]*content:\s*\"([^\"]+)\"", dashboard
    )
    assert dash_glyph, "dashboard lost its correct-answer glyph"

    player_glyph = re.search(
        r"\.answer-btn\.correct::after\s*\{[^}]*content:\s*\"([^\"]+)\"", css
    )
    assert player_glyph
    assert player_glyph.group(1) == dash_glyph.group(1), (
        "player and dashboard disagree on the correct-answer glyph "
        f"({player_glyph.group(1)!r} vs {dash_glyph.group(1)!r})"
    )


def test_glyphs_are_not_gated_behind_the_toggle() -> None:
    """WCAG 1.4.1 is a baseline, not a preference a guest opts into."""
    css = _QUESTION_CSS.read_text("utf-8")
    for line in css.splitlines():
        if "answer-btn.correct::after" in line or "answer-btn.wrong::after" in line:
            assert ".a11y" not in line, (
                "reveal glyph is scoped to the accessibility class — a colour-blind "
                "guest on a borrowed phone never finds that switch"
            )


def test_reveal_applies_the_vocabulary_the_css_styles() -> None:
    """player-reveal.js adds .correct/.wrong; the glyphs must target those."""
    reveal = _REVEAL_JS.read_text("utf-8")
    assert "classList.add('correct')" in reveal
    assert "classList.add('wrong')" in reveal

    css = _QUESTION_CSS.read_text("utf-8")
    assert ".answer-btn.correct::after" in css
    assert ".answer-btn.is-correct::after" not in css, (
        "glyph targets .is-correct, which is never applied to an answer button"
    )


# --------------------------------------------------------------------------
# 2. The storage key exists twice on purpose — keep the copies in step
# --------------------------------------------------------------------------


def test_head_script_and_module_agree_on_the_storage_key() -> None:
    """A one-sided rename reintroduces the paint-then-jump flash silently."""
    head_keys = re.findall(
        r"localStorage\.getItem\('([^']+)'\)", _PLAYER_HTML.read_text("utf-8")
    )
    module_key = re.search(
        r"var A11Y_KEY = '([^']+)';", _A11Y_JS.read_text("utf-8")
    )

    assert module_key, "A11Y_KEY vanished from player-a11y.js"
    assert module_key.group(1) in head_keys, (
        f"the <head> script does not read {module_key.group(1)!r} — the page will "
        "paint at normal size and then jump when the bundle applies the class"
    )


def test_head_script_runs_before_the_stylesheet_takes_effect() -> None:
    """The class must be set in <head>, not from the bundle at end of <body>."""
    html = _PLAYER_HTML.read_text("utf-8")
    head = html.split("</head>", 1)[0]
    assert "quizify_a11y" in head, "no-flash inline script left the <head>"


# --------------------------------------------------------------------------
# 3. Cascade order — the whole type scale hangs off it
# --------------------------------------------------------------------------


def test_a11y_css_is_registered_and_last() -> None:
    build = _BUILD_CSS.read_text("utf-8")
    modules = re.findall(r'"(\d\d-[a-z0-9-]+\.css)"', build)
    assert "09-a11y.css" in modules, "09-a11y.css is not in the CSS build"
    assert modules[-1] == "09-a11y.css", (
        "09-a11y.css must stay last — it overrides --font-size-* tokens from "
        f"00-tokens.css at equal specificity. Current order: {modules}"
    )


def test_a11y_overrides_the_same_tokens_it_needs_to_beat() -> None:
    """Both sizing channels move together, or half the screen scales."""
    tokens = (_CSS_SRC / "00-tokens.css").read_text("utf-8")
    a11y = _A11Y_CSS.read_text("utf-8")

    declared = set(re.findall(r"(--font-size-[a-z0-9]+):", tokens))
    overridden = set(re.findall(r"(--font-size-[a-z0-9]+):", a11y))

    # timer/hero are deliberately left alone — scaling 64/72px is what pushes
    # the question text off a 390px viewport.
    expected = declared - {"--font-size-timer", "--font-size-hero"}
    assert expected <= overridden, (
        f"accessibility mode leaves these token sizes behind: {sorted(expected - overridden)}"
    )
    assert "font-size: 112.5%" in a11y, "root percentage bump gone — rem rules stop scaling"


def test_built_css_contains_the_mode() -> None:
    """styles.css is committed; a forgotten rebuild ships a dead toggle."""
    styles = _STYLES.read_text("utf-8")
    assert ".a11y {" in styles, "run scripts/build_css.py — 09-a11y.css is not in styles.css"
    assert ".answer-btn.correct::after" in styles, "run scripts/build_css.py"


# --------------------------------------------------------------------------
# 4. Wiring: module bundled, toggle wired, labels translated
# --------------------------------------------------------------------------


def test_module_is_bundled() -> None:
    build = _BUILD_BUNDLE.read_text("utf-8")
    assert '"player-a11y.js"' in build, "player-a11y.js missing from PLAYER_MODULES"
    assert "QuizifyA11y" in _BUNDLE.read_text("utf-8"), (
        "run scripts/build_bundle.py — the toggle has no module to talk to"
    )


def test_toggle_button_exists_and_is_wired() -> None:
    html = _PLAYER_HTML.read_text("utf-8")
    core = _CORE_JS.read_text("utf-8")

    assert 'id="a11y-toggle-btn"' in html
    assert 'aria-pressed' in html.split('id="a11y-toggle-btn"', 1)[1][:200], (
        "toggle is not announced as a pressed-state control"
    )
    assert "setupA11yToggle" in core
    assert re.search(r"function init\(\)[\s\S]{0,600}setupA11yToggle\(\);", core), (
        "setupA11yToggle is defined but never called from init()"
    )


def test_toggle_labels_are_translated_everywhere() -> None:
    """A screen-reader label falling back to the raw key reads as gibberish."""
    for lang in _LANGS:
        data = json.loads((_I18N / f"{lang}.json").read_text("utf-8"))
        a11y = data.get("a11y", {})
        for key in ("modeOn", "modeOff"):
            assert key in a11y, f"{lang}.json is missing a11y.{key}"
            assert a11y[key].strip(), f"{lang}.json has an empty a11y.{key}"
        assert a11y["modeOn"] != a11y["modeOff"], (
            f"{lang}.json uses the same label for both toggle states"
        )


def test_core_uses_the_translated_labels() -> None:
    core = _CORE_JS.read_text("utf-8")
    assert "a11y.modeOn" in core and "a11y.modeOff" in core


def test_header_toggle_labels_are_repainted_once_i18n_lands() -> None:
    """Both toggles announced their raw i18n key before this was wired up.

    ``initPageTranslations()`` only walks ``data-i18n*`` attributes, and these
    two buttons paint ``aria-label`` from JS at ``init()`` time — before the
    async translation fetch resolves. Without the flush a screen reader reads
    out "game dot sound mute". Verified on device: the label was the literal
    key until this call was added.
    """
    core = _CORE_JS.read_text("utf-8")

    assert "_flushToggleLabels" in core
    assert re.search(
        r"initPageTranslations\(\);[\s\S]{0,500}_flushToggleLabels\(\);", core
    ), "_flushToggleLabels is never called after translations load"

    for ref in ("_renderSoundToggle = render;", "_renderA11yToggle = render;"):
        assert ref in core, f"missing {ref} — that toggle will never be repainted"
