"""HTTP views for Quizify."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING

from aiohttp import web
from homeassistant.components.http import HomeAssistantView

from custom_components.quizify.const import DOMAIN
from custom_components.quizify.server.serializers import (
    build_game_status_response,
    get_game_state,
)

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

_LOGGER = logging.getLogger(__name__)

_VERSION = "0.1.0"


def _read_file(path: Path) -> str:
    """Read file contents (runs in executor)."""
    return path.read_text(encoding="utf-8")


class AdminView(HomeAssistantView):
    """Serve the admin page."""

    url = "/quizify/admin"
    name = "quizify:admin"
    requires_auth = False

    def __init__(self, hass: HomeAssistant) -> None:
        """Initialize the admin view."""
        self.hass = hass

    async def get(self, request: web.Request) -> web.Response:  # noqa: ARG002
        """Serve the admin HTML page."""
        html_path = Path(__file__).parent.parent / "www" / "admin.html"

        if not html_path.exists():
            _LOGGER.error("Admin page not found: %s", html_path)
            return web.Response(text="Admin page not found", status=500)

        html_content = await self.hass.async_add_executor_job(_read_file, html_path)
        return web.Response(text=html_content, content_type="text/html")


class LauncherView(HomeAssistantView):
    """Serve the launcher page."""

    url = "/quizify/launcher"
    name = "quizify:launcher"
    requires_auth = False

    def __init__(self, hass: HomeAssistant) -> None:
        """Initialize the launcher view."""
        self.hass = hass

    async def get(self, request: web.Request) -> web.Response:  # noqa: ARG002
        """Serve the launcher HTML page."""
        html_path = Path(__file__).parent.parent / "www" / "launcher.html"

        if not html_path.exists():
            _LOGGER.error("Launcher page not found: %s", html_path)
            return web.Response(text="Launcher page not found", status=500)

        html_content = await self.hass.async_add_executor_job(_read_file, html_path)
        return web.Response(text=html_content, content_type="text/html")


class PlayerView(HomeAssistantView):
    """Serve the player page."""

    url = "/quizify/player"
    name = "quizify:player"
    requires_auth = False

    def __init__(self, hass: HomeAssistant) -> None:
        """Initialize the player view."""
        self.hass = hass

    async def get(self, request: web.Request) -> web.Response:  # noqa: ARG002
        """Serve the player HTML page."""
        html_path = Path(__file__).parent.parent / "www" / "player.html"

        if not html_path.exists():
            _LOGGER.error("Player page not found: %s", html_path)
            return web.Response(text="Player page not found", status=500)

        html_content = await self.hass.async_add_executor_job(_read_file, html_path)
        return web.Response(text=html_content, content_type="text/html")


class DashboardView(HomeAssistantView):
    """Serve the dashboard page."""

    url = "/quizify/dashboard"
    name = "quizify:dashboard"
    requires_auth = False

    def __init__(self, hass: HomeAssistant) -> None:
        """Initialize the dashboard view."""
        self.hass = hass

    async def get(self, request: web.Request) -> web.Response:  # noqa: ARG002
        """Serve the dashboard HTML page."""
        html_path = Path(__file__).parent.parent / "www" / "dashboard.html"

        if not html_path.exists():
            _LOGGER.error("Dashboard page not found: %s", html_path)
            return web.Response(text="Dashboard page not found", status=500)

        html_content = await self.hass.async_add_executor_job(_read_file, html_path)
        return web.Response(text=html_content, content_type="text/html")


class GameStatusView(HomeAssistantView):
    """API endpoint for game status."""

    url = "/api/quizify/game-status"
    name = "api:quizify:game-status"
    requires_auth = False

    def __init__(self, hass: HomeAssistant) -> None:
        """Initialize the game status view."""
        self.hass = hass

    async def get(self, request: web.Request) -> web.Response:
        """Return current game status."""
        game_state = get_game_state(self.hass)
        game_id = request.query.get("game_id")
        response = build_game_status_response(game_state, game_id)
        return web.json_response(response)


class StatusView(HomeAssistantView):
    """API endpoint for integration status."""

    url = "/api/quizify/status"
    name = "api:quizify:status"
    requires_auth = False

    def __init__(self, hass: HomeAssistant) -> None:
        """Initialize the status view."""
        self.hass = hass

    async def get(self, request: web.Request) -> web.Response:  # noqa: ARG002
        """Return integration status."""
        return web.json_response({"version": _VERSION, "status": "ok"})
