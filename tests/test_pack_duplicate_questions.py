"""No pack may ask the same question twice (#593).

The English World Cup pack shipped `world_cup_en_038` and `world_cup_en_039`:
the same question about Paul Breitner, the same three answers, the same
correct one. A round of ten questions drawn from 110 hits a pair like that
often enough for a player to notice, and it reads as a bug rather than as
trivia.

Finding it took reading the pack. This test replaces the reading.

**What counts as the same question here.** The first attempt scored every pair
on shared words and flagged 39 pairs in ``ciencia-es`` alone — all of them
false. A pack that asks for the chemical symbol of gold, of potassium and of
iron repeats its sentence on purpose, and three questions about how many
players a team fields share the options ``Cinco / Seis / Siete`` because those
are the plausible wrong answers, not because anybody copied anything. Reusing a
good set of distractors is what a quiz author is supposed to do.

So the rule here needs all three at once:

* the same set of three answer options,
* the same one of them marked correct,
* and question wording that overlaps by at least 40%.

Two questions can share any one of those innocently. Sharing all three means
the same question is in the pack twice. Measured against the pack as it stood
before #594, this catches both real duplicates and nothing else.
"""

from __future__ import annotations

import itertools
import json
import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
QUESTIONS = REPO / "custom_components" / "quizify" / "questions"

WORDING_OVERLAP = 0.40


def _words(text: str) -> set[str]:
    """Words longer than three characters, accents kept.

    Short words carry the grammar rather than the subject — dropping them is
    what keeps "¿Qué país tiene más lagos?" and "¿Qué país tiene la costa más
    larga?" from looking like one question when they are two.
    """
    cleaned = re.sub(r"[^a-zäöüßáéíóúñü0-9 ]", " ", text.lower())
    return {w for w in cleaned.split() if len(w) > 3}


def _overlap(first: str, second: str) -> float:
    a, b = _words(first), _words(second)
    return len(a & b) / max(1, len(a | b))


def _multiple_choice(pack: dict) -> list[dict]:
    return [
        q for q in pack["questions"]
        if q.get("type") != "estimate" and "answers" in q
    ]


def _options(question: dict) -> tuple[str, ...]:
    return tuple(sorted(a["text"] for a in question["answers"]))


def _correct(question: dict) -> str:
    return next(a["text"] for a in question["answers"] if a.get("correct"))


PACK_FILES = sorted(p for p in QUESTIONS.glob("*.json") if p.stem != "versions")


@pytest.mark.parametrize("path", PACK_FILES, ids=lambda p: p.stem)
def test_no_pack_asks_the_same_question_twice(path: Path) -> None:
    pack = json.loads(path.read_text(encoding="utf-8"))
    duplicates = []
    for first, second in itertools.combinations(_multiple_choice(pack), 2):
        if _options(first) != _options(second):
            continue
        if _correct(first) != _correct(second):
            continue
        score = _overlap(first["question"], second["question"])
        if score >= WORDING_OVERLAP:
            duplicates.append(
                f"{first['id']} / {second['id']} (wording {score:.0%})\n"
                f"    {first['question']}\n"
                f"    {second['question']}"
            )
    assert not duplicates, (
        f"{path.stem} asks the same question twice — same options, same "
        "correct answer, near-identical wording:\n  "
        + "\n  ".join(duplicates)
    )


def test_the_guard_would_have_caught_the_pair_that_prompted_it() -> None:
    """A test whose failure mode is never exercised is a test nobody trusts.

    Rebuilds the pair from #593 (`world_cup_en_038` / `_039`) from its own
    text and asserts the rule fires on it — so a later loosening of the
    threshold cannot quietly turn this file into decoration.
    """
    answers = [
        {"text": "Paul Breitner", "correct": True},
        {"text": "Franz Beckenbauer", "correct": False},
        {"text": "Karl-Heinz Rummenigge", "correct": False},
    ]
    first = {
        "id": "a",
        "question": "Which player scored in two different World Cup finals, eight years apart?",
        "answers": answers,
    }
    second = {
        "id": "b",
        "question": "Which West Germany player scored in two different World Cup finals, eight years apart?",
        "answers": answers,
    }
    assert _options(first) == _options(second)
    assert _correct(first) == _correct(second)
    assert _overlap(first["question"], second["question"]) >= WORDING_OVERLAP
