"""Tiny async JSON-on-disk store for the persisted admin token.

Replaces ``homeassistant.helpers.storage.Store`` so the server can run
outside of Home Assistant. The on-disk format is intentionally simple
(``{"token": "..."}``); HA's Store wraps payloads in a versioned envelope,
but we only persist a single value, so the difference is invisible to the
caller. The standalone and HA paths cannot share a token file (different
locations), and that's fine — admin bootstraps once per host.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..runtime import Runtime

_LOGGER = logging.getLogger(__name__)


class TokenStore:
    """Atomic JSON file for the persisted admin session token."""

    def __init__(self, runtime: Runtime, filename: str = "admin_token.json") -> None:
        self._runtime = runtime
        self._path = runtime.data_dir / filename
        self._lock = asyncio.Lock()

    async def load(self) -> dict | None:
        """Return the persisted dict, or None if missing/corrupt."""
        if not self._path.exists():
            return None
        try:
            content = await self._runtime.run_in_executor(self._path.read_text)
            return json.loads(content)
        except (OSError, json.JSONDecodeError) as err:
            _LOGGER.warning("Admin token store corrupt or unreadable: %s", err)
            return None

    async def save(self, data: dict) -> None:
        """Atomically persist *data* as JSON."""
        async with self._lock:
            tmp = self._path.with_suffix(".tmp")
            content = json.dumps(data)

            def _write() -> None:
                self._path.parent.mkdir(parents=True, exist_ok=True)
                tmp.write_text(content)
                os.replace(tmp, self._path)

            try:
                await self._runtime.run_in_executor(_write)
            except OSError as err:
                _LOGGER.warning("Failed to persist admin token: %s", err)

    async def remove(self) -> None:
        """Delete the storage file (idempotent)."""
        async with self._lock:
            def _rm() -> None:
                try:
                    self._path.unlink()
                except FileNotFoundError:
                    pass

            try:
                await self._runtime.run_in_executor(_rm)
            except OSError as err:
                _LOGGER.warning("Failed to delete admin token file: %s", err)
