"""The Lightning Round's control loop (#42/#201/#285), lifted out of the
transport handler by #788.

Five questions, a fixed window each, no reveal in between; the window is cut
short the moment every connected player has answered. All of that is a *rule*,
which is why it lives here now and not next to the socket sends. The scoring
and the question queue stay in :mod:`custom_components.quizify.game.lightning`;
this only decides when things happen and asks the broadcaster to fan them out.

It deliberately takes no ``MilestoneSink``. The house *should* play along here
(#708), but not by reusing the normal round's beats: ``announce_question``
reads a question aloud, and five of those inside seventy-five seconds would
talk over the mode it is meant to accompany. Lightning needs its own phrases,
its own light recipe and its own bus event, which is #708's remaining half.
"""

from __future__ import annotations

import asyncio
import logging
import math
from typing import Any

from ..state import GamePhase
from .protocols import LightningBroadcaster

_LOGGER = logging.getLogger(__name__)

__all__ = ["LightningDriver"]


class LightningDriver:
    """Drive splash → question → advance → recap for the fast mode."""

    #: How often the wait polls for "everybody answered". The display shows
    #: whole seconds, so ticks are emitted on the ceil() change, not per poll.
    POLL_INTERVAL = 0.25

    def __init__(
        self,
        broadcaster: LightningBroadcaster,
        *,
        splash_grace: float = 1.0,
        splash_hold: float = 3.0,
    ) -> None:
        self._out = broadcaster
        self._splash_grace = splash_grace
        self._splash_hold = splash_hold

    async def run(
        self, game_state: Any, *, auto_dismiss_splash: bool = False
    ) -> None:
        """Run the whole mode to its recap.

        With ``auto_dismiss_splash`` (the #285 auto flow) the loop first holds
        the intro splash for ``splash_hold`` seconds and dismisses it itself —
        there is no host "Start" tap any more.
        """
        try:
            await self._run(game_state, auto_dismiss_splash=auto_dismiss_splash)
        except asyncio.CancelledError:
            pass
        except Exception:  # noqa: BLE001
            # #307: an unexpected exception here used to kill the lightning
            # task silently, hanging the round on the current question with a
            # frozen clock. Log it so the failure surfaces.
            _LOGGER.exception("Lightning loop crashed")

    async def _run(
        self, game_state: Any, *, auto_dismiss_splash: bool
    ) -> None:
        if auto_dismiss_splash:
            # Hold the intro splash on its own, then advance out of it.
            await asyncio.sleep(self._splash_hold)
            if game_state.phase != GamePhase.LIGHTNING:
                return
            game_state.begin_lightning_questions()
        # Brief grace so clients swap from the intro splash (#201) to the
        # question view before the first clock starts ticking.
        await asyncio.sleep(self._splash_grace)

        while game_state.phase == GamePhase.LIGHTNING:
            lr = game_state.lightning
            if lr is None:
                return
            await self._out.send_lightning_question(game_state, lr)
            # Re-arm the question clock now (after the broadcast) so the
            # countdown the players see matches the server window.
            lr.restart_clock()
            if not await self._wait_out_question(game_state, lr):
                return
            # #746, the re-check the hot-seat loop got in #671 and this one did
            # not: the wait only tests the phase AFTER a sleep, so the
            # all-answered ``break`` leaves it untested. An end_game landing in
            # that gap was still followed by ``lr.advance()`` — a lightning
            # question scored, and a recap frame broadcast, after the finale.
            if game_state.phase != GamePhase.LIGHTNING:
                return
            # No reveal — score silently and arm the next question.
            if not lr.advance():
                game_state.finish_lightning_round()
                await self._out.send_lightning_recap(game_state)
                return

    async def _wait_out_question(self, game_state: Any, lr: Any) -> bool:
        """Wait for the fixed window or for everyone to answer.

        Returns False when the phase left LIGHTNING mid-wait, i.e. the caller
        must not settle anything.
        """
        deadline = lr.seconds_per_question
        waited = 0.0
        step = self.POLL_INTERVAL
        # The display shows whole seconds (1 Hz), so only push a tick when the
        # ceil(remaining) actually changes — no point broadcasting at the 4 Hz
        # poll cadence (#258).
        last_shown = None
        while waited < deadline:
            shown = math.ceil(lr.time_remaining())
            if shown != last_shown:
                await self._out.send_lightning_tick(game_state, lr)
                last_shown = shown
            connected = [p.name for p in game_state.get_players() if p.connected]
            if lr.all_connected_answered(connected):
                return True
            await asyncio.sleep(step)
            waited += step
            if game_state.phase != GamePhase.LIGHTNING:
                return False
        return True
