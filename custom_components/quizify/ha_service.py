"""Shared fire-and-forget Home Assistant service-call helper.

The light, lobby-music and TTS integrations all scheduled an HA service
call on the event loop the same way: wrap ``hass.services.async_call(...,
blocking=False)`` in a coroutine, swallow+log any exception, and hand it to
``hass.async_create_task`` so the caller never blocks or has to await it.

That fire-and-forget pattern was duplicated three times (issue #312). It now
lives here once. Behaviour is unchanged: the call is non-blocking, exceptions
are caught and logged at WARNING with a caller-supplied context, and the task
is detached via ``hass.async_create_task``.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

_LOGGER = logging.getLogger(__name__)


def fire_and_forget_service(
    hass: HomeAssistant | None,
    domain: str,
    service: str,
    data: dict[str, object],
    error_context: str,
) -> None:
    """Schedule ``domain.service`` as a detached, non-blocking HA service call.

    No-ops when ``hass`` is ``None`` (standalone dev server). Any exception
    raised by the service call is caught and logged at WARNING level with the
    given ``error_context`` (e.g. ``"Party lights light.turn_on (entities=...)"``)
    so a failing call never escapes onto the event loop.
    """
    if hass is None:
        return

    async def _do_call() -> None:
        try:
            await hass.services.async_call(domain, service, data, blocking=False)
        except Exception as err:  # noqa: BLE001
            _LOGGER.warning("%s failed: %s", error_context, err)

    hass.async_create_task(_do_call())
