"""The duel between two returning players, on the TV lobby (issue #613).

The code asked for this itself: `get_player_standing` justifies the lobby line
by saying it is "supposed to start a rivalry", and only the solo view existed.

**This is a deliberate reversal of #371, not an extension of it.** That issue
sends each player only their OWN standing; #624 kept that posture this
afternoon. A head-to-head on the TV puts two people's record in front of the
whole room. Markus made that call explicitly — at home it is the fun, at a party
with colleagues "Ben 0–5" can be the moment someone stops playing. The phones
therefore stay out of it: TV and admin only.

Scope is the detailed history, which prunes at RETENTION_DAYS /
MAX_DETAILED_RECORDS, so the line says "last 90 days" rather than implying an
all-time record it cannot support.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
_CC = _REPO_ROOT / "custom_components" / "quizify"
_WWW = _CC / "www"


class _Analytics:
    def __init__(self, games: list[dict]) -> None:
        from custom_components.quizify.analytics import QuizifyAnalytics

        self._impl = QuizifyAnalytics.__new__(QuizifyAnalytics)
        self._impl._data = {"games": games}

    def duel(self, present: list[str]):
        return self._impl.get_head_to_head(present)


def _game(scores: dict[str, int]) -> dict:
    return {"player_scores": scores}


def test_a_single_shared_game_is_not_a_record() -> None:
    """"1–0" after one game reads like a record and is a coincidence."""
    games = [_game({"Anna": 10, "Ben": 8})]

    assert _Analytics(games).duel(["Anna", "Ben"]) is None


def test_two_meetings_produce_the_duel() -> None:
    games = [
        _game({"Anna": 10, "Ben": 8}),
        _game({"Anna": 5, "Ben": 9}),
        _game({"Anna": 7, "Ben": 3}),
    ]

    duel = _Analytics(games).duel(["Anna", "Ben"])

    assert duel == {
        "left": "Anna",
        "right": "Ben",
        "left_wins": 2,
        "right_wins": 1,
        "games": 3,
    }


def test_the_winner_of_a_meeting_is_between_those_two() -> None:
    """Not the game's overall winner.

    Cara topping the table does not settle anything between Anna and Ben.
    """
    games = [
        _game({"Anna": 4, "Ben": 3, "Cara": 99}),
        _game({"Anna": 6, "Ben": 2, "Cara": 99}),
    ]

    duel = _Analytics(games).duel(["Anna", "Ben"])

    assert duel is not None
    assert (duel["left_wins"], duel["right_wins"]) == (2, 0)


def test_a_draw_counts_as_a_meeting_and_goes_to_nobody() -> None:
    games = [
        _game({"Anna": 5, "Ben": 5}),
        _game({"Anna": 7, "Ben": 3}),
    ]

    duel = _Analytics(games).duel(["Anna", "Ben"])

    assert duel is not None
    assert duel["games"] == 2
    assert (duel["left_wins"], duel["right_wins"]) == (1, 0)


def test_the_most_met_pair_wins_when_several_are_present() -> None:
    """Three regulars in the lobby is one duel, not three."""
    games = [
        _game({"Anna": 9, "Ben": 4}),
        _game({"Anna": 8, "Ben": 5}),
        _game({"Anna": 7, "Ben": 6}),
        _game({"Anna": 3, "Cara": 9}),
        _game({"Anna": 2, "Cara": 8}),
    ]

    duel = _Analytics(games).duel(["Anna", "Ben", "Cara"])

    assert duel is not None
    assert {duel["left"], duel["right"]} == {"Anna", "Ben"}
    assert duel["games"] == 3


def test_games_only_one_of_them_played_are_ignored() -> None:
    games = [
        _game({"Anna": 10}),
        _game({"Ben": 10}),
        _game({"Anna": 10, "Ben": 1}),
    ]

    assert _Analytics(games).duel(["Anna", "Ben"]) is None


def test_a_lobby_of_one_has_no_duel() -> None:
    assert _Analytics([_game({"Anna": 1, "Ben": 2})]).duel(["Anna"]) is None


def test_it_is_sent_in_the_lobby_only_and_never_to_phones() -> None:
    """The reversal of #371 is bounded: the room sees it, the phones do not."""
    source = (_CC / "server" / "websocket.py").read_text("utf-8")
    body = source.split("async def _send_head_to_head", 1)[1].split(
        "\n    def ", 1
    )[0]
    body = re.sub(r'""".*?"""', "", body, flags=re.S)

    assert "phase != GamePhase.LOBBY" in body
    assert "broadcast_to_admins_and_dashboards" in body
    # A plain broadcast would reach every phone — the thing #371 avoided.
    assert "self._conn.broadcast(" not in body


def test_the_tv_states_the_ninety_day_scope() -> None:
    """The detailed history prunes, so calling it all-time would be a claim the
    data cannot support."""
    html = (_WWW / "dashboard.html").read_text("utf-8")

    assert "dashboard.h2hRecent" in html
    for code in ("de", "en", "es"):
        bundle = json.loads((_WWW / "i18n" / f"{code}.json").read_text("utf-8"))
        dash = bundle["dashboard"]
        assert dash.get("h2hLabel")
        assert "90" in dash["h2hRecent"]


def test_the_names_are_escaped() -> None:
    html = (_WWW / "dashboard.html").read_text("utf-8")
    body = html.split("function handleHeadToHead(", 1)[1].split("\n        }", 1)[0]

    assert "escapeHtml(msg.left)" in body
    assert "escapeHtml(msg.right)" in body


def test_the_lobby_is_compacted_on_short_screens() -> None:
    """A television does not scroll, so a lobby taller than the screen simply
    loses its bottom.

    Measured with three players in the lobby: the column was already 732px on
    a 1280x720 screen *before* this feature — the roster chips were clipped by
    12px and nobody had noticed — and the duel line lands at 829px, entirely
    off the picture. 1366x768 fails the same way. Only 1080p had the room.

    The rule below compacts the lobby on short viewports instead of shrinking
    it everywhere, because the QR still has to scan from across the room on a
    screen that does have the height. Without it the feature is invisible on
    every 720p television, and invisible in the worst way: nothing looks
    broken.
    """
    html = (_WWW / "dashboard.html").read_text("utf-8")

    assert "@media (max-height: 850px)" in html
    rule = html.split("@media (max-height: 850px)", 1)[1].split("\n        }\n", 1)[0]
    # The QR is the one block big enough to buy back the missing height.
    assert "#lobby-qr" in rule
    assert "180px" in rule
