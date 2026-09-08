"""The final round's betting window (#656), lifted out of the transport handler
by #788.

One rule, and it is the whole point of the object: the window ends at its
deadline whether or not the players cooperate. An AFK phone — or a room that
all walked out — used to hang the final round on the betting screen forever,
the same class of hang as #586, so this gets an unconditional timer rather than
a condition that depends on clients behaving.

The window also reports one house beat (#708). It is the only detour moment
with no counterpart in an ordinary round — the question is loaded but withheld,
nobody is answering anything, and the room used to hold whatever colour the
previous reveal left it for the whole time the bets were being placed.
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any

from .protocols import MilestoneSink, WagerBroadcaster, fire_milestone

if TYPE_CHECKING:
    from ..state import QuizifyGameState

_LOGGER = logging.getLogger(__name__)

__all__ = ["WagerWindowDriver"]


class WagerWindowDriver:
    """Hold the betting window open for its duration, then close it."""

    def __init__(
        self,
        broadcaster: WagerBroadcaster,
        *,
        duration: float,
        milestones: MilestoneSink | None = None,
    ) -> None:
        self._out = broadcaster
        self._duration = duration
        self._milestones = milestones

    async def run(self, game_state: QuizifyGameState) -> None:
        # #708: reported before the wait, not after it — the beat is "bets are
        # open", and the room should change as the window appears rather than
        # as it closes. The deadline travels with it so a blueprint can time a
        # build against the same clock the players see.
        self._milestone("wager_open", game_state, self._duration)
        try:
            await asyncio.sleep(self._duration)
        except asyncio.CancelledError:
            return
        try:
            await self._out.close_wager_window(game_state)
        except Exception:  # noqa: BLE001 — a stuck window strands the game
            _LOGGER.exception("Wager window failed to close")

    def _milestone(self, name: str, *args: Any) -> None:
        """Fire one house beat, if a sink is wired."""
        fire_milestone(self._milestones, name, *args)
