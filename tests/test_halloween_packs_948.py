"""October stops standing empty in the season window (#948).

#663 filled December and early January, the World Cup packs fill June and July.
Halloween is the other date in the year a party quiz has an obvious theme, and
until now the picker had nothing to pin for it.

As in ``test_holiday_packs_663.py`` the content is covered by the pack-as-pack
tests (duplicates, estimates, versions); what is guarded here is the wiring:

* the window exists and parses in all three languages;
* every language carries the identical window, so the badge appears on the same
  day for a German, an English and a Spanish host;
* the window does not overlap any other seasonal pack, so the picker never has
  to arbitrate;
* ``pick_active_season`` returns the Halloween pack on the days in question and
  lets go of it on 1 November.
"""

from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path

import pytest

from custom_components.quizify.game.seasons import (
    Season,
    is_in_season,
    parse_season,
    pick_active_season,
)

REPO = Path(__file__).resolve().parent.parent
QUESTIONS = REPO / "custom_components" / "quizify" / "questions"

HALLOWEEN = ("halloween-de", "halloween-en", "halloween-es")
WINDOW = ((10, 1), (10, 31))


def _pack(slug: str) -> dict:
    return json.loads((QUESTIONS / f"{slug}.json").read_text(encoding="utf-8"))


def _season(slug: str) -> Season | None:
    return parse_season(_pack(slug).get("season"))


@pytest.mark.parametrize("slug", HALLOWEEN)
def test_every_halloween_pack_declares_a_window_that_parses(slug: str) -> None:
    season = _season(slug)
    assert season is not None, f"{slug} has no usable season window"
    assert season.label.strip(), f"{slug} would badge with the generic label"


@pytest.mark.parametrize("slug", HALLOWEEN)
def test_all_three_languages_share_the_october_window(slug: str) -> None:
    season = _season(slug)
    assert season is not None
    assert (season.start, season.end) == WINDOW, slug


@pytest.mark.parametrize("slug", HALLOWEEN)
def test_a_halloween_pack_keeps_the_shared_theme(slug: str) -> None:
    """A new theme value needs an icon, a tint, a tab and an i18n key; the
    seasonal packs deliberately ride on ``trivia`` instead (#663)."""
    assert _pack(slug).get("theme") == "trivia"


def test_october_overlaps_no_other_seasonal_pack() -> None:
    """Walk the whole year: no day may belong to Halloween and any other
    shipped window at once."""
    halloween = _season(HALLOWEEN[0])
    assert halloween is not None
    others = {}
    for path in QUESTIONS.glob("*.json"):
        if path.stem in HALLOWEEN or path.name == "versions.json":
            continue
        data = json.loads(path.read_text(encoding="utf-8"))
        season = parse_season(data.get("season")) if isinstance(data, dict) else None
        if season is not None:
            others[path.stem] = season
    assert others, "expected the Christmas, New Year and World Cup windows"

    day = date(2026, 1, 1)
    while day <= date(2026, 12, 31):
        if is_in_season(halloween, day):
            clash = [slug for slug, s in others.items() if is_in_season(s, day)]
            assert not clash, f"{day} is also covered by {clash}"
        day += timedelta(days=1)


@pytest.mark.parametrize(
    "today,expected",
    [
        (date(2026, 9, 30), None),
        (date(2026, 10, 1), "halloween-de"),
        (date(2026, 10, 31), "halloween-de"),
        (date(2026, 11, 1), None),
        (date(2026, 12, 10), "weihnachten-de"),
    ],
)
def test_the_picker_pins_halloween_on_its_days(
    today: date, expected: str | None
) -> None:
    library = {
        slug: _season(slug)
        for slug in ("halloween-de", "weihnachten-de", "weltmeisterschaft", "musik-de")
    }
    assert pick_active_season(library, today) == expected
