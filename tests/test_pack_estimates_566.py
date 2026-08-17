"""Estimate questions inside ordinary packs (#566).

Estimate questions (#275) lived in two packs that are nothing *but* estimates.
Five per themed pack, added rather than swapped in, spreads the slider across
the library the way #554 spread pictures.

What can go wrong here is different from the image work. Nothing ships, so
there are no files to orphan and no licences to verify; the failure modes are
in the numbers. A slider whose range excludes its own answer silently drops the
question at load time (the loader logs and skips it, see
``_parse_estimate_question``), and a range so tight it brackets the answer
hands the game away. Both are invisible in review and cheap to assert.

The pairs are a registry for the same reason the image test is: a new pack pair
is one ``EstimateSet`` entry that inherits the whole checklist instead of a
copy of it.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
QUESTIONS = REPO / "custom_components" / "quizify" / "questions"

EXPECTED_ESTIMATE_QUESTIONS = 5

# The two packs that are entirely estimates (#275). They predate #566 and are
# not part of it — they hold 15 each and are excluded from the per-pack count.
DEDICATED_ESTIMATE_PACKS = frozenset({"schaetzfragen-de", "estimation-en"})


@dataclass(frozen=True)
class EstimateSet:
    """One theme's packs and the five facts they share across languages.

    A number survives translation unchanged, so the members of a set carry the
    *same* answers in the same order — that parity is the cheapest guard
    against a fact drifting in one language and not the other.
    """

    theme: str
    packs: tuple[str, ...]

    def path(self, pack: str) -> Path:
        return QUESTIONS / f"{pack}.json"


ESTIMATE_SETS: tuple[EstimateSet, ...] = (
    EstimateSet(theme="nature", packs=("tiere-natur", "animals-nature", "naturaleza-es")),
    EstimateSet(theme="geography", packs=("geographie", "geography", "geografia-es")),
    EstimateSet(theme="history", packs=("geschichte-de", "history-en", "historia-es")),
    EstimateSet(theme="science", packs=("wissenschaft-de", "science-en", "ciencia-es")),
    EstimateSet(theme="technology", packs=("technik-de", "tech-en", "tecnologia-es")),
    EstimateSet(theme="food", packs=("essen-de", "food-en", "comida-es")),
    EstimateSet(theme="sport", packs=("sport-de", "sport-en", "deportes-es")),
    EstimateSet(theme="world-cup", packs=("weltmeisterschaft", "world-cup")),
    EstimateSet(theme="music", packs=("musik-de", "music-en", "musica-es")),
    EstimateSet(theme="pop-culture", packs=("popkultur", "pop-culture", "cultura-pop-es")),
)

PACKS_WITH_ESTIMATES = [p for s in ESTIMATE_SETS for p in s.packs]


def load(pack: str) -> list[dict]:
    return json.loads((QUESTIONS / f"{pack}.json").read_text(encoding="utf-8"))[
        "questions"
    ]


def estimates(pack: str) -> list[dict]:
    return [q for q in load(pack) if q.get("type") == "estimate"]


@pytest.mark.parametrize("pack", PACKS_WITH_ESTIMATES)
class TestEstimateQuestionsPerPack:
    def test_pack_carries_exactly_five(self, pack: str) -> None:
        assert len(estimates(pack)) == EXPECTED_ESTIMATE_QUESTIONS

    def test_answer_lies_inside_its_range(self, pack: str) -> None:
        """A stray answer is dropped at load time, not raised — assert here."""
        for q in estimates(pack):
            assert q["min"] < q["max"], q["id"]
            assert q["min"] <= q["answer"] <= q["max"], q["id"]

    def test_range_is_not_a_giveaway(self, pack: str) -> None:
        """The answer must not sit in a range so narrow it announces itself.

        A slider spanning less than four times the answer's own magnitude
        leaves so few plausible values that closeness scoring stops measuring
        anything. The existing dedicated packs clear this comfortably.
        """
        for q in estimates(pack):
            span = q["max"] - q["min"]
            assert span >= 20, f"{q['id']}: range spans only {span}"

    def test_every_estimate_has_a_unit(self, pack: str) -> None:
        for q in estimates(pack):
            assert q.get("unit", "").strip(), q["id"]

    def test_estimates_carry_no_answers_array(self, pack: str) -> None:
        """An ``answers`` list on an estimate means the type was bolted on."""
        for q in estimates(pack):
            assert "answers" not in q, q["id"]

    def test_fun_fact_and_difficulty_present(self, pack: str) -> None:
        for q in estimates(pack):
            assert q.get("fun_fact", "").strip(), q["id"]
            assert q.get("difficulty") in {"easy", "medium", "hard"}, q["id"]

    def test_ids_are_unique_within_the_pack(self, pack: str) -> None:
        ids = [q["id"] for q in load(pack)]
        assert len(ids) == len(set(ids))


@pytest.mark.parametrize("eset", ESTIMATE_SETS, ids=lambda s: s.theme)
class TestLanguageParity:
    def test_same_facts_in_every_language(self, eset: EstimateSet) -> None:
        """Same numbers, same order — only the wording differs."""
        reference = [
            (q["answer"], q["min"], q["max"]) for q in estimates(eset.packs[0])
        ]
        for pack in eset.packs[1:]:
            assert [
                (q["answer"], q["min"], q["max"]) for q in estimates(pack)
            ] == reference, pack

    def test_question_text_is_not_shared(self, eset: EstimateSet) -> None:
        """Catches a pack that was copied and never translated."""
        texts = [{q["question"] for q in estimates(p)} for p in eset.packs]
        for i, first in enumerate(texts):
            for second in texts[i + 1 :]:
                assert not (first & second)


class TestRegistryCoversTheLibrary:
    def test_no_pack_gains_estimates_without_an_entry(self) -> None:
        """A themed pack that grows estimates must join the registry.

        Without this, a later pair inherits none of the guards above and the
        omission looks exactly like a pack that simply has not been done yet.
        """
        known = set(PACKS_WITH_ESTIMATES) | DEDICATED_ESTIMATE_PACKS
        for path in QUESTIONS.glob("*.json"):
            if path.stem == "versions" or path.stem in known:
                continue
            data = json.loads(path.read_text(encoding="utf-8"))
            found = [
                q for q in data.get("questions", []) if q.get("type") == "estimate"
            ]
            assert not found, f"{path.stem} has estimates but no EstimateSet entry"

    def test_dedicated_packs_are_left_alone(self) -> None:
        """#566 adds to themed packs; it does not touch the original two."""
        for pack in DEDICATED_ESTIMATE_PACKS:
            assert len(estimates(pack)) == 15
