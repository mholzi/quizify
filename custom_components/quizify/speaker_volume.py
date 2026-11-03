"""Quizify's own volume for voice and room sounds (#1185, P6).

Voice and sounds used to play at whatever volume the speaker happened to be
on, at 23:00 with children asleep next door too. The host can now move a
volume slider in the setup. When — and only when — they have moved it,
Quizify sets that volume right before an announcement or a sound, and puts
the speaker back where it was once it has gone quiet again.

One instance per config entry, shared by the announcer and the room sounds,
so two lines in a row set the volume once and restore it once.
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

from .ha_service import fire_and_forget_service

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

    from .house_settings import HouseSettings

_LOGGER = logging.getLogger(__name__)

# The restore waits for the speaker to start (a cloud voice is generated
# first) and then to stop. Both legs are bounded, so a speaker that never
# reports ``playing`` gets its volume back seconds later, not never.
_POLL_S = 0.25
_START_S = 5.0
_END_S = 60.0
# Quiet after playing for this long counts as done; a voice line followed by
# the fanfare is one stretch, not two.
_SETTLE_S = 1.5


class SpeakerVolume:
    """Set the host's chosen volume before a line, restore it afterwards."""

    def __init__(self, hass: HomeAssistant | None, settings: HouseSettings) -> None:
        self._hass = hass
        self._settings = settings
        # entity -> the volume_level it had before Quizify touched it.
        self._original: dict[str, float] = {}
        self._restore_tasks: dict[str, asyncio.Task] = {}

    def before_play(self, entity_id: str | None) -> None:
        """Called right before ``tts.speak`` / ``media_player.play_media``.

        No-op unless the host moved the slider (``volume_pct`` is set). The
        original level is read once per stretch of playing; another line in
        the same stretch only restarts the restore watch.
        """
        pct = self._settings.volume_pct
        hass = self._hass
        if pct is None or hass is None or not entity_id:
            return
        if entity_id not in self._original:
            state = hass.states.get(entity_id)
            level = None
            if state is not None:
                level = (getattr(state, "attributes", None) or {}).get("volume_level")
            if isinstance(level, (int, float)):
                self._original[entity_id] = float(level)
        target = max(0, min(100, int(pct))) / 100
        fire_and_forget_service(
            hass,
            "media_player",
            "volume_set",
            {"entity_id": entity_id, "volume_level": target},
            f"Quizify volume (media_player={entity_id})",
        )
        task = self._restore_tasks.get(entity_id)
        if task is not None and not task.done():
            task.cancel()
        self._restore_tasks[entity_id] = hass.async_create_task(
            self._restore_when_quiet(entity_id)
        )

    def _playing(self, entity_id: str) -> bool:
        hass = self._hass
        state = hass.states.get(entity_id) if hass is not None else None
        return state is not None and state.state == "playing"

    async def _restore_when_quiet(self, entity_id: str) -> None:
        try:
            waited = 0.0
            while not self._playing(entity_id) and waited < _START_S:
                await asyncio.sleep(_POLL_S)
                waited += _POLL_S
            quiet = 0.0
            waited = 0.0
            while quiet < _SETTLE_S and waited < _END_S:
                await asyncio.sleep(_POLL_S)
                waited += _POLL_S
                quiet = 0.0 if self._playing(entity_id) else quiet + _POLL_S
        except asyncio.CancelledError:
            return
        original = self._original.pop(entity_id, None)
        self._restore_tasks.pop(entity_id, None)
        if original is None:
            return
        fire_and_forget_service(
            self._hass,
            "media_player",
            "volume_set",
            {"entity_id": entity_id, "volume_level": original},
            f"Quizify volume restore (media_player={entity_id})",
        )

    def detach(self) -> None:
        """Stop watching (unload). The speaker keeps whatever it has now."""
        for task in self._restore_tasks.values():
            if not task.done():
                task.cancel()
        self._restore_tasks.clear()
