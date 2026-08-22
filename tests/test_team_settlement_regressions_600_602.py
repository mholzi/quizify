"""The three team-settlement defects found on 2026-08-22 (#600, #601, #602).

All three sat in the same place — the moment a round closes and a team's
standing response is turned into a score — and all three survived a 1968-test
suite because the existing tests set the clock and the connection state by
hand. These tests deliberately do not: they tap through the real entry points
and let the production defaults stand.
"""

from __future__ import annotations

import time
from pathlib import Path
from unittest.mock import MagicMock

from custom_components.quizify.game.state import QuizifyGameState


def _ws(closed: bool = False) -> MagicMock:
    ws = MagicMock()
    ws.closed = closed
    return ws


class _Runtime:
    def __init__(self, tmp_path: Path) -> None:
        self.data_dir = tmp_path

    async def run_in_executor(self, func, *args):  # noqa: ANN001, ANN002
        return func(*args)

    def create_task(self, coro):  # noqa: ANN001
        import asyncio

        return asyncio.ensure_future(coro)


def _game(tmp_path: Path, category: str, members: tuple[str, ...] = ("Anna", "Jan"),
          solo: tuple[str, ...] = ("Mira",)) -> QuizifyGameState:
    st = QuizifyGameState(runtime=_Runtime(tmp_path), entry_id="test")
    for name in (*members, *solo):
        st.add_player(name, _ws())
    st.create_team("Sofa", members[0])
    for name in members[1:]:
        st.join_team(st.get_team_of(members[0])["team_id"], name)
    st.start_game(category=category, difficulty="easy", num_rounds=3, language="en")
    st.start_next_question()
    return st


def _team(game: QuizifyGameState):  # noqa: ANN202
    return game.team_registry.all_teams()[0]


def _correct_index(game: QuizifyGameState) -> int:
    q = game.get_current_question()
    return next(i for i, a in enumerate(q.answers) if a.correct)


# --------------------------------------------------------------------------
# #600 — the two clocks
# --------------------------------------------------------------------------


def test_a_real_tap_produces_an_elapsed_a_human_could_have_taken(
    tmp_path: Path,
) -> None:
    """The regression itself: no hand-set timestamp, just a tap.

    `Team.set_answer` used a wall clock while the round start was monotonic,
    so this subtraction returned the seconds since 1970 — about 1.79e9. Any
    assertion loose enough to pass then would have been useless, so this pins
    the only thing that matters: the elapsed time of a tap must be a number of
    seconds from *this round*.
    """
    game = _game(tmp_path, "picture-round-en")
    game.submit_answer("Anna", _correct_index(game))
    game.evaluate_round()

    assert 0.0 <= _team(game).last_elapsed < 60.0


def test_the_team_speed_bonus_is_actually_paid(tmp_path: Path) -> None:
    """With both clocks agreeing, a fast team answer earns a speed bonus.

    Before the fix every team answer was scored as maximally slow, so the
    documented "the last tap costs you speed" rule was inert in production
    even though a test pinned it — that test supplied its own timestamp.
    """
    game = _game(tmp_path, "picture-round-en")
    game.submit_answer("Anna", _correct_index(game))
    game.evaluate_round()

    assert _team(game).round_score_breakdown["speed_bonus"] > 0


# --------------------------------------------------------------------------
# #601 — re-entrant evaluation
# --------------------------------------------------------------------------


def test_a_locked_phone_does_not_score_the_round_twice(tmp_path: Path) -> None:
    """One round, one score — even when a teammate's socket is closed.

    The settlement path called `submit_answer`, which then found every active
    player submitted and re-entered `evaluate_round()` from inside the
    settlement loop. Measured before the fix: three evaluations, 22 points for
    an 11-point round, and a three-entry history.
    """
    game = _game(tmp_path, "picture-round-en", solo=())
    # Jan's phone locked: his socket is closed, so he is not "active" — which
    # is what let the all-submitted check pass mid-settlement.
    game.get_player("Jan").ws = _ws(closed=True)
    game.get_player("Jan").connected = False

    game.submit_answer("Anna", _correct_index(game))
    game.evaluate_round()

    team = _team(game)
    # Measured on the unfixed code, for exactly this setup:
    # rounds_played=3, history=['timeout', 'correct', 'correct'], score=22
    # for an 11-point round.
    assert team.rounds_played == 1
    assert len(team.round_history) == 1
    assert team.round_scores == [team.round_score]
    assert team.score == team.round_score


def test_the_solo_players_do_not_double_their_history(tmp_path: Path) -> None:
    """The other shape of the same defect: a one-member team plus solo players.

    Everyone else had already answered, so settling the team tripped the
    all-submitted check and the whole round was evaluated a second time —
    doubling `round_history` and `rounds_played` for every player in the game.
    """
    game = _game(tmp_path, "picture-round-en", members=("Anna",), solo=("Mira",))
    correct = _correct_index(game)
    game.submit_answer("Mira", correct)
    game.submit_answer("Anna", correct)
    game.evaluate_round()

    assert len(game.get_player("Mira").round_history) == 1
    assert game.get_player("Mira").rounds_played == 1
    assert _team(game).rounds_played == 1


# --------------------------------------------------------------------------
# #602 — estimate rounds in team mode
# --------------------------------------------------------------------------


def test_a_team_scores_its_estimate_guess(tmp_path: Path) -> None:
    """The team, not the invisible individual score, takes the points.

    Before the fix `_evaluate_estimate_round` had no team branch at all: the
    points landed on the member's own score, the team finished the round on
    zero, and the solo player won a round the team had won.
    """
    game = _game(tmp_path, "estimation-en")
    answer = game.get_current_question().estimate_answer

    game.submit_guess("Anna", answer)        # exact
    game.submit_guess("Mira", answer * 0.5)  # far off
    game.evaluate_round()

    team = _team(game)
    assert team.score > 0
    assert team.rounds_played == 1
    assert team.round_history == ["correct"]
    assert team.score > game.get_player("Mira").score


def test_an_estimate_round_in_team_mode_runs_to_the_clock(tmp_path: Path) -> None:
    """Guessing does not end the round early for a team.

    `submit_guess` reached past `state.all_submitted()` into the registry,
    which does not know the team rule, so the round closed the moment each
    member had guessed once — leaving nothing to re-decide.
    """
    game = _game(tmp_path, "estimation-en")
    answer = game.get_current_question().estimate_answer

    game.submit_guess("Anna", answer)
    game.submit_guess("Jan", answer)
    game.submit_guess("Mira", answer)

    assert game.get_round_summary() is None, "the round must still be open"


def test_the_last_guess_is_the_one_that_counts(tmp_path: Path) -> None:
    """A team's guess behaves like its answer: any member may change it."""
    game = _game(tmp_path, "estimation-en")
    answer = game.get_current_question().estimate_answer

    game.submit_guess("Anna", answer * 0.5)
    _team(game).answered_at = time.monotonic() - 5  # let the change lock run out
    game.submit_guess("Jan", answer)

    team = _team(game)
    assert team.current_guess == answer
    assert team.guess_by == "Jan"


def test_a_team_that_never_guessed_records_a_timeout(tmp_path: Path) -> None:
    """A missed estimate round looks like every other missed round."""
    game = _game(tmp_path, "estimation-en")
    game.submit_guess("Mira", game.get_current_question().estimate_answer)
    game.evaluate_round()

    team = _team(game)
    assert team.round_history == ["timeout"]
    assert team.rounds_played == 1
    assert team.score == 0
