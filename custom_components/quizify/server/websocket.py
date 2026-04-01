"""WebSocket handler for Quizify real-time communication."""

from __future__ import annotations

import asyncio
import logging
import random
import uuid
from typing import TYPE_CHECKING, Any

from aiohttp import WSMsgType, web

from custom_components.quizify.const import (
    DOMAIN,
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
    get_game_state,
    serialize_finale,
    serialize_leaderboard,
    serialize_player_list,
    serialize_question_for_admin,
    serialize_question_for_player,
    serialize_round_summary,
)

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

_LOGGER = logging.getLogger(__name__)


class QuizifyWebSocketHandler:
    """Handle WebSocket connections for Quizify."""

    HEARTBEAT_INTERVAL = 30

    def __init__(self, hass: HomeAssistant) -> None:
        """Initialize handler."""
        self.hass = hass
        self._conn = ConnectionManager(hass)
        self._timer_tick_task: asyncio.Task | None = None
        # Rate limiting
        self._message_timestamps: dict[int, list[float]] = {}  # ws id -> recent message timestamps
        self._RATE_LIMIT_WINDOW = 1.0  # seconds
        self._RATE_LIMIT_MAX = 15  # max messages per window

    async def handle(self, request: web.Request) -> web.WebSocketResponse:
        """Handle WebSocket connection."""
        ws = web.WebSocketResponse(heartbeat=self.HEARTBEAT_INTERVAL)
        await ws.prepare(request)

        role = request.query.get("role")
        is_admin = role == "admin"
        is_dashboard = role == "dashboard"
        admin_token = request.query.get("token")

        # Validate admin token for reconnection
        if is_admin and admin_token and self._conn.validate_admin_token(admin_token):
            _LOGGER.info("Admin reconnected with valid session token")

        self._conn.add_connection(ws, is_admin=is_admin, is_dashboard=is_dashboard)

        _LOGGER.debug(
            "WebSocket connected (admin=%s), total: %d",
            is_admin,
            len(self._conn.connections),
        )

        try:
            async for msg in ws:
                if msg.type == WSMsgType.TEXT:
                    # Rate limiting
                    ws_id = id(ws)
                    now = asyncio.get_event_loop().time()
                    timestamps = self._message_timestamps.setdefault(ws_id, [])
                    # Remove old timestamps outside window
                    self._message_timestamps[ws_id] = [t for t in timestamps if now - t < self._RATE_LIMIT_WINDOW]
                    if len(self._message_timestamps[ws_id]) >= self._RATE_LIMIT_MAX:
                        _LOGGER.warning("Rate limit exceeded for WebSocket %s", ws_id)
                        continue  # skip this message silently
                    self._message_timestamps[ws_id].append(now)
                    try:
                        await self._handle_message(ws, msg.json(), is_admin)
                    except Exception as err:  # noqa: BLE001
                        _LOGGER.warning("Failed to handle WebSocket message: %s", err)
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
        game_state = get_game_state(self.hass)

        if not game_state:
            await self._conn.send_error(ws, ERR_GAME_NOT_STARTED, "No active game")
            return

        if msg_type == "admin_connect":
            await self._handle_admin_connect(ws, game_state)

        elif msg_type == "join":
            await self._handle_join(ws, data, game_state)

        elif msg_type == "submit_answer":
            await self._handle_submit_answer(ws, data, game_state)

        elif msg_type == "use_powerup":
            await self._handle_use_powerup(ws, data, game_state)

        elif msg_type == "start_game":
            if not is_admin:
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

        elif msg_type == "reconnect":
            await self._handle_reconnect(ws, data, game_state)

        elif msg_type == "get_state":
            state_msg = game_state.get_state_snapshot()
            state_msg["type"] = "game_state"
            await self._conn._safe_send(ws, state_msg)

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

            # Send join confirmation with session token and assigned color
            powerup = game_state.get_player_powerup(name)
            player_obj = game_state.get_player(name)
            await self._conn._safe_send(ws, {
                "type": "joined",
                "player_id": name,
                "powerup": powerup.value if powerup else None,
                "session_token": session_token,
                "color": player_obj.color if player_obj else "",
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

        _LOGGER.info("Player session-reconnected: %s", name)

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

        # Map shuffled index back to original index
        if 0 <= shuffled_index < len(game_state.shuffle_map):
            original_index = game_state.shuffle_map[shuffled_index]
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
            })

            # If round was auto-evaluated (all submitted), broadcast summary
            if game_state.phase == GamePhase.ANSWER_REVEAL:
                await self._broadcast_round_summary(game_state)
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
        """Handle admin start_game command."""
        if game_state.phase != GamePhase.LOBBY:
            await self._conn.send_error(ws, ERR_GAME_ALREADY_STARTED, "Game already running")
            return

        raw_category = data.get("category")
        difficulty = data.get("difficulty")
        num_rounds = data.get("num_rounds", 10)
        language = data.get("language", "de")

        # category may be None (mixed), a string (single), or a list (multi)
        if isinstance(raw_category, list):
            category = None
            categories = raw_category if raw_category else None
        else:
            category = raw_category or None
            categories = None

        try:
            game_state.start_game(
                category=category,
                categories=categories,
                difficulty=difficulty,
                num_rounds=num_rounds,
                language=language,
            )
        except ValueError as err:
            await self._conn.send_error(ws, ERR_GAME_ALREADY_STARTED, str(err))
            return

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

    async def _handle_reset_game(
        self, ws: web.WebSocketResponse, game_state: QuizifyGameState
    ) -> None:
        """Handle admin reset_game command (return to lobby)."""
        self._cancel_timer_tick()
        game_state.reset_to_lobby()

        await self._conn.broadcast({"type": "game_reset"})

        state = game_state.get_state_snapshot()
        state["type"] = "game_state"
        await self._conn.broadcast(state)

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

        # Shuffle answers — build mapping
        indices = list(range(len(question.answers)))
        random.shuffle(indices)
        game_state.set_round_shuffle(indices, [question.answers[i].text for i in indices])

        # Broadcast question to players (no correct flag)
        player_msg = serialize_question_for_player(
            question=question,
            shuffled_answers=game_state.shuffled_answers,
            round_num=game_state.round,
            total_rounds=game_state.total_rounds,
            timer_duration=game_state.round_duration,
        )
        await self._conn.broadcast_to_players(player_msg)

        # Send question with correct answer to admin
        admin_msg = serialize_question_for_admin(
            question=question,
            round_num=game_state.round,
            total_rounds=game_state.total_rounds,
            timer_duration=game_state.round_duration,
        )
        await self._conn.broadcast_to_admins(admin_msg)

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
        """Start async task that sends timer_tick every second."""
        self._cancel_timer_tick()

        async def tick_loop() -> None:
            try:
                duration = game_state.round_duration
                remaining = duration
                while remaining > 0 and game_state.phase == GamePhase.QUESTION_ACTIVE:
                    await self._conn.broadcast({"type": "timer_tick", "remaining": round(remaining, 1)})
                    await asyncio.sleep(1.0)
                    remaining -= 1.0

                # Timer expired
                if game_state.phase == GamePhase.QUESTION_ACTIVE:
                    await self._conn.broadcast({"type": "timer_tick", "remaining": 0})
                    # Auto-evaluate round
                    game_state.evaluate_round()
                    if game_state.phase == GamePhase.ANSWER_REVEAL:
                        await self._broadcast_round_summary(game_state)
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

        # Find the correct answer's shuffled index
        correct_shuffled_idx = -1
        for a in summary.question.answers:
            if a.correct:
                original_idx = summary.question.answers.index(a)
                for shuffled_idx, orig_idx in enumerate(game_state.shuffle_map):
                    if orig_idx == original_idx:
                        correct_shuffled_idx = shuffled_idx
                        break
                break

        leaderboard = serialize_leaderboard(game_state.get_players())

        # Build all_answers: what each player answered this round
        all_answers = []
        for player in game_state.get_players():
            if player.submitted and player.current_answer is not None:
                submitted_orig = player.current_answer
                submitted_shuffled = None
                for sh_idx, orig_idx in enumerate(game_state.shuffle_map):
                    if orig_idx == submitted_orig:
                        submitted_shuffled = sh_idx
                        break
                answer_text = game_state.shuffled_answers[submitted_shuffled] if submitted_shuffled is not None else "?"
                is_correct = summary.question.answers[submitted_orig].correct if submitted_orig < len(summary.question.answers) else False
                breakdown = player.round_score_breakdown
                all_answers.append({
                    "player_name": player.name,
                    "answer_index": submitted_shuffled,
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

        summary_msg = serialize_round_summary(
            correct_answer_index=correct_shuffled_idx,
            correct_answer_text=summary.correct_answer.text,
            fun_fact=summary.fun_fact,
            leaderboard=leaderboard,
            round_num=game_state.round,
            total_rounds=game_state.total_rounds,
            all_answers=all_answers,
            question_text=summary.question.question,
            num_answer_options=len(game_state.shuffled_answers),
        )
        await self._conn.broadcast(summary_msg)

    # ------------------------------------------------------------------
    # Disconnect handling
    # ------------------------------------------------------------------

    async def _handle_disconnect(self, ws: web.WebSocketResponse) -> None:
        """Handle WebSocket disconnection."""
        game_state = get_game_state(self.hass)
        if not game_state:
            return

        # Handle admin disconnect — keep game alive for grace period
        if self._conn.is_admin_connection(ws) or (
            not game_state.get_player_by_ws(ws) and self._conn._admin_session_token
        ):
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
            gs = get_game_state(self.hass)
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
    # Admin auth helper
    # ------------------------------------------------------------------

    def _is_authorized_admin(
        self, ws: web.WebSocketResponse, is_admin: bool, game_state: QuizifyGameState
    ) -> bool:
        """Return True if the connection is authorized to perform admin actions."""
        player = game_state.get_player_by_ws(ws)
        return is_admin or bool(player and player.is_admin)

    # ------------------------------------------------------------------
    # Finale broadcast helper
    # ------------------------------------------------------------------

    async def _broadcast_finale(self, game_state: QuizifyGameState) -> None:
        """Build and broadcast the finale message (podium + superlatives + share texts)."""
        from custom_components.quizify.game.scoring import calculate_podium  # noqa: PLC0415
        from custom_components.quizify.game.share import build_share_data  # noqa: PLC0415

        podium = calculate_podium(game_state.get_players())
        all_players = game_state.get_players()
        share_data = build_share_data(game_state)
        awards = [s.to_dict() for s in compute_superlatives(all_players)]
        finale_msg = serialize_finale(
            podium, all_players, share_texts=share_data.get("share_texts"), superlatives=awards
        )
        await self._conn.broadcast(finale_msg)

    # ------------------------------------------------------------------
    # Broadcast callback for game state
    # ------------------------------------------------------------------

    async def broadcast_state(self, payload: dict[str, Any] | None = None) -> None:
        """Broadcast callback — called by game state on auto-events."""
        if payload is not None:
            event = payload.get("event")
            if event == "round_evaluated":
                game_state = get_game_state(self.hass)
                if game_state:
                    await self._broadcast_round_summary(game_state)
                return
            if event == "game_ended":
                game_state = get_game_state(self.hass)
                if game_state:
                    await self._broadcast_finale(game_state)
                return

        # Default: broadcast full state
        game_state = get_game_state(self.hass)
        if game_state:
            state = game_state.get_state_snapshot()
            state["type"] = "game_state"
            await self._conn.broadcast(state)

    async def cleanup_game_tasks(self) -> None:
        """Cancel all pending tasks."""
        self._cancel_timer_tick()
        await self._conn.cleanup()
        _LOGGER.debug("Cleaned up all pending game tasks")
