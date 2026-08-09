"""Keep the README's library figures honest (docs refresh, 2026-08-09).

The README claimed "2,893 questions across 20 themed packs ... in German and
English" for months after that stopped being true: 1.3.0 added Spanish, 1.4.0
added the two estimation packs, and 1.5.0/1.6.1 took Spanish to six packs. The
numbers are quoted in two places and the language list in four, so every pack
PR could — and did — leave them behind without anything failing.

This test recomputes the figures from the shipped pack files and asserts the
README quotes them. It fails the moment a pack is added or removed without the
README being updated, which is exactly when a human would otherwise forget.

``questions/community/`` is excluded on purpose: ``example-pack.json`` is the
schema sample from the "Custom Question Packs" section, not something a host
plays, and counting it would make the README quote 29 packs to a reader who can
only ever select 28.
"""

from __future__ import annotations

import json
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
QUESTIONS_DIR = REPO / "custom_components" / "quizify" / "questions"
README = REPO / "README.md"


def _shipped_packs() -> list[dict]:
    """Load every shipped pack (top-level question files with a question list)."""
    packs = []
    for path in sorted(QUESTIONS_DIR.glob("*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data.get("questions"), list):
            packs.append(data)
    return packs


def test_readme_quotes_the_real_pack_and_question_counts() -> None:
    packs = _shipped_packs()
    pack_count = len(packs)
    question_count = sum(len(p["questions"]) for p in packs)

    readme = README.read_text(encoding="utf-8")

    assert f"{pack_count} themed packs" in readme, (
        f"README does not quote the real pack count ({pack_count}). "
        "Update both the intro bullet and the Question Packs heading."
    )
    assert f"{question_count:,} questions" in readme, (
        f"README does not quote the real question count ({question_count:,}). "
        "Update both the intro bullet and the Question Packs heading."
    )


def test_readme_names_every_shipped_language() -> None:
    """A new pack language must reach the README, not just the picker."""
    languages = {p.get("language") for p in _shipped_packs()}
    readme = README.read_text(encoding="utf-8").lower()

    names = {"de": "german", "en": "english", "es": "spanish"}
    for code in sorted(languages):
        assert code in names, (
            f"Pack language {code!r} has no README wording yet — add it to this "
            "test's mapping and to the README language lists."
        )
        assert names[code] in readme, (
            f"{names[code].title()} packs ship but the README never says so."
        )


def test_readme_names_every_shipped_theme() -> None:
    """The theme list in the pack schema doc must cover what actually ships."""
    themes = {p.get("theme") for p in _shipped_packs() if p.get("theme")}
    readme = README.read_text(encoding="utf-8")

    for theme in sorted(themes):
        assert f"`{theme}`" in readme, (
            f"Theme {theme!r} ships but is missing from the README's list of "
            "valid `theme` values, so a pack author cannot discover it."
        )
