"""Team formation and the shared answer state (#365, part 1).

The decisions this pins down (Markus, 2026-08-12):

* teams are formed **in the lobby**, by the players themselves, in any number,
  and a player who joins none is simply not in a team;
* the answer standing when the clock stops is the team's answer — any member
  may change it until then, and the **last** change is the one that counts;
* a short lock after each change stops two members flipping the answer back
  and forth in the final seconds.

Only the model and the lobby-phase API are covered here. Scoring a team as one
participant, and the three screens, follow in part 2 — this file exists so that
part lands on something already pinned.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from custom_components.quizify.game.state import GamePhase, QuizifyGameState
from custom_components.quizify.game.team import (
    ANSWER_CHANGE_LOCK_SECONDS,
    Team,
    TeamRegistry,
)


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


# ----------------------------------------------------------------------
# The answer that stands
# ----------------------------------------------------------------------


def test_last_tap_wins() -> None:
    """Any member may overwrite the answer — the last one counts."""
    team = Team(name="Sofa", members=["Anna", "Jan"])

    assert team.set_answer(1, "Anna", now=100.0) is True
    assert team.set_answer(2, "Jan", now=100.0 + ANSWER_CHANGE_LOCK_SECONDS) is True

    assert team.current_answer == 2
    assert team.answer_by == "Jan"


def test_lock_blocks_an_immediate_second_change() -> None:
    """Two members flipping the answer in the last seconds is the failure mode."""
    team = Team(name="Sofa", members=["Anna", "Jan"])
    team.set_answer(1, "Anna", now=100.0)

    assert team.set_answer(2, "Jan", now=100.5) is False
    assert team.current_answer == 1, "the locked-out change must not apply"
    assert team.lock_remaining(now=100.5) == pytest.approx(1.5)


def test_confirming_the_standing_answer_is_never_refused() -> None:
    """Re-tapping what already stands is agreement, not a change.

    Without this, a member who taps the same answer during the lock gets told
    to wait for something that would not have changed anything.
    """
    team = Team(name="Sofa", members=["Anna", "Jan"])
    team.set_answer(1, "Anna", now=100.0)

    assert team.set_answer(1, "Jan", now=100.2) is True
    assert team.change_count == 0


def test_speed_keys_on_the_last_change() -> None:
    """``answered_at`` follows the final tap, not the first.

    The speed bonus reads this: if it kept the first timestamp, tapping
    anything instantly and thinking afterwards would be free.
    """
    team = Team(name="Sofa", members=["Anna", "Jan"])
    team.set_answer(1, "Anna", now=100.0)
    team.set_answer(2, "Jan", now=105.0)

    assert team.answered_at == 105.0


def test_reset_round_clears_the_answer_but_keeps_the_team() -> None:
    team = Team(name="Sofa", members=["Anna", "Jan"], score=42, streak=3)
    team.set_answer(1, "Anna", now=100.0)

    team.reset_round()

    assert team.current_answer is None
    assert team.answer_by is None
    assert team.score == 42, "round reset must not touch the running score"
    assert team.members == ["Anna", "Jan"]


# ----------------------------------------------------------------------
# Registry
# ----------------------------------------------------------------------


def test_joining_moves_a_player_out_of_the_old_team() -> None:
    reg = TeamRegistry()
    sofa = reg.create("Sofa", "Anna")
    kueche = reg.create("Küche", "Jan")

    reg.join(kueche.team_id, "Anna")

    assert reg.get_by_member("Anna").name == "Küche"
    assert "Anna" not in sofa.members


def test_the_last_member_leaving_dissolves_the_team() -> None:
    reg = TeamRegistry()
    team = reg.create("Sofa", "Anna")

    reg.leave("Anna")

    assert reg.get(team.team_id) is None
    assert reg.is_active is False


def test_an_unnamed_team_gets_a_suggestion_not_a_rejection() -> None:
    """A nameless team is still a valid thing to want."""
    reg = TeamRegistry()
    team = reg.create("   ", "Anna")

    assert team.name, "team must end up with some name"
    assert team.members == ["Anna"]


def test_teams_get_distinct_colors() -> None:
    reg = TeamRegistry()
    colors = {reg.create(f"T{i}", f"P{i}").color for i in range(4)}

    assert len(colors) == 4, "four teams must be distinguishable on the TV"


# ----------------------------------------------------------------------
# Lobby-only formation, through the game state
# ----------------------------------------------------------------------


def test_forming_a_team_in_the_lobby(state: QuizifyGameState) -> None:
    team = state.create_team("Sofa", "Anna")

    assert team is not None
    assert state.team_mode is True
    assert state.get_team_of("Anna")["name"] == "Sofa"


def test_a_player_without_a_team_is_not_an_error(state: QuizifyGameState) -> None:
    """Mira joins nobody. That is a legitimate way to play."""
    state.create_team("Sofa", "Anna")
    state.join_team(state.get_team_of("Anna")["team_id"], "Jan")

    assert state.get_team_of("Mira") is None
    assert len(state.team_registry.all_teams()) == 1


def test_teams_are_frozen_once_the_game_starts(state: QuizifyGameState) -> None:
    """Formation is a lobby activity — mid-game requests are refused whole.

    Half-applying one (moving the player but not their answer state) is the
    bug this prevents.
    """
    state.create_team("Sofa", "Anna")
    state.phase = GamePhase.QUESTION_ACTIVE

    assert state.create_team("Küche", "Jan") is None
    assert state.join_team(state.get_team_of("Anna")["team_id"], "Mira") is None
    assert state.leave_team("Anna") is False
    assert state.get_team_of("Anna")["name"] == "Sofa"


def test_team_mode_is_off_until_somebody_forms_one(state: QuizifyGameState) -> None:
    """No switch to forget: the mode is derived from there being a team."""
    assert state.team_mode is False

    state.create_team("Sofa", "Anna")
    assert state.team_mode is True

    state.leave_team("Anna")
    assert state.team_mode is False


def test_leaving_the_game_leaves_the_team(state: QuizifyGameState) -> None:
    state.create_team("Sofa", "Anna")
    team_id = state.get_team_of("Anna")["team_id"]
    state.join_team(team_id, "Jan")

    state.remove_player("Jan")

    assert state.team_registry.get(team_id).members == ["Anna"]


def test_wiping_the_lobby_wipes_the_teams(state: QuizifyGameState) -> None:
    state.create_team("Sofa", "Anna")

    state.clear_all_players()

    assert state.team_mode is False
    assert state.team_registry.all_teams() == []
