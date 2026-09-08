"""The season window stops standing empty eleven months a year (#663).

``game/seasons.py`` has let a pack declare a recurring calendar window since
#276, and for as long as it has existed the only packs using it were the three
World Cup ones. A mechanism that surfaces something for two months of the year
and nothing for the other ten is not a weak feature; from the host's side it is
not a feature at all.

The holiday packs fill the other end of the calendar: Christmas from 1 to 26
December, New Year from 27 December to 6 January. What is worth guarding here
is not the content — the duplicate, estimate and version tests already cover a
pack as a pack — but the *wiring*, which is the part that has silently gone
missing before:

* the windows exist and are parseable at all (a typo in ``"12-01"`` degrades to
  a non-seasonal pack with nothing but a log line to show for it);
* they cover the December-into-January stretch without a gap, which is the
  whole point of building two packs rather than one;
* they do not overlap, so the picker never has to arbitrate between them;
* every language carries the identical window, so a Spanish host does not get
  the badge a week later than a German one;
* and ``pick_active_season`` actually returns them on the days in question,
  which is the only assertion here that exercises the mechanism end to end
  rather than the JSON that feeds it.
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

CHRISTMAS = ("weihnachten-de", "christmas-en", "navidad-es")
NEW_YEAR = ("silvester-de", "new-years-eve-en", "nochevieja-es")
HOLIDAY_PACKS = CHRISTMAS + NEW_YEAR

# The windows the packs are supposed to declare, as (start, end) month/day.
EXPECTED_WINDOWS = {
    CHRISTMAS: ((12, 1), (12, 26)),
    NEW_YEAR: ((12, 27), (1, 6)),
}


def _pack(slug: str) -> dict:
    return json.loads((QUESTIONS / f"{slug}.json").read_text(encoding="utf-8"))


def _season(slug: str) -> Season | None:
    return parse_season(_pack(slug).get("season"))


@pytest.mark.parametrize("slug", HOLIDAY_PACKS)
def test_every_holiday_pack_declares_a_window_that_parses(slug: str) -> None:
    """A malformed window is not an error — it is a pack that quietly stops
    being seasonal, which is exactly the failure this issue is about."""
    season = _season(slug)
    assert season is not None, f"{slug} has no usable season window"
    assert season.label.strip(), f"{slug} would badge with the generic label"


@pytest.mark.parametrize(
    "group", list(EXPECTED_WINDOWS), ids=["christmas", "new-year"]
)
def test_all_three_languages_share_one_window(group: tuple[str, ...]) -> None:
    """The badge has to appear on the same day in every language."""
    start, end = EXPECTED_WINDOWS[group]
    for slug in group:
        season = _season(slug)
        assert season is not None
        assert (season.start, season.end) == (start, end), slug


def test_the_two_windows_meet_without_a_gap_or_an_overlap() -> None:
    """26 December hands over to the 27th, and neither day belongs to both.

    A gap here would leave the days between Christmas and New Year with nothing
    surfaced, which is precisely the stretch the second pack exists for.
    """
    christmas = _season(CHRISTMAS[0])
    new_year = _season(NEW_YEAR[0])
    assert christmas is not None and new_year is not None

    for day in (date(2026, 12, 26), date(2026, 12, 27)):
        active = [
            s for s in (christmas, new_year) if is_in_season(s, day)
        ]
        assert len(active) == 1, f"{day} is covered by {len(active)} windows"

    # Walk the whole stretch: every day from 1 December to 6 January is covered
    # by exactly one of the two.
    day = date(2026, 12, 1)
    while day <= date(2027, 1, 6):
        covered = sum(
            1 for s in (christmas, new_year) if is_in_season(s, day)
        )
        assert covered == 1, f"{day} is covered by {covered} holiday windows"
        day += timedelta(days=1)


def test_the_new_year_window_wraps_across_the_turn_of_the_year() -> None:
    """The wrap-around branch of ``is_in_season`` is the one a hand-written
    ``start <= today <= end`` check gets wrong."""
    new_year = _season(NEW_YEAR[0])
    assert new_year is not None
    assert is_in_season(new_year, date(2026, 12, 31))
    assert is_in_season(new_year, date(2027, 1, 1))
    assert is_in_season(new_year, date(2027, 1, 6))
    assert not is_in_season(new_year, date(2027, 1, 7))


@pytest.mark.parametrize(
    "today,expected",
    [
        (date(2026, 12, 10), "weihnachten-de"),
        (date(2026, 12, 26), "weihnachten-de"),
        (date(2026, 12, 27), "silvester-de"),
        (date(2027, 1, 6), "silvester-de"),
        (date(2027, 1, 7), None),
        (date(2026, 7, 15), "weltmeisterschaft"),
    ],
)
def test_the_picker_pins_the_pack_whose_day_it_is(
    today: date, expected: str | None
) -> None:
    """End to end over the German library: the same call the featured-pack view
    makes, against the real season fields of the real packs."""
    library = {
        slug: _season(slug)
        for slug in ("weihnachten-de", "silvester-de", "weltmeisterschaft", "musik-de")
    }
    assert pick_active_season(library, today) == expected


@pytest.mark.parametrize("slug", HOLIDAY_PACKS)
def test_a_holiday_pack_is_not_seasonal_the_rest_of_the_year(slug: str) -> None:
    """Six months from the window, nothing is pinned — the packs add a badge to
    the calendar rather than a permanent one."""
    season = _season(slug)
    assert season is not None
    for day in (date(2026, 3, 15), date(2026, 6, 1), date(2026, 9, 30)):
        assert not is_in_season(season, day), f"{slug} claims to be in season on {day}"


@pytest.mark.parametrize("slug", HOLIDAY_PACKS)
def test_a_holiday_pack_reuses_an_existing_theme(slug: str) -> None:
    """A new ``theme`` value is not free: the picker's theme tabs are hand-written
    buttons in ``admin.html`` with an i18n key each, and every shipped theme
    needs an icon, a tint and a server-side emoji (see
    tests/test_pack_labels_and_icons.py). The holiday packs are content inside
    the existing mechanism, so they take the theme the other cross-cutting packs
    already use."""
    assert _pack(slug)["theme"] == "trivia"
