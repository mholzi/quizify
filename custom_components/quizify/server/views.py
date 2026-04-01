"""HTTP views for Quizify."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING

import aiohttp
from aiohttp import web
from homeassistant.components.http import HomeAssistantView

from custom_components.quizify.const import DOMAIN
from custom_components.quizify.server.serializers import (
    build_game_status_response,
    get_game_state,
)

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

# GitHub raw URL for pack version manifests
_PACK_VERSIONS_URL = (
    "https://raw.githubusercontent.com/mholzi/quizify/main/"
    "custom_components/quizify/questions/versions.json"
)

_LOGGER = logging.getLogger(__name__)

_VERSION = "1.0.13"

_NO_CACHE_HEADERS = {
    "Cache-Control": "no-cache, no-store, must-revalidate",
    "Pragma": "no-cache",
    "Expires": "0",
}


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
        return web.Response(text=html_content, content_type="text/html", headers=_NO_CACHE_HEADERS)


class LauncherView(HomeAssistantView):
    """Serve the launcher page."""

    url = "/quizify/launcher"
    name = "quizify:launcher"
    requires_auth = False  # Served inside HA's auth-protected iframe panel; no sensitive data

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
        return web.Response(text=html_content, content_type="text/html", headers=_NO_CACHE_HEADERS)


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
        return web.Response(text=html_content, content_type="text/html", headers=_NO_CACHE_HEADERS)


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
        return web.Response(text=html_content, content_type="text/html", headers=_NO_CACHE_HEADERS)


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


class AnalyticsView(HomeAssistantView):
    """Serve the analytics page."""

    url = "/quizify/analytics"
    name = "quizify:analytics"
    requires_auth = False

    def __init__(self, hass: HomeAssistant) -> None:
        """Initialize the analytics view."""
        self.hass = hass

    async def get(self, request: web.Request) -> web.Response:  # noqa: ARG002
        """Serve the analytics HTML page."""
        html_path = Path(__file__).parent.parent / "www" / "analytics.html"

        if not html_path.exists():
            _LOGGER.error("Analytics page not found: %s", html_path)
            return web.Response(text="Analytics page not found", status=500)

        html_content = await self.hass.async_add_executor_job(_read_file, html_path)
        return web.Response(text=html_content, content_type="text/html", headers=_NO_CACHE_HEADERS)


class AnalyticsDataView(HomeAssistantView):
    """API endpoint for analytics data."""

    url = "/api/quizify/analytics/data"
    name = "api:quizify:analytics:data"
    requires_auth = False

    def __init__(self, hass: HomeAssistant) -> None:
        """Initialize the analytics data view."""
        self.hass = hass

    async def get(self, request: web.Request) -> web.Response:
        """Return analytics data as JSON."""
        analytics = self.hass.data.get(DOMAIN, {}).get("analytics")
        if not analytics:
            return web.json_response({"total_games": 0})

        period = request.query.get("period", "30d")
        metrics = analytics.compute_metrics(period)
        return web.json_response(metrics)


class PackVersionsView(HomeAssistantView):
    """API endpoint — installed question pack versions."""

    url = "/api/quizify/packs"
    name = "api:quizify:packs"
    requires_auth = False

    def __init__(self, hass: HomeAssistant) -> None:
        """Initialize the pack versions view."""
        self.hass = hass

    async def get(self, request: web.Request) -> web.Response:  # noqa: ARG002
        """Return installed pack version metadata."""
        game = self.hass.data.get(DOMAIN, {}).get("game")
        if game is None:
            return web.json_response({})

        # Ensure packs are loaded
        await self.hass.async_add_executor_job(game._question_bank.load_all_categories)
        installed = game._question_bank.get_pack_versions()
        return web.json_response(installed)


class PackUpdateCheckView(HomeAssistantView):
    """API endpoint — compare installed packs against latest versions on GitHub."""

    url = "/api/quizify/packs/updates"
    name = "api:quizify:packs:updates"
    requires_auth = False

    def __init__(self, hass: HomeAssistant) -> None:
        """Initialize the pack update check view."""
        self.hass = hass

    async def get(self, request: web.Request) -> web.Response:  # noqa: ARG002
        """Check GitHub for updated question packs.

        Returns a JSON object with:
          - installed: dict of slug -> {version, name, language, question_count}
          - upstream: dict of slug -> version (from GitHub versions.json), or null on error
          - updates: list of {slug, name, installed_version, upstream_version}
        """
        game = self.hass.data.get(DOMAIN, {}).get("game")
        if game is None:
            return web.json_response({"error": "game not initialised"}, status=503)

        await self.hass.async_add_executor_job(game._question_bank.load_all_categories)
        installed = game._question_bank.get_pack_versions()

        # Fetch upstream versions.json from GitHub (best-effort, 5s timeout)
        upstream: dict | None = None
        try:
            timeout = aiohttp.ClientTimeout(total=5)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get(_PACK_VERSIONS_URL) as resp:
                    if resp.status == 200:
                        upstream = await resp.json(content_type=None)
        except Exception as exc:  # noqa: BLE001
            _LOGGER.warning("Pack update check failed: %s", exc)

        updates = []
        if upstream:
            for slug, meta in installed.items():
                upstream_version = upstream.get(slug)
                if upstream_version and upstream_version != meta["version"]:
                    updates.append(
                        {
                            "slug": slug,
                            "name": meta["name"],
                            "installed_version": meta["version"],
                            "upstream_version": upstream_version,
                        }
                    )

        return web.json_response(
            {
                "installed": installed,
                "upstream": upstream,
                "updates": updates,
                "upstream_available": upstream is not None,
            }
        )
