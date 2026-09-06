"""The host screen shows the teams, and says what they do to the toggles (#804).

Teams are formed by the guests **in the lobby**, i.e. after the host has
already answered the setup questions. The television has grouped its lobby by
team since #365; ``admin.js`` rendered a flat list of names and mentioned teams
nowhere outside the lightning recap. So the one person running the evening was
the only one in the room who could not see that the game had gone into team
mode — and therefore could not see why the Hot Seat auction and the final
wager, both ticked on the setup screen and both described there in full, would
behave differently.

Two halves, pinned separately:

* the lobby roster groups by team, using the same shape ``dashboard.html``
  uses, with the ordinary roster card inside a group so the kick button and the
  host crown keep working;
* the Hot Seat and wager rows carry a note that appears the moment a team
  exists, so the difference is explained before Start rather than by the
  silence where the auction should have been.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parent.parent
_WWW = _REPO / "custom_components" / "quizify" / "www"

ADMIN_JS = (_WWW / "js" / "admin.js").read_text(encoding="utf-8")
ADMIN_HTML = (_WWW / "admin.html").read_text(encoding="utf-8")
STYLES = (_WWW / "css" / "styles.css").read_text(encoding="utf-8")

LANGUAGES = ("de", "en", "es")
TEAM_NOTE_KEYS = ("hotSeatTeamNote", "wagerTeamNote")


# ---------------------------------------------------------------------------
# The roster
# ---------------------------------------------------------------------------


def test_admin_js_follows_teams_update() -> None:
    """A team can form or dissolve with the roster unchanged (#365).

    ``teams_update`` is the only frame that reports it, so a host screen that
    ignores it keeps painting a lobby that stopped being true.
    """
    assert "case 'teams_update':" in ADMIN_JS


def test_admin_js_adopts_the_teams_carried_by_the_roster_frame() -> None:
    """``player_joined`` / ``player_left`` carry ``teams`` for a reason (#365).

    A player leaving also leaves their team, and the last one out dissolves it.
    """
    joined = ADMIN_JS.index("case 'player_joined':")
    tail = ADMIN_JS[joined : joined + 700]
    assert "msg.teams" in tail


def test_admin_js_groups_the_lobby_by_team() -> None:
    """The grouping the television has used since #365, on the host screen."""
    assert "lobby-e-team" in ADMIN_JS
    assert "team.members" in ADMIN_JS


def test_a_player_in_no_team_keeps_their_own_row() -> None:
    """A player in no team is a team of one, not a leftover group (#365)."""
    assert "inTeam[" in ADMIN_JS


def test_the_team_group_is_styled() -> None:
    """Without the rules the group is an unlabelled run of cards."""
    assert ".lobby-e-team {" in STYLES
    assert ".lobby-e-team-name {" in STYLES


# ---------------------------------------------------------------------------
# The note
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("node_id", ("hot-seat-team-note", "wager-team-note"))
def test_the_two_rows_carry_a_team_note(node_id: str) -> None:
    assert f'id="{node_id}"' in ADMIN_HTML


@pytest.mark.parametrize("node_id", ("hot-seat-team-note", "wager-team-note"))
def test_the_note_starts_hidden(node_id: str) -> None:
    """No team, no note — most games are not team games."""
    marker = f'id="{node_id}"'
    start = ADMIN_HTML.rindex("<p", 0, ADMIN_HTML.index(marker))
    assert "hidden" in ADMIN_HTML[start : ADMIN_HTML.index(marker)]


def test_admin_js_reveals_the_notes_in_team_mode() -> None:
    assert "hot-seat-team-note" in ADMIN_JS
    assert "wager-team-note" in ADMIN_JS


def test_the_note_is_styled() -> None:
    assert ".vF-team-note {" in STYLES


@pytest.mark.parametrize("language", LANGUAGES)
@pytest.mark.parametrize("key", TEAM_NOTE_KEYS)
def test_the_note_is_translated(language: str, key: str) -> None:
    """A hardcoded English sentence on a German host screen is the #625 bug."""
    bundle = json.loads(
        (_WWW / "i18n" / f"{language}.json").read_text(encoding="utf-8")
    )
    text = bundle["setup"]["eigene"][key]
    assert isinstance(text, str) and text.strip()


@pytest.mark.parametrize("key", TEAM_NOTE_KEYS)
def test_the_markup_asks_for_the_translated_string(key: str) -> None:
    assert f'data-i18n="setup.eigene.{key}"' in ADMIN_HTML
