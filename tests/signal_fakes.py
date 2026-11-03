"""A fake Home Assistant that records what the room would have heard (#1185).

The house consumers (voice, lights, room sounds, bus events) talk to HA through
three doors: ``hass.services.async_call``, ``hass.bus.async_fire`` /
``async_listen`` and ``hass.states.get``. :class:`FakeHass` implements exactly
those, executes nothing, and writes every call into one ordered timeline, so a
test can ask "what did the room get, and in which order?" without a real HA.

Bus listeners are called synchronously, the way HA runs ``@callback``
listeners, so the lights and the sound effects react inside the same
``async_fire`` call that fired the event.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any


@dataclass
class Signal:
    """One thing that reached the room: a service call or a bus event."""

    kind: str  # "service" or "event"
    name: str  # "tts.speak", "light.turn_on", "quizify_answer_revealed", …
    data: dict[str, Any]
    at: float = field(default_factory=time.monotonic)


class _Bus:
    def __init__(self, recorder: FakeHass) -> None:
        self._recorder = recorder
        self._listeners: dict[str, list] = {}

    def async_listen(self, event_type: str, listener):
        self._listeners.setdefault(event_type, []).append(listener)

        def _unsub() -> None:
            if listener in self._listeners.get(event_type, []):
                self._listeners[event_type].remove(listener)

        return _unsub

    def async_fire(self, event_type: str, data: dict[str, Any] | None = None) -> None:
        self._recorder.signals.append(Signal("event", event_type, dict(data or {})))
        event = SimpleNamespace(event_type=event_type, data=dict(data or {}))
        for listener in list(self._listeners.get(event_type, [])):
            listener(event)


class _Services:
    def __init__(self, recorder: FakeHass) -> None:
        self._recorder = recorder

    async def async_call(self, domain, service, data, blocking=False):  # noqa: ARG002
        self._recorder.signals.append(
            Signal("service", f"{domain}.{service}", dict(data or {}))
        )
        self._recorder.on_service(domain, service, dict(data or {}))


class _States:
    def __init__(self) -> None:
        self.values: dict[str, SimpleNamespace] = {}

    def get(self, entity_id: str):
        return self.values.get(entity_id)

    def set(self, entity_id: str, state: str, **attributes: Any) -> None:
        self.values[entity_id] = SimpleNamespace(
            entity_id=entity_id, state=state, attributes=dict(attributes)
        )


class FakeHass:
    """Just enough of ``HomeAssistant`` for the house consumers."""

    def __init__(self) -> None:
        self.signals: list[Signal] = []
        self.bus = _Bus(self)
        self.services = _Services(self)
        self.states = _States()
        self.data: dict[str, Any] = {}
        self.tasks: list[asyncio.Task] = []

    def async_create_task(self, coro):
        task = asyncio.ensure_future(coro)
        self.tasks.append(task)
        return task

    def on_service(self, domain: str, service: str, data: dict[str, Any]) -> None:
        """Hook for tests that want a call to change state (a speaker that
        starts playing on ``tts.speak``, say). Does nothing by default."""

    # -- reading the timeline ------------------------------------------------

    def names(self) -> list[str]:
        return [s.name for s in self.signals]

    def of(self, name: str) -> list[Signal]:
        return [s for s in self.signals if s.name == name]

    def room(self) -> list[Signal]:
        """Everything a person in the room could notice: voice, lights,
        sound, scenes and the bus events automations run on."""
        return list(self.signals)

    def clear(self) -> None:
        self.signals.clear()

    async def settle(self, rounds: int = 3) -> None:
        """Let the fire-and-forget service tasks run."""
        for _ in range(rounds):
            await asyncio.sleep(0)

    async def drain(self) -> None:
        """Cancel whatever is still running (light pulses, waits)."""
        for task in self.tasks:
            if not task.done():
                task.cancel()
        for task in self.tasks:
            try:
                await task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
