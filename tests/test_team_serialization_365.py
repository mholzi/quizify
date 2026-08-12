"""Teams ride the existing leaderboard, unchanged (#365, part 2).

The decision "a team behaves in the ranking exactly like a single player" is
only worth anything if the code takes it literally: the dashboard rows, the
reveal leaderboard and the finale podium must be the *same* code fed teams
instead of players. These tests hold that line — the day someone adds a
team-shaped rendering path, the reason for it should be a failing test here,
not a hunch.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from custom_components.quizify.game.state import QuizifyGameState
from custom_components.quizify.game.team import Team
from custom_components.quizify.server.serializers import serialize_leaderboard


def _ws() -> MagicMock:
    ws = MagicMock()
    ws.closed = False
    return ws


class _Runtime:
    def __init__(self, tmp_path: Path) -> None:
        self.data_dir = tmp_path

    async def run_in_executor(self, func, *args):  # noqa: ANN001, ANN002
        return func(*args)

    def create_task(self, coro):  # noqa: ANN001
        import asyncio

        return asyncio.ensure_future(coro)


@pytest.fixture
def state(tmp_path: Path) -> QuizifyGameState:
    st = QuizifyGameState(runtime=_Runtime(tmp_path), entry_id="test")
    for name in ("Anna", "Jan", "Mira"):
        st.add_player(name, _ws())
    return st


def test_a_team_serializes_through_the_player_leaderboard() -> None:
    """No team-specific serializer exists, and none should."""
    sofa = Team(name="Sofa", members=["Anna", "Jan"], score=54, streak=3)
    sofa.round_history = ["correct", "correct", "wrong"]
    kueche = Team(name="Küche", members=["Tim"], score=41)

    rows = serialize_leaderboard([sofa, kueche])

    assert [r["name"] for r in rows] == ["Sofa", "Küche"]
    assert rows[0]["score"] == 54
    assert rows[0]["streak"] == 3
    assert rows[0]["rank"] == 1
    assert rows[1]["rank"] == 2


def test_tied_teams_share_a_rank() -> None:
    """The competition ranking from #308 applies to teams for free."""
    rows = serialize_leaderboard(
        [Team(name="Sofa", score=30), Team(name="Küche", score=30)]
    )

    assert [r["rank"] for r in rows] == [1, 1]


def test_the_ranking_is_teams_in_team_mode(state: QuizifyGameState) -> None:
    """Members are represented by their team — and only by their team."""
    state.create_team("Sofa", "Anna")
    state.join_team(state.get_team_of("Anna")["team_id"], "Jan")
    state.leave_team("Mira")  # no-op; Mira never joined one

    names = [p.name for p in state.get_ranked_participants()]

    assert "Anna" not in names and "Jan" not in names
    assert names[0] == "Sofa"


def test_a_player_in_no_team_keeps_their_own_row(state: QuizifyGameState) -> None:
    """A lone player is a team of one, not an error state.

    Found by playing it: with the teams returned alone, the one guest who
    stayed solo vanished from the leaderboard, the TV and the podium.
    """
    state.create_team("Sofa", "Anna")
    state.join_team(state.get_team_of("Anna")["team_id"], "Jan")

    names = [p.name for p in state.get_ranked_participants()]

    assert sorted(names) == ["Mira", "Sofa"]


def test_the_ranking_is_players_without_teams(state: QuizifyGameState) -> None:
    """An ordinary game is untouched — that is what makes the mode safe."""
    names = sorted(p.name for p in state.get_ranked_participants())

    assert names == ["Anna", "Jan", "Mira"]


def test_the_leaderboard_follows_the_mode(state: QuizifyGameState) -> None:
    before = {row["name"] for row in state.get_leaderboard()}
    state.create_team("Sofa", "Anna")

    after = {row["name"] for row in state.get_leaderboard()}

    assert before == {"Anna", "Jan", "Mira"}
    assert after == {"Sofa", "Jan", "Mira"}, (
        "Anna is now represented by her team and must not also appear as "
        "herself — a leaderboard carrying both would double-count her. The "
        "two who joined nothing keep their own rows."
    )


def test_a_team_carries_the_whole_finale_contract() -> None:
    """The end screen reads these keys by name; a missing one shows as 0.

    v1.1.4 lost an evening to exactly that, so the shape is asserted rather
    than assumed.
    """
    team = Team(name="Sofa", members=["Anna"], score=12, streak=2)
    team.max_streak = 4
    team.round_history = ["correct", "wrong"]
    team.powerups_used = 1

    row = serialize_leaderboard([team])[0]

    assert row["best_streak"] == 4
    assert row["rounds_played"] == 2
    assert row["powerups_used"] == 1
    assert row["submitted"] is False
    assert row["is_admin"] is False
    assert "color" in row
