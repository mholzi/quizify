"""Game state management for Quizify."""

from __future__ import annotations

import asyncio
import logging
import secrets
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING, Any

from ..const import (
    DEFAULT_ROUND_DURATION,
    DIFFICULTY_DEFAULT,
    ERR_ALREADY_SUBMITTED,
    ERR_GAME_ALREADY_STARTED,
    ERR_GAME_NOT_STARTED,
    ERR_INVALID_ACTION,
    ERR_NOT_IN_GAME,
    ERR_NO_QUESTIONS_REMAINING,
    ERR_ROUND_EXPIRED,
    MIN_PLAYERS,
)
from .player import PlayerSession
from .player_registry import PlayerRegistry
from .powerups import FREEZE_DURATION, TIME_BOOST_DURATION, PowerUpEffect, PowerUpManager, PowerUpType
from .questions import Answer, Question, QuestionBank
from .highlights import compute_superlatives
from .scoring import calculate_podium, calculate_round_score
from .share import build_share_data
from .timer import QuestionTimer
from .types import DIFFICULTY_MULTIPLIERS, TIME_LIMITS, Difficulty, RoundResult

if TYPE_CHECKING:
    from aiohttp import web
    from homeassistant.core import HomeAssistant

_LOGGER = logging.getLogger(__name__)


class GamePhase(str, Enum):
    """Game phase states."""

    LOBBY = "LOBBY"
    QUESTION_ACTIVE = "QUESTION_ACTIVE"
    ANSWER_REVEAL = "ANSWER_REVEAL"
    FINALE = "FINALE"


@dataclass
class AnswerResult:
    """Result of a single player's answer for a round."""

    player_id: str
    correct: bool
    points_earned: int
    new_streak: int
    new_total: int
    speed_bonus: int = 0
    streak_bonus: int = 0
    difficulty_multiplier: float = 1.0


@dataclass
class RoundSummary:
    """Summary of a completed round."""

    question: Question
    correct_answer: Answer
    fun_fact: str
    results: list[AnswerResult] = field(default_factory=list)
    leaderboard: list[dict[str, Any]] = field(default_factory=list)


