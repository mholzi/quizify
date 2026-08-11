"""The auto Lightning Round must not spend the main game's questions (#544).

Observed on a real install (v1.7.0-RC7): a 5-round Easy game on
``picture-round-en`` ended after round 2. The pack ships 17 questions, 7 of
them easy; rounds 1 and 2 took two, the lightning detour took the remaining
five and claimed them out of the main queue (``drop_from_queue``, #350), and
the next normal round found an empty queue. With a fixed difficulty there is
no ladder fallback, so ``state.py`` logged "No more questions available" and
ended the game — the host asked for five rounds and got two, with nothing in
the UI to explain it.

The fix reserves what the main game still owes: lightning claims a queued
question only while more than ``total_rounds - round`` of them remain, and
declines to start when nothing is spare (the caller already falls back to a
normal round when ``start()`` returns False).

These tests drive ``LightningRound.start(reserve=...)`` against a stub bank so
the arithmetic is visible, and then re-run the real scenario end-to-end
through the game state.
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT))

from custom_components.quizify.game.lightning import LightningRound  # noqa: E402
from custom_components.quizify.game.questions import Question  # noqa: E402
from custom_components.quizify.game.state import (  # noqa: E402
    GamePhase,
    QuizifyGameState,
)


def _q(idx: int, difficulty: str = "easy") -> Question:
    return Question(
        id=f"q{idx}",
        question=f"Question {idx}?",
        answers=[],
        category="picture-round-en",
        difficulty=difficulty,
        language="en",
    )


class _StubBank:
    """Minimal QuestionBank stand-in exposing only what start() touches."""

    def __init__(self, pool: list[Question], queued: list[Question]) -> None:
        self._pool = pool
        self._queued = {q.id for q in queued}
        self.shown: list[str] = []
        self.dropped: set[str] = set()

    def load_all_categories(self) -> None:
        return None

    def build_pool(self, **_kwargs: object) -> list[Question]:
        return list(self._pool)

    def shown_this_game_ids(self) -> set[str]:
        return set(self.shown)

    def remaining_queue_ids(self) -> set[str]:
        return set(self._queued)

    def record_shown(self, qid: str) -> None:
        self.shown.append(qid)

    def drop_from_queue(self, ids: set[str]) -> None:
        self.dropped |= ids
        self._queued -= ids


def _round(pool: list[Question], queued: list[Question]) -> LightningRound:
    return LightningRound(_StubBank(pool, queued), ["A"], category="picture-round-en")


def test_leaves_the_main_game_its_remaining_rounds() -> None:
    """The exact #544 arithmetic: 5 queued, 3 rounds left → 2 claimable."""
    pool = [_q(i) for i in range(1, 6)]
    lr = _round(pool, pool)

    assert lr.start(reserve=3) is True

    assert len(lr._questions) == 2, "lightning took questions the main game needs"
    assert len(lr._bank.remaining_queue_ids()) == 3


def test_skips_itself_when_nothing_is_spare() -> None:
    """Every pending question is owed to a later round → no detour at all.

    ``False`` is not a failure here: ``_start_auto_lightning`` treats it as
    "play on", which is the whole point — a missing bonus round beats a game
    that stops three rounds early.
    """
    pool = [_q(i) for i in range(1, 4)]
    lr = _round(pool, pool)

    assert lr.start(reserve=3) is False
    assert lr._bank.dropped == set()
    assert len(lr._bank.remaining_queue_ids()) == 3


def test_unqueued_questions_are_free() -> None:
    """Questions outside the main queue were never promised to a round.

    A big pack has plenty the main game is not holding; lightning should still
    get its full five there rather than being throttled by the reserve.
    """
    queued = [_q(i) for i in range(1, 4)]
    pool = queued + [_q(i) for i in range(10, 20)]
    lr = _round(pool, queued)

    assert lr.start(reserve=3) is True
    assert len(lr._questions) == 5
    # The three queued ones stayed put; the five came from the free pool.
    assert len(lr._bank.remaining_queue_ids()) == 3


def test_reserve_zero_keeps_the_old_behaviour() -> None:
    """From the lobby there is no round in flight, so nothing is reserved."""
    pool = [_q(i) for i in range(1, 8)]
    lr = _round(pool, pool)

    assert lr.start(reserve=0) is True
    assert len(lr._questions) == 5


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
    ws = MagicMock()
    ws.closed = False
    st = QuizifyGameState(runtime=_Runtime(tmp_path), entry_id="test")
    st.add_player("A", ws)
    return st


def test_real_pack_five_round_easy_game_keeps_its_rounds(
    state: QuizifyGameState,
) -> None:
    """End-to-end on the pack that surfaced this: 5 rounds must stay 5.

    Drives the real question bank, the real picture pack and the real
    ``start_lightning_round`` entry, then walks the remaining rounds. Before
    the fix the queue was empty here and the game ended at round 2.
    """
    state.start_game(
        category="picture-round-en",
        difficulty="easy",
        num_rounds=5,
        language="en",
    )
    for _ in range(2):
        assert state.start_next_question() is not None
        # Stand in for the round timer + evaluation, which the fast loop does
        # in production; this test is about question supply, not timing.
        state.phase = GamePhase.ANSWER_REVEAL

    assert state.round == 2
    state.phase = GamePhase.ANSWER_REVEAL
    state.start_lightning_round(
        category="picture-round-en", difficulty="easy", auto=True
    )

    # Whatever lightning did or didn't take, the main game must still be able
    # to serve every round the host asked for.
    served = 0
    while state.round < state.total_rounds:
        state.phase = GamePhase.ANSWER_REVEAL
        q = state.start_next_question()
        if q is None:
            break
        served += 1
    assert state.round == 5, (
        f"game stranded after round {state.round}; only {served} further "
        "questions were available (#544)"
    )
