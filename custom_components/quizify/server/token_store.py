"""Tiny async JSON-on-disk store for the persisted admin token.

Replaces ``homeassistant.helpers.storage.Store`` so the server can run
outside of Home Assistant. The on-disk format is intentionally simple
(``{"token": "..."}``); HA's Store wraps payloads in a versioned envelope,
but we only persist a single value, so the difference is invisible to the
caller. The standalone and HA paths cannot share a token file (different
locations), and that's fine — admin bootstraps once per host.

The read/write routine itself lives in :mod:`custom_components.quizify.storage`
(#790) — this class is just the token's name for it.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from ..storage import JsonFile

if TYPE_CHECKING:
    from ..runtime import Runtime


class TokenStore:
    """Atomic JSON file for the persisted admin session token."""

    def __init__(self, runtime: Runtime, filename: str = "admin_token.json") -> None:
        self._file = JsonFile(
            runtime, runtime.data_dir / filename, label="Admin token store"
        )
        self._lock = asyncio.Lock()

    async def load(self) -> dict | None:
        """Return the persisted dict, or None if missing/corrupt."""
        data = await self._file.load(None)
        return data if isinstance(data, dict) else None

    async def save(self, data: dict) -> None:
        """Atomically persist *data* as JSON."""
        async with self._lock:
            await self._file.save(data)

    async def remove(self) -> None:
        """Delete the storage file (idempotent)."""
        async with self._lock:
            await self._file.remove()
