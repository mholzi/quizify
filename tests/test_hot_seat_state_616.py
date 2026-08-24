"""Hot Seat detour wiring in the game state (#616).

The rules themselves are covered in ``test_hot_seat_616.py``. This file is
about the seam: arming the trigger, detouring out of a live round, settling,
and — the part detours have historically got wrong — landing back on the
round that was interrupted (#285, #544).
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from custom_components.quizify.game.phase_controller import GamePhase
from custom_components.quizify.game.state import QuizifyGameState


def _fake_ws() -> MagicMock:
    ws = MagicMock()
    ws.closed = False
    return ws


class _Runtime:
    def __init__(self, tmp_path: Path) -> None:
        self.data_dir = tmp_path


def _new_game(tmp_path: Path, names: tuple[str, ...]) -> QuizifyGameState:
    gs = QuizifyGameState(runtime=_Runtime(tmp_path), entry_id="test")
    for name in names:
        gs.add_player(name, _fake_ws())
    return gs


@pytest.fixture
def game(tmp_path: Path) -> QuizifyGameState:
    return _new_game(tmp_path, ("Anna", "Ben", "Cem", "Dana"))


def _start(gs: QuizifyGameState, **kwargs) -> None:
    kwargs.setdefault("num_rounds", 10)
    gs.start_game(hot_seat_seed=7, lightning_seed=7, **kwargs)


# ----------------------------------------------------------------------
# Arming
# ----------------------------------------------------------------------


def test_target_lands_inside_the_window(game: QuizifyGameState) -> None:
    """Rounds 3 … n-1: the game establishes itself, the finale stays the finale."""
    _start(game)
    assert game.hot_seat_target_round is not None
    assert 3 <= game.hot_seat_target_round <= 9


def test_the_two_detours_never_share_a_round(tmp_path: Path) -> None:
    """Back-to-back detours would read as a broken round, not two bonuses."""
    for seed in range(40):
        gs = _new_game(tmp_path, ("Anna", "Ben", "Cem"))
        gs.start_game(num_rounds=10, lightning_seed=seed, hot_seat_seed=seed)
        if gs.hot_seat_target_round is not None:
            assert gs.hot_seat_target_round != gs.lightning_target_round


def test_short_game_arms_nothing(game: QuizifyGameState) -> None:
    _start(game, num_rounds=3)
    assert game.hot_seat_target_round is None
    assert game.should_trigger_hot_seat() is False


def test_toggle_off_arms_nothing(game: QuizifyGameState) -> None:
    _start(game, hot_seat_enabled=False)
    assert game.hot_seat_target_round is None
    assert game.should_trigger_hot_seat() is False


def test_trigger_only_between_rounds(game: QuizifyGameState) -> None:
    _start(game)
    game.round = game.hot_seat_target_round - 1
    game.phase = GamePhase.ANSWER_REVEAL
    assert game.should_trigger_hot_seat() is True
    game.phase = GamePhase.QUESTION_ACTIVE
    assert game.should_trigger_hot_seat() is False


def test_trigger_fires_once_per_game(game: QuizifyGameState) -> None:
    _start(game)
    game.round = game.hot_seat_target_round - 1
    game.phase = GamePhase.ANSWER_REVEAL
    assert game.should_trigger_hot_seat() is True
    game.start_hot_seat_auction()
    game.phase = GamePhase.ANSWER_REVEAL
    assert game.should_trigger_hot_seat() is False


def test_a_failed_start_still_burns_the_flag(tmp_path: Path) -> None:
    """Otherwise a skip would be retried at every single round advance."""
    gs = _new_game(tmp_path, ("Solo",))  # below the minimum for an auction
    gs.start_game(num_rounds=10, hot_seat_seed=1)
    gs.round = (gs.hot_seat_target_round or 3) - 1
    gs.phase = GamePhase.ANSWER_REVEAL
    assert gs.start_hot_seat_auction() is False
    gs.phase = GamePhase.ANSWER_REVEAL
    assert gs.should_trigger_hot_seat() is False


# ----------------------------------------------------------------------
# The detour
# ----------------------------------------------------------------------


def _detour(game: QuizifyGameState, *, scores: int = 40) -> None:
    """Detour out of round 4, with everyone holding points.

    The points matter: a bid must cost something, so a room on zero cannot
    hold an auction at all (see test_a_broke_player_cannot_block_the_chair).
    """
    _start(game)
    for player in game.get_players():
        player.score = scores
    game.round = 4
    game.phase = GamePhase.ANSWER_REVEAL
    assert game.start_hot_seat_auction() is True


def test_auction_enters_its_own_phase(game: QuizifyGameState) -> None:
    _detour(game)
    assert game.phase == GamePhase.HOT_SEAT_AUCTION
    assert game.in_hot_seat_detour is True


def test_closing_the_auction_moves_to_the_question(game: QuizifyGameState) -> None:
    _detour(game)
    game.hot_seat.record_bid("Anna", 50)
    assert game.close_hot_seat_auction() == "Anna"
    assert game.phase == GamePhase.HOT_SEAT


def test_no_bids_awards_nobody_and_stays_put(game: QuizifyGameState) -> None:
    _detour(game)
    game.hot_seat.record_bid("Anna", 0)
    assert game.close_hot_seat_auction() is None
    assert game.phase == GamePhase.HOT_SEAT_AUCTION


def test_abort_returns_to_the_interrupted_round(game: QuizifyGameState) -> None:
    _detour(game)
    game.abort_hot_seat()
    assert game.phase == GamePhase.ANSWER_REVEAL
    assert game.round == 4
    assert game.in_hot_seat_detour is False


def test_resume_lands_back_on_the_interrupted_round(game: QuizifyGameState) -> None:
    """The failure mode every detour has had: resuming on the wrong round."""
    _detour(game)
    game.hot_seat.record_bid("Anna", 50)
    game.close_hot_seat_auction()
    game.finish_hot_seat()
    assert game.phase == GamePhase.HOT_SEAT_REVEAL
    assert game.resume_after_hot_seat() is True
    assert game.phase == GamePhase.ANSWER_REVEAL
    assert game.round == 4
    assert game.hot_seat is None


def test_resume_refuses_outside_the_reveal(game: QuizifyGameState) -> None:
    _detour(game)
    assert game.resume_after_hot_seat() is False


# ----------------------------------------------------------------------
# Settlement reaches the scoreboard
# ----------------------------------------------------------------------


def test_a_win_is_applied_to_the_real_score(game: QuizifyGameState) -> None:
    _detour(game)
    anna = game.get_player("Anna")
    game.hot_seat.record_bid("Anna", 50)
    game.close_hot_seat_auction()
    hs = game.hot_seat
    hs.record_answer("Anna", hs._shuffle.index(hs.correct_index))
    game.finish_hot_seat()
    assert anna.score == 60


def test_a_loss_is_applied_and_floored_at_zero(game: QuizifyGameState) -> None:
    _detour(game, scores=10)
    anna = game.get_player("Anna")
    game.hot_seat.record_bid("Anna", 100)
    game.close_hot_seat_auction()
    hs = game.hot_seat
    wrong = next(
        i for i in range(len(hs.question.answers)) if i != hs.correct_index
    )
    hs.record_answer("Anna", hs._shuffle.index(wrong))
    game.finish_hot_seat()
    assert anna.score == 0


def test_an_unanswered_question_still_settles(game: QuizifyGameState) -> None:
    """The rule this mode was designed around — see #653."""
    _detour(game)
    anna = game.get_player("Anna")
    game.hot_seat.record_bid("Anna", 50)
    game.close_hot_seat_auction()
    game.finish_hot_seat()  # nobody answered
    assert anna.score == 20


def test_spectator_stakes_reach_the_scoreboard(game: QuizifyGameState) -> None:
    _detour(game)
    anna, ben = game.get_player("Anna"), game.get_player("Ben")
    game.hot_seat.record_bid("Anna", 50)
    game.close_hot_seat_auction()
    hs = game.hot_seat
    hs.record_bet("Ben", "wont", 50)
    hs.record_answer("Anna", hs._shuffle.index(hs.correct_index))
    game.finish_hot_seat()
    assert anna.score == 60
    assert ben.score == 20  # backed against a correct answer


def test_reset_disarms_everything(game: QuizifyGameState) -> None:
    _detour(game)
    game.reset_to_lobby()
    assert game.hot_seat is None
    assert game.hot_seat_target_round is None
    assert game.in_hot_seat_detour is False
