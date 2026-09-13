"""A team is not dissolved mid-game when its last member is removed (#936).

``remove_player`` always detached the player from their team, and
``TeamRegistry.leave`` pops a team once it is empty. The disconnect path calls
``remove_player`` after the grace period in any phase, so a team of one whose
phone slept for a minute lost its row and every point. If it was the only team,
``team_mode`` flipped off — and since #923 stopped writing ``player.score`` for
members, the leaderboard, podium and leader sensor showed 0 for everyone.

Mid-game the team set is frozen; the leaver's name stays in ``Team.members``
until the room is back in the lobby.
"""

from __future__ import annotations

from pathlib import Path

from custom_components.quizify.game.phase_controller import GamePhase
from custom_components.quizify.game.state import QuizifyGameState


class _Runtime:
    def __init__(self, tmp_path: Path) -> None:
        self.data_dir = tmp_path

    async def run_in_executor(self, func, *args):  # noqa: ANN001, ANN002
        return func(*args)

    def create_task(self, coro):  # noqa: ANN001
        coro.close()
        return None


def _lobby(tmp_path: Path) -> QuizifyGameState:
    """One team of one — Sofa (Anna) — and a guest, Eva, who joined none."""
    st = QuizifyGameState(runtime=_Runtime(tmp_path), entry_id="test")
    for name in ("Anna", "Eva"):
        st.add_player(name)
    st.create_team("Sofa", "Anna")
    return st


def _start(st: QuizifyGameState) -> None:
    st.start_game(
        category="picture-round-en",
        difficulty="easy",
        num_rounds=3,
        language="en",
        lightning_enabled=False,
        hot_seat_enabled=False,
    )
    st.start_next_question()


def _sofa(st: QuizifyGameState):  # noqa: ANN202
    return next(
        (t for t in st.team_registry.all_teams() if t.name == "Sofa"), None
    )


def test_team_of_one_keeps_its_row_when_removed_mid_question(
    tmp_path: Path,
) -> None:
    st = _lobby(tmp_path)
    _start(st)
    assert st.phase == GamePhase.QUESTION_ACTIVE
    _sofa(st).score = 112

    st.remove_player("Anna")

    team = _sofa(st)
    assert team is not None, "the team was dissolved mid-game"
    assert team.score == 112
    assert team.members == ["Anna"]
    assert st.team_mode is True
    assert team in st.get_ranked_participants()
    leader = st.leader
    assert leader is not None
    assert leader.name == "Sofa"
    assert leader.score == 112


def test_removed_member_who_rejoins_mid_game_is_back_on_the_team(
    tmp_path: Path,
) -> None:
    """Tokens are cleared on removal, so the phone comes back as a fresh join."""
    st = _lobby(tmp_path)
    _start(st)
    st.remove_player("Anna")

    ok, _err = st.add_player("Anna")

    assert ok
    team_of = st.get_team_of("Anna")
    assert team_of is not None
    assert team_of["name"] == "Sofa"


def test_lobby_removal_still_dissolves_an_empty_team(tmp_path: Path) -> None:
    """The #365 rule is unchanged while teams are still being formed."""
    st = _lobby(tmp_path)

    st.remove_player("Anna")

    assert _sofa(st) is None
    assert st.team_mode is False


def test_departed_member_leaves_the_team_back_in_the_lobby(
    tmp_path: Path,
) -> None:
    """Kept for the game, not for the rematch: no zombie team row (#799)."""
    st = _lobby(tmp_path)
    st.add_player("Jan")
    st.join_team(st.get_team_of("Anna")["team_id"], "Jan")
    st.create_team("Cara", "Eva")
    _start(st)

    st.remove_player("Anna")
    st.remove_player("Eva")
    assert _sofa(st).members == ["Anna", "Jan"]

    st.end_game()
    st.reset_to_lobby()

    assert _sofa(st).members == ["Jan"]
    assert all(t.name != "Cara" for t in st.team_registry.all_teams())
