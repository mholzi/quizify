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
from collections.abc import Coroutine
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

import aiohttp

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

    def create_task(self, coro: Coroutine[Any, Any, Any]) -> Any:
        """Schedule *coro* on the running event loop, fire-and-forget."""

    async def run_in_executor(self, func, *args) -> Any:
        """Run a blocking *func* in an executor thread and return its result."""

    def get_client_session(self) -> aiohttp.ClientSession:
        """Return a shared aiohttp client session for outbound HTTP.

        Reused across all outbound fetches (GitHub version/issue polls, the
        community-pack submit proxy) so the server doesn't build and tear down a
        fresh TCP/TLS connection pool per request (#456). Callers MUST NOT close
        the returned session — its lifetime is owned by the runtime.
        """


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

    def create_task(self, coro: Coroutine[Any, Any, Any]) -> Any:
        return self._hass.async_create_task(coro)

    async def run_in_executor(self, func, *args) -> Any:
        return await self._hass.async_add_executor_job(func, *args)

    def get_client_session(self) -> aiohttp.ClientSession:
        """Return HA's shared aiohttp client session (#456).

        ``async_get_clientsession`` returns the singleton session HA already
        manages and closes on shutdown, so Quizify's outbound fetches reuse the
        host's connection pool instead of spinning up their own per request.
        """
        from homeassistant.helpers.aiohttp_client import (  # noqa: PLC0415
            async_get_clientsession,
        )

        return async_get_clientsession(self._hass)


class StandaloneRuntime:
    """Runtime for the standalone dev/release server (no Home Assistant)."""

    def __init__(self, data_dir: Path) -> None:
        self._data_dir = Path(data_dir)
        self._data_dir.mkdir(parents=True, exist_ok=True)
        # Lazily created on first get_client_session() call and reused for the
        # process lifetime; closed by aclose() on server shutdown (#456).
        self._session: aiohttp.ClientSession | None = None

    @property
    def data_dir(self) -> Path:
        return self._data_dir

    def create_task(self, coro: Coroutine[Any, Any, Any]) -> Any:
        return asyncio.ensure_future(coro)

    async def run_in_executor(self, func, *args) -> Any:
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, func, *args)

    def get_client_session(self) -> aiohttp.ClientSession:
        """Return the process-wide aiohttp client session, creating it once.

        The standalone dev server has no HA to hand us a managed session, so we
        keep one lazily-created session and reuse it for every outbound fetch
        (#456). Must be called on the event loop (ClientSession binds to the
        running loop). Closed by :meth:`aclose` on shutdown.
        """
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession()
        return self._session

    async def aclose(self) -> None:
        """Close the lazily-created client session, if any (shutdown hook)."""
        if self._session is not None and not self._session.closed:
            await self._session.close()
        self._session = None
