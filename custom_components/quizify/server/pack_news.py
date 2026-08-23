"""Which packs arrived with the update the host just installed.

Packs ship inside the integration, so a new pack reaches a host through a
HACS update like any other file — there is nothing to fetch and nothing to
install by hand. The only useful moment is *after* the update: "you now have
World Cup, 100 questions". That is what this store answers.

It keeps two sets on disk:

``known``
    Every pack slug this host has been shown before. Seeded on the first run
    from whatever is installed, so a fresh install is never greeted with a
    list of "new" packs — on a fresh install everything is new and none of it
    is news.

``pending``
    Slugs that appeared after that first run and have not been dismissed yet.
    Kept on disk rather than in memory so the banner survives a page reload,
    a browser restart, and a host who updates on Monday and opens the admin
    page on Friday.

Community packs are excluded by the caller: the host dropped those in
themselves, so announcing them as arrivals would be telling them something
they already know.
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

_SCHEMA_VERSION = 1


class PackNewsStore:
    """Atomic JSON file remembering which packs the host has already seen."""

    def __init__(self, runtime: Runtime, filename: str = "pack_news.json") -> None:
        self._runtime = runtime
        self._path = runtime.data_dir / filename
        self._lock = asyncio.Lock()

    async def _read(self) -> dict:
        """Return the persisted record, or an empty one if missing/corrupt.

        A corrupt file is treated as a first run rather than an error: the
        worst case is one suppressed banner, and refusing to serve the admin
        page over an unreadable bookkeeping file would be the larger failure.
        """
        if not self._path.exists():
            return {}
        try:
            content = await self._runtime.run_in_executor(self._path.read_text)
            data = json.loads(content)
        except (OSError, json.JSONDecodeError) as err:
            _LOGGER.warning("Pack news store corrupt or unreadable: %s", err)
            return {}
        return data if isinstance(data, dict) else {}

    async def _write(self, data: dict) -> None:
        """Atomically persist *data* as JSON (best-effort)."""
        tmp = self._path.with_suffix(".tmp")
        content = json.dumps(data)

        def _do_write() -> None:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            tmp.write_text(content)
            os.replace(tmp, self._path)

        try:
            await self._runtime.run_in_executor(_do_write)
        except OSError as err:
            _LOGGER.warning("Failed to persist pack news: %s", err)

    async def sync(self, installed: set[str]) -> list[str]:
        """Fold the currently installed packs into the record; return pending.

        Writes only when the record actually changes. That matters because the
        read endpoint is unauthenticated: without the comparison, any client
        able to reach port 8123 could turn repeated GETs into repeated disk
        writes. In the steady state — nothing installed, nothing dismissed —
        this method touches the disk exactly zero times.
        """
        async with self._lock:
            data = await self._read()
            known = set(data.get("known") or [])
            pending = set(data.get("pending") or [])

            if not known:
                # First run: record what is here and announce nothing.
                known = set(installed)
                pending = set()
            else:
                # A pack that has since been removed is no longer news, so
                # intersect rather than accumulate — otherwise a slug deleted
                # from disk would sit in the banner forever with no way for
                # the host to make it go away except dismissing it.
                pending = (pending | (installed - known)) & installed
                known |= installed

            record = {
                "version": _SCHEMA_VERSION,
                "known": sorted(known),
                "pending": sorted(pending),
            }
            if record != data:
                await self._write(record)
            return sorted(pending)

    async def dismiss(self) -> None:
        """Clear the pending list (the host closed the banner)."""
        async with self._lock:
            data = await self._read()
            if not data.get("pending"):
                return
            await self._write(
                {
                    "version": _SCHEMA_VERSION,
                    "known": sorted(set(data.get("known") or [])),
                    "pending": [],
                }
            )
