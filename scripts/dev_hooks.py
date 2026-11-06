"""Dev-server hooks that put every state of the UI on a button (#1182).

The states review (UX review 2) could only run on the wire: with the Mac too
loaded for a browser, nothing could make an endpoint fail, a picture break or
a socket drop on demand. These hooks make each row of its state inventory
reproducible against ``scripts/dev_server.py``, from a script or by hand.

**Dev-only.** This module lives in ``scripts/``, is imported by
``dev_server.py`` alone and is never shipped in ``custom_components/``. Every
hook is off unless its flag is given; the two endpoints exist only with
``--dev``.

Flags (all repeatable where it makes sense)::

    --fail /api/quizify/analytics/data=500      answer that path with 500
    --fail /api/quizify/flags=401               …or 401, or
    --fail /api/quizify/analytics/*=timeout     hang 30 s, then 504
    --fail /api/quizify/tts-entities=json-error 200 with a non-JSON body
    --delay /api/quizify/*=6000                 hold matching requests 6 s
    --delay /quizify/static/img/*=8000          slow pictures
    --broken-images                             every pack picture 404s
    --seed-players 6 --seed-phase QUESTION_ACTIVE   bots, then a phase
    --dev                                       the endpoints below

Endpoints (``--dev`` only)::

    POST /api/quizify/dev/drop-sockets?role=player|admin|dashboard|all&for=60
        Close those sockets and refuse their upgrades with 503 for N s.
    POST /api/quizify/dev/restart
        What an HA restart does to the game: every socket closed, the game
        and all session tokens gone (they live in memory), the admin token
        kept (it is on disk), and the running-game marker read back.
"""

from __future__ import annotations

import asyncio
import fnmatch
import logging
import time
from typing import Any

from aiohttp import web

_LOGGER = logging.getLogger("quizify.dev_hooks")

WS_PATH = "/api/quizify/ws"
_IMG_PREFIX = "/quizify/static/img/packs/"
_ROLES = ("player", "admin", "dashboard")
_BLOCK_KEY: web.AppKey[dict[str, float]] = web.AppKey("dev_block_until", dict)
_SEED_KEY: web.AppKey[Any] = web.AppKey("dev_seed_task", object)


def _pairs(values: list[str] | None, what: str) -> list[tuple[str, str]]:
    out = []
    for raw in values or []:
        pattern, sep, value = raw.rpartition("=")
        if not sep or not pattern:
            raise SystemExit(f"--{what} wants PATTERN=VALUE, got {raw!r}")
        out.append((pattern, value))
    return out


def add_arguments(parser: Any) -> None:
    """Register the hook flags on the dev server's argument parser."""
    parser.add_argument("--fail", action="append", metavar="PATH=CODE",
                        help="answer matching requests with CODE "
                             "(an HTTP status, 'timeout' or 'json-error')")
    parser.add_argument("--delay", action="append", metavar="PATH=MS",
                        help="hold matching requests for MS milliseconds")
    parser.add_argument("--broken-images", action="store_true",
                        help="every pack picture answers 404")
    parser.add_argument("--seed-players", type=int, default=0, metavar="N",
                        help="add N bot players at start-up")
    parser.add_argument("--seed-phase", default="LOBBY",
                        choices=("LOBBY", "QUESTION_ACTIVE", "ANSWER_REVEAL", "FINALE"),
                        help="drive the seeded game to this phase")
    parser.add_argument("--dev", action="store_true",
                        help="enable the /api/quizify/dev/* endpoints")


def _role_of(request: web.Request) -> str:
    return request.query.get("role") or "player"


def make_middleware(args: Any) -> Any:
    """The fault-injection middleware, or None when no flag asks for one."""
    fails = _pairs(args.fail, "fail")
    delays = _pairs(args.delay, "delay")
    if not (fails or delays or args.broken_images or args.dev):
        return None

    @web.middleware
    async def dev_faults(request: web.Request, handler: Any) -> web.StreamResponse:
        path = request.path
        if path == WS_PATH:
            until = request.app[_BLOCK_KEY].get(_role_of(request), 0.0)
            if until > time.monotonic():
                return web.Response(status=503, text="dev: socket blocked")
        for pattern, ms in delays:
            if fnmatch.fnmatch(path, pattern):
                await asyncio.sleep(int(ms) / 1000)
                break
        if args.broken_images and path.startswith(_IMG_PREFIX):
            return web.Response(status=404, text="dev: broken image")
        for pattern, code in fails:
            if not fnmatch.fnmatch(path, pattern):
                continue
            if code == "timeout":
                await asyncio.sleep(30)
                return web.Response(status=504, text="dev: timeout")
            if code == "json-error":
                return web.Response(status=200, text="<html>not json</html>",
                                    content_type="text/html")
            status = int(code)
            return web.json_response({"error": f"dev: forced {status}"}, status=status)
        return await handler(request)

    return dev_faults


