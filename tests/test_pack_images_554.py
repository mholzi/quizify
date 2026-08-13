"""Image questions inside ordinary packs (#554).

Until #554 pictures lived only in packs that are nothing *but* pictures (#537).
These five per pack sit inside a 150-question themed pack, which changes what
can go wrong: the pack still loads, the questions still play, and a broken
picture simply never shows up in the 3% of draws that would have revealed it.

So the guards here are the same shape as the picture-round ones — the link
between question and file, both languages carrying the same set, no orphaned
weight in a repository every install downloads — plus the two rules that only
matter once a pack is mixed:

* a question that declares ``reveal_style`` must actually have an image, since
  an unblur with nothing to unblur is the trap #434 sat on;
* the text questions around them must stay untouched.

The nature pair was the pilot; geography and history followed. Rather than copy
a hundred lines per pair, the pairs are a registry and every guard runs over all
of them — a new pack pair is one ``ImageSet`` entry, and it inherits the whole
checklist instead of whatever its author remembers to repeat.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
COMPONENT = REPO / "custom_components" / "quizify"
QUESTIONS = COMPONENT / "questions"
IMAGES = COMPONENT / "www" / "img" / "packs"

EXPECTED_IMAGE_QUESTIONS = 5


@dataclass(frozen=True)
class ImageSet:
    """One pack pair and the folder of pictures the two share."""

    folder: str
    packs: tuple[str, str]
    text_question_floor: int

    @property
    def image_dir(self) -> Path:
        return IMAGES / self.folder

    @property
    def prefix(self) -> str:
        return f"/quizify/static/img/packs/{self.folder}/"


PACK_SETS = (
    ImageSet("nature", ("tiere-natur.json", "animals-nature.json"), 149),
    ImageSet("geography", ("geographie.json", "geography.json"), 149),
    ImageSet("history", ("geschichte-de.json", "history-en.json"), 149),
    ImageSet("science", ("wissenschaft-de.json", "science-en.json"), 150),
    ImageSet("tech", ("technik-de.json", "tech-en.json"), 150),
    ImageSet("food", ("essen-de.json", "food-en.json"), 150),
)

# (set, pack) for the guards that look at one pack at a time.
PACKS = [(s, name) for s in PACK_SETS for name in s.packs]

# pytest hands an ids callable one argvalue at a time, so with two argnames a
# callable cannot see the pair. Explicit id lists keep the readable names.
SET_IDS = [s.folder for s in PACK_SETS]
PACK_IDS = [f"{s.folder}/{name}" for s, name in PACKS]


def _pack(name: str) -> dict:
    return json.loads((QUESTIONS / name).read_text(encoding="utf-8"))


def _image_questions(iset: ImageSet, name: str) -> list[dict]:
    return [q for q in _pack(name)["questions"] if q.get("image_url")]


@pytest.mark.parametrize("iset,name", PACKS, ids=PACK_IDS)
def test_the_pack_carries_five_image_questions(iset: ImageSet, name: str) -> None:
    """The ask was five per pack — count it rather than trust the diff."""
    assert len(_image_questions(iset, name)) == EXPECTED_IMAGE_QUESTIONS


@pytest.mark.parametrize("iset,name", PACKS, ids=PACK_IDS)
def test_every_image_question_points_at_a_file_that_exists(iset: ImageSet, name: str) -> None:
    for q in _image_questions(iset, name):
        url = q["image_url"]
        assert url.startswith(iset.prefix), f"{q['id']} points outside the pack: {url}"
        assert (iset.image_dir / url[len(iset.prefix):]).is_file(), f"{q['id']} → missing {url}"


@pytest.mark.parametrize("iset,name", PACKS, ids=PACK_IDS)
def test_a_reveal_style_never_stands_without_an_image(iset: ImageSet, name: str) -> None:
    """The #434 trap: a progressive reveal with nothing to unblur."""
    for q in _pack(name)["questions"]:
        if q.get("reveal_style"):
            assert q.get("image_url"), f"{q['id']} declares a reveal style but no image"


@pytest.mark.parametrize("iset", PACK_SETS, ids=SET_IDS)
def test_both_languages_use_the_same_pictures(iset: ImageSet) -> None:
    """A picture asked in German but not in English is a silent gap — and the
    whole reason the pair is cheaper than two packs is the shared images."""
    sets = [{q["image_url"] for q in _image_questions(iset, name)} for name in iset.packs]
    assert sets[0] == sets[1]


@pytest.mark.parametrize("iset", PACK_SETS, ids=SET_IDS)
def test_no_image_is_shipped_without_a_question(iset: ImageSet) -> None:
    """Dead weight in a repository every install downloads."""
    used = {q["image_url"][len(iset.prefix):] for q in _image_questions(iset, iset.packs[0])}
    on_disk = {p.name for p in iset.image_dir.glob("*.webp")}
    assert on_disk == used, f"unused images in {iset.folder}: {sorted(on_disk - used)}"


@pytest.mark.parametrize("iset", PACK_SETS, ids=SET_IDS)
def test_every_image_is_credited_with_a_verifiable_source(iset: ImageSet) -> None:
    """Not a licence requirement — public domain asks for nothing. It is what
    keeps the provenance checkable when somebody asks in a year."""
    credits = (iset.image_dir / "credits.md").read_text(encoding="utf-8")
    for image in sorted(iset.image_dir.glob("*.webp")):
        assert image.name in credits, f"{image.name} is not recorded in credits.md"
    assert "Public domain" in credits
    # The licence has to have been checked on the file record itself — the
    # search that finds an image says nothing about how it is licensed, which
    # is how a CC BY-SA photograph nearly ended up in the nature set.
    assert "file record" in credits


@pytest.mark.parametrize("iset,name", PACKS, ids=PACK_IDS)
def test_the_text_questions_are_untouched(iset: ImageSet, name: str) -> None:
    """Five added, none replaced — the open question in #554, answered by
    keeping every question that was already in the pack."""
    plain = [q for q in _pack(name)["questions"] if not q.get("image_url")]
    assert len(plain) >= iset.text_question_floor, f"{name} lost text questions: {len(plain)} left"


@pytest.mark.parametrize("iset", PACK_SETS, ids=SET_IDS)
def test_images_stay_within_a_sane_budget(iset: ImageSet) -> None:
    """HACS downloads the whole repository on every install. Five pictures per
    pack across 30 packs is the part of #554 that has to stay bounded."""
    total_kb = sum(p.stat().st_size for p in iset.image_dir.glob("*.webp")) / 1024
    assert total_kb < 1024, f"{iset.folder} images grew to {total_kb:.0f} KB"


# The picture-round packs (#537) are pictures all the way down and carry their
# own guards in test_picture_round_537.py. They live in the same tree, so the
# registry check below has to know they are somebody else's business.
NOT_A_PACK_PAIR = {"picture-round"}


def test_no_pack_folder_is_missing_from_the_registry() -> None:
    """A pair added on disk but not here would ship unguarded — which is the
    failure mode a registry invites and the reason this test exists."""
    on_disk = {p.name for p in IMAGES.iterdir() if p.is_dir()} - NOT_A_PACK_PAIR
    assert on_disk == {s.folder for s in PACK_SETS}
