#!/usr/bin/env python3
"""Run Quizify standalone (no Home Assistant) for fast iteration.

Usage:

    # From the repo root, in a venv with aiohttp + voluptuous installed:
    python scripts/dev_server.py
    python scripts/dev_server.py --host 0.0.0.0 --port 8080
    python scripts/dev_server.py --data-dir /tmp/quizify

Then open http://localhost:8080/quizify/launcher in a browser.

The dev server mounts the same HTTP/WebSocket handlers the Home Assistant
integration uses, so any change to ``custom_components/quizify/`` is testable
in <1s without an HA reload.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path

# Allow `from custom_components.quizify...` imports when running from the
# repo root. We don't install Quizify as a package; the integration is
# laid out for HA's custom_components convention.
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from aiohttp import web  # noqa: E402

from custom_components.quizify.analytics import QuizifyAnalytics  # noqa: E402
from custom_components.quizify.game.state import QuizifyGameState  # noqa: E402
from custom_components.quizify.question_stats import (  # noqa: E402
    QuestionStatsService,
)
from custom_components.quizify.runtime import StandaloneRuntime  # noqa: E402
from custom_components.quizify.server import build_aiohttp_app  # noqa: E402
from custom_components.quizify.server.context import (  # noqa: E402
    APP_CTX_KEY,
    AppContext,
)
from custom_components.quizify.server.websocket import (  # noqa: E402
    QuizifyWebSocketHandler,
)

_LOGGER = logging.getLogger("quizify.dev_server")


def _default_data_dir() -> Path:
    """Where standalone state (admin token, analytics, history) lives.

    Defaults to ``./.quizify-data/`` next to the repo so a checkout is
    self-contained; users can override with --data-dir.
    """
    return _REPO_ROOT / ".quizify-data"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run the Quizify core as a standalone aiohttp server."
    )
    parser.add_argument("--host", default="127.0.0.1", help="bind host (default: 127.0.0.1)")
    # Default to $PORT if set (so harnesses like preview_start can pick a free
    # port via env), else 8080.
    parser.add_argument(
        "--port",
        type=int,
        default=int(os.environ.get("PORT", "8080")),
        help="bind port (default: $PORT or 8080)",
    )
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=None,
        help=f"persistent state directory (default: {_default_data_dir()})",
    )
    parser.add_argument(
        "-v", "--verbose", action="store_true", help="enable debug logging"
    )
    return parser.parse_args(argv)


async def _on_startup(_app: web.Application) -> None:
    """Print the launcher URL once the server is actually listening."""
    _LOGGER.info(
        "Quizify dev server ready — open http://%s:%s/quizify/launcher",
        _CURRENT_HOST,
        _CURRENT_PORT,
    )


# Set by main() so the on_startup hook can log the URL.
_CURRENT_HOST = "127.0.0.1"
_CURRENT_PORT = 8080


async def _on_cleanup(app: web.Application) -> None:
    """Cancel pending game tasks on shutdown."""
    ctx: AppContext | None = app.get(APP_CTX_KEY)
    if ctx and ctx.ws_handler:
        await ctx.ws_handler.cleanup_game_tasks()


def build_app(data_dir: Path) -> web.Application:
    """Build the standalone aiohttp app and its AppContext."""
    runtime = StandaloneRuntime(data_dir=data_dir)

    analytics = QuizifyAnalytics(runtime)
    question_stats = QuestionStatsService(runtime)

    game_state = QuizifyGameState(runtime=runtime, entry_id="standalone")
    game_state._stats_service = analytics
    game_state._question_stats = question_stats

    ctx_holder: dict[str, AppContext] = {}

    ws_handler = QuizifyWebSocketHandler(
        runtime=runtime,
        # Provider closure: returns the game state currently held in ctx.
        # Indirection mirrors HA mode (where game state may be swapped on
        # re-setup); in standalone it's always the same instance.
        game_state_provider=lambda: ctx_holder["ctx"].game,
    )

    ctx = AppContext(
        runtime=runtime,
        game=game_state,
        analytics=analytics,
        ws_handler=ws_handler,
        question_stats=question_stats,
    )
    ctx_holder["ctx"] = ctx

    # Wire the broadcast callback the same way HA mode does.
    game_state.set_broadcast_callback(ws_handler.broadcast_state)

    app = build_aiohttp_app(ctx, ws_handler)
    app.on_startup.append(_on_startup)
    app.on_cleanup.append(_on_cleanup)
    return app


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )

    data_dir = args.data_dir or _default_data_dir()
    _LOGGER.info("Quizify standalone — data dir: %s", data_dir)

    # Stash host/port for the on_startup log line.
    global _CURRENT_HOST, _CURRENT_PORT  # noqa: PLW0603
    _CURRENT_HOST, _CURRENT_PORT = args.host, args.port

    app = build_app(data_dir=data_dir)

    # Async load analytics from disk before serving. aiohttp's run_app
    # accepts an on_startup hook, but Analytics.load is async so we
    # piggyback on startup directly.
    async def _load_state(application: web.Application) -> None:
        ctx: AppContext = application[APP_CTX_KEY]
        await ctx.analytics.load()
        if ctx.question_stats is not None:
            await ctx.question_stats.load()
        await ctx.ws_handler._conn.async_load_admin_token()

    app.on_startup.append(_load_state)

    web.run_app(app, host=args.host, port=args.port, print=lambda _msg: None)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
