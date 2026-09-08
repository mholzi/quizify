"""The duel is between entrants, not between people (#931).

Found in the v1.18.0-RC1 live test on the real HA: team **Sofa** (Anna + Bert)
beat **Cleo**, who played alone. The podium, the awards, the leaderboard and
both phones named Sofa — one participant. The head-to-head strip under it read

    HEAD TO HEAD
    Anna 13 - 6 Cleo   (last 90 days)

Anna never entered that game. ``_broadcast_head_to_head`` asked
``get_players()`` for the room, so the pair selection saw three people where
the game had two entrants, and silently picked one of the two members of the
winning team. A person's name and a 90-day record sat directly under a team
victory with nothing saying they belong to a different scope.

This is the second half of the screenshot #923 was filed from. #925 closed the
score ledger and the ``Tonight`` line went with it; the duel is the #613
feature and was never in that fix's scope, so it survived.

**The rule chosen here:** the duel compares whoever the ranking is about.
``get_ranked_participants()`` is the same list the podium, the leaderboard,
the answer counter (#835) and the recorded game (#923) are built from, and in
solo mode it *is* ``get_players()`` — so nothing changes for a room without
teams. In team mode the candidates become the teams and the guests who joined
none, which is exactly the set the analytics record has held since #925. A
team with no shared history simply has no duel, and the line goes away.

**And it has to go away on every surface.** Four draw the strip — the TV lobby
and TV end screen (``#lobby-h2h`` / ``#end-h2h``), the admin lobby and admin
end screen — all four fed by the one shared sender. The sender used to stay
silent when there was no duel, which is fine for a screen that never had one
and wrong for the lobby, where the line is recomputed as the room changes: two
entrants on screen a second ago are one team now. So it sends a frame either
way, and both renderers already treat a frame without ``left``/``right`` as
"hide" — no new element, no new text. Teams also do not move the roster, so
the three team handlers recompute it themselves; without that the lobby line
stood untouched from before the team existed.
"""

from __future__ import annotations

import asyncio
import re
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.quizify.analytics import QuizifyAnalytics
from custom_components.quizify.game.state import GamePhase, QuizifyGameState
from custom_components.quizify.server.connection import ConnectionManager
from custom_components.quizify.server.websocket import QuizifyWebSocketHandler

_REPO_ROOT = Path(__file__).resolve().parent.parent
_CC = _REPO_ROOT / "custom_components" / "quizify"


class _Runtime:
    def __init__(self, tmp_path: Path) -> None:
        self.data_dir = tmp_path

    async def run_in_executor(self, func, *args):  # noqa: ANN001, ANN002
        return func(*args)

    def create_task(self, coro):  # noqa: ANN001
        return asyncio.ensure_future(coro)


def _analytics(tmp_path: Path, games: list[dict[str, int]]) -> QuizifyAnalytics:
    """Analytics whose detailed history is exactly these score maps.

    Built without touching disk: the duel only ever reads ``games``.
    """
    impl = QuizifyAnalytics.__new__(QuizifyAnalytics)
    impl._data = {"games": [{"player_scores": scores} for scores in games]}
    return impl


def _ws() -> MagicMock:
    ws = MagicMock()
    ws.closed = False
    ws.send_json = AsyncMock()
    return ws


@pytest.fixture
def game(tmp_path: Path) -> QuizifyGameState:
    return QuizifyGameState(runtime=_Runtime(tmp_path), entry_id="test")


@pytest.fixture
def handler(game: QuizifyGameState, tmp_path: Path) -> QuizifyWebSocketHandler:
    h = QuizifyWebSocketHandler(
        runtime=_Runtime(tmp_path), game_state_provider=lambda: game
    )
    h._conn = ConnectionManager(_Runtime(tmp_path), lambda: game)
    h._conn.broadcast = AsyncMock()
    h._conn.send = AsyncMock()
    h._conn.send_error = AsyncMock()
    h._conn.broadcast_to_admins_and_dashboards = AsyncMock()
    return h


def _duels(handler: QuizifyWebSocketHandler) -> list[dict[str, Any]]:
    return [
        call.args[0]
        for call in handler._conn.broadcast_to_admins_and_dashboards.await_args_list
        if call.args and call.args[0].get("type") == "head_to_head"
    ]


