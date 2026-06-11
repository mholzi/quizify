"""Regression test for the Joker / per-player-shuffle mapping bug (#254).

The Joker power-up greys out one WRONG answer. The chosen index is canonical
(the question's original answer order), but each player sees the answers in
their OWN per-player shuffle. The bug: websocket.py mapped the canonical index
through the *canonical* shuffle_map instead of the player's shuffle, so the
disabled button could land on the CORRECT answer in that player's order
(1-in-3). The fix maps through get_player_shuffle(player.name).

This test drives the real _handle_use_powerup handler and asserts that, for
several distinct player shuffles, the button index the server tells the client
to disable always points to a WRONG answer in THAT player's order.
"""

from __future__ import annotations

import sys
from itertools import permutations
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT))

from custom_components.quizify.game.powerups import PowerUpType  # noqa: E402
from custom_components.quizify.game.state import QuizifyGameState  # noqa: E402
from custom_components.quizify.server.connection import ConnectionManager  # noqa: E402
from custom_components.quizify.server.websocket import (  # noqa: E402
    QuizifyWebSocketHandler,
)


class _FakeRuntime:
    def __init__(self, tmp_path: Path) -> None:
        self.data_dir = tmp_path

    def create_task(self, coro):  # pragma: no cover - not exercised here
        import asyncio

        return asyncio.ensure_future(coro)


def _fake_ws() -> MagicMock:
    ws = MagicMock()
    ws.closed = False
    return ws


@pytest.fixture
def state(tmp_path: Path) -> QuizifyGameState:
    return QuizifyGameState(runtime=_FakeRuntime(tmp_path), entry_id="test")


def _handler(state: QuizifyGameState) -> QuizifyWebSocketHandler:
    runtime = _FakeRuntime(state._runtime.data_dir)  # type: ignore[attr-defined]
    h = QuizifyWebSocketHandler(runtime=runtime, game_state_provider=lambda: state)
    h._conn = ConnectionManager(runtime, lambda: state)
    h._conn.broadcast = AsyncMock()
    h._conn.send = AsyncMock()
    return h


def _give(state: QuizifyGameState, name: str, powerup: PowerUpType) -> None:
    state._powerup_manager._inventory[name] = powerup


def _correct_original_index(state: QuizifyGameState) -> int:
    q = state._current_question
    assert q is not None
    return next(i for i, a in enumerate(q.answers) if a.correct)


@pytest.mark.asyncio
@pytest.mark.parametrize("perm_idx", range(6))
async def test_joker_disables_wrong_answer_in_player_order(
    state: QuizifyGameState, perm_idx
) -> None:
    """For several distinct player shuffles, the disabled button index must
    point to a WRONG answer in that player's order — never the correct one
    (#254)."""
    ws = _fake_ws()
    state.add_player("Alice", ws)
    state.start_game(language="de", num_rounds=3, difficulty="easy")
    state.start_next_question()

    q = state._current_question
    assert q is not None
    n = len(q.answers)
    correct_orig = _correct_original_index(state)

    # Build the perm_idx-th permutation of this question's answer positions and
    # force it as Alice's per-player shuffle. The canonical round shuffle is left
    # as whatever start_next_question set, so canonical != player order (the bug
    # surface). Skipping the identity perm guarantees a real mismatch.
    perms = [p for p in permutations(range(n))]
    player_shuffle = perms[perm_idx % len(perms)]
    state.set_player_shuffle("Alice", list(player_shuffle))

    h = _handler(state)
    _give(state, "Alice", PowerUpType.JOKER)

    await h._handle_use_powerup(ws, {}, state)

    # The private send to Alice carries the button index to disable.
    private_sends = [
        c.args[1]
        for c in h._conn.send.call_args_list
        if len(c.args) >= 2 and c.args[1].get("powerup_type") == "joker"
    ]
    assert private_sends, "expected a private joker send to the using player"
    shuffled_idx = private_sends[-1]["joker_remove_index"]
    assert shuffled_idx is not None

    # Map the player's button position back to the original answer and assert
    # it is a wrong answer in THAT player's order.
    original_idx = player_shuffle[shuffled_idx]
    assert original_idx != correct_orig, (
        f"Joker disabled the correct answer (orig {correct_orig}) for shuffle "
        f"{player_shuffle}: button {shuffled_idx} -> orig {original_idx}"
    )
    assert not q.answers[original_idx].correct
