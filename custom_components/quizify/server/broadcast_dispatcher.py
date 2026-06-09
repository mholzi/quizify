"""BroadcastDispatcher — routes game-state events to broadcast handlers.

Extracted from ``QuizifyWebSocketHandler.broadcast_state`` (issue #184,
splitting the game-server God-objects). The game-state layer fires named
events (``round_evaluated``, ``game_ended``) through the broadcast callback;
this dispatcher maps each event name to the coroutine that builds and sends the
matching client message, and falls back to a full-state broadcast for anything
else (or a payload with no event).

The dispatcher owns only the *routing*; the message-building coroutines stay on
the handler (they are tightly coupled to the connection manager and the
serializers). Behaviour is identical to the previous inline ``if event == ...``
chain — same events, same handlers, same fallback.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any


class BroadcastDispatcher:
    """Maps state-event names to their broadcast coroutines.

    Stateless aside from the handler table it is constructed with. A missing
    event name (or a payload without an ``event`` key) routes to the default
    full-state broadcast.
    """

    def __init__(
        self,
        *,
        handlers: dict[str, Callable[[], Awaitable[None]]],
        default: Callable[[], Awaitable[None]],
    ) -> None:
        """Initialise with the per-event handlers and a default handler.

        ``handlers`` maps an event name (e.g. ``"round_evaluated"``) to a
        zero-arg coroutine factory; ``default`` is used when the payload has no
        event or an unrecognised one.
        """
        self._handlers = handlers
        self._default = default

    async def dispatch(self, payload: dict[str, Any] | None) -> None:
        """Route ``payload`` to the matching handler.

        Mirrors the previous inline logic exactly: if the payload carries a
        recognised ``event``, invoke its handler and return; otherwise fall
        through to the default full-state broadcast.
        """
        if payload is not None:
            event = payload.get("event")
            handler = self._handlers.get(event) if event is not None else None
            if handler is not None:
                await handler()
                return
        await self._default()
