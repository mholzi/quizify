"""Atomic JSON-on-disk store for saved game presets (#433).

A host reconfigures the same two or three setups every session — packs,
difficulty, rounds, timer, lightning, hot seat, power-ups, wager. This keeps
named copies of them so the
setup screen collapses to one tap.

The presets live on the **server**, not in the browser: a preset saved on the
living-room tablet has to exist on the host's phone too, and a browser-local
store fails that the first time a second device is used.

Written in the shape of :mod:`server.token_store` — same executor-offloaded,
atomic write-then-replace, same tolerance for a corrupt file (a broken presets
file must never stop the game from starting; it degrades to "no presets").
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import secrets
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from ..runtime import Runtime

_LOGGER = logging.getLogger(__name__)

#: Hard caps. A JSON file that grows without limit is a slow leak, and the
#: chip row stops being one-tap long before twenty entries.
MAX_PRESETS = 20
MAX_NAME_LENGTH = 40

#: Fields a preset carries. Deliberately NOT the TTS / house settings: those
#: belong to devices, not to the shape of an evening, and a preset that
#: silently repoints a speaker surprises more than it helps.
_INT_FIELDS = ("rounds", "timer")
_STR_FIELDS = ("difficulty", "category")

SCHEMA_VERSION = 1


class PresetValidationError(ValueError):
    """A preset was rejected — the message is safe to return to the caller."""


class PresetStore:
    """Named game setups, persisted as one small JSON file."""

    def __init__(self, runtime: Runtime, filename: str = "presets.json") -> None:
        self._runtime = runtime
        self._path = runtime.data_dir / filename
        self._lock = asyncio.Lock()

    # ------------------------------------------------------------------
    # Disk
    # ------------------------------------------------------------------

    async def _read(self) -> list[dict[str, Any]]:
        if not self._path.exists():
            return []
        try:
            content = await self._runtime.run_in_executor(self._path.read_text)
            data = json.loads(content)
        except (OSError, json.JSONDecodeError) as err:
            _LOGGER.warning("Preset store corrupt or unreadable: %s", err)
            return []
        presets = data.get("presets") if isinstance(data, dict) else None
        return [p for p in presets if isinstance(p, dict)] if presets else []

    async def _write(self, presets: list[dict[str, Any]]) -> None:
        tmp = self._path.with_suffix(".tmp")
        content = json.dumps({"version": SCHEMA_VERSION, "presets": presets})

        def _do() -> None:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            tmp.write_text(content)
            os.replace(tmp, self._path)

        try:
            await self._runtime.run_in_executor(_do)
        except OSError as err:
            _LOGGER.warning("Failed to persist presets: %s", err)

    # ------------------------------------------------------------------
    # API
    # ------------------------------------------------------------------

    async def list(self) -> list[dict[str, Any]]:
        """Return the saved presets in host order."""
        return await self._read()

    async def save(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Create or update one preset and return the stored record.

        Raises :class:`PresetValidationError` for anything the caller can fix.
        """
        record = _validate(payload)
        async with self._lock:
            presets = await self._read()
            existing = payload.get("id")
            if existing:
                for index, preset in enumerate(presets):
                    if preset.get("id") == existing:
                        record["id"] = existing
                        record["created"] = preset.get("created", record["created"])
                        presets[index] = record
                        break
                else:
                    raise PresetValidationError("unknown preset id")
            else:
                if len(presets) >= MAX_PRESETS:
                    raise PresetValidationError(
                        f"at most {MAX_PRESETS} presets can be saved"
                    )
                presets.append(record)
            await self._write(presets)
        return record

    async def delete(self, preset_id: str) -> bool:
        """Remove a preset. Returns False when the id was unknown."""
        async with self._lock:
            presets = await self._read()
            remaining = [p for p in presets if p.get("id") != preset_id]
            if len(remaining) == len(presets):
                return False
            await self._write(remaining)
        return True


def _validate(payload: dict[str, Any]) -> dict[str, Any]:
    """Normalise an incoming preset, raising on anything unusable."""
    if not isinstance(payload, dict):
        raise PresetValidationError("preset must be an object")

    name = str(payload.get("name", "")).strip()
    if not name:
        raise PresetValidationError("name is required")
    if len(name) > MAX_NAME_LENGTH:
        raise PresetValidationError(
            f"name must be at most {MAX_NAME_LENGTH} characters"
        )

    record: dict[str, Any] = {
        "id": payload.get("id") or f"p_{secrets.token_hex(3)}",
        "name": name,
        "created": str(payload.get("created") or ""),
    }

    for field in _INT_FIELDS:
        value = payload.get(field)
        if value is None:
            continue
        try:
            record[field] = int(value)
        except (TypeError, ValueError):
            raise PresetValidationError(f"{field} must be a number") from None

    for field in _STR_FIELDS:
        value = payload.get(field)
        if value is not None:
            record[field] = str(value)

    if payload.get("lightning") is not None:
        record["lightning"] = bool(payload["lightning"])

    # #616: the Hot Seat auction rides the bundle exactly like Lightning. Left
    # out, a saved preset would drop the setting silently and come back with
    # the default — the host would switch the auction off, save, reload, and
    # find it on again with nothing to explain why.
    if payload.get("hot_seat") is not None:
        record["hot_seat"] = bool(payload["hot_seat"])

    # #742: power-ups and the final-round wager ride the bundle for the same
    # reason. These two are the ones the *With kids* preset most needs to
    # carry — a preset that forgets them hands a children's game Steal, Freeze
    # and a last question that can wipe a score.
    if payload.get("powerups") is not None:
        record["powerups"] = bool(payload["powerups"])

    if payload.get("wager") is not None:
        record["wager"] = bool(payload["wager"])

    packs = payload.get("packs")
    if isinstance(packs, list):
        record["packs"] = [str(p) for p in packs]

    return record
