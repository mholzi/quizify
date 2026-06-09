"""Unit tests for the extracted BroadcastDispatcher (#184)."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT))

from custom_components.quizify.server.broadcast_dispatcher import (  # noqa: E402
    BroadcastDispatcher,
)


def _make(calls: list[str]):
    async def handler(name: str) -> None:
        calls.append(name)

    return BroadcastDispatcher(
        handlers={
            "round_evaluated": lambda: handler("round_evaluated"),
            "game_ended": lambda: handler("game_ended"),
        },
        default=lambda: handler("default"),
    )


def _run(coro):
    return asyncio.run(coro)


class TestBroadcastDispatcher:
    def test_routes_round_evaluated(self):
        calls: list[str] = []
        _run(_make(calls).dispatch({"event": "round_evaluated"}))
        assert calls == ["round_evaluated"]

    def test_routes_game_ended(self):
        calls: list[str] = []
        _run(_make(calls).dispatch({"event": "game_ended"}))
        assert calls == ["game_ended"]

    def test_unknown_event_falls_back_to_default(self):
        calls: list[str] = []
        _run(_make(calls).dispatch({"event": "something_else"}))
        assert calls == ["default"]

    def test_none_payload_falls_back_to_default(self):
        calls: list[str] = []
        _run(_make(calls).dispatch(None))
        assert calls == ["default"]

    def test_payload_without_event_falls_back_to_default(self):
        calls: list[str] = []
        _run(_make(calls).dispatch({"type": "game_state"}))
        assert calls == ["default"]
