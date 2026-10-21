"""The English Thanksgiving pack is wired into the season mechanism (#1109)."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

from custom_components.quizify.game.seasons import (
    is_in_season,
    parse_season,
    pick_active_season,
)

QUESTIONS = (
    Path(__file__).resolve().parent.parent
    / "custom_components"
    / "quizify"
    / "questions"
)


def _pack(slug: str = "thanksgiving-en") -> dict:
    return json.loads((QUESTIONS / f"{slug}.json").read_text(encoding="utf-8"))


def test_window_runs_from_19_to_28_november() -> None:
    season = parse_season(_pack().get("season"))
    assert season is not None
    assert (season.start, season.end) == ((11, 19), (11, 28))
    assert is_in_season(season, date(2026, 11, 26))
    assert not is_in_season(season, date(2026, 11, 18))
    assert not is_in_season(season, date(2026, 11, 29))


@pytest.mark.parametrize(
    "today,expected",
    [
        (date(2026, 11, 26), "thanksgiving-en"),
        (date(2026, 11, 29), None),
        (date(2026, 12, 1), "christmas-en"),
    ],
)
def test_picker_pins_thanksgiving_in_its_window(
    today: date, expected: str | None
) -> None:
    library = {
        slug: parse_season(_pack(slug).get("season"))
        for slug in ("thanksgiving-en", "christmas-en")
    }
    assert pick_active_season(library, today) == expected


def test_pack_shape() -> None:
    qs = _pack()["questions"]
    assert len(qs) == 100
    choice = [q for q in qs if q.get("type") != "estimate"]
    assert len(choice) == 95
    for q in choice:
        assert len(q["answers"]) == 3
        assert sum(a["correct"] for a in q["answers"]) == 1, q["id"]
