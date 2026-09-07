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
        """Return the persisted dict, or ``None`` when the file is not there.

        Reads with ``on_corrupt="raise"`` (#873). Every other store in Quizify
        degrades a broken file to a default, and for saved presets or pack news
        that is right — but this file is a *credential*, and "no token on disk"
        is the state that opens the admin-bootstrap window. A permissions
        error after a restore, a directory where the file should be, or a
        half-written JSON blob would otherwise read as "nobody has ever claimed
        admin here" and hand the next ``?role=admin`` handshake from any LAN
        address the host's seat — the takeover #725 closed, arriving through
        the storage layer instead of the expiry path.

        So the caller gets the ``OSError`` / ``ValueError`` and has to decide.
        A **missing** file is still ``None``: that one really is a fresh
        install.
        """
        data = await self._file.load(None, on_corrupt="raise")
        return data if isinstance(data, dict) else None

    async def save(self, data: dict) -> None:
        """Atomically persist *data* as JSON."""
        async with self._lock:
            await self._file.save(data)

    async def remove(self) -> None:
        """Delete the storage file (idempotent)."""
        async with self._lock:
            await self._file.remove()
