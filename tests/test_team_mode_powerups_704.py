"""Power-ups that a team can actually use (#704).

Power-ups were dealt without any team gating, but two of them cannot work the
way team scoring is built:

* **Steal** needs a target whose answer is ``submitted``. The team branch of
  ``submit_answer`` deliberately marks nobody submitted until settlement, so in
  team mode that condition is never true while the power-up is usable — every
  attempt returned ERR_INVALID_ACTION, i.e. "Power-up not available".
* **Double Points** keys on the carrier — whoever's tap stands at the buzzer —
  which is rarely the member who spent it. The activator's power-up was
  consumed and nothing doubled: no badge, no effect.

Steal now follows the call #668 made for the wager window: team mode does not
deal a control that cannot work. Double Points is fixable rather than
removable, so it is fixed — the team answers as one, so it scores as one.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from custom_components.quizify.game.powerups import PowerUpType
from custom_components.quizify.game.state import GamePhase, QuizifyGameState


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


def _new_game(tmp_path: Path) -> QuizifyGameState:
    return QuizifyGameState(runtime=_Runtime(tmp_path), entry_id="test")


@pytest.fixture
def team_game(tmp_path: Path) -> QuizifyGameState:
    """A started game with one two-person team and one solo player."""
    st = _new_game(tmp_path)
    for name in ("Anna", "Jan", "Mira"):
        st.add_player(name, _ws())
    st.create_team("Sofa", "Anna")
    st.join_team(st.get_team_of("Anna")["team_id"], "Jan")
    st.start_game(
        category="picture-round-en", difficulty="easy", num_rounds=3, language="en"
    )
    st.start_next_question()
    return st


@pytest.fixture
def solo_game(tmp_path: Path) -> QuizifyGameState:
    st = _new_game(tmp_path)
    for name in ("Anna", "Jan"):
        st.add_player(name, _ws())
    st.start_game(
        category="picture-round-en", difficulty="easy", num_rounds=3, language="en"
    )
    st.start_next_question()
    return st


def _team(game: QuizifyGameState):
    return game.team_registry.get_by_member("Anna")


def _correct_index(game: QuizifyGameState) -> int:
    question = game.get_current_question()
    return next(i for i, a in enumerate(question.answers) if a.correct)


def _pool_offered_by_the_deal(game: QuizifyGameState) -> list[PowerUpType]:
    """The pool ``start_next_question`` offers the deal for the next round.

    The restriction is expressed as the ``allowed_types`` argument, so reading
    it straight off the call is both exact and independent of which power-up
    the dice happen to pick. ``None`` there means "every type".
    """
    manager = game._powerup_manager  # noqa: SLF001
    captured: dict[str, list[PowerUpType] | None] = {"pool": None}
    real = manager.assign_random_powerup

    def spy(player_id: str, allowed_types: list[PowerUpType] | None = None):
        captured["pool"] = allowed_types
        return real(player_id, allowed_types=allowed_types)

    manager.assign_random_powerup = spy  # type: ignore[method-assign]
    manager._granted_this_game.clear()  # noqa: SLF001
    manager._inventory.clear()  # noqa: SLF001
    game._phase_controller.phase = GamePhase.ANSWER_REVEAL  # noqa: SLF001
    game.round = 0
    try:
        assert game.start_next_question() is not None
    finally:
        manager.assign_random_powerup = real  # type: ignore[method-assign]
    return list(captured["pool"] or list(PowerUpType))


class TestStealIsNotDealtInTeamMode:
    def test_the_deal_pool_excludes_steal(self, team_game: QuizifyGameState) -> None:
        """Whatever the deal hands out in team mode, it is never Steal."""
        pool = _pool_offered_by_the_deal(team_game)
        assert PowerUpType.STEAL not in pool

    def test_nothing_else_is_taken_away(self, team_game: QuizifyGameState) -> None:
        """Only Steal goes; Joker, Freeze, Time Boost and the x2 all remain."""
        pool = _pool_offered_by_the_deal(team_game)
        assert set(pool) == set(PowerUpType) - {PowerUpType.STEAL}

    def test_a_solo_game_still_deals_steal(self, solo_game: QuizifyGameState) -> None:
        """The gate is team mode, not the power-up itself."""
        assert PowerUpType.STEAL in _pool_offered_by_the_deal(solo_game)


class TestDoublePointsBelongsToTheTeam:
    def test_a_non_carrier_activation_still_doubles(
        self, team_game: QuizifyGameState
    ) -> None:
        """The bug: Jan spends the x2, Anna's tap carries, nothing doubled."""
        manager = team_game._powerup_manager  # noqa: SLF001
        manager._inventory["Jan"] = PowerUpType.DOUBLE_POINTS  # noqa: SLF001
        assert manager.use_powerup(player_id="Jan", target_id=None) is not None
        assert manager.is_double_points_active("Jan") is True

        correct = _correct_index(team_game)
        team_game.submit_answer("Anna", correct)  # Anna carries the answer
        team_game.evaluate_round()

        team = _team(team_game)
        assert team.score > 0
        assert team.round_score_breakdown.get("double_points") is True

    def test_the_carriers_own_activation_still_works(
        self, team_game: QuizifyGameState
    ) -> None:
        manager = team_game._powerup_manager  # noqa: SLF001
        manager._inventory["Anna"] = PowerUpType.DOUBLE_POINTS  # noqa: SLF001
        assert manager.use_powerup(player_id="Anna", target_id=None) is not None

        correct = _correct_index(team_game)
        team_game.submit_answer("Anna", correct)
        team_game.evaluate_round()

        assert _team(team_game).round_score_breakdown.get("double_points") is True

    def test_doubling_is_worth_more_than_not_doubling(self, tmp_path: Path) -> None:
        """Same answer, same team shape — the x2 team scores strictly higher."""
        scores = []
        for activate in (False, True):
            st = _new_game(tmp_path)
            for name in ("Anna", "Jan"):
                st.add_player(name, _ws())
            st.create_team("Sofa", "Anna")
            st.join_team(st.get_team_of("Anna")["team_id"], "Jan")
            st.start_game(
                category="picture-round-en",
                difficulty="easy",
                num_rounds=3,
                language="en",
            )
            st.start_next_question()
            manager = st._powerup_manager  # noqa: SLF001
            if activate:
                manager._inventory["Jan"] = PowerUpType.DOUBLE_POINTS  # noqa: SLF001
                manager.use_powerup(player_id="Jan", target_id=None)
            correct = next(
                i for i, a in enumerate(st.get_current_question().answers) if a.correct
            )
            st.submit_answer("Anna", correct, elapsed_override=1.0)
            st.evaluate_round()
            scores.append(st.team_registry.get_by_member("Anna").score)

        plain, doubled = scores
        assert plain > 0
        assert doubled > plain

    def test_another_teams_activation_does_not_leak(self, tmp_path: Path) -> None:
        """The multiplier is the team's, not the room's."""
        st = _new_game(tmp_path)
        for name in ("Anna", "Jan", "Mira", "Tom"):
            st.add_player(name, _ws())
        st.create_team("Sofa", "Anna")
        st.join_team(st.get_team_of("Anna")["team_id"], "Jan")
        st.create_team("Küche", "Mira")
        st.join_team(st.get_team_of("Mira")["team_id"], "Tom")
        st.start_game(
            category="picture-round-en", difficulty="easy", num_rounds=3, language="en"
        )
        st.start_next_question()

        manager = st._powerup_manager  # noqa: SLF001
        manager._inventory["Jan"] = PowerUpType.DOUBLE_POINTS  # noqa: SLF001
        manager.use_powerup(player_id="Jan", target_id=None)

        correct = next(
            i for i, a in enumerate(st.get_current_question().answers) if a.correct
        )
        st.submit_answer("Anna", correct, elapsed_override=1.0)
        st.submit_answer("Mira", correct, elapsed_override=1.0)
        st.evaluate_round()

        sofa = st.team_registry.get_by_member("Anna")
        kueche = st.team_registry.get_by_member("Mira")
        assert sofa.round_score_breakdown.get("double_points") is True
        assert kueche.round_score_breakdown.get("double_points") is not True
        assert sofa.score > kueche.score

    def test_a_solo_player_is_unaffected(self, solo_game: QuizifyGameState) -> None:
        """Nothing changes outside team mode."""
        manager = solo_game._powerup_manager  # noqa: SLF001
        manager._inventory["Anna"] = PowerUpType.DOUBLE_POINTS  # noqa: SLF001
        manager.use_powerup(player_id="Anna", target_id=None)

        correct = _correct_index(solo_game)
        solo_game.submit_answer("Anna", correct, elapsed_override=1.0)
        solo_game.submit_answer("Jan", correct, elapsed_override=1.0)

        anna = solo_game.get_player("Anna")
        jan = solo_game.get_player("Jan")
        assert anna.round_score_breakdown.get("double_points") is True
        assert jan.round_score_breakdown.get("double_points") is not True
        assert anna.score > jan.score
