"""Server module: HTTP views, WebSocket handler, and connection manager.

Both the Home Assistant adapter (``custom_components.quizify.__init__``)
and the standalone dev server (``scripts/dev_server.py``) build an
:class:`~custom_components.quizify.server.context.AppContext` and call
:func:`build_aiohttp_app` (standalone) or the HA helpers below.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING

from aiohttp import web

from .context import APP_CTX_KEY, AppContext
from .views import register_routes

if TYPE_CHECKING:
    from .websocket import QuizifyWebSocketHandler


_LOGGER = logging.getLogger(__name__)

# Project-relative directory of static frontend assets.
WWW_DIR = Path(__file__).parent.parent / "www"

# URL prefix the frontend uses for static assets (CSS, JS, images, i18n).
STATIC_URL_PREFIX = "/quizify/static"

# Path of the WebSocket endpoint. Single source of truth so HA and
# standalone agree without duplicating the string.
WS_PATH = "/api/quizify/ws"


def build_aiohttp_app(
    ctx: AppContext,
    ws_handler: "QuizifyWebSocketHandler",
) -> web.Application:
    """Build a fully-wired aiohttp Application for standalone mode.

    Registers the HTTP routes, the WebSocket endpoint, and the static
    asset directory. Stashes the :class:`AppContext` on the app so
    request handlers can pull it via ``request.app[APP_CTX_KEY]``.
    """
    app = web.Application()
    app[APP_CTX_KEY] = ctx

    register_routes(app.router)
    app.router.add_get(WS_PATH, ws_handler.handle)
    app.router.add_static(STATIC_URL_PREFIX, WWW_DIR, show_index=False)

    _LOGGER.info(
        "Built standalone Quizify aiohttp app (static=%s)",
        WWW_DIR,
    )
    return app
