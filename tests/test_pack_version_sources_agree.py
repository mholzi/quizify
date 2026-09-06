"""Every pack states one version, not two.

Quizify keeps a pack's version in two places: the ``version`` field inside the
pack file, and its entry in ``questions/versions.json``. They are not
interchangeable — ``QuestionBank._load_pack`` builds ``_pack_versions`` from the
**pack file's own field** (``game/questions.py``, ``raw.get("version", "1.0")``),
and that is what the admin screen shows. ``versions.json`` is the catalogue.

So a bump written to only one of them is invisible: CI stays green, the
catalogue claims a new version, and the host reads the old number off the
screen.

Found on 2026-09-06 during the ``rc-cut`` pack check for v1.16.0-RC1. Eleven
packs disagreed. Nine came from PR #818, which rewrote pack content for the
image-licence sweep and bumped only ``versions.json``. The other two,
``essen-de`` and ``food-en``, had been drifting since #583 in August — nobody
noticed, because nothing compared the two sources.

The rc-cut job asks a human to run this comparison by hand every week. This test
does it on every push instead.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

QUESTIONS = (
    Path(__file__).resolve().parent.parent
    / "custom_components"
    / "quizify"
    / "questions"
)
VERSIONS_FILE = QUESTIONS / "versions.json"


def _catalogue() -> dict[str, str]:
    return json.loads(VERSIONS_FILE.read_text(encoding="utf-8"))


def _pack_slugs() -> list[str]:
    return sorted(_catalogue())


def test_the_catalogue_lists_a_file_for_every_entry() -> None:
    """A catalogue entry without a pack file is a version for nothing."""
    missing = [
        slug for slug in _catalogue() if not (QUESTIONS / f"{slug}.json").exists()
    ]
    assert not missing, f"versions.json lists packs that do not exist: {missing}"


def test_every_pack_file_is_in_the_catalogue() -> None:
    """A pack outside the catalogue ships with no recorded version at all."""
    catalogue = _catalogue()
    strays = sorted(
        path.stem
        for path in QUESTIONS.glob("*.json")
        if path.name != "versions.json" and path.stem not in catalogue
    )
    assert not strays, f"pack files missing from versions.json: {strays}"


@pytest.mark.parametrize("slug", _pack_slugs())
def test_the_pack_and_the_catalogue_state_the_same_version(slug: str) -> None:
    """The number the admin screen shows must be the number the catalogue holds.

    The pack file is the source the running integration reads; the catalogue is
    what the repository documents. When they differ, one of them is lying to
    somebody, and there is no way to tell which from either file alone.
    """
    catalogue_version = _catalogue()[slug]
    pack = json.loads((QUESTIONS / f"{slug}.json").read_text(encoding="utf-8"))
    pack_version = pack.get("version")

    assert pack_version is not None, (
        f"{slug}.json has no 'version' field; the admin screen would fall back "
        f"to '1.0' while versions.json says {catalogue_version!r}"
    )
    assert pack_version == catalogue_version, (
        f"{slug}: the pack file says {pack_version!r}, versions.json says "
        f"{catalogue_version!r}. The admin screen reads the pack file, so it "
        f"would show {pack_version!r}. Bump both or neither."
    )
