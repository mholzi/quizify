"""Runtime abstraction so Quizify core can run inside or outside Home Assistant.

The integration's game and server modules historically reached directly into
`hass` for two unrelated capabilities:

  - on-disk paths (e.g. ``hass.config.path("quizify", "...")``)
  - background task scheduling (``hass.async_create_task``)

Both have non-HA equivalents. The :class:`Runtime` protocol below names those
capabilities, the HA adapter wires them through `hass`, and the standalone
adapter writes to a chosen data directory and uses `asyncio.ensure_future`.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any, Awaitable, Protocol, runtime_checkable

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant


_LOGGER = logging.getLogger(__name__)


@runtime_checkable
class Runtime(Protocol):
    """Minimal capabilities the game/server modules need from their host."""

    @property
    def data_dir(self) -> Path:
        """Directory where Quizify can read/write its persistent files.

        Files live directly under this directory (no extra ``quizify/`` prefix).
        """

    def create_task(self, coro: Awaitable[Any]) -> Any:
        """Schedule *coro* on the running event loop, fire-and-forget."""

    async def run_in_executor(self, func, *args) -> Any:
        """Run a blocking *func* in an executor thread and return its result."""


class HARuntime:
    """Runtime backed by a Home Assistant instance."""

    def __init__(self, hass: HomeAssistant) -> None:
        self._hass = hass
        self._data_dir = Path(hass.config.path("quizify"))

    @property
    def data_dir(self) -> Path:
        return self._data_dir

    @property
    def hass(self) -> HomeAssistant:
        """Underlying HA instance, for code paths that genuinely need it."""
        return self._hass

    def create_task(self, coro: Awaitable[Any]) -> Any:
        return self._hass.async_create_task(coro)

    async def run_in_executor(self, func, *args) -> Any:
        return await self._hass.async_add_executor_job(func, *args)


class StandaloneRuntime:
    """Runtime for the standalone dev/release server (no Home Assistant)."""

    def __init__(self, data_dir: Path) -> None:
        self._data_dir = Path(data_dir)
        self._data_dir.mkdir(parents=True, exist_ok=True)

    @property
    def data_dir(self) -> Path:
        return self._data_dir

    def create_task(self, coro: Awaitable[Any]) -> Any:
        return asyncio.ensure_future(coro)

    async def run_in_executor(self, func, *args) -> Any:
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, func, *args)
