"""Regression tests for #670 and #671 — the Hot Seat detour must be torn
down along the same paths Lightning already is, and its toggle must survive
the one-tap rematch.

Background: the Hot Seat auction (#616) arrived after the Lightning loop had
been hardened in #362/#407. It inherited none of that work.

  * #670 — ``_last_settings`` snapshotted ``lightning_enabled`` but not
    ``hot_seat_enabled``, so "Play again — same settings" fell back to
    ``start_game``'s default (True) and handed a kids' game the auction its
    preset had switched off.
  * #671 — ``_cancel_lightning_loop()`` was called from six places,
    ``_cancel_hot_seat_loop()`` from two. ``start_game``, ``play_again``,
    ``reset_game`` and ``cleanup_game_tasks`` all left the loop running, and
    the loop acted on the auction outcome without re-checking the phase it
    had left, so a reset landing in the last poll interval could be followed
    by a ghost round in the fresh lobby.

The four teardown tests each assert one path, deliberately: two triggers for
the same transition need a test per trigger — the lesson from #656/#657.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT))

from custom_components.quizify.game.state import QuizifyGameState  # noqa: E402
from custom_components.quizify.server.connection import (  # noqa: E402
    ConnectionManager,
)
from custom_components.quizify.server.websocket import (  # noqa: E402
    QuizifyWebSocketHandler,
)


class _FakeRuntime:
    def __init__(self, tmp_path: Path) -> None:
        self.data_dir = tmp_path


def _ws() -> MagicMock:
    ws = MagicMock()
    ws.closed = False
    ws.send_json = AsyncMock()
    ws.close = AsyncMock()
    return ws


@pytest.fixture
def game(tmp_path: Path) -> QuizifyGameState:
    return QuizifyGameState(runtime=_FakeRuntime(tmp_path), entry_id="test")


@pytest.fixture
def handler(game: QuizifyGameState, tmp_path: Path) -> QuizifyWebSocketHandler:
    h = QuizifyWebSocketHandler(
        runtime=_FakeRuntime(tmp_path), game_state_provider=lambda: game
    )
    h._conn = ConnectionManager(_FakeRuntime(tmp_path), lambda: game)
    h._conn.broadcast = AsyncMock()
    return h


def _pending_hot_seat_task(handler: QuizifyWebSocketHandler) -> asyncio.Task:
    """A stand-in for the running auction loop.

    Deliberately not the real loop: this asserts the cancel wiring, and a
    test that had to arrange a live auction to check a teardown path would
    stop testing the teardown the first time the auction's own guards moved.
    """

    async def _sleep_forever() -> None:
        await asyncio.sleep(3600)

    task = asyncio.ensure_future(_sleep_forever())
    handler._hot_seat_task = task
    return task


async def _assert_cancelled(handler: QuizifyWebSocketHandler, task) -> None:
    assert handler._hot_seat_task is None
    await asyncio.sleep(0)
    assert task.cancelled()


# ---------------------------------------------------------------------------
# #670 — the toggle survives the rematch
# ---------------------------------------------------------------------------


def test_hot_seat_toggle_persisted_in_last_settings(game: QuizifyGameState) -> None:
    """A preset that switched the auction off keeps it off on "Play again"."""
    game.start_game(num_rounds=10, hot_seat_enabled=False)
    assert game.last_settings["hot_seat_enabled"] is False


def test_hot_seat_toggle_defaults_are_carried_too(game: QuizifyGameState) -> None:
    game.start_game(num_rounds=10)
    assert game.last_settings["hot_seat_enabled"] is True


# ---------------------------------------------------------------------------
# #671 — every teardown path cancels the loop
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cleanup_game_tasks_cancels_hot_seat_loop(
    handler: QuizifyWebSocketHandler,
) -> None:
    task = _pending_hot_seat_task(handler)
    await handler.cleanup_game_tasks()
    await _assert_cancelled(handler, task)


@pytest.mark.asyncio
async def test_reset_game_cancels_hot_seat_loop(
    handler: QuizifyWebSocketHandler, game: QuizifyGameState
) -> None:
    task = _pending_hot_seat_task(handler)
    await handler._handle_reset_game(_ws(), game)
    await _assert_cancelled(handler, task)


@pytest.mark.asyncio
async def test_start_game_cancels_hot_seat_loop(
    handler: QuizifyWebSocketHandler, game: QuizifyGameState
) -> None:
    """A start that lands during a detour must not leave the loop running."""
    game.add_player("Anna", _ws())
    task = _pending_hot_seat_task(handler)
    await handler._handle_start_game(_ws(), {"num_rounds": 3}, game)
    await _assert_cancelled(handler, task)


@pytest.mark.asyncio
async def test_play_again_cancels_hot_seat_loop(
    handler: QuizifyWebSocketHandler, game: QuizifyGameState
) -> None:
    game.add_player("Anna", _ws())
    game.start_game(num_rounds=3)
    task = _pending_hot_seat_task(handler)
    await handler._handle_play_again(_ws(), game)
    await _assert_cancelled(handler, task)
