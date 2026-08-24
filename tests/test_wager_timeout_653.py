"""An unanswered wager loses the stake (#653).

This reverses #301, where a timeout left the wager unsettled so a sleeping
phone cost nothing. The reversal is deliberate and it has a price — see the
issue — so the edges are pinned here rather than left to the one flipped
assertion in ``test_game_state.py``.

The cases that matter are the ones where "lose the stake" could quietly mean
something else: a zero bank, a zero-percent wager, a non-final round, and a
player who never wagered at all.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from custom_components.quizify.game.scoring_engine import wager_loss
from custom_components.quizify.game.state import QuizifyGameState


def _fake_ws() -> MagicMock:
    ws = MagicMock()
    ws.closed = False
    return ws


class _Runtime:
    def __init__(self, tmp_path: Path) -> None:
        self.data_dir = tmp_path


@pytest.fixture
def state(tmp_path: Path) -> QuizifyGameState:
    gs = QuizifyGameState(runtime=_Runtime(tmp_path), entry_id="test")
    gs.add_player("Alice", _fake_ws())
    gs.add_player("Bob", _fake_ws())
    return gs


def _to_final_round(gs: QuizifyGameState, *, rounds: int = 2) -> None:
    """Run the game up to and including the start of the last round."""
    gs.start_game(language="de", num_rounds=rounds, difficulty="easy")
    for _ in range(rounds - 1):
        gs.start_next_question()
        gs.evaluate_round()
    gs.start_next_question()
    assert gs.round == gs.total_rounds


def _bob_answers(gs: QuizifyGameState) -> None:
    """Bob submits so the round can be evaluated without Alice."""
    question = gs._current_question
    correct = next(i for i, a in enumerate(question.answers) if a.correct)
    gs.submit_answer("Bob", correct)


# ----------------------------------------------------------------------
# wager_loss — the shared conversion
# ----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("score", "wager", "expected"),
    [
        (100, 50, 50),
        (100, 100, 100),
        (10, 100, 10),      # never more than the bank
        (0, 100, 0),        # nothing to lose
        (100, 0, 0),        # #308: betting nothing costs nothing
        (100, None, 0),     # never wagered
        (-5, 100, 0),       # a transient negative banks as zero
        (100, 150, 100),    # out of range clamps rather than overcharging
    ],
)
def test_wager_loss(score: int, wager: int | None, expected: int) -> None:
    assert wager_loss(score, wager) == expected


def test_the_two_paths_share_one_conversion() -> None:
    """Submit and timeout must agree on what a percentage costs.

    Before #653 only the submit path existed. A second hand-rolled conversion
    is exactly how the two would end up disagreeing about 50 %.
    """
    for score in (0, 1, 7, 39, 96, 1000):
        for pct in (0, 1, 33, 50, 99, 100):
            bank = max(0, score)
            assert wager_loss(score, pct) == min(int(bank * pct / 100), bank)


# ----------------------------------------------------------------------
# The reversal itself
# ----------------------------------------------------------------------


def test_timeout_costs_the_stake(state: QuizifyGameState) -> None:
    _to_final_round(state)
    alice = state.get_player("Alice")
    alice.score = 100
    alice.wager = 40
    _bob_answers(state)
    state.evaluate_round()
    assert alice.submitted is False
    assert alice.score == 60


def test_timeout_on_everything_empties_the_bank(state: QuizifyGameState) -> None:
    _to_final_round(state)
    alice = state.get_player("Alice")
    alice.score = 100
    alice.wager = 100
    _bob_answers(state)
    state.evaluate_round()
    assert alice.score == 0


def test_the_loss_never_goes_below_zero(state: QuizifyGameState) -> None:
    _to_final_round(state)
    alice = state.get_player("Alice")
    alice.score = 3
    alice.wager = 100
    _bob_answers(state)
    state.evaluate_round()
    assert alice.score == 0


def test_the_loss_shows_up_as_the_round_score(state: QuizifyGameState) -> None:
    """The reveal reports ``points_earned=round_score`` — a silent deduction
    would leave the player watching their total drop with no line explaining
    it."""
    _to_final_round(state)
    alice = state.get_player("Alice")
    alice.score = 100
    alice.wager = 50
    _bob_answers(state)
    state.evaluate_round()
    assert alice.round_scores[-1] == -50


# ----------------------------------------------------------------------
# Where it must NOT reach
# ----------------------------------------------------------------------


def test_no_wager_means_no_deduction(state: QuizifyGameState) -> None:
    _to_final_round(state)
    alice = state.get_player("Alice")
    alice.score = 100
    alice.wager = None
    _bob_answers(state)
    state.evaluate_round()
    assert alice.score == 100


def test_a_zero_percent_wager_costs_nothing(state: QuizifyGameState) -> None:
    """#308's rule survives the reversal: betting nothing is not a penalty."""
    _to_final_round(state)
    alice = state.get_player("Alice")
    alice.score = 100
    alice.wager = 0
    _bob_answers(state)
    state.evaluate_round()
    assert alice.score == 100


def test_a_zero_score_player_loses_nothing(state: QuizifyGameState) -> None:
    _to_final_round(state)
    alice = state.get_player("Alice")
    alice.score = 0
    alice.wager = 100
    _bob_answers(state)
    state.evaluate_round()
    assert alice.score == 0


def test_a_non_final_round_ignores_the_wager(state: QuizifyGameState) -> None:
    """A stray wager on round 1 must not become a timeout penalty."""
    state.start_game(language="de", num_rounds=3, difficulty="easy")
    state.start_next_question()
    alice = state.get_player("Alice")
    alice.score = 100
    alice.wager = 100
    _bob_answers(state)
    state.evaluate_round()
    assert alice.score == 100


def test_a_player_who_answers_is_unaffected_by_this_change(
    state: QuizifyGameState,
) -> None:
    """The submit path still owns its own settlement — this must not double-bill."""
    _to_final_round(state)
    alice = state.get_player("Alice")
    alice.score = 100
    alice.wager = 50
    question = state._current_question
    wrong = next(i for i, a in enumerate(question.answers) if not a.correct)
    state.submit_answer("Alice", wrong)
    _bob_answers(state)
    state.evaluate_round()
    # Exactly one deduction of 50, not two.
    assert alice.score == 50
