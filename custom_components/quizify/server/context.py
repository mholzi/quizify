"""Application context passed to HTTP/WebSocket handlers.

Replaces the previous `hass.data[DOMAIN]` lookups so the same handlers can run
inside Home Assistant *and* under a plain aiohttp server.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..analytics import QuizifyAnalytics
    from ..game.state import QuizifyGameState
    from ..question_stats import QuestionStatsService
    from ..runtime import Runtime
    from .websocket import QuizifyWebSocketHandler


# aiohttp app key for stashing the AppContext on `web.Application`.
APP_CTX_KEY = "quizify_app_ctx"


@dataclass
class AppContext:
    """Everything the views and websocket handlers need to do their job."""

    runtime: Runtime
    game: QuizifyGameState
    analytics: QuizifyAnalytics
    ws_handler: QuizifyWebSocketHandler
    question_stats: QuestionStatsService | None = None