def _sockets_for(conn: Any, role: str) -> list[Any]:
    admins = set(conn._admin_connections)
    boards = set(conn._dashboard_connections)
    if role == "admin":
        return list(admins)
    if role == "dashboard":
        return list(boards)
    if role == "player":
        return [ws for ws in conn.connections if ws not in admins and ws not in boards]
    return list(conn.connections)


async def _drop_sockets(request: web.Request) -> web.Response:
    from custom_components.quizify.server.context import APP_CTX_KEY

    role = request.query.get("role", "all")
    if role not in (*_ROLES, "all"):
        return web.json_response({"error": "role"}, status=400)
    seconds = float(request.query.get("for", "0") or 0)
    conn = request.app[APP_CTX_KEY].ws_handler._conn
    if seconds > 0:
        for r in _ROLES if role == "all" else (role,):
            request.app[_BLOCK_KEY][r] = time.monotonic() + seconds
    victims = _sockets_for(conn, role)
    await asyncio.gather(*(conn.close_socket(ws) for ws in victims))
    _LOGGER.warning("dev: dropped %d %s socket(s), blocked for %ss",
                    len(victims), role, seconds)
    return web.json_response({"dropped": len(victims), "blocked_for": seconds})


async def _restart(request: web.Request) -> web.Response:
    from custom_components.quizify.server.context import APP_CTX_KEY

    ctx = request.app[APP_CTX_KEY]
    handler = ctx.ws_handler
    game = ctx.game
    marker = handler.interrupted_game
    was_running = marker._on_disk
    # Detach the marker first: a dying process does not get to tidy up its
    # file, and the reset below would otherwise note LOBBY and remove it.
    marker._file = None
    handler._cancel_round_tasks()
    await handler._conn.cleanup()
    await handler._conn.close_all_connections()
    # Memory is gone: players, the round, every session token — and the
    # record of why any token was retired, which is how the next reconnect
    # comes to read "restart".
    handler._conn._session_tokens.clear()
    handler._conn._retired_tokens.clear()
    game.clear_all_players()
    game.reset_to_lobby()
    # The marker file is what survives; read it back like a fresh start-up.
    marker._on_disk = False
    await marker.load()
    _LOGGER.warning("dev: simulated restart (game was running: %s)", was_running)
    return web.json_response({"was_running": was_running,
                              "notice": marker.notice})


async def _seed(app: web.Application, args: Any) -> None:
    from custom_components.quizify.game.state import GamePhase
    from custom_components.quizify.server import game_commands
    from custom_components.quizify.server.context import APP_CTX_KEY

    ctx = app[APP_CTX_KEY]
    game = ctx.game
    for i in range(args.seed_players):
        ok, _err = game.add_player(f"Bot {i + 1}", None)
        if ok:
            game.get_player(f"Bot {i + 1}").connected = True
    if args.seed_phase == "LOBBY" or not args.seed_players:
        return
    handler = ctx.ws_handler
    await handler.admin_action_start_game(game)
    for _ in range(150):
        if game.phase == GamePhase.QUESTION_ACTIVE:
            break
        await asyncio.sleep(0.1)
    if args.seed_phase == "ANSWER_REVEAL":
        await game_commands.handle_admin_skip(handler, None, game)  # type: ignore[arg-type]
    elif args.seed_phase == "FINALE":
        await handler.admin_action_end_game(game)
    _LOGGER.warning("dev: seeded %d players, phase now %s",
                    args.seed_players, game.phase)


def install(app: web.Application, args: Any) -> None:
    """Wire whatever the flags asked for into the dev server's app."""
    app[_BLOCK_KEY] = {}
    middleware = make_middleware(args)
    if middleware is not None:
        app.middlewares.append(middleware)
        _LOGGER.warning("dev fault injection on: fail=%s delay=%s broken_images=%s",
                        args.fail, args.delay, args.broken_images)
    if args.dev:
        app.router.add_post("/api/quizify/dev/drop-sockets", _drop_sockets)
        app.router.add_post("/api/quizify/dev/restart", _restart)
        _LOGGER.warning("dev endpoints enabled: /api/quizify/dev/*")
    if args.seed_players:
        async def _seed_later(application: web.Application) -> None:
            application[_SEED_KEY] = asyncio.ensure_future(_seed(application, args))
        app.on_startup.append(_seed_later)
