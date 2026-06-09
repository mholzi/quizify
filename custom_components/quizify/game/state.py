"""Game state management for Quizify."""

from __future__ import annotations

import asyncio
import logging
import random
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
from .scoring import (
    BASE_POINTS,
    MAX_SPEED_BONUS,
    calculate_podium,
    calculate_round_score,
    get_streak_milestone_bonus,
    get_streak_multiplier,
)
from .timer import QuestionTimer
from .types import DIFFICULTY_MULTIPLIERS, TIME_LIMITS, Difficulty

if TYPE_CHECKING:
    from aiohttp import web

    from ..runtime import Runtime

_LOGGER = logging.getLogger(__name__)


class GamePhase(str, Enum):
    """Game phase states."""

    LOBBY = "LOBBY"
    QUESTION_ACTIVE = "QUESTION_ACTIVE"
    ANSWER_REVEAL = "ANSWER_REVEAL"
    FINALE = "FINALE"
    # PAUSED — admin-triggered pause during QUESTION_ACTIVE. Timer is
    # frozen; resume returns to QUESTION_ACTIVE with the remaining time
    # the player had before pause. Used both for explicit "Pause" button
    # and for graceful host-disconnect handling.
    PAUSED = "PAUSED"


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
    # Milestone bonus (0 unless this round's streak landed exactly on a
    # value in STREAK_MILESTONES). Surfaced separately so the client can
    # render a celebratory toast instead of folding it into the breakdown.
    milestone_bonus: int = 0
    milestone_streak: int = 0  # the streak level reached, e.g. 5


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

    def __init__(self, runtime: Runtime | None = None, entry_id: str = "") -> None:
        """Initialize game state."""
        self._runtime = runtime
        self._entry_id = entry_id

        # Core state
        self.game_id: str | None = None
        self.phase: GamePhase = GamePhase.LOBBY
        self.round: int = 0
        self.total_rounds: int = 10
        self.category: str | None = None
        self.difficulty: str = DIFFICULTY_DEFAULT
        self.language: str = "de"
        self.join_url: str | None = None
        # Optional URL of an audio file looped on the configured HA
        # media_player while waiting for players. None unless the user
        # configures one in the options flow; a missing/empty value means
        # "no lobby music" and the playback service stays inert.
        self.lobby_music_url: str | None = None

        # Sub-managers
        self._player_registry = PlayerRegistry()
        self._question_bank = QuestionBank()
        self._powerup_manager = PowerUpManager()

        # Load question history if a runtime is available (HA or standalone).
        if runtime is not None:
            history_path = runtime.data_dir / "question_history.json"
            self._question_bank.load_history(history_path)

        # Current round state
        self._current_question: Question | None = None
        self._timers: dict[str, QuestionTimer] = {}  # player_id → timer
        self._round_start_time: float | None = None
        self._round_duration: float = DEFAULT_ROUND_DURATION
        self._round_summary: RoundSummary | None = None

        # Optional admin-chosen timer override (seconds). When set,
        # overrides the difficulty-derived TIME_LIMITS lookup in
        # start_next_question. Cleared on reset_to_lobby/end_game.
        self._timer_override: int | None = None

        # Last-game settings snapshot for "Play again — same settings".
        # None until a game has been started at least once.
        self._last_settings: dict[str, Any] | None = None

        # Pause bookkeeping.
        self._paused_from: GamePhase | None = None
        self._paused_remaining: dict[str, float] = {}
        self._pause_reason: str | None = None

        # Broadcast callback — set by websocket layer
        self._broadcast_callback: Callable[[dict[str, Any]], Awaitable[None]] | None = None

        # State-change observers (HA sensor entities subscribe here so they
        # can push updates without polling). Pure callbacks, no async.
        self._state_callbacks: list[Callable[[], None]] = []

        # Analytics / stats service (injected from __init__.py)
        self._stats_service = None
        # Per-question stats sink (optional; standalone tests skip it).
        self._question_stats = None
        self._game_start_time: float | None = None

        # Cached finale data (computed once in end_game, cleared in reset_to_lobby)
        self._finale_podium: list | None = None
        self._finale_superlatives: list | None = None

        # Round shuffle state (owned here, not in WS handler).
        # `shuffle_map` is the "canonical" per-round shuffle used by the
        # admin/dashboard view and as a fallback for any code path that
        # doesn't know a player. `player_shuffles` is per-player so two
        # phones sitting next to each other see A/B/C in different
        # orders — anti-cheat against couch-neighbour collusion.
        self.shuffle_map: list[int] = []        # canonical: shuffled_pos -> original_index
        self.shuffled_answers: list[str] = []   # canonical answers in shuffled order
        self.player_shuffles: dict[str, list[int]] = {}  # name -> shuffled_pos -> original_index

    # ------------------------------------------------------------------
    # Player registry delegation
    # ------------------------------------------------------------------

    @property
    def players(self) -> dict[str, PlayerSession]:
        """Player dict — delegated to PlayerRegistry."""
        return self._player_registry.players

    @property
    def leader(self) -> PlayerSession | None:
        """Current top-scoring player, or None if no players yet."""
        players = self._player_registry.players
        if not players:
            return None
        return max(players.values(), key=lambda p: p.score)

    # ------------------------------------------------------------------
    # State change observers (HA sensor push)
    # ------------------------------------------------------------------

    def register_state_callback(self, cb: Callable[[], None]) -> None:
        """Subscribe to state-change notifications (used by sensor entities)."""
        if cb not in self._state_callbacks:
            self._state_callbacks.append(cb)

    def unregister_state_callback(self, cb: Callable[[], None]) -> None:
        """Unsubscribe a previously-registered observer."""
        try:
            self._state_callbacks.remove(cb)
        except ValueError:
            pass

    def _notify_state_callbacks(self) -> None:
        """Fire all registered state observers. Keeps the broadcast pipeline
        alive if one observer raises, but logs the full traceback so a
        programmer error in a callback surfaces loudly instead of being
        reduced to a one-line message."""
        for cb in list(self._state_callbacks):
            try:
                cb()
            except Exception:  # noqa: BLE001
                _LOGGER.exception("State callback raised")

    def add_player(
        self, name: str, ws: web.WebSocketResponse
    ) -> tuple[bool, str | None]:
        """Add a player to the game.

        Returns (success, error_code).
        """
        result = self._player_registry.add_player(
            name=name,
            ws=ws,
            phase_value=self.phase.value,
            average_score_fn=self._player_registry.get_average_score,
        )
        success, _err = result
        # If the player joined mid-round, give them a timer that tracks the
        # round's remaining time. Without this the tick loop's "all timers
        # missing or expired → break" condition treats them as already done
        # and the round evaluates ~1s after start when the late-joiner is
        # the ONLY connected player (the admin-self-join + redirect flow).
        # Late joiners can also still answer the in-flight question this way.
        if (
            success
            and self.phase == GamePhase.QUESTION_ACTIVE
            and self._round_start_time is not None
            and name not in self._timers
        ):
            elapsed = time.monotonic() - self._round_start_time
            remaining = max(0.5, self._round_duration - elapsed)
            timer = QuestionTimer(remaining)
            timer.start()
            self._timers[name] = timer
        if success:
            self._notify_state_callbacks()
        return result

    def remove_player(self, name: str) -> None:
        """Remove a player from the game."""
        self._player_registry.remove_player(name)
        self._timers.pop(name, None)
        self._notify_state_callbacks()

    def clear_all_players(self) -> None:
        """Drop every player from the registry.

        Used by reset_game so the admin gets a truly empty lobby. The
        ``reset_to_lobby`` path intentionally keeps players (for the
        finale's "Play again — same settings" flow); reset_game is the
        explicit "wipe everyone" action.
        """
        self._player_registry.reset()
        self._timers.clear()
        self._notify_state_callbacks()

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
        categories: list[str] | None = None,
        difficulty: str | None = None,
        num_rounds: int = 10,
        language: str | None = None,
        timer_duration: int | None = None,
    ) -> dict[str, Any]:
        """Start a new game session.

        Pass ``categories`` (list of slugs) for multi-category mode.
        Pass ``category`` (single slug) for single-category mode.
        Pass neither for mixed (all packs).

        Returns dict with game info on success.
        Raises ValueError on invalid state.
        """
        if self.phase != GamePhase.LOBBY:
            raise ValueError(ERR_GAME_ALREADY_STARTED)

        self.game_id = secrets.token_urlsafe(8)
        self.category = category
        self.categories = categories or []
        self.difficulty = difficulty or DIFFICULTY_DEFAULT
        self.language = language or "de"
        self.total_rounds = num_rounds
        self.round = 0
        self._timer_override = timer_duration

        # Persist for "Play again — same settings" (one-tap rematch).
        # Snapshot here so a later reset_to_lobby keeps these and
        # play_again can reuse them without re-prompting the admin.
        self._last_settings = {
            "category": category,
            "categories": list(categories) if categories else None,
            "difficulty": difficulty,
            "num_rounds": num_rounds,
            "language": self.language,
            "timer_duration": timer_duration,
        }

        # Load questions
        self._question_bank.load_all_categories()
        self._question_bank.reset(
            category=category,
            categories=categories,
            difficulty=difficulty,
            language=self.language,
        )

        # Verify questions are available
        if not self._question_bank._queue:
            raise ValueError("No questions available for the selected category/difficulty")

        # Drop players who are no longer connected before resetting
        # scores. Players who disconnected during the previous game
        # would otherwise persist in the new lobby/leaderboard with
        # zeroed scores, cluttering the player list and showing as
        # ghosts. Connected players (incl. admin-as-player whose tab
        # still has a live WS) are kept and just have scores reset.
        # NB: list(...) snapshot — we mutate the dict during iteration.
        stale_names = [
            name
            for name, player in list(self._player_registry.players.items())
            if not player.connected
        ]
        for name in stale_names:
            self._player_registry.remove_player(name)

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

        self._notify_state_callbacks()
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

        # Record this question as shown for history tracking
        self._question_bank.record_shown(question.id)

        # Determine time limit. Admin-chosen timer override (set in
        # start_game) wins over the difficulty-derived default — the
        # picker in the admin UI lets the host pick 20/30/45s up front.
        if self._timer_override is not None:
            self._round_duration = float(self._timer_override)
        else:
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
            lucky_player = random.choice(connected)
            powerup = self._powerup_manager.assign_random_powerup(lucky_player.name)
            _LOGGER.debug("Power-up %s assigned to %s", powerup.value, lucky_player.name)

        self.phase = GamePhase.QUESTION_ACTIVE
        self._round_summary = None

        _LOGGER.info(
            "Round %d/%d started: %s (%.0fs)",
            self.round,
            self.total_rounds,
            question.id,
            self._round_duration,
        )
        self._notify_state_callbacks()
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
        player.last_elapsed = elapsed

        # Score immediately
        question = self._current_question
        if question is None:
            return ERR_GAME_NOT_STARTED

        correct = self._question_bank.validate_answer(question, answer_index)
        # Cache the result so _do_evaluate_round doesn't need to re-validate
        # (#145 in code-review issues — also closes the `current_answer or -1`
        # bug that misclassified players who picked answer index 0).
        player.last_answer_correct = correct

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

        # Wager override (gameplay idea #3, Jeopardy-style final round).
        # On the final round, if the player submitted a wager (0-100%
        # of their pre-round score), it REPLACES the normal scoring:
        # right answer adds the wager value, wrong subtracts. Speed/
        # streak/difficulty multipliers are ignored — the wager IS the
        # bet. We snapshot the player's score BEFORE adding points so
        # the wager is computed against what they bet on, not the
        # post-bet total.
        wager_used: int | None = None
        if (
            self.round == self.total_rounds
            and player.wager is not None
        ):
            bank = max(0, player.score)
            wager_pts = int(bank * player.wager / 100)
            wager_used = wager_pts
            if correct:
                points = wager_pts
            else:
                # Lose the wager — but never go below zero so a player
                # can't be priced out of an existing rematch flow.
                points = -min(wager_pts, bank)
            # Clear bonuses since the wager overrides them.
            speed_bonus = 0
            streak_bonus = 0

        # Streak milestone bonus — discrete spike awarded the round the
        # streak EXACTLY equals a milestone value (3, 5, 10, 15, 20, 25).
        # Wager rounds skip this so the bet stays the only thing in play.
        milestone_bonus = 0
        if correct and wager_used is None:
            milestone_bonus = get_streak_milestone_bonus(player.streak)
            if milestone_bonus:
                points += milestone_bonus
                player.streak_milestone_bonus_total += milestone_bonus
                player.streak_milestones_hit += 1

        player.round_score = points
        player.round_score_breakdown = {
            "speed_bonus": speed_bonus,
            "streak_bonus": streak_bonus,
            "difficulty_multiplier": diff_mult,
            "double_points": double_active,
            "wager": wager_used,
            "milestone_bonus": milestone_bonus,
        }
        player.score += points
        if player.score < 0:
            player.score = 0

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
            milestone_bonus=milestone_bonus,
            milestone_streak=player.streak if milestone_bonus else 0,
        )

        # Check if all players have submitted → auto-evaluate.
        # Route through the guarded `evaluate_round()` (not directly to
        # `_do_evaluate_round`) so the `_round_summary is not None` check
        # closes the race with the timer-expiry path. (#143 in code review.)
        if self._player_registry.all_submitted():
            self.evaluate_round()

        return result

    def evaluate_round(self) -> RoundSummary | None:
        """Manually trigger round evaluation (e.g. when timer expires)."""
        if self.phase != GamePhase.QUESTION_ACTIVE:
            return None
        # Guard against double evaluation (race between timer tick and all_submitted)
        if self._round_summary is not None:
            return self._round_summary
        return self._do_evaluate_round()

    def _do_evaluate_round(self) -> RoundSummary:
        """Internal: evaluate the round, build summary, transition to ANSWER_REVEAL."""
        question = self._current_question
        if question is None:
            # Hard invariant: this method is only called when a round is
            # active. If the question is gone, the caller violated the
            # state machine. Raise so the bug is visible instead of
            # silently returning None and crashing downstream. (#144.)
            raise RuntimeError(
                "_do_evaluate_round called with no active question"
            )

        correct_answer = self._question_bank.get_correct_answer(question)

        # Build per-player correctness from cached `last_answer_correct`
        # set during submit_answer (#145). No second validate_answer call,
        # and no `current_answer or -1` bug that misclassified answer
        # index 0 as a timeout.
        player_correct: dict[str, bool] = {}
        for player in self._player_registry.players.values():
            if not player.submitted:
                player.streak = 0
                player.record_round_result("timeout")
                player.round_scores.append(0)
                player_correct[player.name] = False
            else:
                is_correct = player.last_answer_correct
                player_correct[player.name] = is_correct
                player.record_round_result("correct" if is_correct else "wrong")
                player.round_scores.append(player.round_score)
            # Count this round toward the player's "rounds played" tally
            # regardless of timeout/answer — what matters for the late-joiner
            # average is that they've experienced a scored round.
            player.rounds_played += 1

        # Build per-player results
        results: list[AnswerResult] = []
        for player in self._player_registry.players.values():
            if not player.connected:
                continue
            results.append(
                AnswerResult(
                    player_id=player.name,
                    correct=player_correct.get(player.name, False),
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

        # Record this round into the per-question stats. Only count
        # players who actually submitted — timeouts shouldn't blame the
        # question for the player being away. ``last_elapsed`` was
        # captured against the round timer in submit_answer.
        if self._question_stats is not None:
            try:
                submitted_results = [
                    (player_correct.get(p.name, False), p.last_elapsed)
                    for p in self._player_registry.players.values()
                    if p.submitted
                ]
                self._question_stats.record_round(question.id, submitted_results)
            except Exception:  # noqa: BLE001
                _LOGGER.exception("Failed to record question stats")

        _LOGGER.info("Round %d evaluated, transitioning to ANSWER_REVEAL", self.round)
        self._fire_broadcast("round_evaluated")

        return self._round_summary

    # ------------------------------------------------------------------
    # Pause / resume
    # ------------------------------------------------------------------
    #
    # PAUSED freezes the per-player timers and remembers the phase to
    # resume back into. Only QUESTION_ACTIVE is meaningfully pausable —
    # pausing in LOBBY / ANSWER_REVEAL / FINALE is a no-op so the admin
    # button can be wired unconditionally without phase checks in JS.

    def pause(self, reason: str = "admin_paused") -> bool:
        """Pause the game. Returns True if pause happened, False if no-op."""
        if self.phase != GamePhase.QUESTION_ACTIVE:
            return False
        self._paused_from = GamePhase.QUESTION_ACTIVE
        self._pause_reason = reason
        # Snapshot remaining time per player and freeze timers in place.
        # On resume we'll create fresh timers with the saved remaining.
        self._paused_remaining = {}
        for name, timer in self._timers.items():
            self._paused_remaining[name] = max(0.0, timer.get_remaining())
        self._timers.clear()
        self.phase = GamePhase.PAUSED
        _LOGGER.info("Game paused (reason=%s)", reason)
        self._notify_state_callbacks()
        return True

    def resume(self) -> bool:
        """Resume a paused game. Returns True if resume happened."""
        if self.phase != GamePhase.PAUSED:
            return False
        # Restore timers with the remaining time they had at pause.
        # Late-joiners during PAUSED won't be in _paused_remaining and
        # get a fresh full-round timer here.
        full = self._round_duration
        for name in list(self._player_registry.players):
            remaining = self._paused_remaining.get(name, full)
            timer = QuestionTimer(remaining)
            timer.start()
            self._timers[name] = timer
        self._paused_remaining = {}
        self._pause_reason = None
        self.phase = self._paused_from or GamePhase.QUESTION_ACTIVE
        self._paused_from = None
        _LOGGER.info("Game resumed")
        self._notify_state_callbacks()
        return True

    def get_pause_reason(self) -> str | None:
        """Return the current pause reason (or None if not paused)."""
        return getattr(self, "_pause_reason", None)

    def end_game(self) -> dict[str, Any]:
        """End the game and transition to FINALE."""
        self.phase = GamePhase.FINALE
        self._current_question = None

        # Cache podium and superlatives once so get_state_snapshot() can reuse them
        self._finale_podium = calculate_podium(self.get_players())
        self._finale_superlatives = compute_superlatives(self.get_players())

        podium = self._finale_podium

        finale_data = {
            "phase": GamePhase.FINALE.value,
            "leaderboard": self.get_leaderboard(),
            "podium": [
                {"name": p.name, "score": p.score, "rank": i + 1}
                for i, p in enumerate(podium)
            ],
            "total_rounds": self.round,
        }

        # Record to analytics
        self._record_analytics()

        # Save question history so next game prioritises least-recently-shown
        self._question_bank.flush_shown_history()

        # Persist any per-question stats accumulated this game.
        if self._question_stats is not None:
            async def _save_qs() -> None:
                try:
                    await self._question_stats.save_if_dirty()
                except Exception:  # noqa: BLE001
                    _LOGGER.exception("Failed to save question stats")

            if self._runtime is not None:
                self._runtime.create_task(_save_qs())
            else:
                asyncio.ensure_future(_save_qs())

        _LOGGER.info("Game ended after %d rounds", self.round)
        self._fire_broadcast("game_ended")

        return finale_data

    def _record_analytics(self) -> None:
        """Record game to analytics service if available."""
        if not self._stats_service or not self.game_id:
            return

        duration = int(time.time() - (self._game_start_time or time.time()))
        players = {p.name: p.score for p in self.get_players()}
        # Per-player details feed the all-time rollup (best streak, milestone
        # hits). Keep this map narrow so the analytics module never sees the
        # full PlayerSession object — easier to evolve independently.
        player_details = {
            p.name: {
                "best_streak": p.max_streak,
                "streak_milestones_hit": p.streak_milestones_hit,
            }
            for p in self.get_players()
        }

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
                    player_details=player_details,
                )
            except Exception:  # noqa: BLE001
                _LOGGER.exception("Failed to record analytics")

        if self._runtime is not None:
            self._runtime.create_task(_do_record())
        else:
            asyncio.ensure_future(_do_record())

    def reset_to_lobby(self) -> None:
        """Reset the game back to lobby state for a new game."""
        self.phase = GamePhase.LOBBY
        self.game_id = None
        self.round = 0
        self._current_question = None
        self._round_summary = None
        self._timers.clear()
        self._powerup_manager.reset()
        self._finale_podium = None
        self._finale_superlatives = None
        self.shuffle_map = []
        self.shuffled_answers = []
        self._timer_override = None

        for player in self._player_registry.players.values():
            player.reset_for_new_game()

        self._notify_state_callbacks()

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

        held = self._powerup_manager.get_powerup(player_id)

        # Post-submit no-op gate. Joker/Double/TimeBoost only help BEFORE
        # the source locks in their answer — activating them afterward
        # consumes the power-up for nothing (live-test feedback). Reject
        # early so the inventory survives for a future round.
        source_player = self._player_registry.get_player(player_id)
        if held in (
            PowerUpType.JOKER,
            PowerUpType.DOUBLE_POINTS,
            PowerUpType.TIME_BOOST,
        ) and source_player and source_player.submitted:
            return ERR_INVALID_ACTION

        # Resolve target for opponent-targeted power-ups BEFORE consuming
        # inventory. The player UI passes an explicit target via picker, but
        # we also accept null and pick a random active opponent (safety net
        # for older clients / single-tap flow). If no opponent is connected
        # at all, reject so the power-up stays in the inventory instead of
        # silently no-op'ing.
        if held in (PowerUpType.FREEZE, PowerUpType.STEAL):
            target_player = (
                self._player_registry.get_player(target_id) if target_id else None
            )
            if not target_id or target_id == player_id or not target_player or not target_player.is_active:
                # For FREEZE the random fallback should also skip already-submitted
                # players so the pause is actually useful (otherwise freezing a
                # locked-in opponent burns the power-up for nothing).
                opponents = [
                    name
                    for name, p in self._player_registry.players.items()
                    if name != player_id and p.is_active
                    and (held != PowerUpType.FREEZE or not p.submitted)
                ]
                if not opponents:
                    return ERR_INVALID_ACTION
                target_id = random.choice(opponents)

        # FREEZE: explicit-target submitted-check (random fallback already
        # filters submitted players above).
        if held == PowerUpType.FREEZE and target_id:
            target_check = self._player_registry.get_player(target_id)
            if target_check and target_check.submitted:
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

        # Count every successful power-up use for the finale "POWER-UPS GENUTZT"
        # stat. Done here (not per type below) so all four types contribute.
        source_player = self._player_registry.get_player(player_id)
        if source_player:
            source_player.powerups_used += 1

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

        elif effect.type == PowerUpType.STEAL and target_id:
            # Steal half of target's current round score (applied at reveal).
            # The target was validated as an active opponent just above and
            # use_powerup is synchronous, so this re-fetch normally succeeds —
            # but make the invariant explicit: if the target is somehow gone,
            # return an error rather than broadcasting a hollow STEAL effect
            # (0 stolen points) that animates a steal that didn't happen (#167).
            target_player = self._player_registry.get_player(target_id)
            source_player = self._player_registry.get_player(player_id)
            if not target_player or not source_player:
                return ERR_INVALID_ACTION
            stolen = target_player.round_score // 2
            target_player.round_score = max(0, target_player.round_score - stolen)
            target_player.score = max(0, target_player.score - stolen)
            source_player.round_score += stolen
            source_player.score += stolen
            effect.stolen_points = stolen

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

    def set_round_shuffle(self, shuffle_map: list[int], shuffled_answers: list[str]) -> None:
        """Store the canonical shuffle mapping for the current round."""
        self.shuffle_map = shuffle_map
        self.shuffled_answers = shuffled_answers

    def set_player_shuffle(self, player_name: str, shuffle_map: list[int]) -> None:
        """Store a per-player shuffle so each phone sees a different
        A/B/C ordering. The submit_answer path uses this to map the
        player's shuffled index back to the original answer index."""
        self.player_shuffles[player_name] = shuffle_map

    def get_player_shuffle(self, player_name: str) -> list[int]:
        """Return the player's shuffle, falling back to canonical."""
        return self.player_shuffles.get(player_name) or self.shuffle_map

    def clear_player_shuffles(self) -> None:
        """Wipe per-player shuffles. Called at round start."""
        self.player_shuffles = {}

    @property
    def round_duration(self) -> float:
        """Public accessor for current round duration."""
        return self._round_duration

    def get_player_timer(self, player_name: str):
        """Return the authoritative QuestionTimer for a player, or None.

        Exposed so the WebSocket handler can broadcast per-player remaining
        time (so time-boost and freeze are visible on the player's UI, #4).
        """
        return self._timers.get(player_name)

    def get_player_powerup(self, player_name: str):
        """Get the power-up held by a player."""
        return self._powerup_manager.get_powerup(player_name)

    def get_phase(self) -> GamePhase:
        """Return current game phase."""
        return self.phase

    def get_current_question(self) -> Question | None:
        """Return the current question, or None."""
        return self._current_question

    def get_leaderboard(self) -> list[dict[str, Any]]:
        """Return sorted leaderboard data.

        NB: ``is_admin`` is required so the client can determine whether
        the current viewer is the admin (and thus should see the
        "Start New Game" / "Next Round" buttons). It was missing from
        this serializer in earlier versions and produced a chronic
        admin-lockout in the FINALE state — when a game-state snapshot
        was sent on reconnect the leaderboard had no admin marker, so
        the player-end client gated the controls off. See v1.1.4 notes.
        """
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
                "is_admin": p.is_admin,
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
            # The player client uses this to sync its UI locale with the
            # game language (player-core.js handleGameState). Without it
            # the UI stayed at the browser locale (e.g., German chrome
            # over English questions if the host picked EN but the guest's
            # phone is DE). Live-test Mai-27.
            "language": self.language,
            "players": self._player_registry.get_players_state(),
            "leaderboard": self.get_leaderboard(),
        }

        if self.phase == GamePhase.QUESTION_ACTIVE and self._current_question:
            q = self._current_question
            # Calculate time remaining for mid-round joiners
            elapsed = time.monotonic() - self._round_start_time if self._round_start_time else 0.0
            remaining = max(0.0, self._round_duration - elapsed)
            snapshot["question"] = {
                "id": q.id,
                "text": q.question,
                "answers": [a.text for a in q.answers],
                "difficulty": q.difficulty,
                "category": q.category,
                "time_limit": self._round_duration,
                "time_remaining": round(remaining, 1),
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
            # Use cached values computed once in end_game()
            podium = self._finale_podium or calculate_podium(self.get_players())
            snapshot["podium"] = [
                {"name": p.name, "score": p.score, "rank": i + 1}
                for i, p in enumerate(podium)
            ]
            awards = self._finale_superlatives if self._finale_superlatives is not None else compute_superlatives(self.get_players())
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
        # Push sensor updates first — synchronous, cheap, and means HA
        # entities reflect the new state even if the WS broadcast races.
        self._notify_state_callbacks()
        if self._broadcast_callback is None:
            return
        payload = self.get_state_snapshot()
        payload["event"] = event
        coro = self._broadcast_callback(payload)
        if self._runtime is not None:
            self._runtime.create_task(coro)
        else:
            asyncio.ensure_future(coro)


