"""Regression tests for issue #362 — the deferred admin-pause task must be
cancelled on game teardown/reset, and must surface its own crashes.

Background: when the admin-as-player WS closes mid-question, the server
schedules a deferred ``_admin_pause_task`` (fires ``ADMIN_REDIRECT_GRACE``
seconds later unless the admin reconnects). Before the fix, neither
``cleanup_game_tasks()`` nor ``_handle_reset_game()`` cancelled that task,
so a teardown/reset could leave it pending — it would then fire a spurious
"admin_disconnected" pause against the fresh lobby (or leak a reference and
emit "Task exception was never retrieved" at GC time, because it was created
with a bare ``ensure_future`` and no done-callback).

The fix:
  1. ``cleanup_game_tasks()`` and ``_handle_reset_game()`` both call
     ``_cancel_admin_pause()``.
  2. The pause task gets ``add_done_callback(self._log_task_exception)`` so a
     crash in the deferred loop is logged instead of silently swallowed.
"""

from __future__ import annotations

import asyncio
import logging
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT))

from custom_components.quizify.game.state import (  # noqa: E402
    QuizifyGameState,
)
from custom_components.quizify.server.connection import ConnectionManager  # noqa: E402
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
    runtime = _FakeRuntime(tmp_path)
    return QuizifyGameState(runtime=runtime, entry_id="test")


@pytest.fixture
def handler(game: QuizifyGameState, tmp_path: Path) -> QuizifyWebSocketHandler:
    runtime = _FakeRuntime(tmp_path)
    h = QuizifyWebSocketHandler(runtime=runtime, game_state_provider=lambda: game)
    h._conn = ConnectionManager(runtime, lambda: game)
    h._conn.broadcast = AsyncMock()
    # Keep the grace long so the scheduled pause stays pending for the
    # cancellation assertions (it must not fire on its own during the test).
    h.ADMIN_REDIRECT_GRACE = 30.0
    return h


@pytest.mark.asyncio
async def test_cleanup_game_tasks_cancels_pending_admin_pause(
    handler: QuizifyWebSocketHandler,
) -> None:
    """A scheduled admin-pause task is cancelled by cleanup_game_tasks()."""
    handler._schedule_admin_pause("Admin")
    task = handler._admin_pause_task
    assert task is not None
    assert not task.done()

    await handler.cleanup_game_tasks()

    # The handle is cleared and the underlying task is actually cancelled.
    assert handler._admin_pause_task is None
    await asyncio.sleep(0)  # let the cancellation propagate
    assert task.cancelled()


@pytest.mark.asyncio
async def test_reset_game_cancels_pending_admin_pause(
    handler: QuizifyWebSocketHandler, game: QuizifyGameState
) -> None:
    """A scheduled admin-pause task is cancelled by _handle_reset_game()."""
    handler._schedule_admin_pause("Admin")
    task = handler._admin_pause_task
    assert task is not None
    assert not task.done()

    admin_ws = _ws()
    await handler._handle_reset_game(admin_ws, game)

    assert handler._admin_pause_task is None
    await asyncio.sleep(0)
    assert task.cancelled()


@pytest.mark.asyncio
async def test_admin_pause_task_surfaces_its_exception(
    handler: QuizifyWebSocketHandler, caplog: pytest.LogCaptureFixture
) -> None:
    """The pause task carries the _log_task_exception done-callback (#362),
    so a crash inside the deferred loop is logged rather than swallowed."""
    handler.ADMIN_REDIRECT_GRACE = 0.0

    def _boom() -> QuizifyGameState:
        raise RuntimeError("boom")

    handler._get_game_state = _boom  # type: ignore[assignment]

    with caplog.at_level(logging.ERROR):
        handler._schedule_admin_pause("Admin")
        task = handler._admin_pause_task
        assert task is not None
        with pytest.raises(RuntimeError):
            await task

    assert any(
        "Unhandled exception in background task" in rec.getMessage()
        for rec in caplog.records
    )
