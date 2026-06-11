"""WebSocket handler for Quizify real-time communication."""

from __future__ import annotations

import asyncio
import logging
import math
import random
from typing import TYPE_CHECKING, Any

from aiohttp import WSMsgType, web

from custom_components.quizify.const import (
    ERR_ALREADY_SUBMITTED,
    ERR_GAME_ALREADY_STARTED,
    ERR_GAME_FULL,
    ERR_GAME_NOT_STARTED,
    ERR_INVALID_ACTION,
    ERR_NAME_INVALID,
    ERR_NAME_TAKEN,
    ERR_NOT_IN_GAME,
    ERR_ROUND_EXPIRED,
    LOBBY_DISCONNECT_GRACE_PERIOD,
)
from custom_components.quizify.game.highlights import compute_superlatives
from custom_components.quizify.game.phase_controller import TICK_INTERVAL
from custom_components.quizify.game.powerups import PowerUpEffect, PowerUpType
from custom_components.quizify.game.state import AnswerResult, GamePhase, QuizifyGameState
from custom_components.quizify.server.broadcast_dispatcher import BroadcastDispatcher
from custom_components.quizify.server.connection import ConnectionManager
from custom_components.quizify.server.rate_limit import SlidingWindowLimiter
from custom_components.quizify.server.round_message_builder import RoundMessageBuilder
from custom_components.quizify.server.serializers import (
    serialize_finale,
    serialize_leaderboard,
    serialize_player_list,
)

if TYPE_CHECKING:
    from collections.abc import Callable

    from ..runtime import Runtime

_LOGGER = logging.getLogger(__name__)


