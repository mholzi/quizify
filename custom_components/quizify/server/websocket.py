"""WebSocket handler for Quizify real-time communication."""

from __future__ import annotations

import asyncio
import logging
import random
import uuid
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
from custom_components.quizify.game.powerups import PowerUpEffect, PowerUpType
from custom_components.quizify.game.state import AnswerResult, GamePhase, QuizifyGameState
from custom_components.quizify.server.connection import ConnectionManager
from custom_components.quizify.server.serializers import (
    serialize_finale,
    serialize_leaderboard,
    serialize_player_list,
    serialize_question_for_admin,
    serialize_question_for_player,
    serialize_round_summary,
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

    def __init__(
        self,
        runtime: Runtime,
        game_state_provider: "Callable[[], QuizifyGameState | None]",
    ) -> None:
        """Initialize handler."""
        self._runtime = runtime
        self._get_game_state = game_state_provider
        self._conn = ConnectionManager(runtime, game_state_provider)
        self._timer_tick_task: asyncio.Task | None = None
        # Deferred-pause task scheduled by _handle_disconnect when the
        # admin-as-player WS closes mid-question. Cancelled by reconnect
        # or join when admin comes back within ADMIN_REDIRECT_GRACE.
        self._admin_pause_task: asyncio.Task | None = None
        # Optional TTS announcer. Set by __init__.py / dev_server after
        # construction so the handler doesn't have to know about HA
        # services. Calling announce_milestone on None is the no-op path.
        self._tts_announcer = None
        # Rate limiting
        self._message_timestamps: dict[int, list[float]] = {}  # ws id -> recent message timestamps
        self._RATE_LIMIT_WINDOW = 1.0  # seconds
        self._RATE_LIMIT_MAX = 15  # max messages per window

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
            existing_token = self._conn._admin_session_token
            if admin_token and self._conn.validate_admin_token(admin_token):
                is_admin = True
                _LOGGER.info("Admin reconnected with valid session token")
            elif not existing_token:
                # Bootstrap: no token has ever been issued on this HA
                # instance. Grant once and persist so this branch is closed
                # permanently (until admin is explicitly reset).
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
                    ws_id = id(ws)
                    now = asyncio.get_event_loop().time()
                    timestamps = self._message_timestamps.setdefault(ws_id, [])
                    # Remove old timestamps outside window
                    self._message_timestamps[ws_id] = [t for t in timestamps if now - t < self._RATE_LIMIT_WINDOW]
                    if len(self._message_timestamps[ws_id]) >= self._RATE_LIMIT_MAX:
                        _LOGGER.warning("Rate limit exceeded for WebSocket %s", ws_id)
                        await self._conn.send_error(
                            ws, ERR_INVALID_ACTION, "Rate limit exceeded"
                        )
                        continue
                    self._message_timestamps[ws_id].append(now)
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
                    except Exception as err:  # noqa: BLE001
                        _LOGGER.warning("Failed to handle WebSocket message: %s", err)
                        await self._conn.send_error(
                            ws, ERR_INVALID_ACTION, "Server error processing message"
                        )
                elif msg.type == WSMsgType.ERROR:
                    _LOGGER.error("WebSocket error: %s", ws.exception())
        finally:
            self._conn.remove_connection(ws)
            self._message_timestamps.pop(id(ws), None)
            await self._handle_disconnect(ws)
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

        if msg_type == "admin_connect":
            if not is_admin:
                await self._conn.send_error(ws, ERR_INVALID_ACTION, "Admin only")
                return
            await self._handle_admin_connect(ws, game_state)

        elif msg_type == "join":
            await self._handle_join(ws, data, game_state)

        elif msg_type == "submit_answer":
            await self._handle_submit_answer(ws, data, game_state)

        elif msg_type == "use_powerup":
            await self._handle_use_powerup(ws, data, game_state)

        elif msg_type == "start_game":
            # Accept either WS-level admin (admin tab via ?role=admin) OR
            # player-as-admin (a player whose session has is_admin=True from
            # the join message). Without this, the admin-as-player flow can
            # never advance LOBBY → QUESTION_ACTIVE because the player WS
            # isn't tagged admin at the connect level.
            if not self._is_authorized_admin(ws, is_admin, game_state):
                await self._conn.send_error(ws, ERR_INVALID_ACTION, "Admin only")
                return
            await self._handle_start_game(ws, data, game_state)

        elif msg_type in ("next_question", "next_round"):
            if not self._is_authorized_admin(ws, is_admin, game_state):
                await self._conn.send_error(ws, ERR_INVALID_ACTION, "Admin only")
                return
            await self._handle_next_question(ws, game_state)

        elif msg_type == "end_game":
            if not self._is_authorized_admin(ws, is_admin, game_state):
                await self._conn.send_error(ws, ERR_INVALID_ACTION, "Admin only")
                return
            await self._handle_end_game(ws, game_state)

        elif msg_type == "play_again":
            if not self._is_authorized_admin(ws, is_admin, game_state):
                await self._conn.send_error(ws, ERR_INVALID_ACTION, "Admin only")
                return
            await self._handle_play_again(ws, game_state)

        elif msg_type == "pause_game":
            if not self._is_authorized_admin(ws, is_admin, game_state):
                await self._conn.send_error(ws, ERR_INVALID_ACTION, "Admin only")
                return
            await self._handle_pause_game(ws, game_state)

        elif msg_type == "resume_game":
            if not self._is_authorized_admin(ws, is_admin, game_state):
                await self._conn.send_error(ws, ERR_INVALID_ACTION, "Admin only")
                return
            await self._handle_resume_game(ws, game_state)

        elif msg_type == "reset_game":
            if not self._is_authorized_admin(ws, is_admin, game_state):
                await self._conn.send_error(ws, ERR_INVALID_ACTION, "Admin only")
                return
            await self._handle_reset_game(ws, game_state)

        elif msg_type == "admin_skip":
            if not self._is_authorized_admin(ws, is_admin, game_state):
                await self._conn.send_error(ws, ERR_INVALID_ACTION, "Admin only")
                return
            await self._handle_next_question(ws, game_state)

        elif msg_type == "kick_player":
            if not self._is_authorized_admin(ws, is_admin, game_state):
                await self._conn.send_error(ws, ERR_INVALID_ACTION, "Admin only")
                return
            await self._handle_kick_player(ws, data, game_state)

        elif msg_type == "reconnect":
            await self._handle_reconnect(ws, data, game_state)

        elif msg_type == "get_state":
            state_msg = game_state.get_state_snapshot()
            state_msg["type"] = "game_state"
            await self._conn._safe_send(ws, state_msg)

        elif msg_type == "reaction":
            await self._handle_reaction(ws, data, game_state)

        elif msg_type == "submit_wager":
            await self._handle_submit_wager(ws, data, game_state)

        else:
            _LOGGER.warning("Unknown message type: %s", msg_type)
            await self._conn.send_error(ws, ERR_INVALID_ACTION, "Unknown message type")

    # ------------------------------------------------------------------
    # Admin connect
    # ------------------------------------------------------------------

    async def _handle_admin_connect(
        self, ws: web.WebSocketResponse, game_state: QuizifyGameState
    ) -> None:
        """Send full state to admin on connect."""
        # Cancel admin disconnect task if reconnecting
        if self._conn._admin_disconnect_task and not self._conn._admin_disconnect_task.done():
            self._conn.cancel_admin_disconnect()
            _LOGGER.info("Admin reconnected, cancelled disconnect timeout")

        admin_token = self._conn.get_or_create_admin_token()

        state = game_state.get_state_snapshot()
        state["type"] = "game_state"
        state["join_url"] = "/quizify/player"
        state["admin_session_token"] = admin_token
        await self._conn._safe_send(ws, state)

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
                player_obj.is_admin = True

            # Cancel any deferred admin-disconnect pause: the admin's
            # redirect from /quizify/admin to /quizify/player took the
            # fresh-join path (no session token) instead of the
            # reconnect path. Same desired outcome — game keeps running.
            if player_obj and player_obj.is_admin:
                self._cancel_admin_pause()

            # Send join confirmation with session token and assigned color
            powerup = game_state.get_player_powerup(name)
            await self._conn._safe_send(ws, {
                "type": "joined",
                "player_id": name,
                "powerup": powerup.value if powerup else None,
                "session_token": session_token,
                "color": player_obj.color if player_obj else "",
                "is_admin": player_obj.is_admin if player_obj else False,
            })

            # Send current state to the joining player
            state = game_state.get_state_snapshot()
            state["type"] = "game_state"
            await self._conn._safe_send(ws, state)

            # Broadcast player list to everyone
            players = game_state.get_players()
            await self._conn.broadcast({
                "type": "player_joined",
                "players": serialize_player_list(players),
            })
        else:
            error_messages = {
                ERR_NAME_TAKEN: "Name bereits vergeben",
                ERR_NAME_INVALID: "Bitte gib einen Namen ein",
                ERR_GAME_FULL: "Spiel ist voll",
            }
            await self._conn.send_error(
                ws, error_code or ERR_INVALID_ACTION,
                error_messages.get(error_code or "", "Beitritt fehlgeschlagen"),
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
            await self._conn._safe_send(ws, {"type": "reconnect_failed"})
            return

        player = game_state.get_player(name)
        if not player:
            # Player was fully removed — token stale
            self._conn._session_tokens.pop(token, None)
            await self._conn._safe_send(ws, {"type": "reconnect_failed"})
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
                _LOGGER.info("Auto-resumed after admin reconnect")

        # Generate a fresh token and revoke old one
        new_token = self._conn.rotate_session_token(token, name)

        # Send reconnect success with new token
        powerup = game_state.get_player_powerup(name)
        await self._conn._safe_send(ws, {
            "type": "reconnected",
            "player_id": name,
            "session_token": new_token,
            "powerup": powerup.value if powerup else None,
        })

        # Send full game state
        state = game_state.get_state_snapshot()
        state["type"] = "game_state"
        await self._conn._safe_send(ws, state)

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
            await self._conn._safe_send(ws, {
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
            error_messages = {
                ERR_ALREADY_SUBMITTED: "Bereits geantwortet",
                ERR_ROUND_EXPIRED: "Zeit abgelaufen",
                ERR_NOT_IN_GAME: "Nicht im Spiel",
                ERR_GAME_NOT_STARTED: "Kein aktives Spiel",
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
            # Lazy bookkeeping: stash the per-round inbound counter on
            # the player object (separate from `reaction_bonuses_given`
            # which tracks OUTGOING bonuses). Using a dict so the data
            # doesn't have to migrate to the dataclass schema.
            bonuses_in = getattr(recipient, "_reaction_bonuses_received", None) or {}
            if bonuses_in.get(round_num, 0) >= self._REACTION_BONUS_CAP_PER_ROUND:
                continue
            bonuses_in[round_num] = bonuses_in.get(round_num, 0) + 1
            recipient._reaction_bonuses_received = bonuses_in  # type: ignore[attr-defined]
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
        await self._conn._safe_send(ws, {
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
                # Map original index to shuffled index for the player
                shuffled_remove_idx = None
                for shuffled_idx, orig_idx in enumerate(game_state.shuffle_map):
                    if orig_idx == result.joker_remove_index:
                        shuffled_remove_idx = shuffled_idx
                        break

                await self._conn._safe_send(ws, {
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
            await self._conn.send_error(ws, result, "Power-up nicht verfügbar")

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
        await asyncio.sleep(2.5)

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
        game_state.end_game()
        await self._broadcast_finale(game_state)

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
        state = game_state.get_state_snapshot()
        state["type"] = "game_state"
        await self._conn.broadcast(state)
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
        if not getattr(game_state, "_last_settings", None):
            await self._handle_reset_game(ws, game_state)
            return
        settings = game_state._last_settings
        self._cancel_timer_tick()
        # Reset to LOBBY first so start_game's phase guard passes; keeps
        # players (reset_to_lobby leaves connected players in place).
        game_state.reset_to_lobby()
        try:
            game_state.start_game(**settings)
        except ValueError as err:
            await self._conn.send_error(ws, ERR_GAME_ALREADY_STARTED, str(err))
            return
        # Same 2.5s grace as L3 — admin tab is still on the finale view
        # and needs to redirect/reconnect before round 1's timer ticks.
        await asyncio.sleep(2.5)
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
        # Cancel any pending admin-disconnect timer and stale player-removal
        # tasks from the finished game.
        await self._conn.cleanup()
        # Wipe player session tokens to prevent cross-game reuse.
        self._conn.clear_all_player_tokens()

        # Close all player WSes so abandoned connections actually die.
        # Snapshot the WS list first because closing mutates the registry
        # indirectly via the on_disconnect handler.
        stale_wses = [p.ws for p in game_state.get_players() if p.ws is not None]
        for pws in stale_wses:
            try:
                await pws.close()
            except Exception:  # noqa: BLE001 — best-effort cleanup
                pass

        # Drop every player from the registry (full wipe — not just score reset).
        game_state.clear_all_players()
        game_state.reset_to_lobby()

        await self._conn.broadcast({"type": "game_reset"})

        state = game_state.get_state_snapshot()
        state["type"] = "game_state"
        await self._conn.broadcast(state)

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
    # Question flow
    # ------------------------------------------------------------------

    async def _start_next_question(self, game_state: QuizifyGameState) -> None:
        """Start the next question: shuffle answers, broadcast, start timer ticks."""
        self._cancel_timer_tick()

        question = game_state.start_next_question()
        if question is None:
            # Game ended (no more questions or round limit reached)
            if game_state.phase == GamePhase.FINALE:
                await self._broadcast_finale(game_state)
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
        is_final = game_state.round == game_state.total_rounds
        for player in players_now:
            if not player.connected:
                continue
            shuffle = game_state.get_player_shuffle(player.name)
            shuffled_answers = [question.answers[i].text for i in shuffle]
            player_msg = serialize_question_for_player(
                question=question,
                shuffled_answers=shuffled_answers,
                round_num=game_state.round,
                total_rounds=game_state.total_rounds,
                timer_duration=game_state.round_duration,
                is_final_round=is_final,
                player_score=player.score,
            )
            await self._conn._safe_send(player.ws, player_msg)

        # Send question with correct answer to admin
        admin_msg = serialize_question_for_admin(
            question=question,
            round_num=game_state.round,
            total_rounds=game_state.total_rounds,
            timer_duration=game_state.round_duration,
        )
        # Also fan out to TV-dashboard connections so their question-view
        # populates with the same canonical-order payload. Without this the
        # dashboard's answer-grid stays empty and the v1.1.47 #151 answer-
        # distribution bars never attach. Admin-as-player still excluded.
        await self._conn.broadcast_to_admins_and_dashboards(admin_msg)

        # Cache players and leaderboard to avoid redundant calls
        players = game_state.get_players()
        leaderboard = serialize_leaderboard(players)

        # Notify players who got a power-up this round
        for player in players:
            powerup = game_state.get_player_powerup(player.name)
            if powerup and player.connected:
                await self._conn._safe_send(player.ws, {
                    "type": "powerup_assigned",
                    "powerup_type": powerup.value,
                })

        # Broadcast game state with leaderboard so player sees rankings during game
        await self._conn.broadcast({
            "type": "game_state",
            "phase": game_state.phase.value,
            "round": game_state.round,
            "total_rounds": game_state.total_rounds,
            "player_count": len(players),
            "players": leaderboard,
            "leaderboard": leaderboard,
        })

        # Start timer tick task
        self._start_timer_tick(game_state)

    # ------------------------------------------------------------------
    # Timer ticks
    # ------------------------------------------------------------------

    def _start_timer_tick(self, game_state: QuizifyGameState) -> None:
        """Start async task that sends timer_tick every second.

        Reads authoritative remaining time from each player's QuestionTimer
        instead of a local countdown, so time-boost and freeze power-ups are
        reflected in the broadcast (#4 in logical review). Targeted ticks are
        sent per player so each sees their own modified remaining time.
        """
        self._cancel_timer_tick()

        async def tick_loop() -> None:
            try:
                while game_state.phase == GamePhase.QUESTION_ACTIVE:
                    players = game_state.get_players()
                    # Send each player their authoritative remaining time.
                    for p in players:
                        if not p.connected:
                            continue
                        pt = game_state.get_player_timer(p.name)
                        if pt is None:
                            continue
                        remaining = max(0.0, pt.get_remaining())
                        await self._conn._safe_send(p.ws, {
                            "type": "timer_tick",
                            "remaining": round(remaining, 1),
                        })
                    # Also broadcast the minimum remaining to dashboards/admins
                    # so the TV view shows a consistent countdown.
                    min_remaining = 0.0
                    if players:
                        remainings = [
                            max(0.0, game_state.get_player_timer(p.name).get_remaining())
                            for p in players
                            if game_state.get_player_timer(p.name) is not None
                        ]
                        if remainings:
                            min_remaining = min(remainings)
                    # Broadcast to pure-admin + dashboards
                    for ws in list(self._conn._admin_connections):
                        if not ws.closed and not any(p.ws is ws for p in players):
                            await self._conn._safe_send(ws, {
                                "type": "timer_tick",
                                "remaining": round(min_remaining, 1),
                            })
                    for ws in list(self._conn._dashboard_connections):
                        if not ws.closed:
                            await self._conn._safe_send(ws, {
                                "type": "timer_tick",
                                "remaining": round(min_remaining, 1),
                            })
                    await asyncio.sleep(0.5)

                    # Stop if everyone's timer hit zero and phase is still active.
                    # Only count CONNECTED players with a timer — a missing timer
                    # used to be treated as "expired", which made the loop break
                    # the instant a single late-joining player (e.g. admin's own
                    # /quizify/player tab) connected before their per-player
                    # timer existed, ending round 1 in ~1s. Now we require an
                    # actual expired timer to count, and we ignore disconnected
                    # players so they don't keep the timer alive forever.
                    connected = [p for p in players if p.connected]
                    if connected:
                        timers_with_state = [
                            game_state.get_player_timer(p.name) for p in connected
                        ]
                        timers_with_state = [t for t in timers_with_state if t is not None]
                        if timers_with_state and all(t.is_expired() for t in timers_with_state):
                            break

                # Timer expired globally
                if game_state.phase == GamePhase.QUESTION_ACTIVE:
                    # Auto-evaluate round. The state machine's
                    # _fire_broadcast("round_evaluated") handles the summary
                    # broadcast \u2014 do NOT broadcast here (#3 in review).
                    game_state.evaluate_round()
            except asyncio.CancelledError:
                pass

        self._timer_tick_task = asyncio.ensure_future(tick_loop())

    def _cancel_timer_tick(self) -> None:
        """Cancel the timer tick task."""
        if self._timer_tick_task is not None:
            self._timer_tick_task.cancel()
            self._timer_tick_task = None

    # ------------------------------------------------------------------
    # Round summary broadcast
    # ------------------------------------------------------------------

    async def _broadcast_round_summary(self, game_state: QuizifyGameState) -> None:
        """Broadcast round summary to all clients."""
        summary = game_state.get_round_summary()
        if not summary:
            return

        # Find the correct answer's shuffled index (canonical) AND its
        # original index in question.answers — dashboard needs the latter
        # because it renders unshuffled answer tiles (per #151 audit).
        correct_shuffled_idx = -1
        correct_original_idx = -1
        for a in summary.question.answers:
            if a.correct:
                correct_original_idx = summary.question.answers.index(a)
                for shuffled_idx, orig_idx in enumerate(game_state.shuffle_map):
                    if orig_idx == correct_original_idx:
                        correct_shuffled_idx = shuffled_idx
                        break
                break

        leaderboard = serialize_leaderboard(game_state.get_players())

        # Build all_answers: what each player answered this round.
        # answer_index is the ORIGINAL question index (not shuffled) —
        # with per-player shuffles, a shuffled index is meaningless to
        # any other player. The client uses answer_text for display and
        # correct_answer_text for highlighting their own button.
        all_answers = []
        for player in game_state.get_players():
            if player.submitted and player.current_answer is not None:
                submitted_orig = player.current_answer
                answer_text = (
                    summary.question.answers[submitted_orig].text
                    if 0 <= submitted_orig < len(summary.question.answers)
                    else "?"
                )
                is_correct = summary.question.answers[submitted_orig].correct if submitted_orig < len(summary.question.answers) else False
                breakdown = player.round_score_breakdown
                all_answers.append({
                    "player_name": player.name,
                    "answer_index": submitted_orig,  # original index, not shuffled
                    "answer_text": answer_text,
                    "correct": is_correct,
                    "points_earned": player.round_score,
                    "speed_bonus": breakdown.get("speed_bonus", 0),
                    "streak_bonus": breakdown.get("streak_bonus", 0),
                    "difficulty_multiplier": breakdown.get("difficulty_multiplier", 1.0),
                    "double_points": breakdown.get("double_points", False),
                    "streak": player.streak,
                })
            else:
                all_answers.append({
                    "player_name": player.name,
                    "answer_index": None,
                    "answer_text": "—",
                    "correct": False,
                    "points_earned": 0,
                    "no_answer": True,
                })

        # Build players list with is_admin so the reveal client can show
        # the Next Round button to admin-as-player.
        from custom_components.quizify.server.serializers import (  # noqa: PLC0415
            serialize_player_list,
        )
        players_list = serialize_player_list(game_state.get_players())
        last_round = game_state.round >= game_state.total_rounds

        summary_msg = serialize_round_summary(
            correct_answer_index=correct_shuffled_idx,
            correct_answer_index_original=correct_original_idx,
            correct_answer_text=summary.correct_answer.text,
            fun_fact=summary.fun_fact,
            leaderboard=leaderboard,
            round_num=game_state.round,
            total_rounds=game_state.total_rounds,
            all_answers=all_answers,
            question_text=summary.question.question,
            num_answer_options=len(game_state.shuffled_answers),
            players=players_list,
            last_round=last_round,
            question_id=summary.question.id,
        )
        await self._conn.broadcast(summary_msg)

    # ------------------------------------------------------------------
    # Disconnect handling
    # ------------------------------------------------------------------

    async def _handle_disconnect(self, ws: web.WebSocketResponse) -> None:
        """Handle WebSocket disconnection."""
        game_state = self._get_game_state()
        if not game_state:
            return

        # Handle admin disconnect — keep game alive for grace period.
        # Strictly gated on real admin connection (was: OR clause allowed
        # dashboard disconnects to trigger the timeout, see #2 in review).
        if self._conn.is_admin_connection(ws):
            if not self._conn._admin_connections:
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
        except Exception as err:  # noqa: BLE001
            _LOGGER.warning("TTS milestone announcement raised: %s", err)

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
        """Broadcast callback — called by game state on auto-events."""
        if payload is not None:
            event = payload.get("event")
            if event == "round_evaluated":
                game_state = self._get_game_state()
                if game_state:
                    await self._broadcast_round_summary(game_state)
                return
            if event == "game_ended":
                game_state = self._get_game_state()
                if game_state:
                    await self._broadcast_finale(game_state)
                return

        # Default: broadcast full state
        game_state = self._get_game_state()
        if game_state:
            state = game_state.get_state_snapshot()
            state["type"] = "game_state"
            await self._conn.broadcast(state)

    async def cleanup_game_tasks(self) -> None:
        """Cancel all pending tasks."""
        self._cancel_timer_tick()
        await self._conn.cleanup()
        _LOGGER.debug("Cleaned up all pending game tasks")
