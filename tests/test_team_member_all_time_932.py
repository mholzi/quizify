"""A team member's end screen shows *their* all-time line, not the team's (#932).

The end screen pushes each phone the all-time standing it drew in the lobby,
once the finished game has actually landed in the analytics file (#624). #923
changed the lookup key from the person to the **entrant**, because in team mode
the game is recorded under the team — and for a member who had never finished a
game alone, their own name found nothing and the phone showed no line at all.

What that traded away is what this file pins down. Observed on v1.18.0-RC1,
team Sofa (Anna + Bert) beating Cleo, on Anna's phone:

    lobby      All-time · 1 of 69 · 14 wins from 27 games
    end screen All-time · 23 of 70 · 1 win from 1 game

Same wording, same place on the same phone, two different people's records.
Sofa is a team that has played once and won once, so `1 win from 1 game` is the
team's row — read as a personal line it says Anna fell 22 places and lost 27
games of history.

The all-time table is a flat map keyed by **entrant name**, so a member who has
played before *is* found under their own name; the pre-#923 blank was the
first-timer case, not a broken lookup. Their record simply does not move for a
game the team was recorded under — that is what one ledger (#923) means, and it
is what the lobby line already said thirty seconds earlier.

So the key goes back to the player's own name. A member with no history of
their own gets no line, exactly as they got no line in the lobby; the wording
belongs to a person, and nothing here may put a team's numbers inside it.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT))

from custom_components.quizify.analytics import QuizifyAnalytics  # noqa: E402
from custom_components.quizify.game.state import QuizifyGameState  # noqa: E402
from custom_components.quizify.server.websocket import (  # noqa: E402
    QuizifyWebSocketHandler,
)


class _Runtime:
    def __init__(self, tmp_path: Path) -> None:
        self.data_dir = tmp_path

    async def run_in_executor(self, func, *args):  # noqa: ANN001, ANN002
        return func(*args)

    def create_task(self, coro):  # noqa: ANN001
        return asyncio.ensure_future(coro)


async def _analytics(tmp_path: Path) -> QuizifyAnalytics:
    """History before tonight, then tonight — recorded under the team.

    Anna: 3 solo games, 2 wins. Cleo: 3 solo games, 1 win. Then team **Sofa**
    plays its first game and wins it, so the map carries a "Sofa" row with
    exactly one win from one game — the row the bug put on Anna's phone.
    """
    a = QuizifyAnalytics(_Runtime(tmp_path))
    await a.load()
    for game_id, players in (
        ("g1", {"Anna": 100, "Cleo": 40}),
        ("g2", {"Anna": 100, "Cleo": 50}),
        ("g3", {"Cleo": 120, "Anna": 30}),
    ):
        await a.record_game(
            game_id=game_id, category="mixed", difficulty="medium",
            num_rounds=5, players=players, duration_seconds=100,
            player_details={},
        )
    # Tonight: the team is the entrant, exactly as ``_record_analytics`` writes
    # it since #923.
    await a.record_game(
        game_id="tonight", category="mixed", difficulty="medium", num_rounds=10,
        players={"Sofa": 112, "Cleo": 96}, duration_seconds=600,
        player_details={},
    )
    return a


def _team_room(tmp_path: Path) -> QuizifyGameState:
    """Anna + Bert as team Sofa, Cleo playing alone. Tonight's room."""
    st = QuizifyGameState(runtime=_Runtime(tmp_path), entry_id="test")
    for name in ("Anna", "Bert", "Cleo"):
        st.add_player(name)
    st.create_team("Sofa", "Anna")
    st.join_team(st.get_team_of("Anna")["team_id"], "Bert")
    return st


def _handler(game: QuizifyGameState, tmp_path: Path) -> QuizifyWebSocketHandler:
    """Every phone is on the wire; the sends are captured, not transported."""
    h = QuizifyWebSocketHandler(
        runtime=_Runtime(tmp_path), game_state_provider=lambda: game
    )
    conn = MagicMock()
    conn.is_connection_open = MagicMock(return_value=True)
    conn.send_to_player = AsyncMock()
    h._conn = conn
    return h


