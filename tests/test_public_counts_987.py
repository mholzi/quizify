"""The public figures are counted, not remembered (issue #987).

Four surfaces quote how much content ships. Only the README was recounted at
release time; the repository description and the landing page were written once
and carried forward, so on 21.09.2026 they claimed 4,740 questions across 36
packs while the repository held 5,796 across 45.

``scripts/public_counts.py`` exists so the release step can paste instead of
remember. These tests guard the two ways it could quietly stop being true: the
theme table drifting away from the packs, and a new pack landing in no theme at
all — which would leave it out of the table on the landing page while still
counting towards the total.

``tests/test_docs_pack_counts.py`` keeps the README honest; this file keeps the
script honest.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
_PACKS = _REPO / "custom_components" / "quizify" / "questions"


def _load():
    spec = importlib.util.spec_from_file_location(
        "public_counts", _REPO / "scripts" / "public_counts.py"
    )
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_the_total_is_the_sum_of_the_pack_files() -> None:
    mod = _load()
    expected = 0
    packs = 0
    for path in _PACKS.glob("*.json"):
        if path.name == "versions.json":
            continue
        data = json.loads(path.read_text(encoding="utf-8"))
        expected += len(data.get("questions", []))
        packs += 1

    s = mod.summary()
    assert s["questions"] == expected
    assert s["packs"] == packs


def test_every_pack_belongs_to_a_theme() -> None:
    """A new pack that no theme lists would be counted but never shown.

    That is the drift this file is here to catch: the total on the landing page
    would move while the table under it stayed the same, and the two would
    disagree on the same page.
    """
    s = _load().summary()
    assert s["unlisted_packs"] == [], (
        "these packs are in no theme, so the landing-page table would omit them: "
        f"{s['unlisted_packs']}"
    )


def test_no_theme_names_a_pack_that_does_not_exist() -> None:
    """The other direction: a renamed pack file must not leave a hole."""
    s = _load().summary()
    assert s["missing_packs"] == [], (
        f"these theme entries point at missing pack files: {s['missing_packs']}"
    )


def test_the_theme_table_adds_up_to_the_total() -> None:
    """The table is what the landing page prints; its sum has to be the headline
    number, or the page contradicts itself in two places at once."""
    s = _load().summary()
    table_total = sum(row["de"] + row["en"] + row["es"] for row in s["per_theme"])
    assert table_total == s["questions"]


def test_the_check_flag_agrees_with_the_readme() -> None:
    """The flag a release run calls by hand; green here means green there."""
    mod = _load()
    assert mod.main(["--check", "--json"]) == 0
