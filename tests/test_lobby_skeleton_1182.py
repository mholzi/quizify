"""The host lobby before its first roster frame is a skeleton (#1182, L8).

The lobby can be opened before the socket is up (slow network, Nabu Casa,
HA still booting). Until the first frame it showed the HTML placeholder as
the truth: "0" players and "Ready at 2 players · still need 2 more" — a "2"
that even contradicts the real minimum of 1. The states review found exactly
that line in a screenshot, and it was the tell that no roster had ever been
drawn.
"""

from __future__ import annotations

import re
from pathlib import Path

_WWW = Path(__file__).resolve().parent.parent / "custom_components" / "quizify" / "www"


def _tag(html: str, element_id: str) -> str:
    m = re.search(r'<div[^>]*id="' + element_id + r'"[^>]*>', html)
    assert m, element_id
    return m.group(0)


def test_the_countdown_placeholder_starts_hidden() -> None:
    html = (_WWW / "admin.html").read_text("utf-8")
    assert "hidden" in _tag(html, "lobby-countdown").split('class="', 1)[1].split('"')[0]


def test_the_empty_roster_claim_starts_hidden() -> None:
    html = (_WWW / "admin.html").read_text("utf-8")
    assert "hidden" in _tag(html, "lobby-players-empty")


def test_the_roster_starts_as_a_skeleton() -> None:
    html = (_WWW / "admin.html").read_text("utf-8")
    chips = html[html.index('id="lobby-player-chips"') :]
    chips = chips[: chips.index('id="lobby-players-empty"')]
    assert chips.count('class="lobby-e-row-skeleton"') >= 1
    assert 'aria-busy="true"' in _tag(html, "lobby-player-chips")


def test_the_first_roster_replaces_the_skeleton() -> None:
    js = (_WWW / "js" / "admin.js").read_text("utf-8")
    body = js[js.index("function renderLobbyPlayers(players) {") :]
    body = body[: body.index("applyTeamModeSetupNotes();")]
    assert "removeAttribute('aria-busy')" in body
    # Both branches overwrite the list, so the skeleton bars go with it.
    assert body.count("els.lobbyPlayerChips.innerHTML =") == 2
    # …and the countdown/empty line are decided by the roster, not the HTML.
    assert "countdownEl.classList.toggle('hidden'" in body
    assert "els.lobbyPlayersEmpty.classList.toggle('hidden'" in body
