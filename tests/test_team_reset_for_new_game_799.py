"""A rematch starts every team at zero (#799).

``start_game`` reset every ``PlayerSession`` and the power-up manager, and
``reset_to_lobby`` did the same — neither touched the ``TeamRegistry``. Only
``clear_all_players`` (the explicit reset-game button) ever cleared a team's
score, so the one-tap rematch from the finale opened game 2 on game 1's
leaderboard: Sofa 120 / Cara 0 before a single question, awards counting the
old rounds, and the streak bonus carrying on where it left off.

The second half is the same defect one layer down: the stale-player prune in
``start_game`` called the *registry's* ``remove_player`` rather than the game
state's, so a player whose phone stayed dark was dropped from the roster but
left behind in ``Team.members``. A team whose members had all gone dark
survived as a zombie row that timed out every round.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import MagicMock

from custom_components.quizify.game.state import QuizifyGameState


class _Runtime:
    def __init__(self, tmp_path: Path) -> None:
        self.data_dir = tmp_path

    async def run_in_executor(self, func, *args):  # noqa: ANN001, ANN002
        return func(*args)

    def create_task(self, coro):  # noqa: ANN001
        return asyncio.ensure_future(coro)


def _ws() -> MagicMock:
    ws = MagicMock()
    ws.closed = False
    return ws


def _team_game(tmp_path: Path, rounds: int = 3) -> QuizifyGameState:
    """Two teams — Sofa (Anna, Jan) and Cara (Mira) — mid-game."""
    st = QuizifyGameState(runtime=_Runtime(tmp_path), entry_id="test")
    for name in ("Anna", "Jan", "Mira"):
        st.add_player(name, _ws())
    st.create_team("Sofa", "Anna")
    st.join_team(st.get_team_of("Anna")["team_id"], "Jan")
    st.create_team("Cara", "Mira")
    st.start_game(
        category="picture-round-en",
        difficulty="easy",
        num_rounds=rounds,
        language="en",
        lightning_enabled=False,
        hot_seat_enabled=False,
    )
    return st


def _by_name(game: QuizifyGameState, name: str):  # noqa: ANN202
    return next(t for t in game.team_registry.all_teams() if t.name == name)


def _dirty(team) -> None:  # noqa: ANN001
    """Give a team the tallies three played rounds would have left behind."""
    team.score = 120
    team.streak = 4
    team.max_streak = 4
    team.round_history = ["correct", "correct", "correct"]
    team.round_scores = [40, 40, 40]
    team.answer_times = [2.0, 3.0, 1.5]
    team.hard_score = 80
    team.freezes_used = 1
    team.powerups_used = 2
    team.rounds_played = 3


def _assert_clean(team) -> None:  # noqa: ANN001
    assert team.score == 0, f"{team.name} kept {team.score} points"
    assert team.streak == 0
    assert team.max_streak == 0
    assert team.round_history == []
    assert team.round_scores == []
    assert team.answer_times == []
    assert team.hard_score == 0
    assert team.freezes_used == 0
    assert team.powerups_used == 0
    assert team.rounds_played == 0
    assert team.round_score == 0
    assert team.current_answer is None


# --------------------------------------------------------------------------
# The bug as it was reported: Play again keeps the previous game's totals
# --------------------------------------------------------------------------


def test_play_again_starts_every_team_at_zero(tmp_path: Path) -> None:
    """end_game → reset_to_lobby → start_game is the one-tap rematch path."""
    game = _team_game(tmp_path)
    _dirty(_by_name(game, "Sofa"))
    _dirty(_by_name(game, "Cara"))

    game.end_game()
    game.reset_to_lobby()
    game.start_game(
        category="picture-round-en",
        difficulty="easy",
        num_rounds=3,
        language="en",
        lightning_enabled=False,
        hot_seat_enabled=False,
    )

    for name in ("Sofa", "Cara"):
        _assert_clean(_by_name(game, name))


def test_reset_to_lobby_alone_already_clears_the_teams(tmp_path: Path) -> None:
    """The finale's leaderboard must not survive into the lobby it returns to."""
    game = _team_game(tmp_path)
    _dirty(_by_name(game, "Sofa"))

    game.end_game()
    game.reset_to_lobby()

    _assert_clean(_by_name(game, "Sofa"))


def test_start_game_alone_already_clears_the_teams(tmp_path: Path) -> None:
    """The second gate, independent of the first.

    Both entry points reset, so a team carrying a score into a lobby — by any
    route the future invents — still starts the next game at zero.
    """
    game = _team_game(tmp_path)
    game.end_game()
    game.reset_to_lobby()
    _dirty(_by_name(game, "Sofa"))

    game.start_game(
        category="picture-round-en",
        difficulty="easy",
        num_rounds=3,
        language="en",
        lightning_enabled=False,
        hot_seat_enabled=False,
    )

    _assert_clean(_by_name(game, "Sofa"))


def test_the_rematch_leaderboard_is_flat(tmp_path: Path) -> None:
    """What the room actually sees: every row on zero before question one."""
    game = _team_game(tmp_path)
    _by_name(game, "Sofa").score = 120

    game.end_game()
    game.reset_to_lobby()
    game.start_game(
        category="picture-round-en",
        difficulty="easy",
        num_rounds=3,
        language="en",
        lightning_enabled=False,
        hot_seat_enabled=False,
    )

    scores = {row["name"]: row["score"] for row in game.get_leaderboard()}
    assert set(scores) == {"Sofa", "Cara"}
    assert all(v == 0 for v in scores.values()), scores


def test_the_teams_themselves_survive_the_rematch(tmp_path: Path) -> None:
    """Only the scoreboard restarts — the room stays as it stands."""
    game = _team_game(tmp_path)
    game.end_game()
    game.reset_to_lobby()

    sofa = _by_name(game, "Sofa")
    assert sofa.members == ["Anna", "Jan"]
    assert len(game.team_registry.all_teams()) == 2


# --------------------------------------------------------------------------
# The related half: the stale-player prune has to go through remove_player
# --------------------------------------------------------------------------


def test_a_pruned_player_also_leaves_their_team(tmp_path: Path) -> None:
    game = _team_game(tmp_path)
    game.get_player("Jan").connected = False

    game.start_game(
        category="picture-round-en",
        difficulty="easy",
        num_rounds=3,
        language="en",
        lightning_enabled=False,
        hot_seat_enabled=False,
    )

    assert game.get_player("Jan") is None
    assert _by_name(game, "Sofa").members == ["Anna"]


def test_a_team_whose_members_all_went_dark_is_dissolved(
    tmp_path: Path,
) -> None:
    """Otherwise it stands as a zombie row that times out every round."""
    game = _team_game(tmp_path)
    for name in ("Anna", "Jan"):
        game.get_player(name).connected = False

    game.start_game(
        category="picture-round-en",
        difficulty="easy",
        num_rounds=3,
        language="en",
        lightning_enabled=False,
        hot_seat_enabled=False,
    )

    names = [t.name for t in game.team_registry.all_teams()]
    assert names == ["Cara"], names
    assert [row["name"] for row in game.get_leaderboard()] == ["Cara"]
