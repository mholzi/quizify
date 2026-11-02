"""A "last game was interrupted" marker that survives a restart (#1182, L4).

A Home Assistant restart in the middle of a game throws the whole game away:
players, scores, the round. Host and television reconnect into a fresh lobby,
and nothing on any screen says that a game was running and is gone — the
room reads it as somebody pressing Reset by accident.

The fix needs one bit that outlives the process: *was a game running when we
stopped?* This store writes that bit when a game leaves the lobby and removes
it when the game is over (finale, end, reset). If the file is still there at
the next start-up, the previous process died with a game in progress, and the
snapshot carries ``interrupted_game`` in the lobby until the next game starts.

Inert until :meth:`load` has run, so the many tests that build a handler
without a real runtime never touch the disk.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import TYPE_CHECKING, Any

from ..storage import JsonFile

if TYPE_CHECKING:
    from ..runtime import Runtime

_LOGGER = logging.getLogger(__name__)

_FILENAME = "game_running.json"

#: Phases in which no game is in progress: nothing to lose on a restart.
_IDLE_PHASES = frozenset({"LOBBY", "FINALE"})


class InterruptedGameMarker:
    """Persisted "a game is running" bit plus the notice it leaves behind."""

    def __init__(self, runtime: Runtime) -> None:
        self._runtime = runtime
        self._file: JsonFile | None = None
        self._running_on_disk = False
        self._notice: dict[str, Any] | None = None
        self._task: asyncio.Future | None = None

    @property
    def notice(self) -> dict[str, Any] | None:
        """``{"reason": "restart", "at": epoch}`` after an interrupted game."""
        return self._notice

    async def load(self) -> None:
        """Read the marker the previous process left, then arm this one."""
        try:
            self._file = JsonFile(
                self._runtime,
                self._runtime.data_dir / _FILENAME,
                label="Running-game marker",
            )
            data = await self._file.load(None)
            if isinstance(data, dict) and data.get("running"):
                self._notice = {
                    "reason": "restart",
                    "at": data.get("started_at"),
                }
                _LOGGER.info(
                    "The previous run stopped with a game in progress; "
                    "the lobby will say so until the next game starts"
                )
                await self._file.remove()
        except Exception:  # noqa: BLE001 - a notice is never worth a failed setup
            _LOGGER.warning("Could not read the running-game marker", exc_info=True)

    def clear_notice(self) -> None:
        """Drop the notice (a new game started, or the host reset)."""
        self._notice = None

    def watch(self, game_state: Any) -> None:
        """Note the phase on every state change of *game_state*.

        The game's own observer hook (the one the HA sensors use) fires on
        start, every round, the finale and the reset — including the start of
        a game, which sends no snapshot of its own.
        """
        game_state.register_state_callback(
            lambda: self.note_phase(game_state.phase)
        )

    def note_phase(self, phase: Any) -> None:
        """Called with every snapshot: schedule a write on an edge only.

        Synchronous so the snapshot builder can call it; the disk I/O runs as
        a task. A no-op until :meth:`load` has run, or without a loop.
        """
        if self._file is None or phase is None:
            return
        running = str(getattr(phase, "value", phase)) not in _IDLE_PHASES
        if running:
            self._notice = None
        if running == self._running_on_disk:
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        self._task = loop.create_task(self.sync(phase))

    async def sync(self, phase: str | None) -> None:
        """Write or remove the marker on the edge of a game starting or ending."""
        if self._file is None or phase is None:
            return
        running = str(getattr(phase, "value", phase)) not in _IDLE_PHASES
        if running:
            self._notice = None
        if running == self._running_on_disk:
            return
        self._running_on_disk = running
        try:
            if running:
                await self._file.save(
                    {"running": True, "started_at": int(time.time())}
                )
            else:
                await self._file.remove()
        except Exception:  # noqa: BLE001 - bookkeeping must not break a round
            _LOGGER.warning("Could not update the running-game marker", exc_info=True)
