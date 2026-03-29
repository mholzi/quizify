"""
Custom integration to integrate Quizify with Home Assistant.

Quizify is a multiplayer useless knowledge quiz game for Home Assistant.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from homeassistant.components.frontend import (
    async_register_built_in_panel,
    async_remove_panel,
)

from .const import DOMAIN
from .game.state import QuizifyGameState
from .server import async_register_static_paths
from .server.views import (
    AdminView,
    LauncherView,
    PlayerView,
    GameStatusView,
    StatusView,
)
from .server.websocket import QuizifyWebSocketView

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Quizify from a config entry."""
    _LOGGER.debug("Setting up Quizify integration")

    # Initialize domain data storage
    hass.data.setdefault(DOMAIN, {})

    # Initialize game state
    game_state = QuizifyGameState()

    # Store game infrastructure
    hass.data[DOMAIN] = {
        "entry_id": entry.entry_id,
        "game": game_state,
    }

    # Register HTTP views
    hass.http.register_view(AdminView(hass))
    hass.http.register_view(LauncherView(hass))
    hass.http.register_view(PlayerView(hass))
    hass.http.register_view(GameStatusView(hass))
    hass.http.register_view(StatusView(hass))

    # Register static file paths
    await async_register_static_paths(hass)

    # Register sidebar panel
    async_register_built_in_panel(
        hass,
        component_name="iframe",
        sidebar_title="Quizify",
        sidebar_icon="mdi:head-question",
        frontend_url_path="quizify",
        config={"url": "/quizify/launcher"},
        require_admin=False,
    )
    _LOGGER.debug("Quizify sidebar panel registered")

    _LOGGER.info("Quizify integration setup complete")
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:  # noqa: ARG001
    """Unload a config entry."""
    _LOGGER.debug("Unloading Quizify integration")

    # Remove sidebar panel
    try:
        async_remove_panel(hass, "quizify")
        _LOGGER.debug("Quizify sidebar panel removed")
    except KeyError:
        _LOGGER.debug("Quizify sidebar panel was not registered, skipping removal")

    # Clean up domain data
    if DOMAIN in hass.data:
        hass.data.pop(DOMAIN)

    _LOGGER.info("Quizify integration unloaded")
    return True
