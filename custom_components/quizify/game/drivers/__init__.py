"""One driver per game mode (#788).

Before this package, four complete game loops lived inside
``QuizifyWebSocketHandler``: lightning, the hot-seat auction/question loop, the
normal-round tick loop and the wager window. They were not I/O adapters — they
encoded rules ("cut the window short once every connected player answered",
"hold the bid reveal four seconds", "settle even when unanswered") interleaved
with socket sends and a hand-rolled phase re-check after every sleep. The
handler's own teardown comment records that the resulting matrix broke five
times (#362, #407, #656, #671, #746), each time because a new loop was a fresh
copy of poll / tick / re-check / settle / cancel.

Each driver here owns exactly that: timing, the phase re-checks and the
settlement. It talks to the outside world through the two narrow protocols in
:mod:`.protocols` — a per-mode ``Broadcaster`` for the fan-out points and a
``MilestoneSink`` for the house beats — and builds no wire payloads itself,
which is what keeps the game layer clear of the server layer (#747).

The asyncio tasks themselves stay on the handler, where the #746 registry can
see them; a driver is the coroutine, not its lifetime.
"""

from __future__ import annotations

from .hot_seat import HotSeatDriver
from .lightning import LightningDriver
from .normal_round import NormalRoundDriver
from .protocols import (
    HotSeatBroadcaster,
    LightningBroadcaster,
    MilestoneSink,
    RoundBroadcaster,
    WagerBroadcaster,
)
from .wager import WagerWindowDriver

__all__ = [
    "HotSeatBroadcaster",
    "HotSeatDriver",
    "LightningBroadcaster",
    "LightningDriver",
    "MilestoneSink",
    "NormalRoundDriver",
    "RoundBroadcaster",
    "WagerBroadcaster",
    "WagerWindowDriver",
]
