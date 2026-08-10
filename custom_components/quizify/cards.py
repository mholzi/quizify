"""Lovelace resource registration for the Quizify custom cards (#278).

A custom card only exists for a dashboard once its JavaScript is registered as
a Lovelace *resource*. Doing that by hand means walking the host through
Settings → Dashboards → Resources, which is exactly the kind of step people
skip before reporting the card as broken. When Lovelace runs in storage mode
(the default) we can add the resource ourselves.

Two rules govern everything here:

* **Setup must never fail because of this.** The card is a convenience; the
  game does not depend on it. Every path is wrapped, and a failure downgrades
  to a log line telling the host what to add manually.
* **Never register twice.** The resource list is persisted, so an unguarded
  add would append a duplicate on every restart.

YAML-mode Lovelace has no writable resource store — there the host owns
``configuration.yaml`` and we only log what to put in it.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

_LOGGER = logging.getLogger(__name__)

#: URL the card is served from. It lives under the existing static mount
#: (``STATIC_URL_PREFIX`` + ``www/``), so no extra route is needed.
CARD_URL = "/quizify/static/cards/quizify-host-card.js"


def _resource_url(version: str | None) -> str:
    """Return the card URL with the manifest version as a cache-buster.

    Same trick the HTML pages use: a dashboard that cached the previous card
    would otherwise keep it until the host cleared their browser.
    """
    return f"{CARD_URL}?v={version}" if version else CARD_URL


async def async_register_card_resource(
    hass: HomeAssistant, version: str | None = None
) -> bool:
    """Add the host card to Lovelace's resource list if it isn't there yet.

    Returns ``True`` when the resource is present afterwards (either because we
    added it or because it already was), ``False`` when the host has to add it
    by hand. Never raises.
    """
    url = _resource_url(version)
    try:
        lovelace: Any = hass.data.get("lovelace")
        resources: Any = getattr(lovelace, "resources", None)
        if resources is None and isinstance(lovelace, dict):
            # Older cores kept Lovelace's pieces in a plain dict.
            resources = lovelace.get("resources")

        if resources is None:
            _LOGGER.info(
                "Lovelace resources are not available (YAML mode?) — add %s "
                "manually under Settings > Dashboards > Resources to use the "
                "Quizify host card",
                url,
            )
            return False

        if not getattr(resources, "loaded", True):
            await resources.async_load()
            resources.loaded = True

        for item in resources.async_items() or []:
            existing = str(item.get("url", ""))
            # Compare without the cache-buster so a version bump updates the
            # entry instead of adding a second one.
            if existing.split("?")[0] == CARD_URL:
                if existing != url:
                    await resources.async_update_item(
                        item["id"], {"url": url, "res_type": "module"}
                    )
                    _LOGGER.debug("Updated Quizify card resource to %s", url)
                return True

        await resources.async_create_item({"res_type": "module", "url": url})
        _LOGGER.info("Registered Quizify host card as a Lovelace resource (%s)", url)
        return True
    except Exception:  # noqa: BLE001 — a broken card must not break setup
        _LOGGER.warning(
            "Could not register the Quizify host card automatically. Add %s as a "
            "JavaScript Module under Settings > Dashboards > Resources to use it.",
            url,
            exc_info=True,
        )
        return False