class QuizifyGameState:
    """Manages the overall game state for a Quizify session."""

    def __init__(self, hass: HomeAssistant | None = None, entry_id: str = "") -> None:
        """Initialize game state."""
        self._hass = hass
        self._entry_id = entry_id

        # Core state
        self.game_id: str | None = None
        self.phase: GamePhase = GamePhase.LOBBY
        self.round: int = 0
        self.total_rounds: int = 10
        self.category: str | None = None
        self.difficulty: str = DIFFICULTY_DEFAULT
        self.join_url: str | None = None

        # Sub-managers
        self._player_registry = PlayerRegistry()
        self._question_bank = QuestionBank()
        self._powerup_manager = PowerUpManager()

        # Current round state
        self._current_question: Question | None = None
        self._timers: dict[str, QuestionTimer] = {}  # player_id → timer
        self._round_start_time: float | None = None
        self._round_duration: float = DEFAULT_ROUND_DURATION
        self._round_summary: RoundSummary | None = None

        # Timer task for auto-evaluate when time expires
        self._timer_task: asyncio.Task | None = None

        # Broadcast callback — set by websocket layer
        self._broadcast_callback: Callable[[dict[str, Any]], Awaitable[None]] | None = None

        # Background task tracking to prevent GC
        self._bg_tasks: set[asyncio.Task] = set()

        # Analytics / stats service (injected from __init__.py)
        self._stats_service = None
        self._game_start_time: float | None = None

    # ------------------------------------------------------------------
    # Player registry delegation
    # ------------------------------------------------------------------

    @property
    def players(self) -> dict[str, PlayerSession]:
        """Player dict — delegated to PlayerRegistry."""
        return self._player_registry.players

    def add_player(
        self, name: str, ws: web.WebSocketResponse
    ) -> tuple[bool, str | None]:
        """Add a player to the game.

        Returns (success, error_code).
        """
        return self._player_registry.add_player(
            name=name,
            ws=ws,
            phase_value=self.phase.value,
            average_score_fn=self._player_registry.get_average_score,
        )

    def remove_player(self, name: str) -> None:
        """Remove a player from the game."""
        self._player_registry.remove_player(name)
        self._timers.pop(name, None)

    def get_player(self, name: str) -> PlayerSession | None:
        """Get player by name."""
        return self._player_registry.get_player(name)

    def get_player_by_ws(self, ws: web.WebSocketResponse) -> PlayerSession | None:
        """Get player by WebSocket."""
        return self._player_registry.get_player_by_ws(ws)

    def get_players(self) -> list[PlayerSession]:
        """Return list of all player sessions."""
        return list(self._player_registry.players.values())

    # ------------------------------------------------------------------
    # Game flow
    # ------------------------------------------------------------------

    def start_game(
        self,
        category: str | None = None,
        difficulty: str | None = None,
        num_rounds: int = 10,
    ) -> dict[str, Any]:
        """Start a new game session.

        Returns dict with game info on success.
        Raises ValueError on invalid state.
        """
        if self.phase != GamePhase.LOBBY:
            raise ValueError(ERR_GAME_ALREADY_STARTED)

        self.game_id = secrets.token_urlsafe(8)
        self.category = category
        self.difficulty = difficulty or DIFFICULTY_DEFAULT
        self.total_rounds = num_rounds
        self.round = 0

        # Load questions
        self._question_bank.load_all_categories()
        self._question_bank.reset(category=category, difficulty=difficulty)

        # Verify questions are available
        if not self._question_bank._queue:
            raise ValueError("No questions available for the selected category/difficulty")

        # Reset player scores
        for player in self._player_registry.players.values():
            player.reset_for_new_game()

        # Reset power-ups
        self._powerup_manager.reset()

        self._game_start_time = time.time()

        _LOGGER.info(
            "Game started: id=%s, category=%s, difficulty=%s, rounds=%d",
            self.game_id,
            category,
            difficulty,
            num_rounds,
        )

        return {
            "game_id": self.game_id,
            "total_rounds": self.total_rounds,
            "category": self.category,
            "difficulty": self.difficulty,
        }

    def start_next_question(self) -> Question | None:
        """Load the next question and transition to QUESTION_ACTIVE.

        Returns the Question or None if no more questions.
        """
        if self.phase not in (GamePhase.LOBBY, GamePhase.ANSWER_REVEAL):
            _LOGGER.warning("Cannot start question in phase %s", self.phase)
            return None

        # Check round limit
        if self.round >= self.total_rounds:
            self.end_game()
            return None

        question = self._question_bank.get_next_question(
            category=self.category, difficulty=self.difficulty
        )
        if question is None:
            _LOGGER.warning("No more questions available")
            self.end_game()
            return None

        self.round += 1
        self._current_question = question

        # Determine time limit from difficulty
        try:
            diff_enum = Difficulty(question.difficulty)
        except ValueError:
            diff_enum = Difficulty.MEDIUM
        self._round_duration = float(TIME_LIMITS.get(diff_enum, DEFAULT_ROUND_DURATION))
        self._round_start_time = time.monotonic()

        # Reset per-round state
        for player in self._player_registry.players.values():
            player.reset_round()
        self._powerup_manager.reset_round()

        # Create per-player timers
        self._timers.clear()
        for name in self._player_registry.players:
            self._timers[name] = QuestionTimer(self._round_duration)
            self._timers[name].start()

        # Randomly assign power-ups (one per round, random player)
        connected = [p for p in self._player_registry.players.values() if p.connected]
        if connected:
            import random
            lucky_player = random.choice(connected)
            powerup = self._powerup_manager.assign_random_powerup(lucky_player.name)
            _LOGGER.debug("Power-up %s assigned to %s", powerup.value, lucky_player.name)

        self.phase = GamePhase.QUESTION_ACTIVE
        self._round_summary = None

        # Start timer task for auto-evaluation
        self._cancel_timer_task()
        if self._hass is not None:
            self._timer_task = self._hass.async_create_task(
                self._round_timer_task()
            )
        else:
            # Fallback for non-HA contexts (testing)
            loop = asyncio.get_event_loop()
            self._timer_task = loop.create_task(self._round_timer_task())
            self._bg_tasks.add(self._timer_task)
            self._timer_task.add_done_callback(self._bg_tasks.discard)

        _LOGGER.info(
            "Round %d/%d started: %s (%.0fs)",
            self.round,
            self.total_rounds,
            question.id,
            self._round_duration,
        )
        return question

    def submit_answer(self, player_id: str, answer_index: int) -> AnswerResult | str:
        """Submit a player's answer for the current round.

        Returns AnswerResult on success, or an error code string.
        """
        if self.phase != GamePhase.QUESTION_ACTIVE:
            return ERR_GAME_NOT_STARTED

        player = self._player_registry.get_player(player_id)
        if player is None:
            return ERR_NOT_IN_GAME

        if player.submitted:
            return ERR_ALREADY_SUBMITTED

        timer = self._timers.get(player_id)
        if timer and timer.is_expired():
            return ERR_ROUND_EXPIRED

        # Record submission
        elapsed = timer.get_elapsed() if timer else 0.0
        player.submit_answer(answer_index, time.time())

        # Score immediately
        question = self._current_question
        if question is None:
            return ERR_GAME_NOT_STARTED

        correct = self._question_bank.validate_answer(question, answer_index)

        try:
            diff_enum = Difficulty(question.difficulty)
        except ValueError:
            diff_enum = Difficulty.MEDIUM

        double_active = self._powerup_manager.is_double_points_active(player_id)

        if correct:
            player.streak += 1
            player.answer_times.append(elapsed)
        else:
            player.streak = 0

        if player.streak > player.max_streak:
            player.max_streak = player.streak

        from custom_components.quizify.game.scoring import (
            BASE_POINTS, MAX_SPEED_BONUS, DIFFICULTY_MULTIPLIERS, get_streak_multiplier
        )

        points = calculate_round_score(
            correct=correct,
            elapsed=elapsed,
            time_limit=self._round_duration,
            difficulty=diff_enum,
            streak=player.streak,
            double_points_active=double_active,
        )

        # Calculate breakdown for client display
        speed_bonus = 0
        streak_bonus = 0
        diff_mult = DIFFICULTY_MULTIPLIERS.get(diff_enum, 1.0)
        if correct:
            time_fraction = max(0.0, 1.0 - elapsed / self._round_duration) if self._round_duration > 0 else 0.0
            speed_bonus = int(MAX_SPEED_BONUS * time_fraction)
            streak_mult = get_streak_multiplier(player.streak)
            streak_bonus = int((BASE_POINTS + speed_bonus) * diff_mult * (streak_mult - 1.0))

        player.round_score = points
        player.round_score_breakdown = {
            "speed_bonus": speed_bonus,
            "streak_bonus": streak_bonus,
            "difficulty_multiplier": diff_mult,
            "double_points": double_active,
        }
        player.score += points

        # Track hard question score
        if correct and diff_enum == Difficulty.HARD:
            player.hard_score += points

        result = AnswerResult(
            player_id=player_id,
            correct=correct,
            points_earned=points,
            new_streak=player.streak,
            new_total=player.score,
            speed_bonus=speed_bonus,
            streak_bonus=streak_bonus,
            difficulty_multiplier=diff_mult,
        )

        # Check if all players have submitted → auto-evaluate
        if self._player_registry.all_submitted():
            self._cancel_timer_task()
            self._do_evaluate_round()

        return result

    def evaluate_round(self) -> RoundSummary | None:
        """Manually trigger round evaluation (e.g. when timer expires)."""
        if self.phase != GamePhase.QUESTION_ACTIVE:
            return None
        return self._do_evaluate_round()

    def _do_evaluate_round(self) -> RoundSummary:
        """Internal: evaluate the round, build summary, transition to ANSWER_REVEAL."""
        question = self._current_question
        assert question is not None  # noqa: S101

        correct_answer = self._question_bank.get_correct_answer(question)

        # Score any players who didn't submit (they get 0, streak resets)
        # Also record round results and per-round scores for share cards / superlatives
        for player in self._player_registry.players.values():
            if not player.submitted:
                player.streak = 0
                player.record_round_result("timeout")
                player.round_scores.append(0)
            else:
                is_correct = self._question_bank.validate_answer(
                    question, player.current_answer or -1
                )
                player.record_round_result("correct" if is_correct else "wrong")
                player.round_scores.append(player.round_score)

        # Build per-player results
        results: list[AnswerResult] = []
        for player in self._player_registry.players.values():
            if not player.connected:
                continue
            results.append(
                AnswerResult(
                    player_id=player.name,
                    correct=player.submitted
                    and self._question_bank.validate_answer(
                        question, player.current_answer or -1
                    ),
                    points_earned=player.round_score,
                    new_streak=player.streak,
                    new_total=player.score,
                )
            )

        leaderboard = self.get_leaderboard()

        self._round_summary = RoundSummary(
            question=question,
            correct_answer=correct_answer,
            fun_fact=question.fun_fact,
            results=results,
            leaderboard=leaderboard,
        )

        self.phase = GamePhase.ANSWER_REVEAL

        _LOGGER.info("Round %d evaluated, transitioning to ANSWER_REVEAL", self.round)
        self._fire_broadcast("round_evaluated")

        return self._round_summary

    def end_game(self) -> dict[str, Any]:
        """End the game and transition to FINALE."""
        self._cancel_timer_task()
        self.phase = GamePhase.FINALE
        self._current_question = None

        podium = calculate_podium(self.get_players())

        # Build share cards
        share_data = build_share_data(self)

        finale_data = {
            "phase": GamePhase.FINALE.value,
            "leaderboard": self.get_leaderboard(),
            "podium": [
                {"name": p.name, "score": p.score, "rank": i + 1}
                for i, p in enumerate(podium)
            ],
            "total_rounds": self.round,
            "share_texts": share_data.get("share_texts", {}),
        }

        # Record to analytics
        self._record_analytics()

        _LOGGER.info("Game ended after %d rounds", self.round)
        self._fire_broadcast("game_ended")

        return finale_data

    def _record_analytics(self) -> None:
        """Record game to analytics service if available."""
        if not self._stats_service or not self.game_id:
            return

        duration = int(time.time() - (self._game_start_time or time.time()))
        players = {p.name: p.score for p in self.get_players()}

        async def _do_record() -> None:
            try:
                await self._stats_service.record_game(
                    game_id=self.game_id,
                    category=self.category,
                    difficulty=self.difficulty,
                    num_rounds=self.round,
                    players=players,
                    duration_seconds=duration,
                    started_at=int(self._game_start_time) if self._game_start_time else None,
                )
            except Exception as err:  # noqa: BLE001
                _LOGGER.warning("Failed to record analytics: %s", err)

        task = asyncio.ensure_future(_do_record())
        self._bg_tasks.add(task)
        task.add_done_callback(self._bg_tasks.discard)

    def reset_to_lobby(self) -> None:
        """Reset the game back to lobby state for a new game."""
        self._cancel_timer_task()
        self.phase = GamePhase.LOBBY
        self.game_id = None
        self.round = 0
        self._current_question = None
        self._round_summary = None
        self._timers.clear()
        self._powerup_manager.reset()

        for player in self._player_registry.players.values():
            player.reset_for_new_game()

    # ------------------------------------------------------------------
    # Power-ups
    # ------------------------------------------------------------------

    def use_powerup(self, player_id: str, target_id: str | None = None) -> PowerUpEffect | str:
        """Use the player's held power-up.

        Returns PowerUpEffect on success, or error code string.
        """
        if self.phase != GamePhase.QUESTION_ACTIVE:
            return ERR_INVALID_ACTION

        question = self._current_question
        if question is None:
            return ERR_INVALID_ACTION

        # Determine wrong answer indices for joker
        wrong_indices = [
            i for i, a in enumerate(question.answers) if not a.correct
        ]

        effect = self._powerup_manager.use_powerup(
            player_id=player_id,
            target_id=target_id,
            wrong_answer_indices=wrong_indices,
        )

        if effect is None:
            return ERR_INVALID_ACTION

        # Apply side-effects
        if effect.type == PowerUpType.FREEZE and target_id:
            player = self._player_registry.get_player(player_id)
            if player:
                player.freezes_used += 1
            target_timer = self._timers.get(target_id)
            if target_timer:
                target_timer.pause_for_player(FREEZE_DURATION)

        elif effect.type == PowerUpType.TIME_BOOST:
            own_timer = self._timers.get(player_id)
            if own_timer:
                own_timer.add_time(TIME_BOOST_DURATION)

        _LOGGER.info(
            "Power-up %s used by %s (target: %s)",
            effect.type.value,
            player_id,
            target_id,
        )

        return effect

    # ------------------------------------------------------------------
    # State access
    # ------------------------------------------------------------------

    def get_phase(self) -> GamePhase:
        """Return current game phase."""
        return self.phase

    def get_current_question(self) -> Question | None:
        """Return the current question, or None."""
        return self._current_question

    def get_leaderboard(self) -> list[dict[str, Any]]:
        """Return sorted leaderboard data."""
        players = sorted(
            self._player_registry.players.values(),
            key=lambda p: p.score,
            reverse=True,
        )
        return [
            {
                "rank": i + 1,
                "name": p.name,
                "score": p.score,
                "streak": p.streak,
                "connected": p.connected,
            }
            for i, p in enumerate(players)
        ]

    def get_round_summary(self) -> RoundSummary | None:
        """Return the last round summary."""
        return self._round_summary

    def get_state_snapshot(self) -> dict[str, Any]:
        """Build a full state snapshot for WebSocket serialization."""
        snapshot: dict[str, Any] = {
            "phase": self.phase.value,
            "game_id": self.game_id,
            "round": self.round,
            "total_rounds": self.total_rounds,
            "category": self.category,
            "difficulty": self.difficulty,
            "players": self._player_registry.get_players_state(),
            "leaderboard": self.get_leaderboard(),
        }

        if self.phase == GamePhase.QUESTION_ACTIVE and self._current_question:
            q = self._current_question
            snapshot["question"] = {
                "id": q.id,
                "text": q.question,
                "answers": [a.text for a in q.answers],
                "difficulty": q.difficulty,
                "category": q.category,
                "time_limit": self._round_duration,
            }

        if self.phase == GamePhase.ANSWER_REVEAL and self._round_summary:
            s = self._round_summary
            snapshot["round_summary"] = {
                "correct_answer": s.correct_answer.text,
                "fun_fact": s.fun_fact,
                "results": [
                    {
                        "player_id": r.player_id,
                        "correct": r.correct,
                        "points_earned": r.points_earned,
                        "new_streak": r.new_streak,
                        "new_total": r.new_total,
                    }
                    for r in s.results
                ],
            }

        if self.phase == GamePhase.FINALE:
            podium = calculate_podium(self.get_players())
            snapshot["podium"] = [
                {"name": p.name, "score": p.score, "rank": i + 1}
                for i, p in enumerate(podium)
            ]
            awards = compute_superlatives(self.get_players())
            if awards:
                snapshot["superlatives"] = [s.to_dict() for s in awards]

        return snapshot

    # ------------------------------------------------------------------
    # Broadcast / event dispatch
    # ------------------------------------------------------------------

    def set_broadcast_callback(
        self, callback: Callable[[dict[str, Any]], Awaitable[None]]
    ) -> None:
        """Set the callback used to broadcast state to connected clients."""
        self._broadcast_callback = callback

    def _fire_broadcast(self, event: str) -> None:
        """Fire a broadcast event via the callback if set."""
        if self._broadcast_callback is None:
            return
        payload = self.get_state_snapshot()
        payload["event"] = event
        task = asyncio.ensure_future(self._broadcast_callback(payload))
        self._bg_tasks.add(task)
        task.add_done_callback(self._bg_tasks.discard)

    # ------------------------------------------------------------------
    # Timer management
    # ------------------------------------------------------------------

    async def _round_timer_task(self) -> None:
        """Wait for the round duration then auto-evaluate."""
        try:
            await asyncio.sleep(self._round_duration)
            if self.phase == GamePhase.QUESTION_ACTIVE:
                _LOGGER.info("Round %d timer expired, auto-evaluating", self.round)
                self._do_evaluate_round()
        except asyncio.CancelledError:
            pass

    def _cancel_timer_task(self) -> None:
        """Cancel the running timer task if any."""
        if self._timer_task is not None:
            self._timer_task.cancel()
            self._timer_task = None
