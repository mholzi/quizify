"""The season standing reaches the end screen — and only once it is true (#624).

The all-time table (#371) rendered in the lobby only: before the game, the
moment it is least interesting. The end screen, where "you just overtook Anna"
belongs, never showed it.

**The issue calls this "a serializer field plus one render line" because the
server already computes the standing. It is not.** `_record_analytics` writes
the finished game in a *detached* task — deliberately, so a slow disk cannot
delay the end screen. The finale therefore goes out while the all-time table
still describes the state BEFORE this game.

Attaching the standing to the finale would show the player a rank that predates
the round they just watched, contradicting the podium on screen. A line that
lies is worse than no line.

So the standing is sent as its own later message, fired when the record lands.
A player who has already closed the tab never receives it, which is correct.
"""

from __future__ import annotations

import re
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
_CC = _REPO_ROOT / "custom_components" / "quizify"
_WWW = _CC / "www"


def _without_comments(source: str) -> str:
    source = re.sub(r"/\*.*?\*/", "", source, flags=re.S)
    return re.sub(r"^\s*//.*$", "", source, flags=re.M)


def _py_function(source: str, signature: str) -> str:
    start = source.index(signature)
    lines = source[start:].splitlines()
    out = [lines[0]]
    for line in lines[1:]:
        if line and not line[0].isspace():
            break
        if re.match(r"^    (async )?def ", line):
            break
        out.append(line)
    return "\n".join(out)


def test_the_event_fires_only_after_the_record_succeeds() -> None:
    """Firing it regardless would send the pre-game standing on a failed write.

    The `return` in the except branch is the whole guarantee here.
    """
    source = (_CC / "game" / "state.py").read_text("utf-8")
    body = _py_function(source, "    def _record_analytics(self)")

    assert 'self._fire_broadcast("analytics_recorded")' in body
    except_at = body.index("Failed to record analytics")
    fire_at = body.index('_fire_broadcast("analytics_recorded")')
    assert except_at < fire_at, "the fire must sit after the failure path"
    tail = body[except_at:fire_at]
    assert "return" in tail, "a failed record must not fire the event"


def test_the_dispatcher_is_registered_for_that_event() -> None:
    source = (_CC / "server" / "websocket.py").read_text("utf-8")

    assert '"analytics_recorded": self._dispatch_all_time_standings' in source


def test_each_player_gets_their_own_standing() -> None:
    """A broadcast would be wrong: the interesting number is your own rank."""
    source = (_CC / "server" / "websocket.py").read_text("utf-8")
    body = _without_comments(
        _py_function(source, "    async def _dispatch_all_time_standings(self)")
    )

    assert "for player in game_state.get_players()" in body
    assert "self._all_time_standing(player.name)" in body
    assert '"type": "all_time_update"' in body
    # Asserts the absent CALL, not the absent word: the docstring above
    # explains why this is not a broadcast, and `_without_comments` strips
    # comments, not docstrings. Fourth time today an assertion nearly tripped
    # over the prose the fix writes about itself — so pin behaviour, never
    # vocabulary.
    assert "self._conn.broadcast(" not in body


def test_it_skips_closed_sockets_and_missing_standings() -> None:
    """Fail-soft, exactly as the join path is.

    A first-timer has no standing, and someone who closed the tab has no
    socket; neither may raise on what is decoration.
    """
    body = _without_comments(
        _py_function(
            (_CC / "server" / "websocket.py").read_text("utf-8"),
            "    async def _dispatch_all_time_standings(self)",
        )
    )

    assert "player.ws is None or player.ws.closed" in body
    assert "if standing is None" in body


def test_the_end_screen_has_a_slot_that_starts_hidden() -> None:
    """Hidden until the message lands: the standing arrives after the finale,
    and an empty slot beats a rank that predates the game."""
    html = (_WWW / "player.html").read_text("utf-8")
    slot = html.split('id="end-alltime"', 1)[0].rsplit("<p", 1)[1] + 'id="end-alltime"'

    assert "hidden" in slot
    assert "pl-allTime" in slot


def test_the_renderer_can_target_the_end_screen() -> None:
    """Same renderer, same phrasing, including the no-win variant.

    Writing a second renderer would have meant two places to keep the
    zero-wins wording right.
    """
    source = (_WWW / "js" / "player-lobby.js").read_text("utf-8")

    assert "function renderAllTime(standing, elementId)" in source
    assert "elementId || 'pl-alltime'" in source


def test_the_router_sends_it_to_the_end_screen_slot() -> None:
    core = (_WWW / "js" / "player-core.js").read_text("utf-8")
    bundle = (_WWW / "js" / "player.bundle.js").read_text("utf-8")

    assert "case 'all_time_update':" in core
    assert "lobby.renderAllTime(msg.all_time, 'end-alltime')" in core
    assert "case 'all_time_update':" in bundle
