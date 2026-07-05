"""Regression test (#354): reactions that arrive DURING a flush must
still be broadcast.

_flush_reactions_after_window snapshots + clears _reaction_buffer, then
awaits one broadcast per entry. A reaction arriving during those awaits
is appended to the buffer, but _enqueue_reaction only arms a NEW flush
task when the current one is None/done — and mid-flush it is neither.
Previously the flush coroutine returned without re-checking the buffer,
so the late reaction sat unbroadcast until some future reaction happened
to arrive (or was dropped on cleanup).

The fix re-arms the flush task at the end of the coroutine whenever the
buffer is non-empty, looping until the buffer is drained.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT))

from custom_components.quizify.game.state import QuizifyGameState  # noqa: E402
from custom_components.quizify.server.connection import ConnectionManager  # noqa: E402
from custom_components.quizify.server.websocket import (  # noqa: E402
    QuizifyWebSocketHandler,
)


class _FakeRuntime:
    def __init__(self, tmp_path: Path) -> None:
        self.data_dir = tmp_path

    def create_task(self, coro):
        return asyncio.ensure_future(coro)


@pytest.fixture
def game(tmp_path: Path) -> QuizifyGameState:
    runtime = _FakeRuntime(tmp_path)
    return QuizifyGameState(runtime=runtime, entry_id="test")


@pytest.fixture
def handler(game: QuizifyGameState) -> QuizifyWebSocketHandler:
    runtime = _FakeRuntime(game._runtime.data_dir)  # type: ignore[attr-defined]
    h = QuizifyWebSocketHandler(
        runtime=runtime,
        game_state_provider=lambda: game,
    )
    h._conn = ConnectionManager(runtime, lambda: game)
    # Fast window so the tests don't crawl.
    h._REACTION_FLUSH_WINDOW = 0.02
    return h


async def _wait_flush_idle(handler: QuizifyWebSocketHandler, timeout: float = 2.0):
    """Wait until the buffer is drained and no flush task is pending."""
    deadline = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < deadline:
        task = handler._reaction_flush_task
        idle = (task is None or task.done()) and not handler._reaction_buffer
        if idle:
            return
        await asyncio.sleep(handler._REACTION_FLUSH_WINDOW / 2)
    raise AssertionError("flush never went idle (buffer not drained)")


class TestReactionFlushDrain:
    @pytest.mark.asyncio
    async def test_reaction_arriving_during_flush_is_broadcast(
        self, handler: QuizifyWebSocketHandler
    ) -> None:
        broadcasts: list[dict] = []
        injected = {"done": False}

        async def fake_broadcast(msg: dict) -> None:
            broadcasts.append(msg)
            # Simulate a reaction landing WHILE the first flush is awaiting
            # its broadcasts — exactly the window the bug stranded.
            if not injected["done"]:
                injected["done"] = True
                handler._enqueue_reaction("Bob", "🎉")

        handler._conn.broadcast = fake_broadcast  # type: ignore[assignment]

        handler._enqueue_reaction("Alice", "👏")
        await _wait_flush_idle(handler)

        pairs = {(m["player_name"], m["emoji"]) for m in broadcasts}
        assert ("Alice", "👏") in pairs
        # The late reaction was NOT stranded — it got broadcast too.
        assert ("Bob", "🎉") in pairs

    @pytest.mark.asyncio
    async def test_multiple_late_reactions_all_drain(
        self, handler: QuizifyWebSocketHandler
    ) -> None:
        """Several reactions arriving across successive flush passes all
        eventually broadcast; the buffer ends empty (loop terminates)."""
        broadcasts: list[dict] = []
        remaining = ["🔥", "😂", "🎯"]

        async def fake_broadcast(msg: dict) -> None:
            broadcasts.append(msg)
            if remaining:
                handler._enqueue_reaction("Bob", remaining.pop(0))

        handler._conn.broadcast = fake_broadcast  # type: ignore[assignment]

        handler._enqueue_reaction("Alice", "👏")
        await _wait_flush_idle(handler)

        pairs = {(m["player_name"], m["emoji"]) for m in broadcasts}
        for emoji in ("👏", "🔥", "😂", "🎯"):
            assert ("Bob", emoji) in pairs or ("Alice", emoji) in pairs
        assert not handler._reaction_buffer

    @pytest.mark.asyncio
    async def test_no_late_reaction_terminates_cleanly(
        self, handler: QuizifyWebSocketHandler
    ) -> None:
        """The happy path still stops after one window — no runaway
        re-arming when nothing arrives during the flush."""
        handler._conn.broadcast = AsyncMock()

        handler._enqueue_reaction("Alice", "👏")
        await _wait_flush_idle(handler)

        handler._conn.broadcast.assert_awaited_once()
        assert not handler._reaction_buffer
        assert (
            handler._reaction_flush_task is None
            or handler._reaction_flush_task.done()
        )
