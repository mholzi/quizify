"""Marker writes are serialized: the disk ends on the last noted phase (#1223)."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from custom_components.quizify.runtime import StandaloneRuntime
from custom_components.quizify.server.interrupted_game import InterruptedGameMarker


async def _marker(tmp_path: Path) -> InterruptedGameMarker:
    m = InterruptedGameMarker(StandaloneRuntime(data_dir=tmp_path))
    await m.load()
    return m


@pytest.mark.asyncio
async def test_rapid_start_then_reset_leaves_no_file(tmp_path: Path) -> None:
    m = await _marker(tmp_path)
    m.note_phase("QUESTION_ACTIVE")
    m.note_phase("LOBBY")
    await m.drain()
    assert not (tmp_path / "game_running.json").exists()


@pytest.mark.asyncio
async def test_rapid_reset_then_start_leaves_the_file(tmp_path: Path) -> None:
    m = await _marker(tmp_path)
    m.note_phase("QUESTION_ACTIVE")
    m.note_phase("LOBBY")
    m.note_phase("QUESTION_ACTIVE")
    await m.drain()
    assert (tmp_path / "game_running.json").is_file()


@pytest.mark.asyncio
async def test_many_flips_end_on_the_last_phase(tmp_path: Path) -> None:
    m = await _marker(tmp_path)
    for i in range(9):
        m.note_phase("QUESTION_ACTIVE" if i % 2 == 0 else "LOBBY")
        await asyncio.sleep(0)
    await m.drain()
    assert (tmp_path / "game_running.json").is_file()
    m.note_phase("FINALE")
    await m.drain()
    assert not (tmp_path / "game_running.json").exists()


@pytest.mark.asyncio
async def test_drain_is_idle_without_pending_writes(tmp_path: Path) -> None:
    m = await _marker(tmp_path)
    await m.drain()
    assert not m._tasks
