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
        self.connections: set[web.WebSocketResponse] = set()
        self._admin_connections: set[web.WebSocketResponse] = set()
        self._dashboard_connections: set[web.WebSocketResponse] = set()
        self._pending_removals: dict[str, asyncio.Task] = {}
        self._timer_tick_task: asyncio.Task | None = None
        # Answer shuffle mapping: original_index -> shuffled_index per round
        self._shuffle_map: list[int] = []  # shuffled_index -> original_index
        self._shuffled_answers: list[str] = []
        self._game_state_ref: object | None = None  # set on first game_state access
        # Session tokens for player reconnect
        self._session_tokens: dict[str, str] = {}  # token → player_name
        # Player disconnect grace period (keep session alive)
        self._PLAYER_SESSION_GRACE = 60  # seconds
        # Admin session
        self._admin_session_token: str | None = None
        self._admin_disconnect_task: asyncio.Task | None = None
        self._ADMIN_SESSION_GRACE = 120  # seconds

    async def handle(self, request: web.Request) -> web.WebSocketResponse:
        """Handle WebSocket connection."""
        ws = web.WebSocketResponse(heartbeat=self.HEARTBEAT_INTERVAL)
        await ws.prepare(request)

        role = request.query.get("role")
        is_admin = role == "admin"
        is_dashboard = role == "dashboard"
        admin_token = request.query.get("token")

        # Validate admin token for reconnection
        if is_admin and admin_token and admin_token == self._admin_session_token:
            _LOGGER.info("Admin reconnected with valid session token")

        self.connections.add(ws)
        if is_admin:
            self._admin_connections.add(ws)
        if is_dashboard:
            self._dashboard_connections.add(ws)
        # Keep game state reference for broadcast logic
        gs = get_game_state(self.hass)
        if gs:
            self._game_state_ref = gs

        _LOGGER.debug(
            "WebSocket connected (admin=%s), total: %d",
            is_admin,
            len(self.connections),
        )

        try:
            async for msg in ws:
                if msg.type == WSMsgType.TEXT:
                    try:
                        await self._handle_message(ws, msg.json(), is_admin)
                    except Exception as err:  # noqa: BLE001
                        _LOGGER.warning("Failed to handle WebSocket message: %s", err)
                elif msg.type == WSMsgType.ERROR:
                    _LOGGER.error("WebSocket error: %s", ws.exception())
        finally:
            self.connections.discard(ws)
            self._admin_connections.discard(ws)
            self._dashboard_connections.discard(ws)
            await self._handle_disconnect(ws)
            _LOGGER.debug("WebSocket disconnected, total: %d", len(self.connections))

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
            await self._send_error(ws, ERR_GAME_NOT_STARTED, "No active game")
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
                await self._send_error(ws, ERR_INVALID_ACTION, "Admin only")
                return
            await self._handle_start_game(ws, data, game_state)

        elif msg_type == "next_question":
            if not is_admin:
                await self._send_error(ws, ERR_INVALID_ACTION, "Admin only")
                return
            await self._handle_next_question(ws, game_state)

        elif msg_type == "end_game":
            if not is_admin:
                await self._send_error(ws, ERR_INVALID_ACTION, "Admin only")
                return
            await self._handle_end_game(ws, game_state)

        elif msg_type == "reset_game":
            if not is_admin:
                await self._send_error(ws, ERR_INVALID_ACTION, "Admin only")
                return
            await self._handle_reset_game(ws, game_state)

        elif msg_type == "reconnect":
            await self._handle_reconnect(ws, data, game_state)

        elif msg_type == "get_state":
            state_msg = game_state.get_state_snapshot()
            state_msg["type"] = "game_state"
            await self._safe_send(ws, state_msg)

        else:
            _LOGGER.warning("Unknown message type: %s", msg_type)
            await self._send_error(ws, ERR_INVALID_ACTION, "Unknown message type")

    # ------------------------------------------------------------------
    # Admin connect
    # ------------------------------------------------------------------

    async def _handle_admin_connect(
        self, ws: web.WebSocketResponse, game_state: QuizifyGameState
    ) -> None:
        """Send full state to admin on connect."""
        # Cancel admin disconnect task if reconnecting
        if self._admin_disconnect_task and not self._admin_disconnect_task.done():
            self._admin_disconnect_task.cancel()
            self._admin_disconnect_task = None
            _LOGGER.info("Admin reconnected, cancelled disconnect timeout")

        # Generate or reuse admin session token
        if not self._admin_session_token:
            self._admin_session_token = str(uuid.uuid4())

        state = game_state.get_state_snapshot()
        state["type"] = "game_state"
        state["join_url"] = "/quizify/player"
        state["admin_session_token"] = self._admin_session_token
        await self._safe_send(ws, state)

    # ------------------------------------------------------------------
    # Player join
    # ------------------------------------------------------------------

    async def _handle_join(
        self, ws: web.WebSocketResponse, data: dict, game_state: QuizifyGameState
    ) -> None:
        """Handle player join."""
        name = data.get("name", "").strip()
        is_admin = data.get("is_admin", False)

        if not name:
            await self._send_error(ws, ERR_NAME_INVALID, "Name is required")
            return

        # Auto-append number if name is taken
        original_name = name
        counter = 2
        while game_state.get_player(name) and game_state.get_player(name).connected:
            name = f"{original_name} {counter}"
            counter += 1

        success, error_code = game_state.add_player(name, ws)

        if success:
            player = game_state.get_player(name)
            # Cancel pending removal on reconnect
            self._cancel_pending_removal(name)

            # If admin joins as player, keep the WS in admin connections
            # so it receives both admin state updates and player messages
            if is_admin:
                self._admin_connections.add(ws)

            # Generate session token for reconnect
            session_token = str(uuid.uuid4())
            self._session_tokens[session_token] = name

            # Send join confirmation with session token
            powerup = game_state._powerup_manager.get_powerup(name)
            await self._safe_send(ws, {
                "type": "joined",
                "player_id": name,
                "powerup": powerup.value if powerup else None,
                "session_token": session_token,
            })

            # Send current state to the joining player
            state = game_state.get_state_snapshot()
            state["type"] = "game_state"
            await self._safe_send(ws, state)

            # Broadcast player list to everyone
            players = game_state.get_players()
            await self._broadcast({
                "type": "player_joined",
                "players": serialize_player_list(players),
            })
        else:
            error_messages = {
                ERR_NAME_TAKEN: "Name bereits vergeben",
                ERR_NAME_INVALID: "Bitte gib einen Namen ein",
                ERR_GAME_FULL: "Spiel ist voll",
            }
            await self._send_error(
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
        name = self._session_tokens.get(token)

        if not name:
            # Token not found — treat as unknown, client should show join form
            await self._safe_send(ws, {"type": "reconnect_failed"})
            return

        player = game_state.get_player(name)
        if not player:
            # Player was fully removed — token stale
            self._session_tokens.pop(token, None)
            await self._safe_send(ws, {"type": "reconnect_failed"})
            return

        # Restore player connection
        player.ws = ws
        player.connected = True
        self._cancel_pending_removal(name)

        _LOGGER.info("Player session-reconnected: %s", name)

        # Generate a fresh token and revoke old one
        new_token = str(uuid.uuid4())
        self._session_tokens.pop(token, None)
        self._session_tokens[new_token] = name

        # Send reconnect success with new token
        powerup = game_state._powerup_manager.get_powerup(name)
        await self._safe_send(ws, {
            "type": "reconnected",
            "player_id": name,
            "session_token": new_token,
            "powerup": powerup.value if powerup else None,
        })

        # Send full game state
        state = game_state.get_state_snapshot()
        state["type"] = "game_state"
        await self._safe_send(ws, state)

        # Broadcast updated player list
        players = game_state.get_players()
        await self._broadcast({
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
            await self._send_error(ws, ERR_NOT_IN_GAME, "Not in game")
            return

        if game_state.phase != GamePhase.QUESTION_ACTIVE:
            # Silently ignore submit_answer when not in question phase
            return

        shuffled_index = data.get("answer_index")
        if shuffled_index is None or not isinstance(shuffled_index, int):
            await self._send_error(ws, ERR_INVALID_ACTION, "Invalid answer index")
            return

        # Map shuffled index back to original index
        if 0 <= shuffled_index < len(self._shuffle_map):
            original_index = self._shuffle_map[shuffled_index]
        else:
            await self._send_error(ws, ERR_INVALID_ACTION, "Answer index out of range")
            return

        result = game_state.submit_answer(player.name, original_index)

        if isinstance(result, AnswerResult):
            await self._safe_send(ws, {
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
            await self._send_error(
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
            await self._send_error(ws, ERR_NOT_IN_GAME, "Not in game")
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
                for shuffled_idx, orig_idx in enumerate(self._shuffle_map):
                    if orig_idx == result.joker_remove_index:
                        shuffled_remove_idx = shuffled_idx
                        break

                await self._safe_send(ws, {
                    "type": "powerup_applied",
                    "powerup_type": "joker",
                    "source_player": result.source_player,
                    "joker_remove_index": shuffled_remove_idx,
                })
            else:
                await self._broadcast(effect_data)
        elif isinstance(result, str):
            await self._send_error(ws, result, "Power-up nicht verfügbar")

    # ------------------------------------------------------------------
    # Admin: start game
    # ------------------------------------------------------------------

    async def _handle_start_game(
        self, ws: web.WebSocketResponse, data: dict, game_state: QuizifyGameState
    ) -> None:
        """Handle admin start_game command."""
        if game_state.phase != GamePhase.LOBBY:
            await self._send_error(ws, ERR_GAME_ALREADY_STARTED, "Game already running")
            return

        category = data.get("category")
        difficulty = data.get("difficulty")
        num_rounds = data.get("num_rounds", 10)

        try:
            game_info = game_state.start_game(
                category=category,
                difficulty=difficulty,
                num_rounds=num_rounds,
            )
        except ValueError as err:
            await self._send_error(ws, ERR_GAME_ALREADY_STARTED, str(err))
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
            await self._send_error(ws, ERR_INVALID_ACTION, "Cannot advance now")
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
        finale_data = game_state.end_game()

        from custom_components.quizify.game.scoring import calculate_podium  # noqa: PLC0415

        podium = calculate_podium(game_state.get_players())
        all_players = game_state.get_players()
        share_texts = finale_data.get("share_texts")
        awards = [s.to_dict() for s in compute_superlatives(all_players)]
        finale_msg = serialize_finale(podium, all_players, share_texts=share_texts, superlatives=awards)
        await self._broadcast(finale_msg)

    # ------------------------------------------------------------------
    # Admin: reset game
    # ------------------------------------------------------------------

    async def _handle_reset_game(
        self, ws: web.WebSocketResponse, game_state: QuizifyGameState
    ) -> None:
        """Handle admin reset_game command (return to lobby)."""
        self._cancel_timer_tick()
        game_state.reset_to_lobby()

        await self._broadcast({"type": "game_reset"})

        state = game_state.get_state_snapshot()
        state["type"] = "game_state"
        await self._broadcast(state)

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
                from custom_components.quizify.game.scoring import calculate_podium  # noqa: PLC0415
                from custom_components.quizify.game.share import build_share_data  # noqa: PLC0415

                podium = calculate_podium(game_state.get_players())
                all_players = game_state.get_players()
                share_data = build_share_data(game_state)
                awards = [s.to_dict() for s in compute_superlatives(all_players)]
                finale_msg = serialize_finale(podium, all_players, share_texts=share_data.get("share_texts"), superlatives=awards)
                await self._broadcast(finale_msg)
            return

        # Shuffle answers — build mapping
        indices = list(range(len(question.answers)))
        random.shuffle(indices)
        self._shuffle_map = indices  # shuffled_pos -> original_index
        self._shuffled_answers = [question.answers[i].text for i in indices]

        # Broadcast question to players (no correct flag)
        player_msg = serialize_question_for_player(
            question=question,
            shuffled_answers=self._shuffled_answers,
            round_num=game_state.round,
            total_rounds=game_state.total_rounds,
            timer_duration=game_state._round_duration,
        )
        await self._broadcast_to_players(player_msg)

        # Send question with correct answer to admin
        admin_msg = serialize_question_for_admin(
            question=question,
            round_num=game_state.round,
            total_rounds=game_state.total_rounds,
            timer_duration=game_state._round_duration,
        )
        await self._broadcast_to_admins(admin_msg)

        # Notify players who got a power-up this round
        for player in game_state.get_players():
            powerup = game_state._powerup_manager.get_powerup(player.name)
            if powerup and player.connected:
                await self._safe_send(player.ws, {
                    "type": "powerup_assigned",
                    "powerup_type": powerup.value,
                })

        # Broadcast game state with leaderboard so player sees rankings during game
        await self._broadcast({
            "type": "game_state",
            "phase": game_state.phase.value,
            "round": game_state.round,
            "total_rounds": game_state.total_rounds,
            "player_count": len(game_state.get_players()),
            "players": serialize_leaderboard(game_state.get_players()),
            "leaderboard": serialize_leaderboard(game_state.get_players()),
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
                duration = game_state._round_duration
                remaining = duration
                while remaining > 0 and game_state.phase == GamePhase.QUESTION_ACTIVE:
                    await self._broadcast({"type": "timer_tick", "remaining": round(remaining, 1)})
                    await asyncio.sleep(1.0)
                    remaining -= 1.0

                # Timer expired
                if game_state.phase == GamePhase.QUESTION_ACTIVE:
                    await self._broadcast({"type": "timer_tick", "remaining": 0})
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
                for shuffled_idx, orig_idx in enumerate(self._shuffle_map):
                    if orig_idx == original_idx:
                        correct_shuffled_idx = shuffled_idx
                        break
                break

        leaderboard = serialize_leaderboard(game_state.get_players())

        # Build all_answers: what each player answered this round
        all_answers = []
        for player in game_state.get_players():
            if player.submitted is not None:
                # Map original answer index to shuffled index for display
                submitted_orig = player.submitted
                submitted_shuffled = None
                for sh_idx, orig_idx in enumerate(self._shuffle_map):
                    if orig_idx == submitted_orig:
                        submitted_shuffled = sh_idx
                        break
                answer_text = self._shuffled_answers[submitted_shuffled] if submitted_shuffled is not None else "?"
                is_correct = summary.question.answers[submitted_orig].correct if submitted_orig < len(summary.question.answers) else False
                breakdown = player.round_score_breakdown if hasattr(player, "round_score_breakdown") else {}
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
        )
        await self._broadcast(summary_msg)

    # ------------------------------------------------------------------
    # Disconnect handling
    # ------------------------------------------------------------------

    async def _handle_disconnect(self, ws: web.WebSocketResponse) -> None:
        """Handle WebSocket disconnection."""
        game_state = get_game_state(self.hass)
        if not game_state:
            return

        # Handle admin disconnect — keep game alive for grace period
        if ws in self._admin_connections or (
            not game_state.get_player_by_ws(ws) and self._admin_session_token
        ):
            if not self._admin_connections:
                _LOGGER.info("Admin disconnected, keeping game alive for %ds", self._ADMIN_SESSION_GRACE)

                async def admin_timeout() -> None:
                    await asyncio.sleep(self._ADMIN_SESSION_GRACE)
                    _LOGGER.info("Admin session grace period expired, clearing token")
                    self._admin_session_token = None

                self._admin_disconnect_task = asyncio.ensure_future(admin_timeout())

        player = game_state.get_player_by_ws(ws)
        if not player:
            return

        player.connected = False
        _LOGGER.info("Player disconnected: %s", player.name)

        # Broadcast updated player list
        players = game_state.get_players()
        await self._broadcast({
            "type": "player_left",
            "players": serialize_player_list(players),
        })

        # Schedule removal after grace period
        grace = LOBBY_DISCONNECT_GRACE_PERIOD if game_state.phase == GamePhase.LOBBY else self._PLAYER_SESSION_GRACE

        async def remove_after_timeout(name: str, timeout: float) -> None:
            await asyncio.sleep(timeout)
            gs = get_game_state(self.hass)
            if gs:
                p = gs.get_player(name)
                if p and not p.connected:
                    gs.remove_player(name)
                    # Clean up session tokens for this player
                    stale_tokens = [t for t, n in self._session_tokens.items() if n == name]
                    for t in stale_tokens:
                        self._session_tokens.pop(t, None)
                    remaining = gs.get_players()
                    await self._broadcast({
                        "type": "player_left",
                        "players": serialize_player_list(remaining),
                    })
                    _LOGGER.info("Removed disconnected player after grace period: %s", name)

        task = asyncio.ensure_future(remove_after_timeout(player.name, grace))
        self._pending_removals[player.name] = task

    def _cancel_pending_removal(self, name: str) -> None:
        """Cancel a pending player removal on reconnect."""
        task = self._pending_removals.pop(name, None)
        if task and not task.done():
            task.cancel()
            _LOGGER.info("Cancelled removal for reconnecting player: %s", name)

    # ------------------------------------------------------------------
    # Broadcast helpers
    # ------------------------------------------------------------------

    async def _broadcast(self, message: dict) -> None:
        """Broadcast message to all connected clients in parallel."""
        if not self.connections:
            return
        tasks = [
            self._safe_send(ws, message)
            for ws in list(self.connections)
            if not ws.closed
        ]
        if tasks:
            await asyncio.gather(*tasks)

    async def _broadcast_to_players(self, message: dict) -> None:
        """Broadcast to all player connections.

        Admin-as-player connections (in both self.connections and _admin_connections)
        also receive player messages so they see questions/answers without the correct flag.
        """
        # All connections that have joined as players (have a PlayerSession)
        player_names = {p.name for p in self._game_state_ref.get_players()} if self._game_state_ref else set()

        tasks = []
        for ws in list(self.connections):
            if ws.closed:
                continue
            # Include if: not admin, OR admin who also joined as player
            is_pure_admin = ws in self._admin_connections and not any(
                p.ws is ws for p in (self._game_state_ref.get_players() if self._game_state_ref else [])
            )
            if not is_pure_admin:
                tasks.append(self._safe_send(ws, message))
        if tasks:
            await asyncio.gather(*tasks)

    async def _broadcast_to_admins(self, message: dict) -> None:
        """Broadcast admin-only messages (with correct answer) to pure admin connections.

        Admin-as-player connections do NOT receive these — they get player messages instead
        so they don't see the correct answer before submitting.
        """
        if not self._admin_connections:
            return
        player_ws_set = {
            p.ws for p in (self._game_state_ref.get_players() if self._game_state_ref else [])
        }
        tasks = [
            self._safe_send(ws, message)
            for ws in list(self._admin_connections)
            if not ws.closed and ws not in player_ws_set  # skip admin-as-player
        ]
        if tasks:
            await asyncio.gather(*tasks)

    async def _safe_send(self, ws: web.WebSocketResponse, message: dict) -> None:
        """Send message to a single WebSocket, catching errors."""
        try:
            await ws.send_json(message)
        except Exception as err:  # noqa: BLE001
            _LOGGER.warning("Failed to send to WebSocket: %s", err)

    async def _send_error(self, ws: web.WebSocketResponse, code: str, message: str) -> None:
        """Send an error message to a client."""
        await self._safe_send(ws, {
            "type": "error",
            "code": code,
            "message": message,
        })

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
                    from custom_components.quizify.game.scoring import calculate_podium  # noqa: PLC0415
                    from custom_components.quizify.game.share import build_share_data  # noqa: PLC0415

                    podium = calculate_podium(game_state.get_players())
                    all_players = game_state.get_players()
                    share_data = build_share_data(game_state)
                    awards = [s.to_dict() for s in compute_superlatives(all_players)]
                    finale_msg = serialize_finale(podium, all_players, share_texts=share_data.get("share_texts"), superlatives=awards)
                    await self._broadcast(finale_msg)
                return

        # Default: broadcast full state
        game_state = get_game_state(self.hass)
        if game_state:
            state = game_state.get_state_snapshot()
            state["type"] = "game_state"
            await self._broadcast(state)

    async def cleanup_game_tasks(self) -> None:
        """Cancel all pending tasks."""
        self._cancel_timer_tick()

        for task in list(self._pending_removals.values()):
            if not task.done():
                task.cancel()
        self._pending_removals.clear()

        _LOGGER.debug("Cleaned up all pending game tasks")
