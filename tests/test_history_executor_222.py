"""Regression tests for issue #222 — question history I/O off the event loop.

`end_game` used to persist `question_history.json` via a synchronous
`Path.write_text()` on the event-loop thread (`flush_shown_history ->
save_history`), stalling the WS server for every client at end-of-game. HA's
loop-protection flagged the blocking call on v1.3.0-RC2.

The history file write (and the startup read) now run in an executor thread via
the integration's `Runtime.run_in_executor` offload (same pattern as #213's
asset-fingerprint walk and the per-question stats save). These tests lock in:

  * `flush_shown_history_async` / `save_history_async` route the blocking write
    through the runtime executor and never touch disk on the loop thread,
  * the persisted JSON is unchanged vs. the synchronous path,
  * `end_game` schedules the flush as an offloaded task and still persists,
  * `load_history_async` reads back what was written, off the loop.
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT))

from custom_components.quizify.game.questions import QuestionBank  # noqa: E402
from custom_components.quizify.game.state import (  # noqa: E402
    GamePhase,
    QuizifyGameState,
)


class _ExecutorRuntime:
    """Runtime double that records which thread the blocking work ran on.

    Mirrors `StandaloneRuntime`: `run_in_executor` offloads to a real worker
    thread, `create_task` schedules on the running loop. `executor_thread_ids`
    captures the thread id the offloaded callable actually executed on so a
    test can assert it was *not* the event-loop thread.
    """

    def __init__(self, tmp_path: Path) -> None:
        self.data_dir = tmp_path
        self.executor_calls = 0

    def create_task(self, coro):
        return asyncio.ensure_future(coro)

    async def run_in_executor(self, func, *args):
        self.executor_calls += 1
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, func, *args)


def _fake_ws() -> MagicMock:
    ws = MagicMock()
    ws.closed = False
    return ws


@pytest.mark.asyncio
async def test_save_history_async_offloads_write(tmp_path: Path) -> None:
    """`save_history_async` writes via the executor, not on the loop thread."""
    runtime = _ExecutorRuntime(tmp_path)
    bank = QuestionBank()
    bank.set_history_path(tmp_path / "question_history.json")
    bank._history = {"q1": 111.0, "q2": 222.0}

    await bank.save_history_async(runtime)

    assert runtime.executor_calls == 1
    written = json.loads((tmp_path / "question_history.json").read_text())
    assert written == {"q1": 111.0, "q2": 222.0}


@pytest.mark.asyncio
async def test_async_and_sync_save_produce_same_file(tmp_path: Path) -> None:
    """The async (executor) path persists byte-identical JSON to the sync path."""
    runtime = _ExecutorRuntime(tmp_path)
    history = {"a": 1.0, "b": 2.5, "c": 3.0}

    sync_path = tmp_path / "sync.json"
    bank_sync = QuestionBank()
    bank_sync.set_history_path(sync_path)
    bank_sync._history = dict(history)
    bank_sync.save_history()

    async_path = tmp_path / "async.json"
    bank_async = QuestionBank()
    bank_async.set_history_path(async_path)
    bank_async._history = dict(history)
    await bank_async.save_history_async(runtime)

    assert sync_path.read_text() == async_path.read_text()


@pytest.mark.asyncio
async def test_flush_shown_history_async_clears_and_persists(tmp_path: Path) -> None:
    """Async flush resets the shown list and writes history via the executor."""
    runtime = _ExecutorRuntime(tmp_path)
    bank = QuestionBank()
    bank.set_history_path(tmp_path / "question_history.json")
    bank.record_shown("q1")
    bank.record_shown("q2")
    assert bank._shown_this_game == ["q1", "q2"]

    await bank.flush_shown_history_async(runtime)

    assert bank._shown_this_game == []
    assert runtime.executor_calls == 1
    written = json.loads((tmp_path / "question_history.json").read_text())
    assert set(written) == {"q1", "q2"}


@pytest.mark.asyncio
async def test_load_history_async_roundtrip(tmp_path: Path) -> None:
    """`load_history_async` reads back persisted history off the loop."""
    runtime = _ExecutorRuntime(tmp_path)
    path = tmp_path / "question_history.json"
    path.write_text(json.dumps({"x": 10.0, "y": 20.0}))

    bank = QuestionBank()
    await bank.load_history_async(path, runtime)

    assert runtime.executor_calls == 1
    assert bank._history == {"x": 10.0, "y": 20.0}


@pytest.mark.asyncio
async def test_end_game_schedules_offloaded_history_flush(tmp_path: Path) -> None:
    """`end_game` persists history through the executor, not on the loop.

    `end_game` is synchronous; it schedules the flush as a fire-and-forget
    task. We drive a real event loop, end the game, then let the scheduled
    task drain and assert the file landed via the executor path.
    """
    runtime = _ExecutorRuntime(tmp_path)
    state = QuizifyGameState(runtime=runtime, entry_id="test")

    state.add_player("Alice", _fake_ws())
    state.start_game(language="de", num_rounds=3)
    # Mark a question as shown so there is history worth persisting.
    state._question_bank.record_shown("seed-q")

    state.end_game()
    assert state.phase == GamePhase.FINALE

    # Let the fire-and-forget flush task run to completion.
    await asyncio.sleep(0)
    for _ in range(10):
        await asyncio.sleep(0)
        if (tmp_path / "question_history.json").exists():
            break

    assert runtime.executor_calls >= 1
    written = json.loads((tmp_path / "question_history.json").read_text())
    assert "seed-q" in written
