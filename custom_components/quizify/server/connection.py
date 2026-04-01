"""WebSocket connection manager for Quizify."""
from __future__ import annotations

import asyncio
import logging
import uuid
from typing import TYPE_CHECKING, Any

from aiohttp import web

from custom_components.quizify.server.serializers import get_game_state

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

_LOGGER = logging.getLogger(__name__)


class ConnectionManager:
    """Manages WebSocket connections, session tokens, and broadcast primitives."""

    ADMIN_SESSION_GRACE = 120
    PLAYER_SESSION_GRACE = 60

    def __init__(self, hass: HomeAssistant) -> None:
        """Initialize the connection manager."""
        self.hass = hass
        self.connections: set[web.WebSocketResponse] = set()
        self._admin_connections: set[web.WebSocketResponse] = set()
        self._dashboard_connections: set[web.WebSocketResponse] = set()
        self._session_tokens: dict[str, str] = {}  # token -> player_name
        self._admin_session_token: str | None = None
        self._admin_disconnect_task: asyncio.Task | None = None
        self._pending_removals: dict[str, asyncio.Task] = {}

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
    # Admin session token
    # ------------------------------------------------------------------

    def get_or_create_admin_token(self) -> str:
        """Return the current admin session token, creating one if needed."""
        if not self._admin_session_token:
            self._admin_session_token = str(uuid.uuid4())
        return self._admin_session_token

    def validate_admin_token(self, token: str) -> bool:
        """Return True if *token* matches the current admin session token."""
        return bool(token and token == self._admin_session_token)

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

        self._admin_disconnect_task = asyncio.ensure_future(admin_timeout())

    # ------------------------------------------------------------------
    # Player session tokens
    # ------------------------------------------------------------------

    def create_session_token(self, player_name: str) -> str:
        """Create and store a new session token for *player_name*."""
        token = str(uuid.uuid4())
        self._session_tokens[token] = player_name
        return token

    def rotate_session_token(self, old_token: str, player_name: str) -> str:
        """Revoke *old_token* and issue a fresh one for *player_name*."""
        self._session_tokens.pop(old_token, None)
        return self.create_session_token(player_name)

    def get_player_for_token(self, token: str) -> str | None:
        """Return the player name associated with *token*, or None."""
        return self._session_tokens.get(token)

    def clear_player_tokens(self, player_name: str) -> None:
        """Remove all session tokens belonging to *player_name*."""
        stale = [t for t, n in self._session_tokens.items() if n == player_name]
        for t in stale:
            self._session_tokens.pop(t, None)

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
        gs = get_game_state(self.hass)
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
        gs = get_game_state(self.hass)
        player_ws_set = {p.ws for p in gs.get_players()} if gs else set()
        tasks = [
            self._safe_send(ws, message)
            for ws in list(self._admin_connections)
            if not ws.closed and ws not in player_ws_set
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
