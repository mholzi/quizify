"""Lightning driver: a cancelled round is logged, a stalled one is finished."""

from __future__ import annotations

import asyncio
import logging
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from custom_components.quizify.game.drivers.lightning import (  # noqa: E402
    LightningDriver,
)
from custom_components.quizify.game.state import GamePhase  # noqa: E402

_LOG = "custom_components.quizify.game.drivers.lightning"


def _state(seconds: float = 0.2, questions: int = 5):
    lr = MagicMock()
    lr.index = 1
    lr.seconds_per_question = seconds
    lr.time_remaining.return_value = seconds
    lr.all_connected_answered.return_value = False
    lr.advance.return_value = True
    gs = MagicMock()
    gs.phase = GamePhase.LIGHTNING
    gs.lightning = lr
    gs.get_players.return_value = []

    def _finish() -> None:
        gs.phase = (
            GamePhase.LIGHTNING_RECAP
            if hasattr(GamePhase, "LIGHTNING_RECAP")
            else GamePhase.LOBBY
        )

    gs.finish_lightning_round.side_effect = _finish
    return gs


def _out(question=None):
    out = SimpleNamespace()
    out.send_lightning_question = question or AsyncMock()
    out.send_lightning_tick = AsyncMock()
    out.send_lightning_recap = AsyncMock()
    return out


def _driver(out) -> LightningDriver:
    d = LightningDriver(out, splash_grace=0.0, splash_hold=0.0)
    d.POLL_INTERVAL = 0.02
    d.WATCHDOG_INTERVAL = 0.02
    return d


def _warnings(caplog):
    return [r for r in caplog.records if r.levelno >= logging.WARNING]


async def test_cancel_during_lightning_warns(caplog) -> None:
    caplog.set_level(logging.DEBUG, logger=_LOG)
    gs, out = _state(seconds=5), _out()
    task = asyncio.ensure_future(_driver(out).run(gs))
    await asyncio.sleep(0.1)
    task.cancel()
    await task
    msgs = [r.getMessage() for r in _warnings(caplog)]
    assert any("cancelled while the round is still running" in m for m in msgs)
    assert any("index 1" in m for m in msgs)


async def test_cancel_after_phase_change_is_quiet(caplog) -> None:
    caplog.set_level(logging.DEBUG, logger=_LOG)
    gs, out = _state(seconds=5), _out()
    task = asyncio.ensure_future(_driver(out).run(gs))
    await asyncio.sleep(0.1)
    gs.phase = GamePhase.LOBBY  # end/reset changed the phase first
    task.cancel()
    await task
    assert not _warnings(caplog)


async def test_watchdog_finishes_stalled_round_once(caplog) -> None:
    caplog.set_level(logging.DEBUG, logger=_LOG)
    gs = _state(seconds=0.1)

    async def _hang(*_a, **_k):
        await asyncio.sleep(3600)

    out = _out(question=_hang)
    task = asyncio.ensure_future(_driver(out).run(gs))
    await asyncio.wait_for(task, timeout=3)
    await asyncio.sleep(0.1)
    assert gs.finish_lightning_round.call_count == 1
    assert out.send_lightning_recap.await_count == 1
    msgs = [r.getMessage() for r in _warnings(caplog)]
    assert sum("stalled" in m for m in msgs) == 1
    assert not any("cancelled while" in m for m in msgs)


async def test_watchdog_quiet_when_questions_flow(caplog) -> None:
    caplog.set_level(logging.DEBUG, logger=_LOG)
    gs, out = _state(seconds=0.1), _out()
    calls = {"n": 0}

    def _adv() -> bool:
        calls["n"] += 1
        return calls["n"] < 4  # 4 questions, ~0.1 s each, > 2 x window total

    gs.lightning.advance.side_effect = _adv
    await asyncio.wait_for(_driver(out).run(gs), timeout=5)
    assert out.send_lightning_recap.await_count == 1
    assert gs.finish_lightning_round.call_count == 1
    assert not any("stalled" in r.getMessage() for r in _warnings(caplog))
