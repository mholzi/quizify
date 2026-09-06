"""The Hot Seat's control loop (#616/#804), lifted out of the transport
handler by #788.

Auction → sealed reveal → one question for the chair → settlement. Four rules
are worth naming, because they are the reason this is a driver and not an
adapter: the auction closes early once every connected player has bid; the bid
reveal is held for a beat so the room can read it; an auction nobody bid on is
not a failure but a round that does not happen; and the chair is settled even
when nothing was answered, because it was bought either way (#653).

The phase re-checks after each wait are #671: a reset or an end_game that lands
inside the last poll interval must not be followed by a ghost round or a
settlement against a game that no longer exists.
"""

from __future__ import annotations

import asyncio
import logging
import math
from typing import Any

from ..state import GamePhase
from .protocols import HotSeatBroadcaster, MilestoneSink

_LOGGER = logging.getLogger(__name__)

__all__ = ["HotSeatDriver"]


class HotSeatDriver:
    """Drive auction → reveal → question → settlement for the Hot Seat."""

    #: Poll cadence for both windows. Ticks go out on the ceil() change.
    POLL_INTERVAL = 0.25

    def __init__(
        self,
        broadcaster: HotSeatBroadcaster,
        *,
        milestones: MilestoneSink | None = None,
        reveal_hold: float = 4.0,
    ) -> None:
        self._out = broadcaster
        self._milestones = milestones
        self._reveal_hold = reveal_hold

    async def run(self, game_state: Any) -> None:
        try:
            await self._run(game_state)
        except asyncio.CancelledError:
            pass
        except Exception:  # noqa: BLE001
            _LOGGER.exception("Hot seat loop crashed")

    async def _run(self, game_state: Any) -> None:
        hs = game_state.hot_seat
        if hs is None:
            return

        # --- bidding window ---------------------------------------------
        await self._wait_out(
            game_state,
            hs,
            phase=GamePhase.HOT_SEAT_AUCTION,
            stage="auction",
            done=lambda: hs.all_bid(
                [p.name for p in game_state.get_players() if p.connected]
            ),
        )
        # #671: the wait above only checks the phase INSIDE it. Leaving it
        # (expired, or everyone bid) and acting without a re-check lets a reset
        # that landed in the last poll interval be followed by a ghost round in
        # the fresh lobby.
        if game_state.phase != GamePhase.HOT_SEAT_AUCTION:
            return

        winner = game_state.close_hot_seat_auction()
        if winner is None:
            # Nobody wanted the chair. Not a failure — just a round that does
            # not happen. Fall back to the normal question so the game keeps
            # moving.
            await self._out.send_hot_seat_no_bids()
            game_state.abort_hot_seat()
            await self._out.resume_normal_question(game_state)
            return

        # --- simultaneous reveal ----------------------------------------
        await self._out.send_hot_seat_awarded(game_state, hs)
        await asyncio.sleep(self._reveal_hold)
        if game_state.phase != GamePhase.HOT_SEAT:
            return

        # --- the question ------------------------------------------------
        await self._out.send_hot_seat_question(game_state, hs)
        # #708: the chair's question is an ordinary question as far as the
        # house is concerned — the narrator reads it and the bus event fires,
        # exactly as in a normal round. Before #788 these hooks were reachable
        # from the normal-round path only, so the room went quiet for the whole
        # detour. Fired after the fan-out for the same reason the normal round
        # does it there: the players see it before the room hears it. The
        # spoken options are deliberately withheld — only the seat holder has
        # answer buttons, and reading the options to the room would hand the
        # spectators a board the mode does not give them.
        self._milestone(
            "question_shown",
            hs.question,
            game_state.round,
            game_state.total_rounds,
            None,
        )
        hs.start_answer_clock()
        await self._wait_out(
            game_state,
            hs,
            phase=GamePhase.HOT_SEAT,
            stage="question",
            done=lambda: hs.answered is not None,
            countdown=True,
        )
        # #671: same re-check as after the auction wait — a teardown that lands
        # in the last poll interval must not be followed by a settlement
        # against a game that no longer exists.
        if game_state.phase != GamePhase.HOT_SEAT:
            return

        # Settle even when nothing was answered: the chair was bought either
        # way (#653). This is the one place the mode parts ways with the
        # finale's forgiving timeout.
        game_state.finish_hot_seat()
        await self._out.send_hot_seat_result(game_state, hs)
        # #708: and the reveal beat, so the lights and the narrator close the
        # detour the way they close an ordinary round.
        self._milestone("reveal", game_state)

    async def _wait_out(
        self,
        game_state: Any,
        hs: Any,
        *,
        phase: GamePhase,
        stage: str,
        done: Any,
        countdown: bool = False,
    ) -> None:
        """Poll one of the two windows until it expires or ``done()``.

        Emits a tick only when the displayed whole second changes, and returns
        early (without settling anything) as soon as the phase moves on — the
        caller re-checks, because leaving the wait is not the same as the wait
        having finished.
        """
        last_shown = None
        while not hs.is_expired():
            if game_state.phase != phase:
                return
            remaining = hs.time_remaining()
            shown = math.ceil(remaining)
            if shown != last_shown:
                await self._out.send_hot_seat_tick(stage, shown)
                last_shown = shown
            if countdown:
                # #708: the same per-tick beat the normal round pushes, so the
                # "time running out" warning and the faster light pulse reach
                # the chair's window too. The consumers keep their own
                # once-per-window guards.
                self._milestone("time_running_out", remaining)
            if done():
                return
            await asyncio.sleep(self.POLL_INTERVAL)

    def _milestone(self, name: str, *args: Any) -> None:
        """Fire one house beat, if a sink is wired.

        Guarded here as well as in the sink: a driver must never lose a game
        loop to a misbehaving consumer.
        """
        sink = self._milestones
        if sink is None:
            return
        try:
            getattr(sink, name)(*args)
        except Exception:  # noqa: BLE001
            _LOGGER.exception("Hot seat milestone %s raised", name)
