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

    Synchronous on purpose: called once at app startup, before any
    request handlers run, so there's no event-loop blocking concern.
    Falls back to ``"unknown"`` only if the file is missing or malformed.
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
