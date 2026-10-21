"""The St. Martin season pack (#1110): wiring and the 30-question floor.

An occasion pack must hold at least 30 questions that are genuinely about the
occasion. Content is covered by the generic pack tests (duplicates, versions);
this guards the window and the floor.
"""

from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path

import pytest

from custom_components.quizify.game.seasons import (
    is_in_season,
    parse_season,
    pick_active_season,
)

ROOT = Path(__file__).resolve().parent.parent
QUESTIONS = ROOT / "custom_components" / "quizify" / "questions"
SLUG = "sankt-martin-de"


def _pack(slug: str = SLUG) -> dict:
    return json.loads((QUESTIONS / f"{slug}.json").read_text(encoding="utf-8"))


def test_pack_meets_the_occasion_floor() -> None:
    assert len(_pack()["questions"]) >= 30


def test_pack_is_german_trivia_with_a_parsable_window() -> None:
    pack = _pack()
    assert pack["language"] == "de"
    assert pack["theme"] == "trivia"
    season = parse_season(pack.get("season"))
    assert season is not None
    assert (season.start, season.end) == ((11, 4), (11, 11))
    assert season.label.strip()


def test_every_question_has_one_correct_answer_of_three() -> None:
    for q in _pack()["questions"]:
        assert len(q["answers"]) == 3, q["id"]
        assert sum(a["correct"] for a in q["answers"]) == 1, q["id"]


def test_window_overlaps_no_other_seasonal_pack() -> None:
    season = parse_season(_pack()["season"])
    assert season is not None
    others = {}
    for path in QUESTIONS.glob("*.json"):
        if path.stem == SLUG or path.name == "versions.json":
            continue
        data = json.loads(path.read_text(encoding="utf-8"))
        other = parse_season(data.get("season")) if isinstance(data, dict) else None
        if other is not None:
            others[path.stem] = other
    day = date(2026, 1, 1)
    while day <= date(2026, 12, 31):
        if is_in_season(season, day):
            clash = [s for s, o in others.items() if is_in_season(o, day)]
            assert not clash, f"{day} also covered by {clash}"
        day += timedelta(days=1)


@pytest.mark.parametrize(
    "today,expected",
    [
        (date(2026, 11, 3), None),
        (date(2026, 11, 4), SLUG),
        (date(2026, 11, 11), SLUG),
        (date(2026, 11, 12), None),
    ],
)
def test_picker_pins_the_pack_on_its_days(today: date, expected: str | None) -> None:
    library = {SLUG: parse_season(_pack()["season"]), "musik-de": None}
    assert pick_active_season(library, today) == expected
