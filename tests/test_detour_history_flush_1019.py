"""A mid-game detour must not forget what this game already showed (#1019).

``finish_lightning_round`` and ``finish_hot_seat`` persist the question history
while the game is still running. Before the fix that write went through
``flush_shown_history``, which also emptied ``_shown_this_game`` — the set every
later detour passes as ``exclude_ids``. From then on the #544 reservation only
protected *queued* questions, and on a small pack with nothing spare the Hot
Seat was handed the oldest question of this very game: round 1's.

The per-game set is now cleared only when the game ends (``end_game``) or the
room goes back to the lobby (``reset_to_lobby``).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from custom_components.quizify.game.phase_controller import GamePhase
from custom_components.quizify.game.questions import Answer, Question
from custom_components.quizify.game.state import QuizifyGameState

PACK = "tiny-1019"


class _Runtime:
    def __init__(self, tmp_path: Path) -> None:
        self.data_dir = tmp_path


def _q(idx: int) -> Question:
    return Question(
        id=f"t{idx:02d}",
        question=f"Question {idx}?",
        answers=[
            Answer(text="right", correct=True),
            Answer(text="wrong a", correct=False),
            Answer(text="wrong b", correct=False),
            Answer(text="wrong c", correct=False),
        ],
        category=PACK,
        difficulty="medium",
        language="en",
    )


@pytest.fixture
def game(tmp_path: Path) -> QuizifyGameState:
    gs = QuizifyGameState(runtime=_Runtime(tmp_path), entry_id="test")
    bank = gs.question_bank
    # A 12-question pack, exactly the size of the reported repro.
    bank._categories = {PACK: [_q(i) for i in range(1, 13)]}
    bank._loaded = True
    for name in ("Anna", "Ben", "Cem", "Dana"):
        gs.add_player(name)
    return gs


def _play_round(gs: QuizifyGameState) -> str:
    gs.phase = GamePhase.ANSWER_REVEAL
    q = gs.start_next_question()
    assert q is not None
    gs.phase = GamePhase.ANSWER_REVEAL
    return q.id


def test_hot_seat_after_lightning_never_repeats_a_question(
    game: QuizifyGameState,
) -> None:
    """12-question pack, 10 rounds, Lightning at round 3, Hot Seat at round 4."""
    game.start_game(
        category=PACK,
        difficulty="medium",
        num_rounds=10,
        language="en",
        lightning_enabled=False,
        hot_seat_enabled=False,
    )
    served = [_play_round(game) for _ in range(3)]

    assert game.start_lightning_round(
        category=PACK, difficulty="medium", language="en", auto=True
    )
    served += [q.id for q in game.lightning._questions]
    game.finish_lightning_round()  # mid-game history flush #1
    assert game.resume_after_lightning() is True

    served.append(_play_round(game))
    assert game.round == 4

    for player in game.get_players():
        player.score = 40
    if game.start_hot_seat_auction():
        assert game.hot_seat.question.id not in served, (
            f"Hot Seat repeated {game.hot_seat.question.id} from this game (#1019)"
        )
    # Every question this game served is still known to the bank.
    assert set(served) <= game.question_bank.shown_this_game_ids()


def test_detour_flush_keeps_the_per_game_set(game: QuizifyGameState) -> None:
    """Both mid-game flushes persist history without clearing the set."""
    game.start_game(
        category=PACK,
        difficulty="medium",
        num_rounds=10,
        language="en",
        lightning_enabled=False,
        hot_seat_enabled=False,
    )
    first = _play_round(game)
    bank = game.question_bank

    game.phase = GamePhase.LIGHTNING
    game.finish_lightning_round()
    assert first in bank.shown_this_game_ids()
    assert first in bank._history

    game.phase = GamePhase.HOT_SEAT
    game.finish_hot_seat()
    assert first in bank.shown_this_game_ids()


def test_end_game_and_lobby_clear_the_per_game_set(game: QuizifyGameState) -> None:
    game.start_game(
        category=PACK,
        difficulty="medium",
        num_rounds=10,
        language="en",
        lightning_enabled=False,
        hot_seat_enabled=False,
    )
    _play_round(game)
    game.end_game()
    assert game.question_bank.shown_this_game_ids() == set()

    game.reset_to_lobby()
    game.start_game(
        category=PACK,
        difficulty="medium",
        num_rounds=10,
        language="en",
        lightning_enabled=False,
        hot_seat_enabled=False,
    )
    _play_round(game)
    # An abandoned game (host resets without reaching the finale) must not
    # carry its questions into the next game's exclusion set.
    game.reset_to_lobby()
    assert game.question_bank.shown_this_game_ids() == set()
