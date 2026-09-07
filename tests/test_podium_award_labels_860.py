"""No two podium awards may share a label (#860).

The podium prints two things that both come from ``highlights.*``: the
derived chip for the winner's cumulative total (``highlights.topScore``)
and one card per server-computed superlative (``highlights.awards.*``).
Both carried the English words "Top Score" until this test existed, and
the television's award strip drops the detail line that told them apart
(``dashboard.html``: ``.dashboard-finale--split .award-detail`` is hidden
below 800px of height). So the finale read ``TOP SCORE · CLEO`` above a
leaderboard whose first row said Ben — an award that looked like a
scoring bug.

The label is the whole disambiguation on that screen, so the assertion is
the mechanical one: within a language, no two of those keys may resolve to
the same words. It holds for any future pair, not just this one.
"""

from __future__ import annotations

import ast
import json
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
_PKG = _REPO / "custom_components" / "quizify"
_I18N = _PKG / "www" / "i18n"

LANGUAGES = ("de", "en", "es")


def _bundle(code: str) -> dict:
    return json.loads((_I18N / f"{code}.json").read_text("utf-8"))


def _podium_labels(code: str) -> dict[str, str]:
    """Every key whose value can appear as an award name on the podium."""
    highlights = _bundle(code)["highlights"]
    labels = {"highlights.topScore": highlights["topScore"]}
    for name, value in highlights["awards"].items():
        labels[f"highlights.awards.{name}"] = value
    return labels


def _normalise(label: str) -> str:
    return " ".join(label.split()).casefold()


def test_no_two_podium_awards_share_a_label() -> None:
    for code in LANGUAGES:
        by_label: dict[str, list[str]] = {}
        for key, label in _podium_labels(code).items():
            by_label.setdefault(_normalise(label), []).append(key)
        clashes = {
            label: keys for label, keys in by_label.items() if len(keys) > 1
        }
        assert not clashes, (
            f"{code}.json gives the same award label to more than one key. "
            "The television prints the award name without its detail line, "
            "so two awards under one name cannot be told apart on the "
            f"finale screen (#860):\n  "
            + "\n  ".join(
                f"{label!r} ← {', '.join(keys)}" for label, keys in clashes.items()
            )
        )


def test_every_podium_label_is_non_empty() -> None:
    """Guards the guard: blanks would collide as one and look like a clash."""
    for code in LANGUAGES:
        for key, label in _podium_labels(code).items():
            assert label.strip(), f"{code}.json has an empty label for {key}"


def _awards_from_source() -> dict[str, str]:
    """``award_key`` → the English fallback passed beside it in highlights.py.

    ``compute_superlatives`` hands every award to the local ``_try_award``
    helper as ``(award, icon, detail, winner, award_key=..., ...)``. Read
    the pairs off the AST rather than running a game, so an award that
    needs a rare board still gets checked.
    """
    tree = ast.parse((_PKG / "game" / "highlights.py").read_text("utf-8"))
    pairs: dict[str, str] = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if not (isinstance(node.func, ast.Name) and node.func.id == "_try_award"):
            continue
        key = next(
            (
                kw.value.value
                for kw in node.keywords
                if kw.arg == "award_key" and isinstance(kw.value, ast.Constant)
            ),
            None,
        )
        if key and node.args and isinstance(node.args[0], ast.Constant):
            pairs[key] = node.args[0].value
    return pairs


def test_the_award_scan_sees_every_award() -> None:
    """Guards the guard: a parse that found nothing would pass vacuously."""
    keys = _awards_from_source()
    assert len(keys) == len(_bundle("en")["highlights"]["awards"]), (
        "highlights.py and en.json disagree on how many awards exist: "
        f"{sorted(keys)}"
    )


def test_english_fallbacks_say_the_same_thing_as_the_bundle() -> None:
    """``Superlative.award`` is the English fallback for clients that don't
    speak the i18n protocol, so it has to track ``en.json`` — otherwise a
    rename lands on the phone and the television but not on the fallback."""
    english = _podium_labels("en")
    for key, fallback in _awards_from_source().items():
        assert key in english, f"{key} is awarded but missing from en.json"
        assert fallback == english[key], (
            f"{key}: highlights.py says {fallback!r}, en.json says "
            f"{english[key]!r} — the fallback and the translation have to "
            "name the same award"
        )