def _the_room_from_the_live_test(game: QuizifyGameState) -> None:
    """Anna + Bert open team Sofa; Cleo plays alone. Two entrants."""
    for name in ("Anna", "Bert", "Cleo"):
        game.add_player(name)
    game.create_team("Sofa", "Anna")
    game.join_team(game.get_team_of("Anna")["team_id"], "Bert")


# The evenings Anna and Cleo played as individuals, before teams existed.
_HISTORY = [
    {"Anna": 90, "Cleo": 40},
    {"Anna": 80, "Cleo": 20},
    {"Anna": 10, "Cleo": 70},
]


# ---------------------------------------------------------------------------
# The end screen — the defect as reported
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_team_victory_does_not_get_a_members_name_under_it(
    handler: QuizifyWebSocketHandler, game: QuizifyGameState, tmp_path: Path
) -> None:
    """The reported screen: "Anna 13 - 6 Cleo" under a podium saying Sofa."""
    _the_room_from_the_live_test(game)
    game.set_stats_services(_analytics(tmp_path, _HISTORY), None)
    game.phase = GamePhase.FINALE

    await handler._dispatch_end_head_to_head()

    (frame,) = _duels(handler)
    assert frame["at"] == "finale"
    assert "left" not in frame and "right" not in frame, (
        "Anna is not an entrant of this game — she is one of two members of one"
    )


@pytest.mark.asyncio
async def test_entrants_that_have_met_keep_their_duel(
    handler: QuizifyWebSocketHandler, game: QuizifyGameState, tmp_path: Path
) -> None:
    """Suppression is not the rule — the rule is *whoever the game is between*.

    Team Sofa has played Cleo before, so the strip is about this game and says
    so with the names the podium uses.
    """
    _the_room_from_the_live_test(game)
    game.set_stats_services(
        _analytics(
            tmp_path,
            [
                {"Sofa": 100, "Cleo": 60},
                {"Sofa": 40, "Cleo": 90},
                {"Sofa": 70, "Cleo": 10},
            ],
        ),
        None,
    )
    game.phase = GamePhase.FINALE

    await handler._dispatch_end_head_to_head()

    (frame,) = _duels(handler)
    assert (frame["left"], frame["right"]) == ("Sofa", "Cleo")
    assert (frame["left_wins"], frame["right_wins"], frame["games"]) == (2, 1, 3)


@pytest.mark.asyncio
async def test_the_guest_who_joined_no_team_is_still_an_entrant(
    handler: QuizifyWebSocketHandler, game: QuizifyGameState, tmp_path: Path
) -> None:
    """A team of one is not an error state (#365) — Cleo keeps her own row, so
    her own history is still eligible."""
    _the_room_from_the_live_test(game)
    game.add_player("Dora")
    game.set_stats_services(
        _analytics(tmp_path, [{"Cleo": 90, "Dora": 40}, {"Cleo": 20, "Dora": 70}]),
        None,
    )
    game.phase = GamePhase.FINALE

    await handler._dispatch_end_head_to_head()

    (frame,) = _duels(handler)
    assert {frame["left"], frame["right"]} == {"Cleo", "Dora"}


# ---------------------------------------------------------------------------
# The lobby — the same rule, and the line has to leave
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_the_lobby_shows_the_duel_while_both_play_alone(
    handler: QuizifyWebSocketHandler, game: QuizifyGameState, tmp_path: Path
) -> None:
    """Unchanged: before a team exists they are both individual entrants."""
    for name in ("Anna", "Bert", "Cleo"):
        game.add_player(name)
    game.set_stats_services(_analytics(tmp_path, _HISTORY), None)

    await handler._send_head_to_head(game)

    (frame,) = _duels(handler)
    assert frame["at"] == "lobby"
    assert (frame["left"], frame["right"]) == ("Anna", "Cleo")
    assert (frame["left_wins"], frame["right_wins"]) == (2, 1)


@pytest.mark.asyncio
async def test_opening_a_team_takes_the_lobby_line_off_the_screen(
    handler: QuizifyWebSocketHandler, game: QuizifyGameState, tmp_path: Path
) -> None:
    """The moment Anna is in a team she stops being an entrant, and the line
    that names her has to go — a team change does not move the roster, so
    nothing else was recomputing it."""
    for name in ("Anna", "Bert", "Cleo"):
        game.add_player(name)
    game.set_stats_services(_analytics(tmp_path, _HISTORY), None)
    ws = _ws()
    handler._player_for_ws = lambda gs, w: gs.get_player("Anna")  # type: ignore[assignment]

    await handler._handle_create_team(ws, {"name": "Sofa"}, game)

    (frame,) = _duels(handler)
    assert frame == {"type": "head_to_head", "at": "lobby"}, (
        "silence would have left 'Anna 13 - 6 Cleo' standing on the TV"
    )


