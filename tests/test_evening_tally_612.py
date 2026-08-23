"""Tonight's running score across several games (issue #612).

Multi-game evenings are a supported flow — "play again" is a button — but each
game forgot the last one. Between "this game" and the all-time table sat the
evening, which is the unit people actually argue about: "best of three".

**The evening boundary was the real decision, not the line.** A calendar day is
one comparison and it cuts every party that runs past midnight — at 00:05 the TV
would say "tonight: 1 game" after the fourth, failing exactly the evenings this
line exists for. So a session is a run of games less than six hours apart,
walked backwards from the most recent.

Deliberately silent for a single game: "Tonight: Anna 1 win" directly under the
podium that just said the same thing adds nothing.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent
_CC = _REPO_ROOT / "custom_components" / "quizify"
_WWW = _CC / "www"

HOUR = 3600


class _Analytics:
    """The tally logic under test, with a hand-built game history."""

    def __init__(self, games: list[dict]) -> None:
        from custom_components.quizify.analytics import QuizifyAnalytics

        self._impl = QuizifyAnalytics.__new__(QuizifyAnalytics)
        self._impl._data = {"games": games}

    def tally(self, now: float | None = None):
        return self._impl.get_evening_tally(now=now)


def _game(ended_at: int, winner: str | None) -> dict:
    return {"ended_at": ended_at, "winner": winner}


def test_a_single_game_says_nothing() -> None:
    """It would restate the podium standing directly above it."""
    assert _Analytics([_game(1000, "Anna")]).tally() is None


def test_two_games_close_together_are_one_evening() -> None:
    tally = _Analytics([
        _game(10_000, "Anna"),
        _game(10_000 + HOUR, "Anna"),
    ]).tally()

    assert tally == {"games": 2, "leaders": [{"name": "Anna", "wins": 2}]}


def test_a_long_gap_starts_a_new_evening() -> None:
    """Last week's rematch is not part of tonight."""
    tally = _Analytics([
        _game(10_000, "Ben"),
        _game(10_000 + 8 * HOUR, "Anna"),
        _game(10_000 + 9 * HOUR, "Anna"),
    ]).tally()

    assert tally is not None
    assert tally["games"] == 2
    assert tally["leaders"] == [{"name": "Anna", "wins": 2}]


def test_a_party_past_midnight_stays_one_evening() -> None:
    """The case that killed the calendar-day rule.

    Games at 22:00, 23:30, 00:30 and 01:15 are one party. A date comparison
    would report two of them.
    """
    base = 1_700_000_000
    midnight = base + 2 * HOUR
    tally = _Analytics([
        _game(base, "Anna"),
        _game(base + 90 * 60, "Ben"),
        _game(midnight + 30 * 60, "Ben"),
        _game(midnight + 75 * 60, "Anna"),
    ]).tally()

    assert tally is not None
    assert tally["games"] == 4


def test_leaders_are_ordered_by_wins_then_name() -> None:
    """A tie must render the same way every time, not in dict order."""
    tally = _Analytics([
        _game(10_000, "Ben"),
        _game(10_000 + HOUR, "Anna"),
        _game(10_000 + 2 * HOUR, "Anna"),
        _game(10_000 + 3 * HOUR, "Cara"),
    ]).tally()

    assert tally is not None
    assert tally["leaders"] == [
        {"name": "Anna", "wins": 2},
        {"name": "Ben", "wins": 1},
        {"name": "Cara", "wins": 1},
    ]


def test_a_game_with_no_winner_counts_but_crowns_nobody() -> None:
    """Everyone left, or the host reset it. Still part of the evening."""
    tally = _Analytics([
        _game(10_000, "Anna"),
        _game(10_000 + HOUR, None),
    ]).tally()

    assert tally is not None
    assert tally["games"] == 2
    assert tally["leaders"] == [{"name": "Anna", "wins": 1}]


def test_an_evening_that_ended_hours_ago_is_not_tonight() -> None:
    """Opening the TV the next morning must not show yesterday's tally."""
    games = [_game(10_000, "Anna"), _game(10_000 + HOUR, "Ben")]

    assert _Analytics(games).tally(now=10_000 + 20 * HOUR) is None


@pytest.mark.parametrize("code", ["de", "en", "es"])
def test_the_strings_ship_in_every_language(code: str) -> None:
    import json

    dashboard = json.loads((_WWW / "i18n" / f"{code}.json").read_text("utf-8"))[
        "dashboard"
    ]
    for key in ("tonightLabel", "tonightWins", "tonightWinsOne"):
        assert dashboard.get(key), f"{code}.dashboard.{key} missing"
    assert "{wins}" in dashboard["tonightWins"]
    # The singular must not carry a placeholder — "1 wins" is the bug this
    # separate key exists to prevent.
    assert "{wins}" not in dashboard["tonightWinsOne"]


def test_the_tv_renders_it_and_does_not_double_escape() -> None:
    """`parts[]` is escaped per entry; escaping the join again would render
    "Jan &amp;amp; Anna" for a name containing an ampersand."""
    source = (_WWW / "dashboard.html").read_text("utf-8")
    body = source.split("function handleEveningTally(", 1)[1].split("\n        }", 1)[0]
    body = re.sub(r"//.*$", "", body, flags=re.M)

    assert "escapeHtml(entry.name)" in body
    assert "escapeHtml(parts.join(" not in body
    assert "leaders.slice(0, 3)" in body


def test_it_rides_the_same_recorded_event_as_the_standing() -> None:
    """Both become true at the same instant — when the game is written."""
    source = (_CC / "server" / "websocket.py").read_text("utf-8")
    followups = source.split("async def _dispatch_analytics_followups", 1)[1][:600]

    assert "self._dispatch_evening_tally()" in followups


def test_the_tally_goes_to_the_tv_not_to_every_phone() -> None:
    """Unlike the season standing, this is the room's number."""
    source = (_CC / "server" / "websocket.py").read_text("utf-8")
    body = source.split("async def _dispatch_evening_tally", 1)[1].split(
        "\n    async def ", 1
    )[0]

    assert "broadcast_to_admins_and_dashboards" in body
