"""Regression tests for issue #258 — perf & async throughput.

Locks in two of the #258 fixes:

  * The ~2 MB question bank is loaded exactly once. Preloading it off the
    event loop at `async_setup_entry` makes the inline
    `load_all_categories()` calls in `start_game()` / `LightningRound.start()`
    guaranteed cache hits (guarded by `_loaded`) rather than synchronous disk
    reads on the loop thread.
  * The `ConnectionManager` broadcast helpers serialize the payload once
    (`json.dumps`) and fan out via `send_str` + `asyncio.gather`, so every
    connected client still receives the message and one stalled/erroring
    client does not block delivery to the others.
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT))

from custom_components.quizify.game.questions import QuestionBank  # noqa: E402
from custom_components.quizify.server.connection import ConnectionManager  # noqa: E402


class _FakeRuntime:
    def __init__(self, tmp_path: Path) -> None:
        self.data_dir = tmp_path


def _ws() -> MagicMock:
    ws = MagicMock()
    ws.closed = False
    ws.send_str = AsyncMock()
    ws.send_json = AsyncMock()
    return ws


# ---------------------------------------------------------------------------
# Bank preload: load_all_categories is idempotent and only reads disk once.
# ---------------------------------------------------------------------------


def test_load_all_categories_reads_disk_once(monkeypatch) -> None:
    """After the preload, a second call is a cache hit (no re-glob / re-read).

    Mirrors what `async_setup_entry` buys us: preload once off the loop, then
    `start_game()` / `LightningRound.start()` call into a warm cache.
    """
    bank = QuestionBank()

    calls = {"n": 0}
    real_load_category = bank.load_category

    def _counting_load_category(slug: str):
        calls["n"] += 1
        return real_load_category(slug)

    monkeypatch.setattr(bank, "load_category", _counting_load_category)

    bank.load_all_categories()  # the "preload" at setup
    first = calls["n"]
    assert bank._loaded is True
    assert first > 0  # actually read some packs

    bank.load_all_categories()  # the inline call from start_game — cache hit
    bank.load_all_categories()
    assert calls["n"] == first  # no additional disk reads


def test_reload_forces_fresh_load() -> None:
    """`reload_categories` clears the cache so a forced refresh still works."""
    bank = QuestionBank()
    bank.load_all_categories()
    assert bank._loaded is True
    bank.reload_categories()
    assert bank._loaded is True  # reload re-populates and re-marks loaded


# ---------------------------------------------------------------------------
# Fan-out: serialize once + gather still delivers to every client.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_broadcast_fans_out_to_all_via_send_str(tmp_path: Path) -> None:
    """`broadcast` serializes once and send_str's the same payload to all."""
    conn = ConnectionManager(_FakeRuntime(tmp_path), lambda: None)
    wss = [_ws() for _ in range(4)]
    for ws in wss:
        conn.add_connection(ws, is_admin=False, is_dashboard=False)

    msg = {"type": "lightning_tick", "remaining": 7.0, "index": 2}
    await conn.broadcast(msg)

    expected = json.dumps(msg)
    for ws in wss:
        ws.send_str.assert_awaited_once_with(expected)
        ws.send_json.assert_not_awaited()


@pytest.mark.asyncio
async def test_broadcast_one_stalled_client_does_not_block_others(
    tmp_path: Path,
) -> None:
    """A raising client is swallowed; the rest still receive the message."""
    conn = ConnectionManager(_FakeRuntime(tmp_path), lambda: None)
    good_a, bad, good_b = _ws(), _ws(), _ws()
    bad.send_str = AsyncMock(side_effect=RuntimeError("connection reset"))
    for ws in (good_a, bad, good_b):
        conn.add_connection(ws, is_admin=False, is_dashboard=False)

    # Must not raise even though one send blows up (_safe_send_str swallows).
    await conn.broadcast({"type": "ping"})

    good_a.send_str.assert_awaited_once()
    good_b.send_str.assert_awaited_once()
    bad.send_str.assert_awaited_once()


@pytest.mark.asyncio
async def test_safe_send_str_swallows_errors(tmp_path: Path) -> None:
    """`_safe_send_str` never propagates a send failure."""
    conn = ConnectionManager(_FakeRuntime(tmp_path), lambda: None)
    ws = _ws()
    ws.send_str = AsyncMock(side_effect=ConnectionResetError())
    # Should complete without raising.
    await conn._safe_send_str(ws, "{}")
    ws.send_str.assert_awaited_once_with("{}")


@pytest.mark.asyncio
async def test_concurrent_slow_client_does_not_serialize_room(tmp_path: Path) -> None:
    """gather runs the sends concurrently — a slow client overlaps the fast ones.

    With serial awaits the total time would be ~sum(delays); with gather it is
    ~max(delay). We assert the wall-clock is closer to the max than the sum.
    """
    conn = ConnectionManager(_FakeRuntime(tmp_path), lambda: None)

    async def _slow(_payload: str) -> None:
        await asyncio.sleep(0.05)

    async def _fast(_payload: str) -> None:
        await asyncio.sleep(0.0)

    slow = _ws()
    slow.send_str = AsyncMock(side_effect=_slow)
    fasts = [_ws() for _ in range(3)]
    for ws in fasts:
        ws.send_str = AsyncMock(side_effect=_fast)
    for ws in [slow, *fasts]:
        conn.add_connection(ws, is_admin=False, is_dashboard=False)

    loop = asyncio.get_running_loop()
    start = loop.time()
    await conn.broadcast({"type": "ping"})
    elapsed = loop.time() - start

    # Serial would be >= 0.05 plus the three fast sends; concurrent stays ~0.05.
    assert elapsed < 0.1
    slow.send_str.assert_awaited_once()
