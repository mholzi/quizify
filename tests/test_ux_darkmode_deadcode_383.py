"""Guard against reintroducing unreachable dark-mode CSS (issue #383).

Dark mode never activates: ``player.html`` and ``admin.html`` both hardcode
``<html data-theme="light">`` and no JS ever flips it, so every
``@media (prefers-color-scheme: dark)`` block and every ``[data-theme="dark"]``
rule was dead code that could never apply. #383 removed them.

Re-adding dark mode is a deliberate visual/design change — it must come with
the ``data-theme`` wiring to actually reach users, not slip back in as dead
CSS. These tests fail if either pattern reappears in any built or source CSS.
"""

from __future__ import annotations

from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
CSS_DIR = REPO / "custom_components" / "quizify" / "www" / "css"


def _css_files() -> list[Path]:
    files = sorted(CSS_DIR.rglob("*.css"))
    assert files, f"no CSS files found under {CSS_DIR}"
    return files


def test_no_dark_theme_selector_rule() -> None:
    offenders = [
        p.relative_to(REPO)
        for p in _css_files()
        if '[data-theme="dark"]' in p.read_text("utf-8")
    ]
    assert not offenders, (
        "unreachable dark-mode CSS reintroduced (#383): "
        f'[data-theme="dark"] rule found in {offenders}. Dark mode never '
        "activates (player/admin hardcode data-theme=\"light\"); enabling it "
        "is a design change that must ship the data-theme wiring too."
    )


def test_no_prefers_color_scheme_dark_block() -> None:
    offenders = [
        p.relative_to(REPO)
        for p in _css_files()
        if "prefers-color-scheme: dark" in p.read_text("utf-8")
    ]
    assert not offenders, (
        "unreachable dark-mode CSS reintroduced (#383): "
        f"@media (prefers-color-scheme: dark) block found in {offenders}."
    )
