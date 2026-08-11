"""The picture-round packs (#537).

An image question is the one kind that can rot without anyone noticing: the
JSON stays valid, the pack loads, the question appears — and the picture is
simply missing. So the checks here are mostly about the link between the two
halves, plus the provenance record that makes the licences auditable later.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
COMPONENT = REPO / "custom_components" / "quizify"
QUESTIONS = COMPONENT / "questions"
IMAGES = COMPONENT / "www" / "img" / "packs" / "picture-round"
PREFIX = "/quizify/static/img/packs/picture-round/"

PACKS = ("bilderraetsel-de.json", "picture-round-en.json")


def _pack(name: str) -> dict:
    return json.loads((QUESTIONS / name).read_text(encoding="utf-8"))


@pytest.mark.parametrize("name", PACKS)
def test_every_question_has_an_image_that_exists(name: str) -> None:
    """The failure this pack is most exposed to: a question with a dead image."""
    for question in _pack(name)["questions"]:
        url = question.get("image_url", "")
        assert url.startswith(PREFIX), f"{question['id']}: unexpected image_url {url!r}"
        assert (IMAGES / url[len(PREFIX):]).is_file(), (
            f"{question['id']} points at {url}, which is not in the repository"
        )


@pytest.mark.parametrize("name", PACKS)
def test_pack_shape_matches_every_other_pack(name: str) -> None:
    pack = _pack(name)
    assert pack["version"] and pack["theme"] and pack["language"]
    ids = [q["id"] for q in pack["questions"]]
    assert len(ids) == len(set(ids)), "duplicate question ids"
    for question in pack["questions"]:
        answers = question["answers"]
        assert len(answers) == 3, f"{question['id']}: packs ship three answers"
        correct = [a for a in answers if a.get("correct")]
        assert len(correct) == 1, f"{question['id']}: exactly one answer is correct"
        assert question["fun_fact"].strip(), f"{question['id']}: fun_fact is empty"


def test_both_languages_cover_the_same_images() -> None:
    """A picture available in one language only would be a silent gap."""
    urls = [{q["image_url"] for q in _pack(name)["questions"]} for name in PACKS]
    assert urls[0] == urls[1]


def test_no_image_is_shipped_without_a_question() -> None:
    """Dead weight in a repository every install downloads."""
    used = {q["image_url"][len(PREFIX):] for q in _pack(PACKS[0])["questions"]}
    on_disk = {p.name for p in IMAGES.glob("*.webp")}
    assert on_disk == used, f"unused images: {sorted(on_disk - used)}"


def test_every_image_is_credited() -> None:
    """Not a licence requirement — CC0 and public domain ask for nothing. It is
    what keeps the provenance checkable when someone asks in a year."""
    credits = (IMAGES / "credits.md").read_text(encoding="utf-8")
    for image in sorted(IMAGES.glob("*.webp")):
        assert image.name in credits, f"{image.name} is not recorded in credits.md"
    for term in ("CC0", "Public domain"):
        assert term in credits


def test_packs_are_registered_in_versions_json() -> None:
    versions = json.loads((QUESTIONS / "versions.json").read_text(encoding="utf-8"))
    for name in PACKS:
        assert name[:-5] in versions, f"{name} missing from versions.json"


def test_images_stay_within_a_sane_budget() -> None:
    """HACS downloads the whole repository on every install."""
    total_kb = sum(p.stat().st_size for p in IMAGES.glob("*.webp")) / 1024
    assert total_kb < 4096, f"picture-round images grew to {total_kb:.0f} KB"
