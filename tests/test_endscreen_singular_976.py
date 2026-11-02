"""The end screen counts in the right grammatical number (issue #976).

Found by playing v1.20.0-RC4 on a real Home Assistant: a team game that the
host ended after the first question greets both phones with "1 rounds · 2
players". Nothing is miscounted — ``renderEndHeader`` had already learned to
count people rather than rows (#969) — the line simply had one string per
count, with ``{count}`` substituted into a plural.

That is the screen a host sees most often outside a finished game, because
ending early is exactly what you do while setting the room up.

#976 fixed it with a flat ``…One`` key per count. Since #1177 the bundles carry
a real plural rule (``{count, plural, one{…} other{…}}``) and the renderer asks
for one key; the tests check what ``t()`` actually prints for one and for many,
in all three languages.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from tests.i18n_node import HAVE_NODE, render

_REPO = Path(__file__).resolve().parent.parent
_WWW = _REPO / "custom_components" / "quizify" / "www"
_PLAYER_END = _WWW / "js" / "player-end.js"

# Since #1177 the end screen counts questions ("Frage", not "Runde").
_EXPECTED = {
    "en": {"rounds": ("1 question", "3 questions"), "players": ("1 player", "2 players")},
    "de": {"rounds": ("1 Frage", "3 Fragen"), "players": ("1 Spieler", "2 Spieler")},
    "es": {"rounds": ("1 pregunta", "3 preguntas"), "players": ("1 jugador", "2 jugadores")},
}


@pytest.mark.skipif(not HAVE_NODE, reason="node not installed")
@pytest.mark.parametrize("code", sorted(_EXPECTED))
def test_one_and_many_read_right_in_every_language(code: str) -> None:
    got = render([
        (code, "game.questionsCount", {"count": 1}),
        (code, "game.questionsCount", {"count": 3}),
        (code, "leaderboard.playersCount", {"count": 1}),
        (code, "leaderboard.playersCount", {"count": 2}),
    ])
    want = list(_EXPECTED[code]["rounds"]) + list(_EXPECTED[code]["players"])
    assert got == want


def test_the_renderer_hands_the_count_to_one_key() -> None:
    """No `…One` branch left in renderEndHeader: the plural rule decides."""
    source = _PLAYER_END.read_text("utf-8")
    header = re.search(r"function renderEndHeader\(.+?\n    \}", source, re.S)
    assert header, "renderEndHeader missing from player-end.js"
    body = header.group(0)
    assert "CountOne" not in body
    assert "'leaderboard.playersCount'" in body


def test_the_shipped_bundle_carries_the_fix() -> None:
    """``player.bundle.js`` is generated and committed; a forgotten rebuild
    would ship the old line to every phone while the tests above stay green."""
    bundle = (_WWW / "js" / "player.bundle.js").read_text("utf-8")
    assert "leaderboard.playersCountOne" not in bundle
    assert "'leaderboard.playersCount'" in bundle
