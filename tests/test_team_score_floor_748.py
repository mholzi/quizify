"""One floor under a team's score, whatever the question type (#748).

``_apply_estimate_results_to_teams`` called itself "the mirror of the
bookkeeping block in ``_settle_team_answers``" and then stopped being one: it
floored ``team.score`` at zero and the multiple-choice block did not. So the
answer to "can a team end a round below zero?" was decided by the question
type — an estimate final said no, a multiple-choice final said yes.

The floor is the side that survives. Every individual score in ``state.py`` is
floored at zero (``submit_answer``, the estimate result, the final-round
timeout, STEAL, the Hot Seat settlement), ``ScoringEngine`` bounds the wager
loss at ``-min(wager_pts, bank)`` on purpose, and
``get_ranked_participants`` puts teams and solo players in ONE ranked list that
the dashboard, the reveal and the finale render with one code path. A -30 next
to a 0 for the same lost bet is a rendering nobody designed.

Note on reachability: team mode does not open a betting window today (#668),
so ``player.wager`` is normally ``None`` when settlement runs. These tests set
it directly because settlement reads it unconditionally — the invariant under
test is the settlement contract, which has to hold before a team-level wager
can be built on top of it.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

from custom_components.quizify.game.state import QuizifyGameState


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


def _game(tmp_path: Path, category: str, rounds: int = 1) -> QuizifyGameState:
    """A team of two plus one solo player, parked on round 1 of ``rounds``."""
    st = QuizifyGameState(runtime=_Runtime(tmp_path), entry_id="test")
    for name in ("Anna", "Jan", "Mira"):
        st.add_player(name, _ws())
    st.create_team("Sofa", "Anna")
    st.join_team(st.get_team_of("Anna")["team_id"], "Jan")
    st.start_game(
        category=category, difficulty="easy", num_rounds=rounds, language="en"
    )
    st.start_next_question()
    return st


def _team(game: QuizifyGameState):  # noqa: ANN202
    return game.team_registry.all_teams()[0]


def _wrong_index(game: QuizifyGameState) -> int:
    q = game.get_current_question()
    return next(i for i, a in enumerate(q.answers) if not a.correct)


# --------------------------------------------------------------------------
# The defect: the floor was question-type dependent
# --------------------------------------------------------------------------


def test_a_lost_wager_cannot_push_a_team_below_zero(tmp_path: Path) -> None:
    """The multiple-choice path had no floor, so the team ended at -140.

    ``_settle_team_answers`` lends the carrier the team's *streak* but not its
    *score*, so the engine bounds the loss by the carrier's own shadow bank
    while the subtraction lands on the team's. Give the carrier a bank the team
    does not have and the team goes negative — which is exactly what the
    estimate path had already refused to do.
    """
    game = _game(tmp_path, "picture-round-en")
    team = _team(game)
    carrier = game.get_player("Anna")

    team.score = 60
    carrier.score = 200  # bigger shadow bank than the team's real one
    carrier.wager = 100  # bet the lot on the final round

    game.submit_answer("Anna", _wrong_index(game))
    game.evaluate_round()

    assert team.score == 0, f"team ended the round at {team.score}"


def test_the_estimate_path_keeps_the_same_floor(tmp_path: Path) -> None:
    """The side that was already right stays right after the refactor."""
    game = _game(tmp_path, "estimation-en")
    team = _team(game)
    team.score = 40

    carrier = game.get_player("Anna")
    carrier.round_score = -500  # a loss bigger than the team's bank
    game._apply_estimate_results_to_teams(  # noqa: SLF001
        {team.team_id: "Anna"},
        {"Anna": {"exact": False, "points": -500, "distance": 1.0, "rank": 2}},
    )

    assert team.score == 0


def test_both_paths_book_a_round_through_the_same_function(
    tmp_path: Path,
) -> None:
    """The structural half of the fix: no second copy left to drift.

    Two blocks documented as mirrors drifted anyway, so "they agree today" is
    not the property worth pinning — "there is only one of them" is.
    """
    calls: list[tuple[int, bool]] = []
    original = QuizifyGameState._credit_team_round  # noqa: SLF001

    def _spy(self, team, points, correct, elapsed, breakdown):  # noqa: ANN001
        calls.append((points, correct))
        return original(self, team, points, correct, elapsed, breakdown)

    for category in ("picture-round-en", "estimation-en"):
        game = _game(tmp_path, category)
        game._credit_team_round = _spy.__get__(game)  # noqa: SLF001
        before = len(calls)

        if category == "estimation-en":
            answer = game.get_current_question().estimate_answer
            game.submit_guess("Anna", answer)
            game.submit_guess("Mira", answer * 0.5)
        else:
            game.submit_answer("Anna", _wrong_index(game))
        game.evaluate_round()

        assert len(calls) == before + 1, f"{category} did not use the helper"


# --------------------------------------------------------------------------
# The bookkeeping the two copies used to keep separately
# --------------------------------------------------------------------------


def test_a_scored_round_is_booked_identically_on_both_paths(
    tmp_path: Path,
) -> None:
    """Same points, same correctness → same team row, whatever the question."""
    rows = []
    for category in ("picture-round-en", "estimation-en"):
        game = _game(tmp_path, category)
        team = _team(game)
        game._credit_team_round(  # noqa: SLF001
            team, 150, True, 2.5, {"speed_bonus": 10}
        )
        rows.append(
            (
                team.score,
                team.streak,
                team.max_streak,
                team.last_answer_correct,
                team.last_elapsed,
                team.round_score,
                team.round_history,
                team.round_scores,
                team.rounds_played,
                team.answer_times,
            )
        )

    assert rows[0] == rows[1]


def test_a_missed_round_is_booked_identically_on_both_paths(
    tmp_path: Path,
) -> None:
    """A team that never responded looks the same in MC and in estimate."""
    game_mc = _game(tmp_path, "picture-round-en")
    game_est = _game(tmp_path, "estimation-en")

    game_mc.submit_answer("Mira", 0)
    game_mc.evaluate_round()
    game_est.submit_guess(
        "Mira", game_est.get_current_question().estimate_answer
    )
    game_est.evaluate_round()

    def _row(team):  # noqa: ANN001, ANN202
        return (
            team.score,
            team.streak,
            team.round_history,
            team.round_scores,
            team.rounds_played,
        )

    assert _row(_team(game_mc)) == _row(_team(game_est))


def test_the_shared_streak_rule_matches_the_engine(tmp_path: Path) -> None:
    """The helper owns the streak, so it must reproduce what it replaced.

    ``_settle_team_answers`` used to take ``result.new_streak`` straight from
    the engine. The engine's rule is "+1 on a correct answer, 0 otherwise"
    against the streak the carrier was lent — the same rule the estimate path
    spelled out by hand against ``exact`` (#408).
    """
    game = _game(tmp_path, "picture-round-en", rounds=3)
    team = _team(game)
    team.streak = 4

    game._credit_team_round(team, 100, True, 1.0, {})  # noqa: SLF001
    assert (team.streak, team.max_streak) == (5, 5)

    game._credit_team_round(team, 0, False, 1.0, {})  # noqa: SLF001
    assert (team.streak, team.max_streak) == (0, 5)