def _standings_sent(handler: QuizifyWebSocketHandler) -> dict[str, dict]:
    """name -> the ``all_time`` payload that phone received."""
    out: dict[str, dict] = {}
    for call in handler._conn.send_to_player.await_args_list:
        player, message = call.args
        if message.get("type") == "all_time_update":
            out[player.name] = message["all_time"]
    return out


@pytest.mark.asyncio
async def test_a_team_member_gets_their_own_record_not_the_teams(
    tmp_path: Path,
) -> None:
    """The line under the podium keeps saying what it said in the lobby.

    ``1 win from 1 game`` is team Sofa's first evening. Anna has three games
    and two wins of her own, and reading the team's numbers in a sentence that
    starts with "you" is the whole defect.
    """
    game = _team_room(tmp_path)
    analytics = await _analytics(tmp_path)
    game.set_stats_services(analytics, None)
    handler = _handler(game, tmp_path)

    await handler._dispatch_all_time_standings()

    sent = _standings_sent(handler)
    assert "Anna" in sent, "the member must still get a line"
    assert sent["Anna"] == analytics.get_player_standing("Anna")
    assert sent["Anna"]["games_played"] == 3
    assert sent["Anna"]["wins"] == 2

    team = analytics.get_player_standing("Sofa")
    assert team is not None and team["games_played"] == 1, "harness sanity"
    assert sent["Anna"] != team


@pytest.mark.asyncio
async def test_the_end_line_matches_the_one_the_lobby_showed_them(
    tmp_path: Path,
) -> None:
    """Same phone, same wording, same person — that is the contract.

    The lobby line rides the ``joined`` frame and is looked up by the player's
    own name. Whatever the end screen sends must be the same lookup, or the
    number silently changes identity between two screens of one session.
    """
    game = _team_room(tmp_path)
    analytics = await _analytics(tmp_path)
    game.set_stats_services(analytics, None)
    handler = _handler(game, tmp_path)

    await handler._dispatch_all_time_standings()

    sent = _standings_sent(handler)
    for name in ("Anna", "Bert", "Cleo"):
        assert sent.get(name) == handler._all_time_standing(name), name


@pytest.mark.asyncio
async def test_a_member_with_no_history_gets_no_line_rather_than_the_teams(
    tmp_path: Path,
) -> None:
    """Bert never finished a game alone.

    His lobby showed nothing, so his end screen shows nothing. Handing him the
    team's row instead would be the same lie as Anna's, just harder to notice.
    """
    game = _team_room(tmp_path)
    game.set_stats_services(await _analytics(tmp_path), None)
    handler = _handler(game, tmp_path)

    await handler._dispatch_all_time_standings()

    assert "Bert" not in _standings_sent(handler)


@pytest.mark.asyncio
async def test_the_solo_player_in_the_same_room_is_unchanged(
    tmp_path: Path,
) -> None:
    """The control. Cleo is her own entrant, so tonight counts for her.

    Four games, one of them tonight's — the line she reads at the end is the
    lobby's line plus this game, which is what it looked like all along.
    """
    game = _team_room(tmp_path)
    analytics = await _analytics(tmp_path)
    game.set_stats_services(analytics, None)
    handler = _handler(game, tmp_path)

    await handler._dispatch_all_time_standings()

    cleo = _standings_sent(handler)["Cleo"]
    assert cleo["games_played"] == 4
    assert cleo["wins"] == 1
    assert cleo == analytics.get_player_standing("Cleo")


@pytest.mark.asyncio
async def test_solo_mode_keeps_sending_one_standing_per_phone(
    tmp_path: Path,
) -> None:
    """No teams at all: nothing about this change may reach that room."""
    game = QuizifyGameState(runtime=_Runtime(tmp_path), entry_id="test")
    for name in ("Anna", "Cleo"):
        game.add_player(name)
    analytics = await _analytics(tmp_path)
    game.set_stats_services(analytics, None)
    handler = _handler(game, tmp_path)

    await handler._dispatch_all_time_standings()

    sent = _standings_sent(handler)
    assert set(sent) == {"Anna", "Cleo"}
    assert sent["Anna"] == analytics.get_player_standing("Anna")
    assert sent["Cleo"] == analytics.get_player_standing("Cleo")
