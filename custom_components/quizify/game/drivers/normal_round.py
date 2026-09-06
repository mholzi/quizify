"""The normal round's countdown loop (#203/#413), lifted out of the transport
handler by #788.

The *timing* — which players have a live timer, each one's authoritative
remaining time (so time-boost / freeze power-ups are reflected, #4), the
dashboard minimum and the all-expired stop condition — already lived in the
PhaseController. What lived in the WebSocket handler was everything around it:
the tick cadence, the per-recipient coalescing, the two stop conditions and the
auto-evaluate. Those are the round's rules over time, so they live here now.
"""

from __future__ import annotations

import asyncio
import logging
import math
from typing import Any

from ..phase_controller import TICK_INTERVAL
from ..state import GamePhase
from .protocols import MilestoneSink, RoundBroadcaster

_LOGGER = logging.getLogger(__name__)

__all__ = ["NormalRoundDriver"]


class NormalRoundDriver:
    """Tick a live question down and evaluate the round when it runs out."""

    def __init__(
        self,
        broadcaster: RoundBroadcaster,
        *,
        milestones: MilestoneSink | None = None,
        tick_interval: float = TICK_INTERVAL,
    ) -> None:
        self._out = broadcaster
        self._milestones = milestones
        self._tick_interval = tick_interval

    async def run(self, game_state: Any) -> None:
        try:
            await self._run(game_state)
        except asyncio.CancelledError:
            pass
        except Exception:  # noqa: BLE001
            # #307: any other exception used to kill the tick task silently,
            # leaving the game frozen in QUESTION_ACTIVE with a stuck
            # countdown. Log it loudly so the failure is diagnosable instead of
            # presenting as a mysterious hang.
            _LOGGER.exception("Timer tick loop crashed")

    async def _run(self, game_state: Any) -> None:
        # #413: the client renders ``Math.ceil(remaining)`` WHOLE seconds, but
        # the loop ticks every ~0.5s — so half the frames redraw the same
        # number and are pure waste (at the 20-player cap: 20 sockets × the
        # dead frame, every tick). Remember the last displayed second per
        # recipient and only emit when that second actually changes. The sleep
        # cadence is unchanged, so countdown accuracy and the auto-evaluate
        # timing are unaffected — only the redundant frames are dropped.
        last_shown_by_name: dict[str, int] = {}
        last_dashboard_shown: int | None = None

        while game_state.phase == GamePhase.QUESTION_ACTIVE:
            players = game_state.get_players()
            by_name = {p.name: p for p in players}
            # Ask the timing unit for this tick's per-player remaining plus the
            # shared dashboard minimum (one timer read each).
            tick = game_state.resolve_tick([p.name for p in players])

            changed: dict[str, float] = {}
            for name, remaining in tick.per_player:
                p = by_name.get(name)
                if p is None or not p.connected:
                    continue
                shown = math.ceil(max(0.0, remaining))
                if last_shown_by_name.get(name) == shown:
                    continue
                last_shown_by_name[name] = shown
                changed[name] = remaining

            min_remaining = tick.dashboard_remaining
            # ONE countdown milestone per tick (#789): the spoken warning
            # (#281) and the bus event that drives the faster light pulse
            # (#280) fan out inside the sink, each with its own
            # once-per-round guard.
            self._milestone("time_running_out", min_remaining)
            dash_shown = math.ceil(max(0.0, min_remaining))
            dashboard_remaining: float | None = None
            if dash_shown != last_dashboard_shown:
                last_dashboard_shown = dash_shown
                dashboard_remaining = min_remaining

            if changed or dashboard_remaining is not None:
                await self._out.send_timer_tick(
                    game_state, changed, dashboard_remaining
                )
            await asyncio.sleep(self._tick_interval)

            # Stop if everyone's timer hit zero and the phase is still active.
            # Re-read fresh timers (state can change during the sleep) for the
            # CONNECTED players only; the all-expired decision itself lives in
            # the PhaseController, which ignores players without a timer so a
            # late-joining connected player (e.g. the admin's own
            # /quizify/player tab) can't end the round before their per-player
            # timer exists.
            connected = [p.name for p in players if p.connected]
            if connected and game_state.all_timers_expired(connected):
                break
            # Fallback: all_timers_expired needs at least one live timer to
            # break on, so the loop hangs in every state where the connected
            # players have none. That covers the original case — everyone
            # disconnected mid-question (#255) — and the one it missed:
            # connected players who never got a timer, where NEITHER condition
            # could fire and the loop spun forever with the countdown frozen at
            # 0 (#586). Keying the fallback on "no live timers" instead of "no
            # connected players" covers both with one condition.
            if (
                not game_state.has_live_timers(connected)
                and game_state.round_wall_clock_expired()
            ):
                break

        # Timer expired globally. The state machine's
        # _fire_broadcast("round_evaluated") handles the summary broadcast — do
        # NOT broadcast here.
        if game_state.phase == GamePhase.QUESTION_ACTIVE:
            game_state.evaluate_round()

    def _milestone(self, name: str, *args: Any) -> None:
        """Fire one house beat, if a sink is wired."""
        sink = self._milestones
        if sink is None:
            return
        try:
            getattr(sink, name)(*args)
        except Exception:  # noqa: BLE001
            _LOGGER.exception("Round milestone %s raised", name)
