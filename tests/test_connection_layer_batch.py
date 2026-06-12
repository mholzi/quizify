"""Regression tests for the connection-layer fixes in the 2026-06-11 batch.

* #310 — _pending_removals self-cleans when a removal task finishes, and an
         existing pending task is cancelled before being overwritten (no
         unbounded growth, no orphaned task).
* #307 — a stalled WebSocket send is bounded by a per-send timeout so it can't
         pin the whole broadcast fan-out.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT))

from custom_components.quizify.server.connection import ConnectionManager  # noqa: E402


class _FakeRuntime:
    def __init__(self, tmp_path: Path) -> None:
        self.data_dir = tmp_path


@pytest.fixture
def conn(tmp_path: Path) -> ConnectionManager:
    return ConnectionManager(_FakeRuntime(tmp_path), lambda: None)


# ---------------------------------------------------------------------------
# #310 — _pending_removals bounded
# ---------------------------------------------------------------------------


class TestPendingRemovalsBounded:
    @pytest.mark.asyncio
    async def test_completed_task_pops_itself(self, conn: ConnectionManager) -> None:
        """A removal task that runs to completion must drop its own registry
        entry — otherwise one done-Task per removed player accumulates forever.
        """
        async def _noop(name: str, timeout: float) -> None:
            return None

        conn.schedule_player_removal("Alice", 0.0, _noop)
        assert "Alice" in conn._pending_removals
        # Let the task complete; the done-callback then pops the entry.
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        assert "Alice" not in conn._pending_removals

    @pytest.mark.asyncio
    async def test_reschedule_cancels_previous_task(
        self, conn: ConnectionManager
    ) -> None:
        """Re-scheduling a removal for the same name must cancel the prior
        still-pending task instead of silently dropping it (orphan leak)."""
        started: list[str] = []

        async def _slow(name: str, timeout: float) -> None:
            started.append(name)
            await asyncio.sleep(60)

        conn.schedule_player_removal("Bob", 60.0, _slow)
        first = conn._pending_removals["Bob"]
        conn.schedule_player_removal("Bob", 60.0, _slow)
        second = conn._pending_removals["Bob"]

        await asyncio.sleep(0)
        assert first is not second
        assert first.cancelled() or first.done()
        assert not second.done()
        second.cancel()

    @pytest.mark.asyncio
    async def test_cancel_pending_removal_still_works(
        self, conn: ConnectionManager
    ) -> None:
        """The reconnect cancel path is unaffected by the done-callback."""
        async def _slow(name: str, timeout: float) -> None:
            await asyncio.sleep(60)

        conn.schedule_player_removal("Carol", 60.0, _slow)
        assert "Carol" in conn._pending_removals
        conn.cancel_pending_removal("Carol")
        assert "Carol" not in conn._pending_removals


# ---------------------------------------------------------------------------
# #307 — per-send timeout
# ---------------------------------------------------------------------------


class TestSendTimeout:
    @pytest.mark.asyncio
    async def test_broadcast_str_send_times_out_and_is_swallowed(
        self, conn: ConnectionManager, monkeypatch
    ) -> None:
        """A WebSocket whose send_str hangs must be bounded by the per-send
        timeout and swallowed, not pin the broadcast (#307)."""
        # Shrink the timeout so the test is fast.
        monkeypatch.setattr(ConnectionManager, "_SEND_TIMEOUT", 0.05)

        stalled = MagicMock()
        stalled.closed = False

        async def _hang(_payload):
            await asyncio.sleep(60)

        stalled.send_str = _hang

        # Should return promptly (within the timeout), not after 60s.
        await asyncio.wait_for(
            conn._safe_send_str(stalled, "{}"), timeout=1.0
        )

    @pytest.mark.asyncio
    async def test_broadcast_continues_past_stalled_client(
        self, conn: ConnectionManager, monkeypatch
    ) -> None:
        """One stalled client must not prevent a healthy client from receiving
        the broadcast."""
        monkeypatch.setattr(ConnectionManager, "_SEND_TIMEOUT", 0.05)
        delivered: list[str] = []

        healthy = MagicMock()
        healthy.closed = False

        async def _ok(payload):
            delivered.append(payload)

        healthy.send_str = _ok

        stalled = MagicMock()
        stalled.closed = False

        async def _hang(_payload):
            await asyncio.sleep(60)

        stalled.send_str = _hang

        conn.connections.add(healthy)
        conn.connections.add(stalled)

        await asyncio.wait_for(conn.broadcast({"type": "ping"}), timeout=1.0)
        assert delivered, "healthy client never received the broadcast"
