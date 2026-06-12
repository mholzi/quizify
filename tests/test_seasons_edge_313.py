"""Seasons edge-case coverage (issue #313).

``test_seasonal_packs_276.py`` covers the happy paths of the seasons helper.
This file pins the three uncovered edges the #313 review called out:

1. ``parse_season`` ValueError path — a ``"MM-XY"`` window whose day part is
   non-numeric ("12-XY") goes through ``int(parts[1])`` and must be caught and
   dropped, not raised.
2. ``_days_until_end`` Feb-29 fallback — when a tie-break window ends on
   Feb 29 and the comparison year is NOT a leap year, the helper must fall
   back to Mar 1 (both for the same-year and the roll-to-next-year branch)
   instead of raising ``ValueError`` from ``date(year, 2, 29)``.
3. ``_days_until_end`` pre-new-year (December) branch — a wrap-around window
   whose end has already passed *this* calendar year must roll its end date
   to next year so the day-count stays positive and ordering is correct.

All dates are injected; nothing touches the real clock.
"""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT))

from custom_components.quizify.game.seasons import (  # noqa: E402
    Season,
    _days_until_end,
    parse_season,
    pick_active_season,
)


# ---------------------------------------------------------------------------
# 1. parse_season — int() ValueError path
# ---------------------------------------------------------------------------


def test_parse_season_non_numeric_day_is_dropped() -> None:
    """A non-numeric day part ("12-XY") hits ``int()`` → ValueError, which
    parse_season swallows and returns None (treated as non-seasonal)."""
    assert parse_season({"start": "12-XY", "end": "12-26", "label": "L"}) is None


def test_parse_season_non_numeric_month_is_dropped() -> None:
    assert parse_season({"start": "XY-01", "end": "12-26", "label": "L"}) is None


def test_parse_season_non_numeric_end_is_dropped() -> None:
    """The same ValueError guard protects the ``end`` field, not just start."""
    assert parse_season({"start": "12-01", "end": "12-Z", "label": "L"}) is None


# ---------------------------------------------------------------------------
# 2. _days_until_end — Feb-29 fallback in a non-leap year
# ---------------------------------------------------------------------------


def test_days_until_end_feb29_non_leap_same_year() -> None:
    """2026 is not a leap year, so ``date(2026, 2, 29)`` would raise. The
    helper must fall back to Mar 1 for ordering."""
    season = Season(start=(2, 15), end=(2, 29), label="Leap")
    today = date(2026, 2, 20)
    # End falls back to Mar 1 (2026-03-01); 9 days from Feb 20.
    assert _days_until_end(season, today) == (date(2026, 3, 1) - today).days


def test_days_until_end_feb29_non_leap_rolls_to_next_year() -> None:
    """When the (fallback) end date is already in the past this year, the
    helper rolls to next year — and that branch also catches the Feb-29
    ValueError, falling back to Mar 1 of ``year + 1``."""
    season = Season(start=(2, 15), end=(2, 29), label="Leap")
    today = date(2026, 6, 1)  # past Feb/Mar already → roll to 2027
    # 2027 is also non-leap, so the next-year branch falls back to Mar 1 2027.
    assert _days_until_end(season, today) == (date(2027, 3, 1) - today).days


def test_days_until_end_feb29_leap_year_uses_real_date() -> None:
    """In a leap year the real Feb-29 date is used (no fallback)."""
    season = Season(start=(2, 15), end=(2, 29), label="Leap")
    today = date(2028, 2, 20)  # 2028 is a leap year
    assert _days_until_end(season, today) == (date(2028, 2, 29) - today).days


# ---------------------------------------------------------------------------
# 3. _days_until_end — pre-new-year (December) roll-forward branch
# ---------------------------------------------------------------------------


def test_days_until_end_rolls_past_end_to_next_year() -> None:
    """A window whose end month/day already passed this year must count
    forward to next year's occurrence (positive day count)."""
    season = Season(start=(12, 20), end=(1, 6), label="Festtage")
    today = date(2026, 12, 28)  # end (Jan 6) is in the future → 2027-01-06
    assert _days_until_end(season, today) == (date(2027, 1, 6) - today).days


def test_pick_active_season_wraparound_pre_newyear_ordering() -> None:
    """End-to-end through ``pick_active_season``: on Dec 28 a tight wrap-around
    window (ends Jan 6) beats a broad one (ends Mar 31). Exercises the
    December roll-forward leg of ``_days_until_end`` for both candidates."""
    seasons_map = {
        "festtage": Season(start=(12, 20), end=(1, 6), label="Festtage"),
        "winter": Season(start=(12, 1), end=(3, 31), label="Winter"),
    }
    assert pick_active_season(seasons_map, date(2026, 12, 28)) == "festtage"
