"""Image questions inside an ordinary pack (#554, nature pair first).

Until now pictures lived only in packs that are nothing *but* pictures (#537).
These five sit inside a 150-question themed pack, which changes what can go
wrong: the pack still loads, the questions still play, and a broken picture
simply never shows up in the 3% of draws that would have revealed it.

So the guards here are the same shape as the picture-round ones — the link
between question and file, both languages carrying the same set, no orphaned
weight in a repository every install downloads — plus the two rules that only
matter once a pack is mixed:

* a question that declares ``reveal_style`` must actually have an image, since
  an unblur with nothing to unblur is the trap #434 sat on;
* the text questions around them must stay untouched.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
COMPONENT = REPO / "custom_components" / "quizify"
QUESTIONS = COMPONENT / "questions"
IMAGES = COMPONENT / "www" / "img" / "packs" / "nature"
PREFIX = "/quizify/static/img/packs/nature/"

PACKS = ("tiere-natur.json", "animals-nature.json")
EXPECTED_IMAGE_QUESTIONS = 5


def _pack(name: str) -> dict:
    return json.loads((QUESTIONS / name).read_text(encoding="utf-8"))


def _image_questions(name: str) -> list[dict]:
    return [q for q in _pack(name)["questions"] if q.get("image_url")]


@pytest.mark.parametrize("name", PACKS)
def test_the_pack_carries_five_image_questions(name: str) -> None:
    """The ask was five per pack — count it rather than trust the diff."""
    assert len(_image_questions(name)) == EXPECTED_IMAGE_QUESTIONS


@pytest.mark.parametrize("name", PACKS)
def test_every_image_question_points_at_a_file_that_exists(name: str) -> None:
    for q in _image_questions(name):
        url = q["image_url"]
        assert url.startswith(PREFIX), f"{q['id']} points outside the pack: {url}"
        assert (IMAGES / url[len(PREFIX):]).is_file(), f"{q['id']} → missing {url}"


@pytest.mark.parametrize("name", PACKS)
def test_a_reveal_style_never_stands_without_an_image(name: str) -> None:
    """The #434 trap: a progressive reveal with nothing to unblur."""
    for q in _pack(name)["questions"]:
        if q.get("reveal_style"):
            assert q.get("image_url"), f"{q['id']} declares a reveal style but no image"


def test_both_languages_use_the_same_pictures() -> None:
    """A picture asked in German but not in English is a silent gap — and the
    whole reason the pair is cheaper than two packs is the shared images."""
    sets = [{q["image_url"] for q in _image_questions(name)} for name in PACKS]
    assert sets[0] == sets[1]


def test_no_image_is_shipped_without_a_question() -> None:
    """Dead weight in a repository every install downloads."""
    used = {q["image_url"][len(PREFIX):] for q in _image_questions(PACKS[0])}
    on_disk = {p.name for p in IMAGES.glob("*.webp")}
    assert on_disk == used, f"unused images: {sorted(on_disk - used)}"


def test_every_image_is_credited_with_a_verifiable_source() -> None:
    """Not a licence requirement — public domain asks for nothing. It is what
    keeps the provenance checkable when somebody asks in a year."""
    credits = (IMAGES / "credits.md").read_text(encoding="utf-8")
    for image in sorted(IMAGES.glob("*.webp")):
        assert image.name in credits, f"{image.name} is not recorded in credits.md"
    assert "Public domain" in credits
    # The licence has to have been checked on the file record itself — the
    # search that finds an image says nothing about how it is licensed, which
    # is how a CC BY-SA photograph nearly ended up in this set.
    assert "file record" in credits


@pytest.mark.parametrize("name", PACKS)
def test_the_text_questions_are_untouched(name: str) -> None:
    """Five added, none replaced — the open question in #554, answered here by
    keeping every question that was already in the pack."""
    plain = [q for q in _pack(name)["questions"] if not q.get("image_url")]
    assert len(plain) >= 149, f"{name} lost text questions: {len(plain)} left"


def test_images_stay_within_a_sane_budget() -> None:
    """HACS downloads the whole repository on every install. Five pictures per
    pack across 30 packs is the part of #554 that has to stay bounded."""
    total_kb = sum(p.stat().st_size for p in IMAGES.glob("*.webp")) / 1024
    assert total_kb < 1024, f"nature images grew to {total_kb:.0f} KB"