class QuizifyWebSocketHandler:
    """Handle WebSocket connections for Quizify."""

    HEARTBEAT_INTERVAL = 30

    # Admin-as-player redirect grace: when the admin clicks "Spiel starten"
    # from /quizify/admin, admin.js navigates the tab to /quizify/player so
    # the admin can answer questions. That navigation closes the admin's
    # player WS for ~1-2 seconds before a new player WS reconnects with the
    # session token. Without this grace period, _handle_disconnect would
    # immediately pause the game with reason "admin_disconnected" — every
    # other player's screen would flash "Lost connection to the host" for
    # the duration of the redirect. With the grace, the redirect completes
    # quietly and the game keeps running.
    #
    # 4 seconds covers: page load + i18n init + WS connect + reconnect
    # round-trip, with margin. If admin genuinely disconnects (closed tab,
    # lost wifi), the pause still fires after 4s — only the spurious
    # redirect-driven flash is suppressed.
    ADMIN_REDIRECT_GRACE = 4.0

    # Grace before round 1's timer starts after a game/restart. The admin-as-
    # player flow redirects the admin tab from /quizify/admin to /quizify/player
    # after start_game; that navigation + WS reconnect + i18n init costs
    # ~1.5-2.5s, during which the server timer is already ticking. Without this
    # buffer the admin lands on round 1 with ~25s left on a 30s timer. Applied
    # at both start_game (L3) and finale→new-game (L73) — identical reason.
    START_REDIRECT_GRACE = 2.5

    # Lightning round (#42/#201): the intro splash ("Bolt Burst") shows on the
    # host/TV + every phone before question 1. LIGHTNING_SPLASH_GRACE lets
    # clients swap from the splash to the question view before the first clock
    # starts ticking, so the countdown the players see matches the server window.
    LIGHTNING_SPLASH_GRACE = 1.0

    def __init__(
        self,
        runtime: Runtime,
        game_state_provider: "Callable[[], QuizifyGameState | None]",
    ) -> None:
        """Initialize handler."""
        self._runtime = runtime
        self._get_game_state = game_state_provider
        self._conn = ConnectionManager(runtime, game_state_provider)
        # Public alias for the connection manager so collaborators (e.g.
        # __init__.py setup/teardown) use a contract-bearing accessor instead
        # of reaching into the private ``_conn`` attribute. Backed by
        # ``_conn`` so test fixtures that assign ``handler._conn`` still work.
        self._timer_tick_task: asyncio.Task | None = None
        # Deferred-pause task scheduled by _handle_disconnect when the
        # admin-as-player WS closes mid-question. Cancelled by reconnect
        # or join when admin comes back within ADMIN_REDIRECT_GRACE.
        self._admin_pause_task: asyncio.Task | None = None
        # Drives the fast lightning-round loop (issue #42). Distinct from
        # the normal per-question tick task so the two modes can't fight.
        self._lightning_task: asyncio.Task | None = None
        # Optional TTS announcer. Set by __init__.py / dev_server after
        # construction so the handler doesn't have to know about HA
        # services. Calling announce_milestone on None is the no-op path.
        self._tts_announcer = None
        # Per-connection message flood guard (#169). Keyed on id(ws); the
        # entry is dropped by _forget_rate_limit() on disconnect so the
        # backing dict is bounded by the number of *live* connections.
        self._rate_limiter = SlidingWindowLimiter(
            max_requests=15,  # max messages per window
            window=1.0,  # seconds
            clock=lambda: asyncio.get_event_loop().time(),
        )
        # Routes named state events (round_evaluated / game_ended) to the
        # matching broadcast, falling back to a full-state push (#184).
        self._broadcast_dispatcher = BroadcastDispatcher(
            handlers={
                "round_evaluated": self._dispatch_round_evaluated,
                "game_ended": self._dispatch_game_ended,
            },
            default=self._dispatch_full_state,
        )
        # Assembles the per-round question + round-summary payloads (#189).
        # The handler keeps ownership of sending and shuffle mutation; the
        # builder produces the exact message dicts to hand to the connection
        # manager. Behaviour-preserving — identical wire shapes.
        self._round_messages = RoundMessageBuilder()

    @property
    def conn(self) -> ConnectionManager:
        """The connection manager owned by this handler (public accessor)."""
        return self._conn

    def _check_rate_limit(self, ws: web.WebSocketResponse) -> bool:
        """Record a message for ``ws`` and report whether it is within the
        per-connection rate limit.

        Delegates to the shared :class:`SlidingWindowLimiter`: old timestamps
        are pruned on every call so each connection's list stays small; the
        connection's entry is dropped by :meth:`_forget_rate_limit` from
        ``handle()``'s ``finally`` on disconnect, so the backing dict is
        bounded by the number of *live* connections (≤ MAX_PLAYERS +
        admin/dashboard), never unbounded (#169). A message that exceeds the
        limit is NOT recorded.
        """
        return self._rate_limiter.check(id(ws))

    def _forget_rate_limit(self, ws: web.WebSocketResponse) -> None:
        """Drop a connection's rate-limit state (called on disconnect)."""
        self._rate_limiter.forget(id(ws))

    async def handle(self, request: web.Request) -> web.WebSocketResponse:
        """Handle WebSocket connection."""
        ws = web.WebSocketResponse(heartbeat=self.HEARTBEAT_INTERVAL)
        await ws.prepare(request)

        role = request.query.get("role")
        is_dashboard = role == "dashboard"
        admin_token = request.query.get("token")

        # Admin role grant rules (#140 fix + #1 + #2 in logical review):
        # 1. Valid session token in ?token= \u2192 always grant (reconnect path).
        # 2. No token persisted to HA storage (fresh install / first-ever
        #    admin) \u2192 grant once as bootstrap. Thereafter the token is
        #    persisted via HA storage, survives restarts, and this branch
        #    never fires again on this HA instance (close the LAN
        #    takeover window that previously reopened on every restart).
        # 3. Token exists but no matching token provided \u2192 reject.
        #
        # Ensure the persisted token is loaded before evaluating rules.
        # async_load_admin_token() is idempotent and cheap after first call.
        await self._conn.async_load_admin_token()
        is_admin = False
        if role == "admin":
            if admin_token and self._conn.validate_admin_token(admin_token):
                is_admin = True
                _LOGGER.info("Admin reconnected with valid session token")
            elif await self._conn.try_bootstrap_admin():
                # Bootstrap: no token has ever been issued on this HA
                # instance. try_bootstrap_admin() grants + persists the token
                # atomically under a lock, so exactly one of two racing
                # first-connections wins (#168). The loser falls through to
                # the no-token branches below and gets player role only.
                is_admin = True
                _LOGGER.warning(
                    "ADMIN BOOTSTRAP: granting admin to first connection "
                    "(ip=%s). Future restarts will require the persisted "
                    "token. If this was NOT you, reset the integration.",
                    request.remote,
                )
            elif admin_token:
                # A token was presented but failed validation — this is the
                # interesting signal (real intrusion attempt or stale token).
                _LOGGER.warning(
                    "Admin connection attempt with INVALID token rejected (ip=%s)",
                    request.remote,
                )
            else:
                # No token presented and one is already on disk — the most
                # common cause is a fresh browser tab on the home LAN, not
                # an attack. Log at DEBUG so it doesn't drown the real
                # signal above. The connection still gets player role only.
                _LOGGER.debug(
                    "Admin connection attempt without token (ip=%s)",
                    request.remote,
                )

        self._conn.add_connection(ws, is_admin=is_admin, is_dashboard=is_dashboard)

        _LOGGER.debug(
            "WebSocket connected (admin=%s), total: %d",
            is_admin,
            len(self._conn.connections),
        )

        try:
            async for msg in ws:
                if msg.type == WSMsgType.TEXT:
                    # Rate limiting: record timestamp BEFORE processing so the
                    # first message is also counted (#21 in logical review).
                    if not self._check_rate_limit(ws):
                        _LOGGER.warning("Rate limit exceeded for WebSocket %s", id(ws))
                        await self._conn.send_error(
                            ws, ERR_INVALID_ACTION, "Rate limit exceeded"
                        )
                        continue
                    # Parse JSON separately so we can distinguish parse errors
                    # from handler errors (#20 in logical review).
                    try:
                        data = msg.json()
                    except ValueError:
                        await self._conn.send_error(
                            ws, ERR_INVALID_ACTION, "Malformed message (invalid JSON)"
                        )
                        continue
                    try:
                        await self._handle_message(ws, data, is_admin)
                    except Exception:  # noqa: BLE001
                        _LOGGER.exception("Failed to handle WebSocket message")
                        await self._conn.send_error(
                            ws, ERR_INVALID_ACTION, "Server error processing message"
                        )
                elif msg.type == WSMsgType.ERROR:
                    _LOGGER.error("WebSocket error: %s", ws.exception())
        finally:
            # #293: capture the admin-connection flag BEFORE remove_connection
            # discards this ws from _admin_connections. The disconnect handler
            # needs it to decide whether to start the admin-session grace
            # timeout; reading is_admin_connection(ws) AFTER removal always
            # returned False, so schedule_admin_timeout never fired (and
            # cancel_admin_disconnect was equally dead).
            was_admin = self._conn.is_admin_connection(ws)
            self._conn.remove_connection(ws)
            self._forget_rate_limit(ws)
            await self._handle_disconnect(ws, was_admin=was_admin)
            _LOGGER.debug("WebSocket disconnected, total: %d", len(self._conn.connections))

        return ws

    # ------------------------------------------------------------------
    # Message dispatch
    # ------------------------------------------------------------------

    async def _handle_message(
        self, ws: web.WebSocketResponse, data: dict, is_admin: bool
    ) -> None:
        """Route incoming WebSocket message."""
        msg_type = data.get("type")
        game_state = self._get_game_state()

        if not game_state:
            await self._conn.send_error(ws, ERR_GAME_NOT_STARTED, "No active game")
            return

        # ``admin_connect`` and ``reset_game`` use a different / special
        # authorization path than the boolean ``admin_required`` table below,
        # so they are handled out-of-band first.
        if msg_type == "admin_connect":
            # WS-level admin only (not the player-as-admin ``_is_authorized_admin``
            # relaxation) — an admin_connect must come from a real ?role=admin tab.
            if not is_admin:
                await self._conn.send_error(ws, ERR_INVALID_ACTION, "Admin only")
                return
            await self._handle_admin_connect(ws, game_state)
            return

        if msg_type == "reset_game":
            # Reset is the recovery escape-hatch (#207). Besides the normal
            # admin check, allow it whenever NO connected admin currently
            # holds the crown: in that orphaned-crown state (the legitimate
            # host lost its admin slot to the #209 name-disambiguation race)
            # the host has no other way back to a clean lobby. Reset is safe
            # and idempotent — it can only return the game to its initial
            # state, never escalate privilege — so this cannot be abused.
            if not self._is_reset_authorized(ws, is_admin, game_state):
                await self._conn.send_error(ws, ERR_INVALID_ACTION, "Admin only")
                return
            await self._handle_reset_game(ws, game_state)
            return

        handlers = self._message_dispatch(data, game_state)
        entry = handlers.get(msg_type)
        if entry is None:
            _LOGGER.warning("Unknown message type: %s", msg_type)
            await self._conn.send_error(ws, ERR_INVALID_ACTION, "Unknown message type")
            return

        handler, admin_required = entry
        if admin_required and not self._is_authorized_admin(ws, is_admin, game_state):
            # Centralized admin guard — same error code/message as the legacy
            # per-type checks. ``_is_authorized_admin`` accepts either WS-level
            # admin (admin tab via ?role=admin) OR a player whose session has
            # is_admin=True (admin-as-player flow). Without that relaxation the
            # admin-as-player flow could never advance LOBBY → QUESTION_ACTIVE.
            await self._conn.send_error(ws, ERR_INVALID_ACTION, "Admin only")
            return

        await handler(ws)

    def _message_dispatch(
        self, data: dict, game_state: QuizifyGameState
    ) -> dict[str, tuple[Callable[[web.WebSocketResponse], Any], bool]]:
        """Build the message-type → (handler, admin_required) dispatch table.

        Each handler is normalized to a single ``(ws)`` coroutine so the
        centralized guard in :meth:`_handle_message` can invoke them uniformly,
        regardless of whether the underlying handler also needs ``data``. The
        boolean is ``True`` iff the message type requires admin authorization.

        ``admin_connect`` and ``reset_game`` are NOT in this table — they use
        special authorization paths and are handled separately.
        """

        async def _get_state(ws: web.WebSocketResponse) -> None:
            state_msg = game_state.get_state_snapshot()
            # Project into the requesting PLAYER's frame (#286): the raw
            # snapshot carries canonical answer order, but ``submit_answer``
            # maps the tapped index through the player's OWN shuffle — sending
            # canonical order here mis-scores ~2/3 of taps after a mid-round
            # reconnect (the client auto-sends get_state on every join). Pure
            # admin/dashboard sockets (no player session) keep canonical order.
            player = game_state.get_player_by_ws(ws)
            if player is not None:
                state_msg = self._round_messages.project_snapshot_for_player(
                    game_state, snapshot=state_msg, player=player
                )
            state_msg["type"] = "game_state"
            await self._conn.send(ws, state_msg)

        return {
            # --- non-admin (player) message types ---
            "join": (lambda ws: self._handle_join(ws, data, game_state), False),
            "submit_answer": (
                lambda ws: self._handle_submit_answer(ws, data, game_state),
                False,
            ),
            "use_powerup": (
                lambda ws: self._handle_use_powerup(ws, data, game_state),
                False,
            ),
            "lightning_answer": (
                lambda ws: self._handle_lightning_answer(ws, data, game_state),
                False,
            ),
            "reconnect": (
                lambda ws: self._handle_reconnect(ws, data, game_state),
                False,
            ),
            "get_state": (_get_state, False),
            "reaction": (lambda ws: self._handle_reaction(ws, data, game_state), False),
            "submit_wager": (
                lambda ws: self._handle_submit_wager(ws, data, game_state),
                False,
            ),
            # --- admin-required message types ---
            "start_game": (
                lambda ws: self._handle_start_game(ws, data, game_state),
                True,
            ),
            "next_question": (
                lambda ws: self._handle_next_question(ws, game_state),
                True,
            ),
            "next_round": (
                lambda ws: self._handle_next_question(ws, game_state),
                True,
            ),
            "admin_skip": (
                lambda ws: self._handle_next_question(ws, game_state),
                True,
            ),
            "end_game": (lambda ws: self._handle_end_game(ws, game_state), True),
            "play_again": (lambda ws: self._handle_play_again(ws, game_state), True),
            "pause_game": (lambda ws: self._handle_pause_game(ws, game_state), True),
            "resume_game": (lambda ws: self._handle_resume_game(ws, game_state), True),
            "kick_player": (
                lambda ws: self._handle_kick_player(ws, data, game_state),
                True,
            ),
            "start_lightning": (
                lambda ws: self._handle_start_lightning(ws, data, game_state),
                True,
            ),
            "start_lightning_questions": (
                lambda ws: self._handle_start_lightning_questions(ws, game_state),
                True,
            ),
            "end_lightning": (
                lambda ws: self._handle_end_lightning(ws, game_state),
                True,
            ),
        }

    # ------------------------------------------------------------------
    # Admin connect
    # ------------------------------------------------------------------

    async def _handle_admin_connect(
        self, ws: web.WebSocketResponse, game_state: QuizifyGameState
    ) -> None:
        """Send full state to admin on connect."""
        # Cancel admin disconnect task if reconnecting
        if self._conn.has_pending_admin_disconnect():
            self._conn.cancel_admin_disconnect()
            _LOGGER.info("Admin reconnected, cancelled disconnect timeout")

        admin_token = self._conn.get_or_create_admin_token()

        state = game_state.get_state_snapshot()
        state["type"] = "game_state"
        state["join_url"] = "/quizify/player"
        state["admin_session_token"] = admin_token
        await self._conn.send(ws, state)

    # ------------------------------------------------------------------
    # Player join
    # ------------------------------------------------------------------

    async def _handle_join(
        self, ws: web.WebSocketResponse, data: dict, game_state: QuizifyGameState
    ) -> None:
        """Handle player join."""
        name = data.get("name", "").strip()

        if not name:
            await self._conn.send_error(ws, ERR_NAME_INVALID, "Name is required")
            return

        # Defense in depth against a second self-join (#244, cousin of #207).
        #
        # The admin "Join as Player" flow registers the host as a player on
        # the SAME admin WebSocket. If the host taps "Join as Player" again,
        # the client opens the modal afresh and (with a different typed name)
        # would otherwise create a duplicate/ghost player from the one admin
        # session. The client now hides the button after the first join, but
        # a crafted message must not slip past either: if THIS connection
        # already holds a connected player under a different name, reject the
        # duplicate join. A re-join under the SAME name is idempotent and
        # handled by the reconnect path in PlayerRegistry.add_player, so it is
        # explicitly allowed here (no-op rejoin / lobby refresh).
        existing = game_state.get_player_by_ws(ws)
        if (
            existing is not None
            and existing.connected
            and existing.name.lower() != name.lower()
        ):
            _LOGGER.warning(
                "Duplicate self-join rejected: connection already holds "
                "player %s, refusing second join as %s",
                existing.name,
                name,
            )
            await self._conn.send_error(
                ws,
                ERR_INVALID_ACTION,
                "Already joined as a player",
            )
            return

        # Auto-append number if name is taken
        original_name = name
        counter = 2
        while game_state.get_player(name) and game_state.get_player(name).connected:
            name = f"{original_name} {counter}"
            counter += 1

        success, error_code = game_state.add_player(name, ws)

        if success:
            # Cancel pending removal on reconnect
            self._conn.cancel_pending_removal(name)

            # Generate session token for reconnect
            session_token = self._conn.create_session_token(name)

            # Admin-as-player: trust `is_admin: true` in the join message.
            #
            # This is the Beatify pattern (which has shipped without bugs
            # for years). Earlier Quizify releases tried to cryptographically
            # validate an admin token threaded through the player's join
            # message; that pattern created a brittle state machine where
            # browser sessionStorage and server token storage could drift
            # apart with no in-product recovery (8 betas worth of bugs).
            #
            # Trust trade-off: a malicious client on the LAN could send
            # `is_admin: true` and become admin. Mitigations:
            #   - The user's home LAN is generally trusted.
            #   - Nabu Casa already requires HA auth to reach the
            #     integration through its tunnel.
            #   - "First admin claims it" still applies; only one admin
            #     slot exists per game.
            # The persisted admin token is still validated for the pure
            # admin-dashboard WebSocket connect (`?role=admin&token=...`),
            # which is the higher-stakes path. Player joins are simpler.
            # See DESIGN.md for the full rationale.
            player_obj = game_state.get_player(name)
            if player_obj and data.get("is_admin"):
                # Single-admin invariant (#208): exactly one player may hold
                # the crown per game. Only grant admin if no *other* player is
                # already admin. A re-claim by the same name (e.g. the admin's
                # redirect from /quizify/admin to /quizify/player re-joining
                # under the same name) is idempotent and still granted; a
                # claim by a *different* player while an admin exists is
                # rejected — the original admin keeps the crown. Rejecting
                # rather than taking over is the safer behaviour: it stops any
                # LAN client from seizing control mid-game by sending
                # `is_admin: true`.
                if game_state.has_other_admin(name):
                    _LOGGER.warning(
                        "Admin claim rejected for %s: a different player "
                        "already holds the single admin slot",
                        name,
                    )
                else:
                    # Crown-recovery (#207 regression of #209): if a *stale*
                    # (disconnected) admin slot still lingers under a different
                    # name — the host's old /admin slot during the
                    # /admin -> /player redirect — demote it before crowning the
                    # re-joining host. has_other_admin() no longer blocks on a
                    # disconnected admin, so without this demotion two players
                    # would briefly carry is_admin and break the #208 invariant.
                    stale_admin = game_state.get_admin()
                    if stale_admin is not None and stale_admin.name != name:
                        stale_admin.is_admin = False
                        _LOGGER.info(
                            "Crown transferred from stale admin %s to %s",
                            stale_admin.name,
                            name,
                        )
                    player_obj.is_admin = True

            # If a lightning round is mid-flight, register the late joiner so
            # they can score from the next question on (issue #42).
            if game_state.phase == GamePhase.LIGHTNING and game_state.lightning:
                game_state.lightning.add_player(name)

            # Cancel any deferred admin-disconnect pause: the admin's
            # redirect from /quizify/admin to /quizify/player took the
            # fresh-join path (no session token) instead of the
            # reconnect path. Same desired outcome — game keeps running.
            if player_obj and player_obj.is_admin:
                self._cancel_admin_pause()

            # Send join confirmation with session token and assigned color
            powerup = game_state.get_player_powerup(name)
            await self._conn.send(ws, {
                "type": "joined",
                "player_id": name,
                "powerup": powerup.value if powerup else None,
                "session_token": session_token,
                "color": player_obj.color if player_obj else "",
                "is_admin": player_obj.is_admin if player_obj else False,
            })

            # Send current state to the joining player. Project the
            # player-agnostic snapshot into THIS player's frame (#253): own
            # shuffle order for the answer buttons, own timer, flat reveal —
            # otherwise a mid-round joiner mis-scores their taps and sees an
            # empty reveal.
            state = game_state.get_state_snapshot()
            if player_obj is not None:
                state = self._round_messages.project_snapshot_for_player(
                    game_state, snapshot=state, player=player_obj
                )
            state["type"] = "game_state"
            await self._conn.send(ws, state)

            # Broadcast player list to everyone
            players = game_state.get_players()
            await self._conn.broadcast({
                "type": "player_joined",
                "players": serialize_player_list(players),
            })
        else:
            # English i18n-fallback strings only — the client localizes off
            # the structured ``code`` via ``t('errors.<CODE>')`` and only falls
            # back to this ``message`` if the key is missing (player-core.js).
            error_messages = {
                ERR_NAME_TAKEN: "Name already taken",
                ERR_NAME_INVALID: "Please enter a name",
                ERR_GAME_FULL: "Game is full",
            }
            await self._conn.send_error(
                ws, error_code or ERR_INVALID_ACTION,
                error_messages.get(error_code or "", "Failed to join"),
            )

    # ------------------------------------------------------------------
    # Player reconnect (session-based)
    # ------------------------------------------------------------------

    async def _handle_reconnect(
        self, ws: web.WebSocketResponse, data: dict, game_state: QuizifyGameState
    ) -> None:
        """Handle player reconnect with session token."""
        token = data.get("session_token", "")
        name = self._conn.get_player_for_token(token)

        if not name:
            # Token not found — treat as unknown, client should show join form
            await self._conn.send(ws, {"type": "reconnect_failed"})
            return

        player = game_state.get_player(name)
        if not player:
            # Player was fully removed — token stale
            self._conn.revoke_token(token)
            await self._conn.send(ws, {"type": "reconnect_failed"})
            return

        # Restore player connection
        player.ws = ws
        player.connected = True
        self._conn.cancel_pending_removal(name)

        # If this is the admin returning from the intentional
        # /quizify/admin → /quizify/player redirect after Start,
        # cancel the deferred pause scheduled by _handle_disconnect.
        # Without this, the pause would fire ~4s after the redirect
        # completes — the user would see the question briefly, then
        # the paused-view, defeating the whole grace-period fix.
        if player.is_admin:
            self._cancel_admin_pause()

        _LOGGER.info("Player session-reconnected: %s", name)

        # If we paused on admin disconnect and this is the admin coming
        # back, auto-resume the game so players don't sit on the paused
        # screen wondering. Resume only when WE caused the pause —
        # leave admin-initiated pauses alone.
        if (
            player.is_admin
            and game_state.phase == GamePhase.PAUSED
            and game_state.get_pause_reason() == "admin_disconnected"
        ):
            if game_state.resume():
                self._start_timer_tick(game_state)
                # Broadcast the resumed state to EVERY player (#287). Without
                # this, the other players stay frozen on the "Host disconnected"
                # paused-view (player-core.js only leaves it on a game_state
                # message) while their timers tick down → scored as timeouts.
                # Per-player PROJECTED — same mechanism as the manual-resume
                # fix (#286) so answer buttons keep the right shuffle order.
                await self._broadcast_state_projected(game_state)
                _LOGGER.info("Auto-resumed after admin reconnect")

        # Generate a fresh token and revoke old one
        new_token = self._conn.rotate_session_token(token, name)

        # Send reconnect success with new token
        powerup = game_state.get_player_powerup(name)
        await self._conn.send(ws, {
            "type": "reconnected",
            "player_id": name,
            "session_token": new_token,
            "powerup": powerup.value if powerup else None,
        })

        # Send full game state, projected into THIS player's frame (#253):
        # the reconnect snapshot otherwise carries canonical answer order
        # (mis-scoring taps) and a nested round_summary the reveal can't read.
        state = game_state.get_state_snapshot()
        state = self._round_messages.project_snapshot_for_player(
            game_state, snapshot=state, player=player
        )
        state["type"] = "game_state"
        await self._conn.send(ws, state)

        # Broadcast updated player list
        players = game_state.get_players()
        await self._conn.broadcast({
            "type": "player_joined",
            "players": serialize_player_list(players),
        })

    # ------------------------------------------------------------------
    # Submit answer
    # ------------------------------------------------------------------

    async def _handle_submit_answer(
        self, ws: web.WebSocketResponse, data: dict, game_state: QuizifyGameState
    ) -> None:
        """Handle answer submission from player."""
        player = game_state.get_player_by_ws(ws)
        if not player:
            await self._conn.send_error(ws, ERR_NOT_IN_GAME, "Not in game")
            return

        if game_state.phase != GamePhase.QUESTION_ACTIVE:
            # Silently ignore submit_answer when not in question phase
            return

        shuffled_index = data.get("answer_index")
        if shuffled_index is None or not isinstance(shuffled_index, int):
            await self._conn.send_error(ws, ERR_INVALID_ACTION, "Invalid answer index")
            return

        # Map shuffled index back to original index — use the player's
        # own shuffle (anti-cheat per-player), falling back to canonical.
        player_shuffle = game_state.get_player_shuffle(player.name)
        if 0 <= shuffled_index < len(player_shuffle):
            original_index = player_shuffle[shuffled_index]
        else:
            await self._conn.send_error(ws, ERR_INVALID_ACTION, "Answer index out of range")
            return

        result = game_state.submit_answer(player.name, original_index)

        if isinstance(result, AnswerResult):
            await self._conn.send(ws, {
                "type": "answer_result",
                "correct": result.correct,
                "points_earned": result.points_earned,
                "speed_bonus": result.speed_bonus,
                "streak_bonus": result.streak_bonus,
                "difficulty_multiplier": result.difficulty_multiplier,
                "new_streak": result.new_streak,
                "new_total": result.new_total,
                "milestone_bonus": result.milestone_bonus,
                "milestone_streak": result.milestone_streak,
            })
            # Broadcast a celebration event whenever a milestone hits so
            # the TV/admin view can flash and other players see the moment.
            if result.milestone_bonus:
                await self._conn.broadcast({
                    "type": "streak_milestone",
                    "player_name": player.name,
                    "streak": result.milestone_streak,
                    "bonus": result.milestone_bonus,
                })
                # Also speak it if TTS is configured. Cheap to look up; the
                # announcer no-ops if no TTS entity is set.
                self._notify_tts_milestone(player.name, result.milestone_streak)
            # NB: round-summary broadcast is fired exclusively by
            # state._fire_broadcast("round_evaluated") \u2192 broadcast_state().
            # Do NOT broadcast here \u2014 that would double-fire when the timer
            # path races with all-submitted (#3 in logical review).
        elif isinstance(result, str):
            # English i18n-fallback strings only (client localizes off ``code``).
            error_messages = {
                ERR_ALREADY_SUBMITTED: "Already answered",
                ERR_ROUND_EXPIRED: "Time is up",
                ERR_NOT_IN_GAME: "Not in the game",
                ERR_GAME_NOT_STARTED: "No active game",
            }
            await self._conn.send_error(
                ws, result, error_messages.get(result, result)
            )

    # ------------------------------------------------------------------
    # Power-ups
    # ------------------------------------------------------------------

    # ------------------------------------------------------------------
    # Reactions (gameplay idea #11)
    # ------------------------------------------------------------------

    # Max reaction-bonus points a single correct answerer can collect per
    # round. Without a cap, a 6-player room could pile 5 reactors × 1 pt =
    # +5, which dwarfs the base 10-point scoring. Cap at 3 so reactions
    # feel like a meaningful "tip your hat" without breaking the scoring.
    _REACTION_BONUS_CAP_PER_ROUND = 3

    async def _handle_reaction(
        self, ws: web.WebSocketResponse, data: dict, game_state: QuizifyGameState
    ) -> None:
        """Broadcast an emoji reaction. If the reaction comes in during
        ANSWER_REVEAL, also award a +1 bonus to each correct answerer
        from that round — cheap "audience appreciation" mechanic. The
        reactor themselves can only grant one such bonus per round
        (tracked on PlayerSession.reaction_bonuses_given), and each
        correct answerer caps at _REACTION_BONUS_CAP_PER_ROUND incoming
        bonuses so a 6-player room can't pile 5 free points on the
        leader every reveal."""
        reactor = game_state.get_player_by_ws(ws)
        if not reactor:
            return  # silent: reactions are best-effort, not a hard error

        emoji = data.get("emoji", "")
        if not isinstance(emoji, str) or not (1 <= len(emoji) <= 8):
            return  # ignore malformed

        # Broadcast the visual reaction unconditionally so floating
        # animations work in any phase.
        await self._conn.broadcast({
            "type": "reaction",
            "emoji": emoji,
            "player_name": reactor.name,
        })

        # Bonus path: only during reveal, only once per round per reactor.
        if game_state.phase != GamePhase.ANSWER_REVEAL:
            return
        summary = game_state.get_round_summary()
        if summary is None:
            return
        round_num = game_state.round
        if round_num in reactor.reaction_bonuses_given:
            return  # already granted a bonus this round
        reactor.reaction_bonuses_given.add(round_num)

        # Award +1 to each player who answered correctly this round,
        # respecting the per-round incoming cap.
        bonus_recipients: list[str] = []
        for result in summary.results:
            if not result.correct:
                continue
            recipient = game_state.get_player(result.player_id)
            if not recipient or recipient.name == reactor.name:
                continue  # can't tip your own hat
            # Per-round inbound counter (separate from `reaction_bonuses_given`
            # which tracks OUTGOING bonuses). A real PlayerSession field so it
            # is reset per game in reset_for_new_game() (#167).
            bonuses_in = recipient._reaction_bonuses_received
            if bonuses_in.get(round_num, 0) >= self._REACTION_BONUS_CAP_PER_ROUND:
                continue
            bonuses_in[round_num] = bonuses_in.get(round_num, 0) + 1
            recipient.score += 1
            recipient.round_score += 1
            bonus_recipients.append(recipient.name)

        if bonus_recipients:
            # Broadcast a leaderboard update so phones see the bonus tick.
            leaderboard = serialize_leaderboard(game_state.get_players())
            await self._conn.broadcast({
                "type": "reaction_bonus",
                "from_player": reactor.name,
                "to_players": bonus_recipients,
                "leaderboard": leaderboard,
            })

    # ------------------------------------------------------------------
    # Wager (gameplay idea #3 — Jeopardy-style final round)
    # ------------------------------------------------------------------

    async def _handle_submit_wager(
        self, ws: web.WebSocketResponse, data: dict, game_state: QuizifyGameState
    ) -> None:
        """Accept a player's wager for the final round. Only valid when
        we're on the last round and still in QUESTION_ACTIVE. The wager
        is a PERCENT (0-100) of the player's current score — server
        translates to absolute points at evaluation time so the
        percentage stays meaningful even after a late-arriving reaction
        bonus shifts scores."""
        player = game_state.get_player_by_ws(ws)
        if not player:
            await self._conn.send_error(ws, ERR_NOT_IN_GAME, "Not in game")
            return

        if game_state.phase != GamePhase.QUESTION_ACTIVE:
            return  # silent: wager window closed
        if game_state.round != game_state.total_rounds:
            return  # only final round accepts a wager

        # A wager only makes sense *before* the answer is locked in — the wager
        # stakes points on getting that answer right. Once the player has
        # submitted, accepting a new wager silently mutated the stake on an
        # already-locked answer (and the client never learned it was a no-op).
        # Reject explicitly so the player isn't misled. (#255.)
        if player.submitted:
            await self._conn.send_error(
                ws, ERR_INVALID_ACTION, "Wager locked after answering"
            )
            return

        wager = data.get("wager")
        try:
            wager_int = int(wager)
        except (TypeError, ValueError):
            await self._conn.send_error(ws, ERR_INVALID_ACTION, "Invalid wager")
            return
        if not 0 <= wager_int <= 100:
            await self._conn.send_error(ws, ERR_INVALID_ACTION, "Wager must be 0-100")
            return

        player.wager = wager_int
        await self._conn.send(ws, {
            "type": "wager_accepted",
            "wager": wager_int,
        })

    async def _handle_use_powerup(
        self, ws: web.WebSocketResponse, data: dict, game_state: QuizifyGameState
    ) -> None:
        """Handle power-up usage."""
        player = game_state.get_player_by_ws(ws)
        if not player:
            await self._conn.send_error(ws, ERR_NOT_IN_GAME, "Not in game")
            return

        target_id = data.get("target_player_id")
        result = game_state.use_powerup(player.name, target_id)

        if isinstance(result, PowerUpEffect):
            # Broadcast power-up application to all
            effect_data: dict[str, Any] = {
                "type": "powerup_applied",
                "powerup_type": result.type.value,
                "source_player": result.source_player,
                "target_player": result.target_player,
            }

            # For joker, send the removed answer to the using player only
            if result.type == PowerUpType.JOKER and result.joker_remove_index is not None:
                # Map the canonical original index to THIS player's shuffled
                # position. The buttons are rendered in the per-player shuffle
                # order, so mapping through the canonical shuffle_map would
                # disable the wrong button — potentially the CORRECT answer
                # (#254). get_player_shuffle falls back to the canonical map
                # when the player has no per-player shuffle.
                player_shuffle = game_state.get_player_shuffle(player.name)
                shuffled_remove_idx = None
                for shuffled_idx, orig_idx in enumerate(player_shuffle):
                    if orig_idx == result.joker_remove_index:
                        shuffled_remove_idx = shuffled_idx
                        break

                await self._conn.send(ws, {
                    "type": "powerup_applied",
                    "powerup_type": "joker",
                    "source_player": result.source_player,
                    "joker_remove_index": shuffled_remove_idx,
                })
                # Public broadcast w/o joker_remove_index — opponents get a
                # "Player X used Joker" toast (the removed-answer index is
                # per-shuffle so it stays in the private send above).
                await self._conn.broadcast({
                    "type": "powerup_applied",
                    "powerup_type": "joker",
                    "source_player": result.source_player,
                })
            elif result.type == PowerUpType.STEAL:
                effect_data["stolen_points"] = result.stolen_points
                await self._conn.broadcast(effect_data)
            else:
                await self._conn.broadcast(effect_data)
        elif isinstance(result, str):
            await self._conn.send_error(ws, result, "Power-up not available")

    # ------------------------------------------------------------------
    # Admin: start game
    # ------------------------------------------------------------------

    async def _handle_start_game(
        self, ws: web.WebSocketResponse, data: dict, game_state: QuizifyGameState
    ) -> None:
        """Handle admin start_game command.

        NB: there is no MIN_PLAYERS check here. The admin-as-player flow in
        admin.js sends `start_game` BEFORE the admin's player tab has joined
        (the admin tab redirects to /quizify/player after firing start_game,
        and the player join lands a moment later). A MIN_PLAYERS>=1 check
        would block that flow entirely — my beta.4 fix did exactly that
        and broke admin-as-player. The original review-era concern about
        \"phantom rounds with zero players\" is self-correcting because the
        admin's player tab joins within ~1s of the redirect; if it doesn't,
        the round just runs no-answer which evaluates harmlessly.
        """
        # An explicit admin "Spiel starten" is a fresh-start intent. If a
        # previous game is still lingering in a non-LOBBY phase (a solo game
        # the admin never formally reset, or a finished FINALE), the old phase
        # guard silently rejected this start and left the OLD game's
        # category/language running — the new settings never applied. Markus
        # 2026-05-31: picked Geographie/DE but kept seeing the previous English
        # mixed game. Reset to LOBBY first (keeps connected players) so the
        # fresh settings always take effect. Mirrors _handle_play_again.
        if game_state.phase != GamePhase.LOBBY:
            self._cancel_timer_tick()
            game_state.reset_to_lobby()

        raw_category = data.get("category")
        difficulty = data.get("difficulty")
        num_rounds = data.get("num_rounds", 10)
        language = data.get("language", "de")
        timer_duration = data.get("timer_duration")

        # Validate num_rounds the same way as timer_duration (#303): the WS
        # value reaches start_game raw, and total_rounds drives
        # ``self.round >= self.total_rounds``. A non-int makes that comparison
        # raise TypeError every start_next_question (game wedged); 0/negative
        # jumps straight to FINALE. Coerce to int + clamp to 1..50, fall back
        # to the default of 10 on anything unparseable.
        try:
            num_rounds = int(num_rounds)
        except (TypeError, ValueError):
            num_rounds = 10
        num_rounds = max(1, min(50, num_rounds))

        # category may be None (mixed), a string (single), or a list (multi)
        if isinstance(raw_category, list):
            category = None
            categories = raw_category if raw_category else None
        else:
            category = raw_category or None
            categories = None

        # Validate timer_duration: must be a positive int in a sane range
        # if provided, otherwise fall back to difficulty-based default.
        timer_value: int | None = None
        if timer_duration is not None:
            try:
                timer_value = int(timer_duration)
                if timer_value < 5 or timer_value > 300:
                    timer_value = None
            except (TypeError, ValueError):
                timer_value = None

        try:
            game_state.start_game(
                category=category,
                categories=categories,
                difficulty=difficulty,
                num_rounds=num_rounds,
                language=language,
                timer_duration=timer_value,
            )
        except ValueError as err:
            await self._conn.send_error(ws, ERR_GAME_ALREADY_STARTED, str(err))
            return

        # Grace period before round 1's timer starts.
        # The admin-as-player flow redirects the admin tab from /quizify/admin
        # to /quizify/player AFTER sending start_game. That navigation +
        # WebSocket reconnect + i18n init typically costs ~1.5-2.5s, during
        # which the timer is already ticking on the server. Without a buffer
        # the admin lands on round 1 with ~25s left on a 30s timer — much
        # harder to land a fast answer, and especially harsh for the player
        # who just spent time typing their name in the modal. Subsequent
        # rounds (Next Round button click) don't have this gap since the
        # admin is already on the player view.
        await asyncio.sleep(self.START_REDIRECT_GRACE)

        # Start the first question
        await self._start_next_question(game_state)

    # ------------------------------------------------------------------
    # Admin: next question
    # ------------------------------------------------------------------

    async def _handle_next_question(
        self, ws: web.WebSocketResponse, game_state: QuizifyGameState
    ) -> None:
        """Handle admin next_question command."""
        if game_state.phase not in (GamePhase.LOBBY, GamePhase.ANSWER_REVEAL):
            await self._conn.send_error(ws, ERR_INVALID_ACTION, "Cannot advance now")
            return

        await self._start_next_question(game_state)

    # ------------------------------------------------------------------
    # Admin: end game
    # ------------------------------------------------------------------

    async def _handle_end_game(
        self, ws: web.WebSocketResponse, game_state: QuizifyGameState
    ) -> None:
        """Handle admin end_game command."""
        self._cancel_timer_tick()
        # end_game() fires the ``game_ended`` state event, which the
        # BroadcastDispatcher routes to _broadcast_finale — that's the single
        # finale broadcast source. Calling _broadcast_finale here too would
        # double-broadcast the podium. (#255.) end_game() is idempotent, so a
        # repeated admin end-game is a harmless no-op.
        game_state.end_game()

    # ------------------------------------------------------------------
    # Admin: reset game
    # ------------------------------------------------------------------

    async def _handle_pause_game(
        self, ws: web.WebSocketResponse, game_state: QuizifyGameState
    ) -> None:
        """Pause the current question. No-op if not in QUESTION_ACTIVE."""
        if not game_state.pause(reason="admin_paused"):
            return  # Silent no-op — UI can call this anytime
        # Stop sending tick updates while paused.
        self._cancel_timer_tick()
        state = game_state.get_state_snapshot()
        state["type"] = "game_state"
        state["pause_reason"] = "admin_paused"
        await self._conn.broadcast(state)

    async def _handle_resume_game(
        self, ws: web.WebSocketResponse, game_state: QuizifyGameState
    ) -> None:
        """Resume from PAUSED → restart timer ticks and broadcast state."""
        if not game_state.resume():
            return
        # Fan out per-player PROJECTED snapshots (#286): the raw snapshot
        # carries canonical answer order, so a plain broadcast here mis-scored
        # ~2/3 of taps after resume because the players re-render their answer
        # buttons from it while submit_answer maps through their own shuffle.
        await self._broadcast_state_projected(game_state)
        # Restart the per-player tick loop.
        self._start_timer_tick(game_state)

    async def _handle_play_again(
        self, ws: web.WebSocketResponse, game_state: QuizifyGameState
    ) -> None:
        """Restart with the previous game's settings — one-tap rematch.

        Cheaper than full reset_game + admin re-enters everything: keeps
        the existing players, just resets scores and starts the next game
        with the cached settings. If we don't have a snapshot yet (server
        never saw a start_game on this instance), fall back to reset.
        """
        if not game_state.last_settings:
            await self._handle_reset_game(ws, game_state)
            return
        settings = game_state.last_settings
        self._cancel_timer_tick()
        # Reset to LOBBY first so start_game's phase guard passes; keeps
        # players (reset_to_lobby leaves connected players in place).
        game_state.reset_to_lobby()
        try:
            game_state.start_game(**settings)
        except ValueError as err:
            await self._conn.send_error(ws, ERR_GAME_ALREADY_STARTED, str(err))
            return
        # Same redirect grace as start_game — admin tab is still on the finale
        # view and needs to redirect/reconnect before round 1's timer ticks.
        await asyncio.sleep(self.START_REDIRECT_GRACE)
        await self._start_next_question(game_state)

    async def _handle_reset_game(
        self, ws: web.WebSocketResponse, game_state: QuizifyGameState
    ) -> None:
        """Handle admin reset_game command (truly fresh lobby).

        reset_to_lobby on its own keeps players in the registry (so the
        finale's "Play again — same settings" path can reuse them). The
        explicit reset_game button means "wipe everything" — so we ALSO:
          1. Close every player WebSocket. Stale/abandoned connections
             (test bots, dead phones) get pruned. Real clients see the
             close and reconnect into a fresh lobby on their own.
          2. Drop every player from the registry via clear_all_players()
             so phantom names like "sdfsd 2" don't survive the reset.
          3. Wipe per-player session tokens so the reconnect path can't
             resurrect a cleared player under their old slot.
        """
        self._cancel_timer_tick()
        self._cancel_lightning_loop()
        # Cancel any pending admin-disconnect timer and stale player-removal
        # tasks from the finished game.
        await self._conn.cleanup()
        # Wipe player session tokens to prevent cross-game reuse.
        self._conn.clear_all_player_tokens()

        # Snapshot the live player WSes BEFORE clearing the registry — we
        # need them both to broadcast the reset signal and to close them
        # afterwards. The host who pressed reset is frequently an
        # admin-as-player (joined the lobby on the admin WS), so their own
        # socket is in this list. That's exactly why ordering matters here
        # (issue #207): if we close the sockets first, the broadcasts below
        # never reach the host (or any real player) and their UI stays
        # frozen on the now-stale lobby. So: clear state → broadcast the
        # reset to everyone still connected → only THEN close the sockets.
        stale_wses = [p.ws for p in game_state.get_players() if p.ws is not None]

        # Drop every player from the registry (full wipe — not just score reset).
        game_state.clear_all_players()
        game_state.reset_to_lobby()

        # Tell every currently-connected client to reset its view to the
        # initial screen (admin → setup, players → join). This MUST run
        # while the sockets are still open.
        await self._conn.broadcast({"type": "game_reset"})

        state = game_state.get_state_snapshot()
        state["type"] = "game_state"
        await self._conn.broadcast(state)

        # Now close the (snapshotted) player sockets so abandoned/stale
        # connections actually die. Real clients have already received the
        # reset above and will reconnect into the fresh lobby on their own.
        for pws in stale_wses:
            try:
                await pws.close()
            except Exception:  # noqa: BLE001 — best-effort cleanup
                pass

    # ------------------------------------------------------------------
    # Admin: kick player
    # ------------------------------------------------------------------

    async def _handle_kick_player(
        self, ws: web.WebSocketResponse, data: dict, game_state: QuizifyGameState
    ) -> None:
        """Remove a player from the lobby. LOBBY-only — kicking mid-game
        would orphan their score and surprise everyone watching the TV.
        Admins can always end the game first if they want a hard reset.
        """
        if game_state.phase != GamePhase.LOBBY:
            await self._conn.send_error(
                ws, ERR_INVALID_ACTION, "Players can only be kicked from the lobby"
            )
            return

        target_name = (data.get("player_name") or data.get("name") or "").strip()
        if not target_name:
            await self._conn.send_error(ws, ERR_INVALID_ACTION, "Missing player_name")
            return

        target = game_state.get_player(target_name)
        if not target:
            await self._conn.send_error(ws, ERR_INVALID_ACTION, "Player not found")
            return

        if target.is_admin:
            await self._conn.send_error(ws, ERR_INVALID_ACTION, "Cannot kick the admin")
            return

        # Close the target's WS politely so their client gets the signal and
        # can show "you were removed" instead of looking offline. We don't
        # rely on the closed event reaching us — remove_player flushes state
        # immediately and the WS cleanup path is idempotent.
        target_ws = target.ws
        game_state.remove_player(target.name)
        self._conn.clear_player_tokens(target.name)

        if target_ws is not None and not target_ws.closed:
            try:
                await target_ws.send_json({"type": "kicked", "reason": "removed_by_admin"})
                await target_ws.close()
            except Exception as err:  # noqa: BLE001
                _LOGGER.debug("Closing kicked player WS raised: %s", err)

        _LOGGER.info("Admin kicked player: %s", target.name)

        await self._conn.broadcast({
            "type": "player_left",
            "players": serialize_player_list(game_state.get_players()),
        })

    # ------------------------------------------------------------------
    # Lightning Round (issue #42)
    # ------------------------------------------------------------------
    #
    # A self-contained fast mode: 5 questions, fixed 15s each, auto-advance
    # on timeout OR when all connected players have answered, NO reveal
    # between questions, flat points per correct, power-ups disabled. The
    # mode's rules live in game/lightning.py; here we just drive the loop
    # and fan out the question/recap payloads over the existing connection
    # layer.

    async def _handle_start_lightning(
        self, ws: web.WebSocketResponse, data: dict, game_state: QuizifyGameState
    ) -> None:
        """Admin triggers a lightning round (from LOBBY or FINALE)."""
        # Stop any normal-round timer first — the two loops are mutually
        # exclusive.
        self._cancel_timer_tick()

        raw_category = data.get("category")
        if isinstance(raw_category, list):
            category = None
            categories = raw_category or None
        else:
            category = raw_category or None
            categories = None
        difficulty = data.get("difficulty")
        language = data.get("language")

        started = game_state.start_lightning_round(
            category=category,
            categories=categories,
            difficulty=difficulty,
            language=language,
        )
        if not started:
            await self._conn.send_error(
                ws, ERR_INVALID_ACTION, "Cannot start lightning round"
            )
            return

        # Broadcast a phase-entry state so every client switches view.
        state = game_state.get_state_snapshot()
        state["type"] = "game_state"
        await self._conn.broadcast(state)

        # Show the intro splash ("Bolt Burst", issue #201) on host/TV + every
        # player phone. The question loop does NOT start yet — it waits for the
        # admin to tap Start (start_lightning_questions).
        await self._broadcast_lightning_splash(game_state)

    async def _handle_start_lightning_questions(
        self, ws: web.WebSocketResponse, game_state: QuizifyGameState
    ) -> None:
        """Admin dismisses the intro splash → run the first question (#201)."""
        if game_state.phase != GamePhase.LIGHTNING:
            return
        if not game_state.begin_lightning_questions():
            return  # splash already dismissed / nothing to do — stay silent
        self._start_lightning_loop(game_state)

    async def _broadcast_lightning_splash(
        self, game_state: QuizifyGameState
    ) -> None:
        """Fan out the intro-splash payload (rules preview) to all clients."""
        lr = game_state.lightning
        if lr is None:
            return
        await self._conn.broadcast({
            "type": "lightning_splash",
            "num_questions": lr.num_questions,
            "seconds_per_question": lr.seconds_per_question,
        })

    async def _handle_lightning_answer(
        self, ws: web.WebSocketResponse, data: dict, game_state: QuizifyGameState
    ) -> None:
        """Record a player's answer to the current lightning question."""
        if game_state.phase != GamePhase.LIGHTNING:
            return  # silent — window closed
        lr = game_state.lightning
        if lr is None:
            return
        player = game_state.get_player_by_ws(ws)
        if not player:
            await self._conn.send_error(ws, ERR_NOT_IN_GAME, "Not in game")
            return

        shuffled_index = data.get("answer_index")
        if not isinstance(shuffled_index, int):
            await self._conn.send_error(ws, ERR_INVALID_ACTION, "Invalid answer index")
            return

        result = lr.record_answer(player.name, shuffled_index)
        if result is None:
            return  # rejected (already answered / expired) — stay silent
        # Lightweight ack: lock the player's buttons + show right/wrong.
        await self._conn.send(ws, {
            "type": "lightning_answer_result",
            "correct": bool(result),
            "index": lr.index,
            "score": lr.scores.get(player.name, 0),
        })

    async def _handle_end_lightning(
        self, ws: web.WebSocketResponse, game_state: QuizifyGameState
    ) -> None:
        """Admin ends the lightning round early → jump to the recap."""
        self._cancel_lightning_loop()
        if game_state.phase == GamePhase.LIGHTNING:
            game_state.finish_lightning_round()
            await self._broadcast_lightning_recap(game_state)

    def _start_lightning_loop(self, game_state: QuizifyGameState) -> None:
        """Drive the fast lightning loop: broadcast question, wait for the
        fixed window or all-answered, advance with no reveal, repeat."""
        self._cancel_lightning_loop()

        async def loop() -> None:
            try:
                # Brief grace so clients swap from the intro splash (#201) to
                # the question view before the first clock starts ticking.
                await asyncio.sleep(self.LIGHTNING_SPLASH_GRACE)
                while game_state.phase == GamePhase.LIGHTNING:
                    lr = game_state.lightning
                    if lr is None:
                        break
                    await self._broadcast_lightning_question(game_state, lr)
                    # Re-arm the question clock now (after the broadcast) so the
                    # countdown the players see matches the server window.
                    lr.restart_clock()
                    # Wait out the fixed window, but cut short once every
                    # connected player has answered.
                    deadline = lr.seconds_per_question
                    waited = 0.0
                    step = 0.25
                    # The display shows whole seconds (1 Hz), so only push a
                    # tick when the ceil(remaining) actually changes — no point
                    # broadcasting at the 4 Hz poll cadence (#258).
                    last_shown = None
                    while waited < deadline:
                        shown = math.ceil(lr.time_remaining())
                        if shown != last_shown:
                            await self._broadcast_lightning_tick(game_state, lr)
                            last_shown = shown
                        connected = [
                            p.name for p in game_state.get_players() if p.connected
                        ]
                        if lr.all_connected_answered(connected):
                            break
                        await asyncio.sleep(step)
                        waited += step
                        if game_state.phase != GamePhase.LIGHTNING:
                            return
                    # No reveal — score silently and arm the next question.
                    has_more = lr.advance()
                    if not has_more:
                        game_state.finish_lightning_round()
                        await self._broadcast_lightning_recap(game_state)
                        return
            except asyncio.CancelledError:
                pass
            except Exception:  # noqa: BLE001
                # #307: an unexpected exception here used to kill the lightning
                # task silently, hanging the round on the current question with
                # a frozen clock. Log it so the failure surfaces.
                _LOGGER.exception("Lightning loop crashed")

        self._lightning_task = asyncio.ensure_future(loop())
        self._lightning_task.add_done_callback(self._log_task_exception)

    def _cancel_lightning_loop(self) -> None:
        if self._lightning_task is not None:
            self._lightning_task.cancel()
            self._lightning_task = None

    async def _broadcast_lightning_question(
        self, game_state: QuizifyGameState, lr: Any
    ) -> None:
        """Send the current lightning question per-player (own shuffle) and
        to admin/dashboard (canonical order)."""
        q = lr.current_question
        if q is None:
            return
        # Fan out the per-player lightning question in parallel (#258).
        lightning_sends = []
        for player in game_state.get_players():
            if not player.connected:
                continue
            lightning_sends.append(self._conn.send(player.ws, {
                "type": "lightning_question",
                "question_text": q.question,
                "answers": lr.shuffled_answers_for(player.name),
                "index": lr.index,
                "num_questions": lr.num_questions,
                "seconds": lr.seconds_per_question,
                "category": q.category,
                "image_url": q.image_url,
            }))
        if lightning_sends:
            await asyncio.gather(*lightning_sends)
        await self._conn.broadcast_to_admins_and_dashboards({
            "type": "lightning_question",
            "question_text": q.question,
            "answers": [a.text for a in q.answers],
            "index": lr.index,
            "num_questions": lr.num_questions,
            "seconds": lr.seconds_per_question,
            "category": q.category,
            "image_url": q.image_url,
        })

    async def _broadcast_lightning_tick(
        self, game_state: QuizifyGameState, lr: Any
    ) -> None:
        remaining = round(lr.time_remaining(), 1)
        await self._conn.broadcast({
            "type": "lightning_tick",
            "remaining": remaining,
            "index": lr.index,
        })

    async def _broadcast_lightning_recap(
        self, game_state: QuizifyGameState
    ) -> None:
        lr = game_state.lightning
        if lr is None:
            return
        await self._conn.broadcast({
            "type": "lightning_recap",
            "recap": lr.build_recap(),
        })

    async def _broadcast_state_projected(
        self,
        game_state: QuizifyGameState,
        *,
        extra: dict[str, Any] | None = None,
    ) -> None:
        """Fan out a ``game_state`` snapshot, projected per recipient.

        Each connected PLAYER gets the snapshot projected into their own frame
        (own shuffled answer order, own timer, flat reveal) via
        ``project_snapshot_for_player`` — the same mechanism the join/reconnect
        path uses (#253). Sending the raw canonical snapshot to players would
        mis-score ~2/3 of their taps because ``submit_answer`` maps the tapped
        index through the player's OWN shuffle (#286). Pure admin/dashboard
        sockets (no player session) get the canonical snapshot untouched.

        ``extra`` merges extra top-level keys (e.g. ``pause_reason``) into every
        message. Used by the resume path (#286 #287) so a resumed game reaches
        every screen with correctly-ordered answer buttons.
        """
        base = game_state.get_state_snapshot()

        # Per-player projected sends.
        player_ws: set[Any] = set()
        sends = []
        for player in game_state.get_players():
            if player.ws is None or not player.connected:
                continue
            player_ws.add(player.ws)
            msg = self._round_messages.project_snapshot_for_player(
                game_state, snapshot=base, player=player
            )
            msg["type"] = "game_state"
            if extra:
                msg.update(extra)
            sends.append(self._conn.send(player.ws, msg))

        # Raw snapshot for pure admin/dashboard sockets — but skip any socket
        # that is also a player (admin-as-player already got its projected
        # copy above, and must NOT see canonical order mid-question).
        raw = dict(base)
        raw["type"] = "game_state"
        if extra:
            raw.update(extra)
        for ws, _is_admin in self._conn.iter_admin_and_dashboard_ws():
            if ws.closed or ws in player_ws:
                continue
            sends.append(self._conn.send(ws, raw))

        if sends:
            await asyncio.gather(*sends)

    # ------------------------------------------------------------------
    # Question flow
    # ------------------------------------------------------------------

    async def _start_next_question(self, game_state: QuizifyGameState) -> None:
        """Start the next question: shuffle answers, broadcast, start timer ticks."""
        self._cancel_timer_tick()

        question = game_state.start_next_question()
        if question is None:
            # Game ended (no more questions or round limit reached).
            # start_next_question() already called end_game(), which fired the
            # ``game_ended`` event → dispatcher → _broadcast_finale (the single
            # finale broadcast source). No direct broadcast here. (#255.)
            return

        # Canonical shuffle — used by admin/dashboard and as a fallback.
        indices = list(range(len(question.answers)))
        random.shuffle(indices)
        game_state.set_round_shuffle(indices, [question.answers[i].text for i in indices])

        # Per-player shuffles: each phone sees A/B/C in its own order
        # (anti-cheat — couch neighbours can't shout "B!"). The
        # submit_answer path uses player.name to look this up.
        game_state.clear_player_shuffles()
        players_now = game_state.get_players()
        for player in players_now:
            player_indices = list(range(len(question.answers)))
            random.shuffle(player_indices)
            game_state.set_player_shuffle(player.name, player_indices)

        # Send question per-player so each gets their own shuffled order.
        # The builder assembles each payload (own shuffle); the handler still
        # owns the send and the connected-player skip (#189).
        is_final = game_state.round == game_state.total_rounds
        # Build every per-player payload, then fan out in parallel (#258) so a
        # single slow client can't delay the question reaching the rest.
        question_sends = []
        for player in players_now:
            if not player.connected:
                continue
            player_msg = self._round_messages.build_player_question(
                game_state, question=question, player=player, is_final=is_final
            )
            question_sends.append(self._conn.send(player.ws, player_msg))
        if question_sends:
            await asyncio.gather(*question_sends)

        # Send question with correct answer to admin
        admin_msg = self._round_messages.build_admin_question(
            game_state, question=question
        )
        # Also fan out to TV-dashboard connections so their question-view
        # populates with the same canonical-order payload. Without this the
        # dashboard's answer-grid stays empty and the v1.1.47 #151 answer-
        # distribution bars never attach. Admin-as-player still excluded.
        await self._conn.broadcast_to_admins_and_dashboards(admin_msg)

        # Cache players to avoid redundant calls
        players = game_state.get_players()

        # Notify players who got a power-up this round (parallel fan-out, #258).
        powerup_sends = []
        for player in players:
            powerup = game_state.get_player_powerup(player.name)
            if powerup and player.connected:
                powerup_sends.append(self._conn.send(player.ws, {
                    "type": "powerup_assigned",
                    "powerup_type": powerup.value,
                }))
        if powerup_sends:
            await asyncio.gather(*powerup_sends)

        # Broadcast game state with leaderboard so player sees rankings during game
        await self._conn.broadcast(
            self._round_messages.build_game_state_with_leaderboard(
                game_state, players=players
            )
        )

        # Start timer tick task
        self._start_timer_tick(game_state)

    # ------------------------------------------------------------------
    # Timer ticks
    # ------------------------------------------------------------------

    def _start_timer_tick(self, game_state: QuizifyGameState) -> None:
        """Start async task that broadcasts the countdown every tick.

        The *timing* — which players have a live timer, each one's
        authoritative remaining time (so time-boost / freeze power-ups are
        reflected, #4), the dashboard minimum and the all-expired stop
        condition — lives in the PhaseController (#203). This loop owns only
        the I/O: turning that timing into ``timer_tick`` wire messages for
        players, pure-admins and dashboards, the sleep cadence and the
        auto-evaluate when the round runs out.
        """
        self._cancel_timer_tick()

        async def tick_loop() -> None:
            try:
                while game_state.phase == GamePhase.QUESTION_ACTIVE:
                    players = game_state.get_players()
                    by_name = {p.name: p for p in players}
                    # Ask the timing unit for this tick's per-player remaining
                    # plus the shared dashboard minimum (one timer read each).
                    tick = game_state.resolve_tick([p.name for p in players])
                    # Build the full (ws, payload) fan-out, then deliver it in
                    # parallel via gather (#258). A single stalled client no
                    # longer delays the whole room — ConnectionManager.send
                    # swallows errors so a plain gather is safe.
                    sends = []
                    # Each connected player gets their authoritative remaining.
                    for name, remaining in tick.per_player:
                        p = by_name.get(name)
                        if p is None or not p.connected:
                            continue
                        sends.append(self._conn.send(p.ws, {
                            "type": "timer_tick",
                            "remaining": round(remaining, 1),
                        }))
                    # Broadcast the minimum remaining to dashboards/admins so
                    # the TV view shows a consistent countdown.
                    min_remaining = tick.dashboard_remaining
                    for ws, is_admin in self._conn.iter_admin_and_dashboard_ws():
                        if ws.closed:
                            continue
                        # An admin who is also a player already got their
                        # per-player tick above — don't double-send.
                        if is_admin and any(p.ws is ws for p in players):
                            continue
                        sends.append(self._conn.send(ws, {
                            "type": "timer_tick",
                            "remaining": round(min_remaining, 1),
                        }))
                    if sends:
                        await asyncio.gather(*sends)
                    await asyncio.sleep(TICK_INTERVAL)

                    # Stop if everyone's timer hit zero and phase is still
                    # active. Re-read fresh timers (state can change during the
                    # sleep) for the CONNECTED players only; the all-expired
                    # decision itself lives in the PhaseController, which ignores
                    # players without a timer so a late-joining connected player
                    # (e.g. the admin's own /quizify/player tab) can't end the
                    # round before their per-player timer exists.
                    connected = [p.name for p in players if p.connected]
                    if connected and game_state.all_timers_expired(connected):
                        break
                    # Fallback: when every player has disconnected mid-question
                    # there are no live timers left for all_timers_expired to
                    # break on, so the loop would spin forever and the admin
                    # couldn't advance. Break once the round wall-clock has run
                    # out, so the round still auto-evaluates with zero connected
                    # players. (#255.)
                    if not connected and game_state.round_wall_clock_expired():
                        break

                # Timer expired globally
                if game_state.phase == GamePhase.QUESTION_ACTIVE:
                    # Auto-evaluate round. The state machine's
                    # _fire_broadcast("round_evaluated") handles the summary
                    # broadcast \u2014 do NOT broadcast here (#3 in review).
                    game_state.evaluate_round()
            except asyncio.CancelledError:
                pass
            except Exception:  # noqa: BLE001
                # #307: any other exception used to kill the tick task
                # silently, leaving the game frozen in QUESTION_ACTIVE with a
                # stuck countdown. Log it loudly so the failure is diagnosable
                # instead of presenting as a mysterious hang.
                _LOGGER.exception("Timer tick loop crashed")

        self._timer_tick_task = asyncio.ensure_future(tick_loop())
        self._timer_tick_task.add_done_callback(self._log_task_exception)

    def _cancel_timer_tick(self) -> None:
        """Cancel the timer tick task."""
        if self._timer_tick_task is not None:
            self._timer_tick_task.cancel()
            self._timer_tick_task = None

    @staticmethod
    def _log_task_exception(task: asyncio.Task) -> None:
        """Done-callback that surfaces a fire-and-forget task's exception (#307).

        ensure_future/create_task tasks whose exception is never retrieved
        otherwise raise "Task exception was never retrieved" at GC time (or get
        swallowed entirely). Reuses the analytics.py logging-done-callback
        pattern so a crashed background loop is at least loud in the log.
        """
        if task.cancelled():
            return
        if (exc := task.exception()) is not None:
            _LOGGER.error("Unhandled exception in background task: %s", exc)

    # ------------------------------------------------------------------
    # Round summary broadcast
    # ------------------------------------------------------------------

    async def _broadcast_round_summary(self, game_state: QuizifyGameState) -> None:
        """Broadcast round summary to all clients.

        Payload assembly (correct-index resolution, the per-player answer
        table, the summary serialization) lives in the RoundMessageBuilder
        (#189); the handler keeps ownership of the broadcast. ``None`` means
        there is no round summary yet — same no-op as before.
        """
        summary_msg = self._round_messages.build_round_summary(game_state)
        if summary_msg is None:
            return
        await self._conn.broadcast(summary_msg)

    # ------------------------------------------------------------------
    # Disconnect handling
    # ------------------------------------------------------------------

    async def _handle_disconnect(
        self, ws: web.WebSocketResponse, was_admin: bool | None = None
    ) -> None:
        """Handle WebSocket disconnection.

        ``was_admin`` is the admin-connection flag captured by ``handle()``
        BEFORE ``remove_connection`` (#293). When omitted (legacy/test callers),
        fall back to querying the connection manager — only correct if the ws
        hasn't been removed yet.
        """
        game_state = self._get_game_state()
        if not game_state:
            return

        # Handle admin disconnect — keep game alive for grace period.
        # Strictly gated on real admin connection (was: OR clause allowed
        # dashboard disconnects to trigger the timeout, see #2 in review).
        is_admin_ws = (
            was_admin
            if was_admin is not None
            else self._conn.is_admin_connection(ws)
        )
        if is_admin_ws:
            if not self._conn.has_admin_connections():
                _LOGGER.info(
                    "Admin disconnected, keeping game alive for %ds",
                    self._conn.ADMIN_SESSION_GRACE,
                )
                self._conn.schedule_admin_timeout()

        player = game_state.get_player_by_ws(ws)
        if not player:
            return

        player.connected = False
        _LOGGER.info("Player disconnected: %s", player.name)

        # Host-disconnect graceful recovery: if the admin-as-player tab
        # drops mid-question, pause the game instead of letting the timer
        # run out while everyone wonders what happened. The admin's
        # PLAYER_SESSION_GRACE (60s by default) gives them time to reload
        # or switch networks; if they don't come back, the existing
        # remove-after-timeout below cleans up and the game can be
        # resumed by another path.
        #
        # BUT: the most common admin disconnect is the intentional
        # admin.html → player.html redirect that fires when admin clicks
        # "Spiel starten" (see admin.js::redirectToPlayer). The new
        # player WS reconnects via session token within 1-2s. Pausing
        # immediately would flash "Lost connection to the host" on every
        # other player's screen for the duration of the redirect — what
        # users perceive as the game "not reliably starting". Defer the
        # pause by ADMIN_REDIRECT_GRACE seconds; if admin reconnects in
        # time the pause never fires.
        if (
            player.is_admin
            and game_state.phase == GamePhase.QUESTION_ACTIVE
        ):
            self._schedule_admin_pause(player.name)

        # Broadcast updated player list
        players = game_state.get_players()
        await self._conn.broadcast({
            "type": "player_left",
            "players": serialize_player_list(players),
        })

        # Schedule removal after grace period
        grace = (
            LOBBY_DISCONNECT_GRACE_PERIOD
            if game_state.phase == GamePhase.LOBBY
            else self._conn.PLAYER_SESSION_GRACE
        )

        async def remove_after_timeout(name: str, timeout: float) -> None:
            await asyncio.sleep(timeout)
            gs = self._get_game_state()
            if gs:
                p = gs.get_player(name)
                if p and not p.connected:
                    gs.remove_player(name)
                    # Clean up session tokens for this player
                    self._conn.clear_player_tokens(name)
                    remaining = gs.get_players()
                    await self._conn.broadcast({
                        "type": "player_left",
                        "players": serialize_player_list(remaining),
                    })
                    _LOGGER.info("Removed disconnected player after grace period: %s", name)

        self._conn.schedule_player_removal(player.name, grace, remove_after_timeout)

    # ------------------------------------------------------------------
    # Admin-redirect pause: defer the QUESTION_ACTIVE pause that would
    # otherwise fire instantly when admin's WS closes (typically the
    # intentional /quizify/admin → /quizify/player redirect after Start).
    # ------------------------------------------------------------------

    def _schedule_admin_pause(self, admin_name: str) -> None:
        """Schedule a pause to fire ADMIN_REDIRECT_GRACE seconds from now.

        If admin reconnects (via session token or fresh join) before the
        timer expires, _cancel_admin_pause clears the task. Re-entrant:
        cancels any prior pending task before scheduling a new one, so a
        rapid disconnect-reconnect-disconnect doesn't stack tasks.
        """
        self._cancel_admin_pause()

        async def pause_after_grace() -> None:
            try:
                await asyncio.sleep(self.ADMIN_REDIRECT_GRACE)
                gs = self._get_game_state()
                if gs is None:
                    return
                # Final sanity checks before pausing — admin may have
                # reconnected via a path that doesn't cancel us, or the
                # game may have advanced past QUESTION_ACTIVE on its own.
                player = gs.get_player(admin_name)
                if player and player.connected:
                    _LOGGER.debug(
                        "Admin %s reconnected within grace — skipping pause",
                        admin_name,
                    )
                    return
                if gs.phase != GamePhase.QUESTION_ACTIVE:
                    return
                if not gs.pause(reason="admin_disconnected"):
                    return
                self._cancel_timer_tick()
                state = gs.get_state_snapshot()
                state["type"] = "game_state"
                state["pause_reason"] = "admin_disconnected"
                await self._conn.broadcast(state)
                _LOGGER.info(
                    "Paused game after admin %s failed to reconnect within %.1fs",
                    admin_name,
                    self.ADMIN_REDIRECT_GRACE,
                )
            except asyncio.CancelledError:
                # Normal path: admin reconnected in time. Don't log.
                pass

        self._admin_pause_task = asyncio.ensure_future(pause_after_grace())

    def _cancel_admin_pause(self) -> None:
        """Cancel any pending deferred admin-disconnect pause."""
        if self._admin_pause_task and not self._admin_pause_task.done():
            self._admin_pause_task.cancel()
        self._admin_pause_task = None

    # ------------------------------------------------------------------
    # Admin auth helper
    # ------------------------------------------------------------------

    def _is_authorized_admin(
        self, ws: web.WebSocketResponse, is_admin: bool, game_state: QuizifyGameState
    ) -> bool:
        """Return True if the connection is authorized to perform admin actions."""
        player = game_state.get_player_by_ws(ws)
        return is_admin or bool(player and player.is_admin)

    def _is_reset_authorized(
        self, ws: web.WebSocketResponse, is_admin: bool, game_state: QuizifyGameState
    ) -> bool:
        """Authorize a reset_game request.

        Stricter-than-nothing escape hatch (#207): a normal authorized admin
        may always reset; additionally, ANY connected client may reset when
        no *connected* admin currently holds the crown. The latter recovers
        the legitimate host from the orphaned-crown state left by the #209
        single-admin name race, where otherwise every admin-only action is
        silently rejected. Reset only ever returns the game to its initial
        state, so granting it in the admin-less case cannot escalate control.
        """
        if self._is_authorized_admin(ws, is_admin, game_state):
            return True
        admin = game_state.get_admin()
        return admin is None or not admin.connected

    def _notify_tts_milestone(self, player_name: str, streak: int) -> None:
        """Forward a milestone hit to the TTS announcer if one is wired.

        Kept as a no-op when ``_tts_announcer`` is None (standalone dev
        server, HA setup without TTS configured) so the handler doesn't
        have to thread an Optional everywhere.
        """
        announcer = self._tts_announcer
        if announcer is None:
            return
        try:
            announcer.announce_milestone(player_name, streak)
        except Exception:  # noqa: BLE001
            _LOGGER.exception("TTS milestone announcement raised")

    # ------------------------------------------------------------------
    # Finale broadcast helper
    # ------------------------------------------------------------------

    async def _broadcast_finale(self, game_state: QuizifyGameState) -> None:
        """Build and broadcast the finale message (podium + superlatives)."""
        from custom_components.quizify.game.scoring import calculate_podium  # noqa: PLC0415

        podium = calculate_podium(game_state.get_players())
        all_players = game_state.get_players()
        awards = [s.to_dict() for s in compute_superlatives(all_players)]
        finale_msg = serialize_finale(podium, all_players, superlatives=awards)
        await self._conn.broadcast(finale_msg)

    # ------------------------------------------------------------------
    # Broadcast callback for game state
    # ------------------------------------------------------------------

    async def broadcast_state(self, payload: dict[str, Any] | None = None) -> None:
        """Broadcast callback — called by game state on auto-events.

        Routing lives in the BroadcastDispatcher (#184); the per-event message
        building stays here. Behaviour is unchanged: each handler re-fetches the
        current game state and no-ops if there isn't one.
        """
        await self._broadcast_dispatcher.dispatch(payload)

    async def _dispatch_round_evaluated(self) -> None:
        """Handler for the ``round_evaluated`` state event."""
        game_state = self._get_game_state()
        if game_state:
            await self._broadcast_round_summary(game_state)

    async def _dispatch_game_ended(self) -> None:
        """Handler for the ``game_ended`` state event."""
        game_state = self._get_game_state()
        if game_state:
            await self._broadcast_finale(game_state)

    async def _dispatch_full_state(self) -> None:
        """Default handler: broadcast a full game-state snapshot."""
        game_state = self._get_game_state()
        if game_state:
            state = game_state.get_state_snapshot()
            state["type"] = "game_state"
            await self._conn.broadcast(state)

    async def cleanup_game_tasks(self) -> None:
        """Cancel all pending tasks."""
        self._cancel_timer_tick()
        self._cancel_lightning_loop()
        await self._conn.cleanup()
        _LOGGER.debug("Cleaned up all pending game tasks")
