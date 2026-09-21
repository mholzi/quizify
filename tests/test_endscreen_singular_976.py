"""The end screen counts in the right grammatical number (issue #976).

Found by playing v1.20.0-RC4 on a real Home Assistant: a team game that the
host ended after the first question greets both phones with "1 rounds · 2
players". Nothing is miscounted — ``renderEndHeader`` had already learned to
count people rather than rows (#969) — the line simply had one string per
count, with ``{count}`` substituted into a plural.

That is the screen a host sees most often outside a finished game, because
ending early is exactly what you do while setting the room up.

The fix follows the convention the lobby and the evening tally already use
(``lobby.alsoOne``, ``dashboard.tonightWinsOne``): a flat ``…One`` key, chosen
by the renderer when the count is one. No pluralisation engine, and no
``{count}`` inside the singular — a placeholder there would reintroduce the
bug the key exists to prevent.

German only needs it for rounds ("1 Runde"); the tests still cover all three
languages, because the parity test in ``test_i18n_hardcoded_strings_625``
enforces one key set for all of them and a missing key there fails far from
here.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parent.parent
_WWW = _REPO / "custom_components" / "quizify" / "www"
_PLAYER_END = _WWW / "js" / "player-end.js"

_SINGULARS = {
    "en": {"roundsCountOne": "1 round", "playersCountOne": "1 player"},
    "de": {"roundsCountOne": "1 Runde", "playersCountOne": "1 Spieler"},
    "es": {"roundsCountOne": "1 ronda", "playersCountOne": "1 jugador"},
}


@pytest.mark.parametrize("code", sorted(_SINGULARS))
def test_every_language_ships_both_singular_forms(code: str) -> None:
    board = json.loads((_WWW / "i18n" / f"{code}.json").read_text("utf-8"))[
        "leaderboard"
    ]
    for key, expected in _SINGULARS[code].items():
        assert board.get(key) == expected, f"{code}.leaderboard.{key}"


@pytest.mark.parametrize("code", sorted(_SINGULARS))
def test_the_singular_carries_no_placeholder(code: str) -> None:
    """"1 rounds" came from substituting a number into a plural. A singular
    with ``{count}`` in it could only say the same thing again."""
    board = json.loads((_WWW / "i18n" / f"{code}.json").read_text("utf-8"))[
        "leaderboard"
    ]
    for key in _SINGULARS[code]:
        assert "{count}" not in board[key], f"{code}.leaderboard.{key}"


@pytest.mark.parametrize("code", sorted(_SINGULARS))
def test_the_plural_still_takes_the_number(code: str) -> None:
    """The counterpart guard: a plural that lost its placeholder would print
    the same sentence for two players and for twenty."""
    board = json.loads((_WWW / "i18n" / f"{code}.json").read_text("utf-8"))[
        "leaderboard"
    ]
    for key in ("roundsCount", "playersCount"):
        assert "{count}" in board[key], f"{code}.leaderboard.{key}"


def test_the_renderer_branches_on_one() -> None:
    """The branch itself, read from the module the bundle is built from.

    Deliberately narrow: both counts choose between the two keys, and the keys
    are written out rather than assembled — #798 reads ``www/`` for verbatim
    key literals, so a prefix built at runtime would make all four look dead.
    """
    source = _PLAYER_END.read_text("utf-8")
    header = re.search(r"function renderEndHeader\(.+?\n    \}", source, re.S)
    assert header, "renderEndHeader missing from player-end.js"
    body = header.group(0)

    assert "rounds === 1" in body
    assert "players === 1" in body
    for key in (
        "leaderboard.roundsCountOne",
        "leaderboard.roundsCount",
        "leaderboard.playersCountOne",
        "leaderboard.playersCount",
    ):
        assert "'%s'" % key in body, key


def test_the_shipped_bundle_carries_the_fix() -> None:
    """``player.bundle.js`` is generated and committed; a forgotten rebuild
    would ship the old line to every phone while the tests above stay green."""
    bundle = (_WWW / "js" / "player.bundle.js").read_text("utf-8")
    assert "leaderboard.roundsCountOne" in bundle
    assert "leaderboard.playersCountOne" in bundle
