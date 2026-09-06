"""The two narrow contracts a game-mode driver is allowed to know about (#788).

A driver owns a mode's *rules over time*: how long a window stays open, what
cuts it short, which phase re-check has to happen after every sleep, and what
settles when the window closes. It must not own the wire format — that is the
server layer's job, and mixing the two is what #747 is about. So a driver never
builds a payload dict and never touches a WebSocket. It calls the two protocols
below, and ``server.websocket.QuizifyWebSocketHandler`` implements them.

``Broadcaster`` (one per mode, because the moments differ per mode)
    Semantic fan-out points — "the question is on", "the clock moved", "here is
    the recap". The implementation decides who receives what and in which
    shape; the driver only decides *when*.

``MilestoneSink``
    The house hooks: narration (#281), the HA event bus (#366) and everything
    that hangs off them (lights, SFX). Before #788 these were called from the
    normal-round path only, which is why the house sat out the detour modes
    (#708). Putting the sink on the driver is what makes "every mode walks
    every path" expressible rather than remembered.

Both are :class:`typing.Protocol` s, so the handler satisfies them structurally
— no base class, no import from ``server`` into ``game``.
"""

from __future__ import annotations

from typing import Any, Protocol

__all__ = [
    "HotSeatBroadcaster",
    "LightningBroadcaster",
    "MilestoneSink",
    "RoundBroadcaster",
    "WagerBroadcaster",
]


class MilestoneSink(Protocol):
    """Where a driver reports the beats the house reacts to (#789/#708).

    Every method is fire-and-forget and must never raise: the implementation
    guards each consumer, because a broken TTS entity is not a reason to stall
    a game loop.
    """

    def question_shown(
        self,
        question: Any,
        round_no: int,
        total_rounds: int,
        options: list[str] | None = None,
    ) -> None:
        """A question just went out to the room."""

    def time_running_out(self, seconds_remaining: float) -> None:
        """The current answer window is running down. Called on every tick;
        each consumer keeps its own once-per-round guard and threshold."""

    def reveal(self, game_state: Any) -> None:
        """The answer is out and the round has been scored."""


class LightningBroadcaster(Protocol):
    """The Lightning Round's fan-out points (#42/#201)."""

    async def send_lightning_question(self, game_state: Any, lr: Any) -> None:
        """Push the current lightning question (own shuffle per phone)."""

    async def send_lightning_tick(self, game_state: Any, lr: Any) -> None:
        """Push the shared lightning countdown."""

    async def send_lightning_recap(self, game_state: Any) -> None:
        """Push the end-of-mode recap."""


class HotSeatBroadcaster(Protocol):
    """The Hot Seat's fan-out points (#616/#804)."""

    async def send_hot_seat_tick(self, stage: str, remaining: int) -> None:
        """Push the countdown for ``"auction"`` or ``"question"``."""

    async def send_hot_seat_no_bids(self) -> None:
        """Tell the room nobody wanted the chair."""

    async def send_hot_seat_awarded(self, game_state: Any, hs: Any) -> None:
        """Break the seal: who paid what, and who is sitting down."""

    async def send_hot_seat_question(self, game_state: Any, hs: Any) -> None:
        """Push the seat holder's question (answers only to the chair)."""

    async def send_hot_seat_result(self, game_state: Any, hs: Any) -> None:
        """Push the settlement."""

    async def resume_normal_question(self, game_state: Any) -> None:
        """Leave the detour and start an ordinary question instead.

        The mode's escape hatch, not a broadcast: an auction nobody bid on is
        a round that does not happen, and the main game has to keep moving.
        """


class RoundBroadcaster(Protocol):
    """The normal round's fan-out points (#203/#413)."""

    async def send_timer_tick(
        self,
        game_state: Any,
        remaining_by_player: dict[str, float],
        dashboard_remaining: float | None,
    ) -> None:
        """Push one tick.

        ``remaining_by_player`` holds only the players whose *displayed* second
        actually changed, and ``dashboard_remaining`` is ``None`` when the
        shared countdown's second did not — the coalescing (#413) is the
        driver's, the addressing is the implementation's.
        """


class WagerBroadcaster(Protocol):
    """The betting window's single fan-out point (#656)."""

    async def close_wager_window(self, game_state: Any) -> None:
        """The deadline passed: arm the round timers and send the question."""
