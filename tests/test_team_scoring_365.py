"""A team answers once and scores once (#365, part 2).

The rule being pinned: *a team behaves in the ranking exactly like a single
player*. Four members do not earn four times — the answer that stands when the
clock stops is scored once, on behalf of whoever set it, with the elapsed time
of that last tap.

These tests drive the real game state and the real scoring engine; only the
question bank is fed a known pack so the correct answer is predictable.
"""

from __future__ import annotations

import time
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from custom_components.quizify.const import ERR_TEAM_LOCKED
from custom_components.quizify.game.state import (
    AnswerResult,
    GamePhase,
    QuizifyGameState,
    TeamAnswerAck,
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
def game(tmp_path: Path) -> QuizifyGameState:
    """A started game with one two-person team and one solo player."""
    st = QuizifyGameState(runtime=_Runtime(tmp_path), entry_id="test")
    for name in ("Anna", "Jan", "Mira"):
        st.add_player(name, _ws())
    st.create_team("Sofa", "Anna")
    st.join_team(st.get_team_of("Anna")["team_id"], "Jan")
    st.start_game(category="picture-round-en", difficulty="easy", num_rounds=3,
                  language="en")
    st.start_next_question()
    return st


def _team(game: QuizifyGameState):
    return game.team_registry.get_by_member("Anna")


def _correct_index(game: QuizifyGameState) -> int:
    question = game.get_current_question()
    return next(i for i, a in enumerate(question.answers) if a.correct)


def test_a_tap_sets_the_team_answer_and_scores_nobody_yet(
    game: QuizifyGameState,
) -> None:
    result = game.submit_answer("Anna", 0)

    assert isinstance(result, TeamAnswerAck)
    assert result.set_by == "Anna"
    assert _team(game).current_answer == 0
    assert game.get_player("Anna").score == 0, "scoring happens at the buzzer"


def test_a_teammate_can_overwrite_the_answer(game: QuizifyGameState) -> None:
    game.submit_answer("Anna", 0)
    _team(game).answered_at = time.monotonic() - 5  # let the lock run out

    assert isinstance(game.submit_answer("Jan", 2), TeamAnswerAck)
    assert _team(game).current_answer == 2
    assert _team(game).answer_by == "Jan"


def test_the_lock_refuses_an_instant_second_change(game: QuizifyGameState) -> None:
    game.submit_answer("Anna", 0)

    assert game.submit_answer("Jan", 1) == ERR_TEAM_LOCKED
    assert _team(game).current_answer == 0


def test_the_team_scores_once_not_once_per_member(game: QuizifyGameState) -> None:
    """Two members, one correct answer, one score."""
    correct = _correct_index(game)
    game.submit_answer("Anna", correct)

    game.evaluate_round()

    team = _team(game)
    assert team.score > 0
    scored_members = [
        p for p in (game.get_player("Anna"), game.get_player("Jan")) if p.score > 0
    ]
    assert len(scored_members) == 1, "exactly one member carries the team's points"
    assert team.score == scored_members[0].score


def test_a_wrong_team_answer_breaks_the_team_streak(game: QuizifyGameState) -> None:
    correct = _correct_index(game)
    wrong = (correct + 1) % 3
    team = _team(game)
    team.streak = 4

    game.submit_answer("Anna", wrong)
    game.evaluate_round()

    assert team.streak == 0
    assert team.round_history[-1] == "wrong"


def test_a_team_that_never_answers_times_out(game: QuizifyGameState) -> None:
    team = _team(game)
    team.streak = 2

    game.evaluate_round()

    assert team.streak == 0
    assert team.round_history[-1] == "timeout"
    assert team.score == 0


def test_the_speed_bonus_follows_the_last_tap(game: QuizifyGameState) -> None:
    """Tapping instantly and rethinking later must not be free.

    Two identical correct answers, one settled as if tapped at the start of the
    round and one as if tapped near the end: the late one must score less.
    """
    correct = _correct_index(game)
    game.submit_answer("Anna", correct)
    team = _team(game)
    team.answered_at = (game._round_start_time or time.monotonic()) + 0.1
    game.evaluate_round()
    fast_points = team.score

    game.start_next_question()
    game.submit_answer("Anna", _correct_index(game))
    team.answered_at = (game._round_start_time or time.monotonic()) + 15.0
    before = team.score
    game.evaluate_round()
    slow_points = team.score - before

    assert slow_points < fast_points


def test_the_round_runs_to_the_clock_in_team_mode(
    game: QuizifyGameState,
) -> None:
    """A team answer is provisional, so the round may not end on the first tap.

    "The answer standing when the clock stops is the team's answer" only means
    something if the clock is what stops it. Found by playing it: with a single
    team the round ended the instant one member tapped, and the promised
    re-decision window never existed.
    """
    assert game.all_submitted() is False

    game.submit_answer("Anna", 0)

    assert game.all_submitted() is False


def test_the_answer_and_lock_are_cleared_for_the_next_question(
    game: QuizifyGameState,
) -> None:
    game.submit_answer("Anna", 0)
    game.evaluate_round()

    game.start_next_question()

    team = _team(game)
    assert team.current_answer is None
    assert team.answer_by is None
    assert team.can_change_answer() is True, "the lock must not survive the round"


def test_a_solo_player_is_unaffected_by_team_mode(game: QuizifyGameState) -> None:
    """Mira joined no team: her tap scores her, as it always did."""
    correct = _correct_index(game)

    result = game.submit_answer("Mira", correct)

    assert isinstance(result, AnswerResult)
    assert game.get_player("Mira").score > 0


def test_teams_are_untouched_when_nobody_formed_one(tmp_path: Path) -> None:
    """No team, no team mode, no behaviour change for an ordinary game."""
    st = QuizifyGameState(runtime=_Runtime(tmp_path), entry_id="test")
    st.add_player("Solo", _ws())
    st.start_game(category="picture-round-en", difficulty="easy", num_rounds=2,
                  language="en")
    st.start_next_question()

    assert st.team_mode is False
    result = st.submit_answer("Solo", 0)
    assert isinstance(result, AnswerResult)
    assert result.player_id == "Solo"
