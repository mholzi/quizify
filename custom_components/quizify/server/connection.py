"""WebSocket connection manager for Quizify."""
from __future__ import annotations

import asyncio
import logging
import time
import uuid
from typing import TYPE_CHECKING, Callable

from aiohttp import web

from .token_store import TokenStore

if TYPE_CHECKING:
    from ..game.state import QuizifyGameState
    from ..runtime import Runtime

_LOGGER = logging.getLogger(__name__)

# Filename for the persisted admin session token. Tokens older than
# _PLAYER_TOKEN_TTL are treated as expired — prevents cross-game token reuse
# on long-lived hosts.
_ADMIN_TOKEN_FILENAME = "admin_token.json"
_PLAYER_TOKEN_TTL = 24 * 60 * 60  # 24 hours


# A small callable that returns the active game state. Indirection so the
# connection manager doesn't need to import the game module directly and
# can keep working after a reset/rebuild.
GameStateProvider = Callable[[], "QuizifyGameState | None"]


class ConnectionManager:
    """Manages WebSocket connections, session tokens, and broadcast primitives."""

    ADMIN_SESSION_GRACE = 120
    PLAYER_SESSION_GRACE = 60

    def __init__(
        self,
        runtime: Runtime,
        game_state_provider: GameStateProvider,
    ) -> None:
        """Initialize the connection manager."""
        self._runtime = runtime
        self._get_game_state = game_state_provider
        self.connections: set[web.WebSocketResponse] = set()
        self._admin_connections: set[web.WebSocketResponse] = set()
        self._dashboard_connections: set[web.WebSocketResponse] = set()
        # Token → (player_name, issued_at_monotonic). Tokens older than
        # _PLAYER_TOKEN_TTL are treated as expired.
        self._session_tokens: dict[str, tuple[str, float]] = {}
        self._admin_session_token: str | None = None
        self._admin_disconnect_task: asyncio.Task | None = None
        self._pending_removals: dict[str, asyncio.Task] = {}

        # Persistent storage for admin token (survives host restarts).
        self._store = TokenStore(runtime, _ADMIN_TOKEN_FILENAME)
        self._admin_token_loaded = False

    # ------------------------------------------------------------------
    # Connection lifecycle
    # ------------------------------------------------------------------

    def add_connection(
        self, ws: web.WebSocketResponse, is_admin: bool, is_dashboard: bool
    ) -> None:
        """Register a new WebSocket connection."""
        self.connections.add(ws)
        if is_admin:
            self._admin_connections.add(ws)
        if is_dashboard:
            self._dashboard_connections.add(ws)

    def remove_connection(self, ws: web.WebSocketResponse) -> None:
        """Unregister a WebSocket connection from all sets."""
        self.connections.discard(ws)
        self._admin_connections.discard(ws)
        self._dashboard_connections.discard(ws)

    def is_admin_connection(self, ws: web.WebSocketResponse) -> bool:
        """Return True if *ws* is an admin connection."""
        return ws in self._admin_connections

    def add_to_admin_connections(self, ws: web.WebSocketResponse) -> None:
        """Add *ws* to the admin connections set."""
        self._admin_connections.add(ws)

    # ------------------------------------------------------------------
    # Admin session token (persisted across restarts)
    # ------------------------------------------------------------------

    async def async_load_admin_token(self) -> None:
        """Load the persisted admin token from disk.

        Idempotent — repeated calls are no-ops after the first load.
        """
        if self._admin_token_loaded:
            return
        data = await self._store.load()
        if data and isinstance(data, dict):
            self._admin_session_token = data.get("token")
            if self._admin_session_token:
                _LOGGER.info("Loaded persisted admin session token from disk")
        self._admin_token_loaded = True

    async def _async_save_admin_token(self) -> None:
        """Persist the current admin token to disk (or delete the file)."""
        if self._admin_session_token:
            await self._store.save({"token": self._admin_session_token})
        else:
            # Delete the storage file outright so the next load returns
            # None and the bootstrap path fires.
            await self._store.remove()

    def get_or_create_admin_token(self) -> str:
        """Return the current admin session token, creating one if needed.

        Newly created tokens are persisted asynchronously so they survive
        host restarts.
        """
        if not self._admin_session_token:
            self._admin_session_token = str(uuid.uuid4())
            # Fire-and-forget persist; missing the write isn't fatal, the
            # token just won't survive the next restart.
            asyncio.ensure_future(self._async_save_admin_token())
        return self._admin_session_token

    def validate_admin_token(self, token: str) -> bool:
        """Return True if *token* matches the current admin session token."""
        return bool(token and token == self._admin_session_token)

    def clear_admin_token(self) -> None:
        """Explicit admin-token reset (e.g. user clicked 'Log out admin')."""
        self._admin_session_token = None
        asyncio.ensure_future(self._async_save_admin_token())

    async def async_clear_admin_token(self) -> None:
        """Async variant: clear in-memory token AND await the storage delete."""
        self._admin_session_token = None
        await self._async_save_admin_token()
        # Mark as loaded so any later async_load is a no-op (file is gone).
        self._admin_token_loaded = True

    def cancel_admin_disconnect(self) -> None:
        """Cancel a running admin-disconnect grace-period task."""
        if self._admin_disconnect_task and not self._admin_disconnect_task.done():
            self._admin_disconnect_task.cancel()
            self._admin_disconnect_task = None

    def schedule_admin_timeout(self) -> None:
        """Start the admin-session grace period; clears the token on expiry."""

        async def admin_timeout() -> None:
            await asyncio.sleep(self.ADMIN_SESSION_GRACE)
            _LOGGER.info("Admin session grace period expired, clearing token")
            self._admin_session_token = None
            await self._async_save_admin_token()

        self._admin_disconnect_task = asyncio.ensure_future(admin_timeout())

    # ------------------------------------------------------------------
    # Player session tokens (with TTL)
    # ------------------------------------------------------------------

    def create_session_token(self, player_name: str) -> str:
        """Create and store a new session token for *player_name*."""
        token = str(uuid.uuid4())
        self._session_tokens[token] = (player_name, time.monotonic())
        return token

    def rotate_session_token(self, old_token: str, player_name: str) -> str:
        """Revoke *old_token* and issue a fresh one for *player_name*."""
        self._session_tokens.pop(old_token, None)
        return self.create_session_token(player_name)

    def get_player_for_token(self, token: str) -> str | None:
        """Return the player name associated with *token*, or None."""
        entry = self._session_tokens.get(token)
        if not entry:
            return None
        name, issued_at = entry
        if time.monotonic() - issued_at > _PLAYER_TOKEN_TTL:
            self._session_tokens.pop(token, None)
            return None
        return name

    def clear_player_tokens(self, player_name: str) -> None:
        """Remove all session tokens belonging to *player_name*."""
        stale = [t for t, (n, _ts) in self._session_tokens.items() if n == player_name]
        for t in stale:
            self._session_tokens.pop(t, None)

    def clear_all_player_tokens(self) -> None:
        """Wipe every player token. Called on game reset to prevent
        cross-game token reuse."""
        self._session_tokens.clear()

    # ------------------------------------------------------------------
    # Pending-removal registry
    # ------------------------------------------------------------------

    def schedule_player_removal(
        self, name: str, timeout: float, remove_fn
    ) -> None:
        """Schedule *remove_fn(name, timeout)* and track the task."""
        task = asyncio.ensure_future(remove_fn(name, timeout))
        self._pending_removals[name] = task

    def cancel_pending_removal(self, name: str) -> None:
        """Cancel a pending removal task for *name* (e.g. on reconnect)."""
        task = self._pending_removals.pop(name, None)
        if task and not task.done():
            task.cancel()
            _LOGGER.info("Cancelled removal for reconnecting player: %s", name)

    def get_pending_removals(self) -> dict[str, asyncio.Task]:
        """Return the pending-removals mapping."""
        return self._pending_removals

    # ------------------------------------------------------------------
    # Broadcast helpers
    # ------------------------------------------------------------------

    async def broadcast(self, message: dict) -> None:
        """Broadcast *message* to all connected clients in parallel."""
        if not self.connections:
            return
        tasks = [
            self._safe_send(ws, message)
            for ws in list(self.connections)
            if not ws.closed
        ]
        if tasks:
            await asyncio.gather(*tasks)

    async def broadcast_to_players(self, message: dict) -> None:
        """Broadcast to all player connections (excludes pure-admin connections).

        Admin-as-player connections (present in both *connections* and
        *_admin_connections*) also receive player messages so they see
        questions/answers without the correct-answer flag.
        """
        gs = self._get_game_state()
        gs_players = gs.get_players() if gs else []

        tasks = []
        for ws in list(self.connections):
            if ws.closed:
                continue
            is_pure_admin = ws in self._admin_connections and not any(
                p.ws is ws for p in gs_players
            )
            if not is_pure_admin:
                tasks.append(self._safe_send(ws, message))
        if tasks:
            await asyncio.gather(*tasks)

    async def broadcast_to_admins(self, message: dict) -> None:
        """Broadcast admin-only messages (with correct answer) to pure admin connections.

        Admin-as-player connections are excluded so they don't see the
        correct answer before submitting.
        """
        if not self._admin_connections:
            return
        gs = self._get_game_state()
        admin_as_player_ws = set()
        if gs:
            for p in gs.get_players():
                if p.is_admin and p.ws is not None:
                    admin_as_player_ws.add(p.ws)
        tasks = [
            self._safe_send(ws, message)
            for ws in list(self._admin_connections)
            if not ws.closed and ws not in admin_as_player_ws
        ]
        if tasks:
            await asyncio.gather(*tasks)

    async def _safe_send(self, ws: web.WebSocketResponse, message: dict) -> None:
        """Send *message* to *ws*, swallowing any send errors."""
        try:
            await ws.send_json(message)
        except Exception as err:  # noqa: BLE001
            _LOGGER.warning("Failed to send to WebSocket: %s", err)

    async def send_error(
        self, ws: web.WebSocketResponse, code: str, message: str
    ) -> None:
        """Send a structured error message to *ws*."""
        await self._safe_send(ws, {"type": "error", "code": code, "message": message})

    # ------------------------------------------------------------------
    # Cleanup
    # ------------------------------------------------------------------

    async def cleanup(self) -> None:
        """Cancel all pending removal and admin-disconnect tasks."""
        for task in list(self._pending_removals.values()):
            if not task.done():
                task.cancel()
        self._pending_removals.clear()

        if self._admin_disconnect_task and not self._admin_disconnect_task.done():
            self._admin_disconnect_task.cancel()
