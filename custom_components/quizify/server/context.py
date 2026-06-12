"""Application context passed to HTTP/WebSocket handlers.

Replaces the previous `hass.data[DOMAIN]` lookups so the same handlers can run
inside Home Assistant *and* under a plain aiohttp server.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..analytics import QuizifyAnalytics
    from ..game.state import QuizifyGameState
    from ..question_stats import QuestionStatsService
    from ..runtime import Runtime
    from .websocket import QuizifyWebSocketHandler


_LOGGER = logging.getLogger(__name__)

# aiohttp app key for stashing the AppContext on `web.Application`.
APP_CTX_KEY = "quizify_app_ctx"

# Fallback if manifest.json can't be parsed (truly bad install). We never
# silently serve a fake matching version — "unknown" makes the drift loud.
_VERSION_FALLBACK = "unknown"

_MANIFEST_PATH = Path(__file__).parent.parent / "manifest.json"


def read_manifest_version() -> str:
    """Read the integration version string out of manifest.json.

    Single source of truth for every cache-buster the frontend uses
    (``?v=`` query strings, the service-worker ``CACHE_VERSION``, the
    ``/api/quizify/status`` payload). Bumping ``manifest.json`` is all
    a release needs.

    BLOCKING (``read_text``) — must NOT be called on the event loop (#343).
    On the HA path ``async_setup_entry`` runs it via
    ``hass.async_add_executor_job`` and passes the result into
    ``AppContext(version=...)`` explicitly; the synchronous ``default_factory``
    on the dataclass is only a fallback for the standalone dev server and
    tests, which are not on HA's loop. Falls back to ``"unknown"`` only if the
    file is missing or malformed.
    """
    try:
        data = json.loads(_MANIFEST_PATH.read_text(encoding="utf-8"))
        version = data.get("version")
        if isinstance(version, str) and version:
            return version
    except (OSError, ValueError) as err:  # pragma: no cover — defensive
        _LOGGER.warning("Could not read version from %s: %s", _MANIFEST_PATH, err)
    return _VERSION_FALLBACK


@dataclass
class AppContext:
    """Everything the views and websocket handlers need to do their job."""

    runtime: Runtime
    game: QuizifyGameState
    analytics: QuizifyAnalytics
    ws_handler: QuizifyWebSocketHandler
    question_stats: QuestionStatsService | None = None
    # Integration version (from manifest.json). Drives cache-busters in
    # the served HTML, the service-worker cache name, and the /status
    # endpoint. Default-factory keeps existing AppContext(...) call sites
    # working without passing version explicitly.
    version: str = field(default_factory=read_manifest_version)
    # Home Assistant's configured language (``hass.config.language``), set
    # only on the HA path. ``None`` on the standalone dev server, which has
    # no hass. The admin page uses this as the first source for its initial
    # UI language (substituted into the ``{{HA_LANG}}`` token).
    ha_language: str | None = None
    # Worker endpoint a composed community pack is POSTed to so it lands as a
    # GitHub issue for review (#180). ``None``/empty keeps the whole in-app
    # submission feature hidden and inert. Mutable so an options-flow change
    # toggles the feature without an HA restart.
    community_submit_url: str | None = None
    # Shared secret sent as the ``X-Quizify-Secret`` header on the worker POST
    # (#256). When set (and the worker has a matching ``SHARED_SECRET``), it
    # closes the open-proxy hole. ``None``/empty → header omitted, fully
    # back-compatible. Mutable so an options-flow change applies without an HA
    # restart, mirroring ``community_submit_url``.
    community_submit_secret: str | None = None