@pytest.mark.asyncio
async def test_leaving_the_team_brings_the_duel_back(
    handler: QuizifyWebSocketHandler, game: QuizifyGameState, tmp_path: Path
) -> None:
    """The rule holds in both directions: the last one out dissolves the team
    and the room is individuals again."""
    for name in ("Anna", "Bert", "Cleo"):
        game.add_player(name)
    game.create_team("Sofa", "Anna")
    game.set_stats_services(_analytics(tmp_path, _HISTORY), None)
    ws = _ws()
    handler._player_for_ws = lambda gs, w: gs.get_player("Anna")  # type: ignore[assignment]

    await handler._handle_leave_team(ws, {}, game)

    (frame,) = _duels(handler)
    assert (frame["left"], frame["right"]) == ("Anna", "Cleo")


@pytest.mark.asyncio
async def test_joining_a_team_recomputes_it_too(
    handler: QuizifyWebSocketHandler, game: QuizifyGameState, tmp_path: Path
) -> None:
    """All three team handlers, not just the one that was easy to reach."""
    for name in ("Anna", "Bert", "Cleo"):
        game.add_player(name)
    game.create_team("Sofa", "Bert")
    game.set_stats_services(_analytics(tmp_path, _HISTORY), None)
    ws = _ws()
    handler._player_for_ws = lambda gs, w: gs.get_player("Anna")  # type: ignore[assignment]

    await handler._handle_join_team(
        ws, {"team_id": game.get_team_of("Bert")["team_id"]}, game
    )

    (frame,) = _duels(handler)
    assert frame == {"type": "head_to_head", "at": "lobby"}


# ---------------------------------------------------------------------------
# Solo mode is the control
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_room_without_teams_is_untouched(
    handler: QuizifyWebSocketHandler, game: QuizifyGameState, tmp_path: Path
) -> None:
    for name in ("Anna", "Bert", "Cleo"):
        game.add_player(name)
    game.set_stats_services(_analytics(tmp_path, _HISTORY), None)
    game.phase = GamePhase.FINALE

    await handler._dispatch_end_head_to_head()

    (frame,) = _duels(handler)
    assert frame == {
        "type": "head_to_head",
        "at": "finale",
        "left": "Anna",
        "right": "Cleo",
        "left_wins": 2,
        "right_wins": 1,
        "games": 3,
    }


def test_participants_are_what_the_sender_asks_for() -> None:
    """A guard, because this is the sixth reader of the same defect class.

    #668 wager, #800 reaction bonus, #804 hot seat, #835 answer progress and
    #923 the score ledger were each a call site that asked the room for people
    when the game was about entrants. Naming ``get_players()`` here again is
    how that comes back.
    """
    source = (_CC / "server" / "websocket.py").read_text("utf-8")
    body = source.split("async def _broadcast_head_to_head", 1)[1].split(
        "\n    def ", 1
    )[0]
    body = re.sub(r"#.*$", "", body, flags=re.M)

    assert "get_ranked_participants()" in body
    assert "get_players()" not in body
    # Still the one shared sender for all four surfaces (#613).
    assert "broadcast_to_admins_and_dashboards" in body


def test_every_team_handler_recomputes_the_lobby_duel() -> None:
    """Create, join and leave all change who the entrants are."""
    source = (_CC / "server" / "websocket.py").read_text("utf-8")
    for name in ("_handle_create_team", "_handle_join_team", "_handle_leave_team"):
        body = source.split(f"async def {name}", 1)[1].split("\n    async def ", 1)[0]
        assert "_send_head_to_head" in body, name


def test_both_renderers_read_an_empty_frame_as_hide() -> None:
    """The suppression uses markup that already exists — no new element, no new
    text. Break this and the server would be clearing a line nobody hides."""
    for path, hide in (
        (_CC / "www" / "js" / "dashboard.js", "classList.add('hidden')"),
        (_CC / "www" / "js" / "admin.js", "display = 'none'"),
    ):
        script = path.read_text("utf-8")
        body = script.split("function handleHeadToHead(", 1)[1].split("\n    }", 1)[0]
        guard = body.split("if (!msg || !msg.left || !msg.right)", 1)[1][:200]
        assert hide in guard, path.name
