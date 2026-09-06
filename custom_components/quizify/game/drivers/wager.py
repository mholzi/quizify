"""The final round's betting window (#656), lifted out of the transport handler
by #788.

One rule, and it is the whole point of the object: the window ends at its
deadline whether or not the players cooperate. An AFK phone — or a room that
all walked out — used to hang the final round on the betting screen forever,
the same class of hang as #586, so this gets an unconditional timer rather than
a condition that depends on clients behaving.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from .protocols import WagerBroadcaster

_LOGGER = logging.getLogger(__name__)

__all__ = ["WagerWindowDriver"]


class WagerWindowDriver:
    """Hold the betting window open for its duration, then close it."""

    def __init__(self, broadcaster: WagerBroadcaster, *, duration: float) -> None:
        self._out = broadcaster
        self._duration = duration

    async def run(self, game_state: Any) -> None:
        try:
            await asyncio.sleep(self._duration)
        except asyncio.CancelledError:
            return
        try:
            await self._out.close_wager_window(game_state)
        except Exception:  # noqa: BLE001 — a stuck window strands the game
            _LOGGER.exception("Wager window failed to close")
