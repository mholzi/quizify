"""WebSocket connection manager for Quizify."""
from __future__ import annotations

import asyncio
import hmac
import json
import logging
import time
import uuid
from typing import TYPE_CHECKING, Awaitable, Callable

from aiohttp import web

from .token_store import TokenStore

if TYPE_CHECKING:
    from ..game.state import QuizifyGameState
    from ..runtime import Runtime

_LOGGER = logging.getLogger(__name__)


def _log_task_exception(task: "asyncio.Task") -> None:
    """Done-callback surfacing a fire-and-forget task's exception (#307).

    Without it, an exception in an ensure_future'd background task (token
    persist, admin-timeout, player-removal) is swallowed or only warns at GC
    time. Reuses the analytics.py logging-done-callback pattern.
    """
    if task.cancelled():
        return
    if (exc := task.exception()) is not None:
        _LOGGER.error("Unhandled exception in background task: %s", exc)

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

    # Per-send wall-clock cap for a single fan-out send (#307). A half-dead
    # phone (TCP zero-window, wifi sleep) can otherwise pin drain() up to the
    # 30s heartbeat; without a cap a single such client stalls the whole
    # broadcast()/per-player gather and the calling handler room-wide. 2s is
    # comfortably above a healthy send yet bounds the worst case.
    _SEND_TIMEOUT = 2.0

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
        # Serializes the admin-bootstrap decision (load + grant-once + persist)
        # so two near-simultaneous /quizify/admin connects on a fresh install
        # can't both pass the "no token yet" check and both be granted admin
        # (#168: only one token persists, so the other admin would be silently
        # locked out). See try_bootstrap_admin().
        self._bootstrap_lock = asyncio.Lock()

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

    def has_admin_connections(self) -> bool:
        """Return True if at least one admin connection is registered."""
        return bool(self._admin_connections)

    def iter_admin_and_dashboard_ws(
        self,
    ) -> "list[tuple[web.WebSocketResponse, bool]]":
        """Yield (ws, is_admin) for every admin and dashboard connection.

        Lets callers fan out to admin + TV-dashboard spectator sockets
        without reaching into the private ``_admin_connections`` /
        ``_dashboard_connections`` sets. ``is_admin`` lets the caller apply
        the admin-only filter (e.g. excluding an admin who is also a player)
        while treating dashboards unconditionally.
        """
        return [(ws, True) for ws in list(self._admin_connections)] + [
            (ws, False) for ws in list(self._dashboard_connections)
        ]

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
            _persist = asyncio.ensure_future(self._async_save_admin_token())
            _persist.add_done_callback(_log_task_exception)
        return self._admin_session_token

    def validate_admin_token(self, token: str) -> bool:
        """Return True if *token* matches the current admin session token.

        The comparison uses :func:`hmac.compare_digest` so it runs in constant
        time and doesn't leak the token byte-by-byte through response timing
        (#259). The empty-token / no-token-set guards short-circuit *before* the
        compare: ``compare_digest`` requires both operands to be set strings of
        the same type, and a None/empty admin token must never validate.
        """
        if not token or not self._admin_session_token:
            return False
        return hmac.compare_digest(token, self._admin_session_token)

    async def try_bootstrap_admin(self) -> bool:
        """Atomically grant + persist the admin token on a fresh install.

        Returns True only for the single connection that wins the bootstrap
        race; every later caller sees the token already set and returns False.

        The whole load → check → create → persist sequence runs under
        ``_bootstrap_lock`` so two concurrent first-connections can't both be
        granted admin (#168). The persist is awaited (not fire-and-forget) so
        the token is durable before admin is granted — the second connection,
        once it acquires the lock, reliably observes it.
        """
        async with self._bootstrap_lock:
            await self.async_load_admin_token()
            if self._admin_session_token:
                # Someone (this run or a previous restart) already holds it.
                return False
            self._admin_session_token = str(uuid.uuid4())
            await self._async_save_admin_token()
            return True

    async def async_clear_admin_token(self) -> None:
        """Async variant: clear in-memory token AND await the storage delete."""
        self._admin_session_token = None
        await self._async_save_admin_token()
        # Mark as loaded so any later async_load is a no-op (file is gone).
        self._admin_token_loaded = True

    def has_pending_admin_disconnect(self) -> bool:
        """Return True if an admin-disconnect grace-period task is running."""
        return bool(
            self._admin_disconnect_task and not self._admin_disconnect_task.done()
        )

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
        self._admin_disconnect_task.add_done_callback(_log_task_exception)

    # ------------------------------------------------------------------
    # Player session tokens (with TTL)
    # ------------------------------------------------------------------

    def _sweep_expired_tokens(self) -> None:
        """Drop session tokens past their TTL.

        ``get_player_for_token`` only evicts a token when it is looked up, so a
        token that is issued and never validated again would linger forever and
        the dict would grow unbounded over a long-lived host (#168). Sweeping
        opportunistically on every new-token creation bounds the dict to active
        plus recently-issued tokens, with no background task.
        """
        now = time.monotonic()
        expired = [
            t
            for t, (_n, issued_at) in self._session_tokens.items()
            if now - issued_at > _PLAYER_TOKEN_TTL
        ]
        for t in expired:
            self._session_tokens.pop(t, None)

    def create_session_token(self, player_name: str) -> str:
        """Create and store a new session token for *player_name*."""
        self._sweep_expired_tokens()
        token = str(uuid.uuid4())
        self._session_tokens[token] = (player_name, time.monotonic())
        return token

    def rotate_session_token(self, old_token: str, player_name: str) -> str:
        """Revoke *old_token* and issue a fresh one for *player_name*."""
        self._session_tokens.pop(old_token, None)
        return self.create_session_token(player_name)

    def revoke_token(self, token: str) -> None:
        """Revoke a single session *token* (no-op if unknown)."""
        self._session_tokens.pop(token, None)

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
        self,
        name: str,
        timeout: float,
        remove_fn: Callable[[str, float], Awaitable[None]],
    ) -> None:
        """Schedule *remove_fn(name, timeout)* and track the task.

        #310: entries used to be popped only on reconnect, so one completed
        done-Task per distinct removed player name accumulated forever on a
        long-lived host (the single never-shrinking structure in the codebase).
        A done-callback now pops the entry once the task finishes (guarded so a
        re-scheduled task for the same name isn't clobbered). Re-scheduling for
        a name with a still-pending task cancels the old one first, so the
        registry never silently leaks a task it can no longer reach.
        """
        existing = self._pending_removals.get(name)
        if existing is not None and not existing.done():
            existing.cancel()
        task = asyncio.ensure_future(remove_fn(name, timeout))
        self._pending_removals[name] = task

        def _cleanup(t: asyncio.Task) -> None:
            # Only drop the entry if it's still THIS task (a reconnect may have
            # already replaced it via cancel_pending_removal / reschedule).
            if self._pending_removals.get(name) is t:
                self._pending_removals.pop(name, None)
            _log_task_exception(t)

        task.add_done_callback(_cleanup)

    def cancel_pending_removal(self, name: str) -> None:
        """Cancel a pending removal task for *name* (e.g. on reconnect)."""
        task = self._pending_removals.pop(name, None)
        if task and not task.done():
            task.cancel()
            _LOGGER.info("Cancelled removal for reconnecting player: %s", name)

    # ------------------------------------------------------------------
    # Broadcast helpers
    # ------------------------------------------------------------------

    async def broadcast(self, message: dict) -> None:
        """Broadcast *message* to all connected clients in parallel."""
        if not self.connections:
            return
        # Serialize once, then send_str to every client (#258) — avoids
        # re-encoding the same payload N times via per-client send_json.
        payload = json.dumps(message)
        tasks = [
            self._safe_send_str(ws, payload)
            for ws in list(self.connections)
            if not ws.closed
        ]
        if tasks:
            await asyncio.gather(*tasks)

    async def broadcast_to_admins_and_dashboards(self, message: dict) -> None:
        """Broadcast admin-only messages (with correct answer) to pure admin
        connections and the TV-dashboard spectator connections (role=dashboard).

        Dashboards need the same canonical-order question payload the admin
        sees — they render an unshuffled view of the same content. Without
        this, the dashboard's question-view answer-grid never populates and
        the v1.1.47 #151 answer-distribution chart has nothing to attach to.
        Admin-as-player connections are still excluded so a player who is
        also admin doesn't see the correct answer before submitting.
        """
        if not self._admin_connections and not self._dashboard_connections:
            return
        gs = self._get_game_state()
        admin_as_player_ws: set[web.WebSocketResponse] = set()
        if gs:
            for p in gs.get_players():
                if p.is_admin and p.ws is not None:
                    admin_as_player_ws.add(p.ws)
        targets: set[web.WebSocketResponse] = set()
        targets.update(self._admin_connections)
        targets.update(self._dashboard_connections)
        payload = json.dumps(message)  # serialize once, send_str per client (#258)
        tasks = [
            self._safe_send_str(ws, payload)
            for ws in targets
            if not ws.closed and ws not in admin_as_player_ws
        ]
        if tasks:
            await asyncio.gather(*tasks)

    async def send(self, ws: web.WebSocketResponse, message: dict) -> None:
        """Send *message* to *ws*, swallowing any send errors.

        The public single-socket send primitive. Errors are intentionally
        swallowed (and logged) so one dead/slow client can't break a fan-out.
        A per-send timeout (#307) bounds a half-dead client so the per-player
        gather fan-outs (timer ticks, question sends) can't stall room-wide.
        """
        try:
            await asyncio.wait_for(ws.send_json(message), timeout=self._SEND_TIMEOUT)
        except asyncio.TimeoutError:
            _LOGGER.warning("Timed out sending to WebSocket (slow/dead client)")
        except Exception as err:  # noqa: BLE001
            _LOGGER.warning("Failed to send to WebSocket: %s", err)

    # Backwards-compatible alias for the former private name. Some tests still
    # patch ``_safe_send``; keep it pointing at the public method.
    _safe_send = send

    async def _safe_send_str(self, ws: web.WebSocketResponse, payload: str) -> None:
        """Send a pre-serialized JSON *payload* to *ws*, swallowing errors.

        Used by the broadcast helpers so the same fan-out message is
        serialized once (json.dumps) rather than re-encoded per client (#258).
        Wrapped in a per-send timeout (#307) so one stalled socket can't pin
        the whole fan-out; a TimeoutError is swallowed like any other send
        error so the broadcast still reaches the healthy clients.
        """
        try:
            await asyncio.wait_for(ws.send_str(payload), timeout=self._SEND_TIMEOUT)
        except asyncio.TimeoutError:
            _LOGGER.warning("Timed out sending to WebSocket (slow/dead client)")
        except Exception as err:  # noqa: BLE001
            _LOGGER.warning("Failed to send to WebSocket: %s", err)

    async def send_error(
        self, ws: web.WebSocketResponse, code: str, message: str
    ) -> None:
        """Send a structured error message to *ws*."""
        await self.send(ws, {"type": "error", "code": code, "message": message})

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
