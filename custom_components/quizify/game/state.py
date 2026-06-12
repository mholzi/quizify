"""Game state management for Quizify."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import random
import secrets
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from ..const import (
    DEFAULT_ROUND_DURATION,
    DIFFICULTY_AUTO,
    DIFFICULTY_AUTO_START,
    DIFFICULTY_DEFAULT,
    ERR_ALREADY_SUBMITTED,
    ERR_FROZEN,
    ERR_GAME_ALREADY_STARTED,
    ERR_GAME_NOT_STARTED,
    ERR_INVALID_ACTION,
    ERR_NO_QUESTIONS_REMAINING,
    ERR_NOT_IN_GAME,
    ERR_ROUND_EXPIRED,
)
from .calibration import GroupCalibrator
from .highlights import compute_superlatives
from .phase_controller import GamePhase, PhaseController
from .player import PlayerSession
from .player_registry import PlayerRegistry
from .powerups import (
    FREEZE_DURATION,
    TIME_BOOST_DURATION,
    PowerUpEffect,
    PowerUpManager,
    PowerUpType,
)
from .questions import Answer, Question, QuestionBank
from .scoring import (
    calculate_podium,
)
from .scoring_engine import ScoringEngine
from .timer import QuestionTimer
from .types import TIME_LIMITS, Difficulty

if TYPE_CHECKING:
    from aiohttp import web

    from ..runtime import Runtime

_LOGGER = logging.getLogger(__name__)

# GamePhase moved to phase_controller (issue #188) but is re-exported here so
# the many `from .state import GamePhase` / `from .game.state import GamePhase`
# call sites across the integration keep working unchanged.
__all__ = ["AnswerResult", "GamePhase", "QuizifyGameState", "RoundSummary"]


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

        # Phase + per-question timing state machine (issue #188). Owns the
        # authoritative phase value, the per-player timers, round-timing
        # bookkeeping and pause/resume. This class delegates its ``phase``,
        # ``_timers``, ``_round_*`` and ``_paused_*`` attributes to it via the
        # properties below so behaviour is unchanged for every caller/test.
        self._phase_controller = PhaseController(
            players_fn=lambda: list(self._player_registry.players)
        )

        # Core state
        self.game_id: str | None = None
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
        # Stateless scoring engine — owns the pure points/breakdown/wager/
        # milestone arithmetic that submit_answer applies (issue #184).
        self._scoring_engine = ScoringEngine()

        # Bind the question-history path if a runtime is available (HA or
        # standalone). The actual disk READ is deferred to
        # ``async_load_history`` so __init__ never blocks the event loop on
        # the read path (issue #222); callers that don't await it (bare unit
        # tests, standalone) simply start with an empty in-memory history.
        if runtime is not None:
            history_path = runtime.data_dir / "question_history.json"
            self._question_bank.set_history_path(history_path)

        # Group-level adaptive difficulty (#40). Only active when the host
        # picks the "auto" difficulty mode; None for fixed easy/medium/hard.
        self._calibrator: GroupCalibrator | None = None

        # Current round state. Per-player timers and round-timing live in the
        # PhaseController (exposed via the delegating properties below).
        self._current_question: Question | None = None
        self._round_summary: RoundSummary | None = None

        # Optional admin-chosen timer override (seconds). When set,
        # overrides the difficulty-derived TIME_LIMITS lookup in
        # start_next_question. Cleared on reset_to_lobby/end_game.
        self._timer_override: int | None = None

        # Last-game settings snapshot for "Play again — same settings".
        # None until a game has been started at least once.
        self._last_settings: dict[str, Any] | None = None

        # Broadcast callback — set by websocket layer
        self._broadcast_callback: (
            Callable[[dict[str, Any]], Awaitable[None]] | None
        ) = None

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
        self._finale_data: dict[str, Any] | None = None

        # Active LightningRound (issue #42), or None when not in a lightning
        # round. Owns its own questions/scores/recap; this class only holds
        # the reference + phase so the WS layer can route to it.
        self._lightning = None  # type: ignore[assignment]

        # True between start_lightning_round() and begin_lightning_questions():
        # the intro splash ("Bolt Burst", issue #201) is on screen and the
        # first question has not been broadcast yet — the admin's Start
        # control advances out of it.
        self._lightning_splash_pending: bool = False

        # Round shuffle state (owned here, not in WS handler).
        # `shuffle_map` is the "canonical" per-round shuffle used by the
        # admin/dashboard view and as a fallback for any code path that
        # doesn't know a player. `player_shuffles` is per-player so two
        # phones sitting next to each other see A/B/C in different
        # orders — anti-cheat against couch-neighbour collusion.
        # canonical: shuffled_pos -> original_index
        self.shuffle_map: list[int] = []
        # canonical answers in shuffled order
        self.shuffled_answers: list[str] = []
        # name -> shuffled_pos -> original_index
        self.player_shuffles: dict[str, list[int]] = {}

    # ------------------------------------------------------------------
    # Phase / timing delegation (issue #188)
    # ------------------------------------------------------------------
    #
    # These attributes are owned by ``self._phase_controller`` but exposed here
    # under their original names so every caller, sensor and test that reads or
    # writes ``state.phase`` / ``state._timers`` / ``state._round_*`` /
    # ``state._paused_*`` keeps working with byte-for-byte identical behaviour.

    @property
    def phase(self) -> GamePhase:
        """Current game phase — owned by the PhaseController."""
        return self._phase_controller.phase

    @phase.setter
    def phase(self, value: GamePhase) -> None:
        self._phase_controller.phase = value

    @property
    def _timers(self) -> dict[str, QuestionTimer]:
        return self._phase_controller.timers

    @_timers.setter
    def _timers(self, value: dict[str, QuestionTimer]) -> None:
        self._phase_controller.timers = value

    @property
    def _round_start_time(self) -> float | None:
        return self._phase_controller.round_start_time

    @_round_start_time.setter
    def _round_start_time(self, value: float | None) -> None:
        self._phase_controller.round_start_time = value

    @property
    def _round_duration(self) -> float:
        return self._phase_controller.round_duration

    @_round_duration.setter
    def _round_duration(self, value: float) -> None:
        self._phase_controller.round_duration = value

    @property
    def _paused_from(self) -> GamePhase | None:
        return self._phase_controller.paused_from

    @_paused_from.setter
    def _paused_from(self, value: GamePhase | None) -> None:
        self._phase_controller.paused_from = value

    @property
    def _paused_remaining(self) -> dict[str, float]:
        return self._phase_controller.paused_remaining

    @_paused_remaining.setter
    def _paused_remaining(self, value: dict[str, float]) -> None:
        self._phase_controller.paused_remaining = value

    @property
    def _pause_reason(self) -> str | None:
        return self._phase_controller.pause_reason

    @_pause_reason.setter
    def _pause_reason(self, value: str | None) -> None:
        self._phase_controller.pause_reason = value

    @property
    def last_settings(self) -> dict[str, Any] | None:
        """Settings of the most recent start_game, or None if never started.

        Used by the one-tap rematch path to restart with the previous game's
        configuration instead of re-prompting the admin.
        """
        return self._last_settings

    @property
    def question_bank(self) -> QuestionBank:
        """The game's QuestionBank.

        Public accessor so HTTP handlers (views.py) don't reach into the
        private ``_question_bank`` attribute (issue #312).
        """
        return self._question_bank

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
        with contextlib.suppress(ValueError):
            self._state_callbacks.remove(cb)

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
        # round's remaining time (handled by the PhaseController, which no-ops
        # outside QUESTION_ACTIVE / when the player already has a timer).
        if success:
            self._phase_controller.add_late_joiner_timer(name)
            self._notify_state_callbacks()
        return result

    def remove_player(self, name: str) -> None:
        """Remove a player from the game."""
        self._player_registry.remove_player(name)
        self._phase_controller.drop_timer(name)
        self._notify_state_callbacks()

    def clear_all_players(self) -> None:
        """Drop every player from the registry.

        Used by reset_game so the admin gets a truly empty lobby. The
        ``reset_to_lobby`` path intentionally keeps players (for the
        finale's "Play again — same settings" flow); reset_game is the
        explicit "wipe everyone" action.
        """
        self._player_registry.reset()
        self._phase_controller.clear_timers()
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

    def has_other_admin(self, name: str) -> bool:
        """Return True if a player other than ``name`` already is admin.

        Enforces the single-admin invariant in the admin-claim path.
        """
        return self._player_registry.has_other_admin(name)

    def get_admin(self) -> PlayerSession | None:
        """Return the current admin player (single-admin invariant), if any."""
        return self._player_registry.get_admin()

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

        # Group-level adaptive difficulty (#40). Only the explicit "auto" mode
        # opts in; any fixed difficulty the host pinned (easy/medium/hard) is
        # honoured verbatim and never calibrated. When auto, the queue is built
        # across ALL difficulties (no per-difficulty filter) so we can serve the
        # calibrated target each round.
        if self.difficulty == DIFFICULTY_AUTO:
            self._calibrator = GroupCalibrator(
                start=Difficulty(DIFFICULTY_AUTO_START)
            )
            queue_difficulty: str | None = None
        else:
            self._calibrator = None
            queue_difficulty = difficulty

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

        # Load questions. The bank is preloaded off the event loop at
        # async_setup_entry (#258), so this is a guaranteed cache hit (guarded
        # by _loaded) rather than a synchronous ~2 MB disk read on the loop.
        self._question_bank.load_all_categories()
        self._question_bank.reset(
            category=category,
            categories=categories,
            difficulty=queue_difficulty,
            language=self.language,
        )

        # Verify questions are available. Raise with the dedicated
        # ERR_NO_QUESTIONS_REMAINING code (#308) so the handler can surface the
        # right error — an empty pack is NOT "game already started". The human
        # message stays as the exception detail for logs.
        if not self._question_bank._queue:
            raise ValueError(ERR_NO_QUESTIONS_REMAINING)

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

        if self._calibrator is not None:
            # Group-level adaptive ("auto") mode: serve the calibrated target
            # difficulty for this round. The calibrator was fed each completed
            # round's group correct-rate in _do_evaluate_round; its target only
            # ever steps one rung at a time and stays put until enough rounds of
            # signal have accumulated, so this is bounded and smooth.
            target = self._calibrator.current_target
            question = self._question_bank.get_next_question_at_difficulty(
                target.value
            )
        else:
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
            round_duration = float(self._timer_override)
        else:
            try:
                diff_enum = Difficulty(question.difficulty)
            except ValueError:
                diff_enum = Difficulty.MEDIUM
            round_duration = float(TIME_LIMITS.get(diff_enum, DEFAULT_ROUND_DURATION))

        # Reset per-round state
        for player in self._player_registry.players.values():
            player.reset_round()
        self._powerup_manager.reset_round()

        # Stamp round timing + create+start per-player timers (PhaseController).
        self._phase_controller.begin_round(round_duration)

        # Randomly assign power-ups (one per round, random player)
        connected = [p for p in self._player_registry.players.values() if p.connected]
        if connected:
            lucky_player = random.choice(connected)
            powerup = self._powerup_manager.assign_random_powerup(lucky_player.name)
            _LOGGER.debug(
                "Power-up %s assigned to %s", powerup.value, lucky_player.name
            )

        self._phase_controller.enter_question_active()
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

        # Freeze lockout (#300): a frozen target cannot submit until the freeze
        # window expires. The round clock keeps running underneath (the freeze
        # does NOT pause get_remaining), so the lockout costs the target real
        # answer time. Reject the submission while still frozen; once the window
        # passes the player may submit normally (if the timer hasn't expired).
        if timer and timer.is_frozen():
            _LOGGER.debug(
                "Rejecting submit from frozen player %s (%.1fs of freeze left)",
                player_id,
                timer.frozen_remaining(),
            )
            return ERR_FROZEN

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

        # Delegate the pure arithmetic (base/speed/difficulty/streak scoring,
        # the final-round wager override, and the streak-milestone spike) to
        # the stateless ScoringEngine (#184). It mutates nothing; this method
        # keeps ownership of applying the result to the player. `player.streak`
        # was already updated above, and `player.score` is still the pre-round
        # total here — exactly the inputs the wager bets against.
        computation = self._scoring_engine.score_submission(
            correct=correct,
            elapsed=elapsed,
            round_duration=self._round_duration,
            difficulty=diff_enum,
            streak=player.streak,
            double_points_active=double_active,
            is_final_round=self.round == self.total_rounds,
            wager=player.wager,
            score_before_wager=player.score,
        )

        points = computation.points
        speed_bonus = computation.speed_bonus
        streak_bonus = computation.streak_bonus
        diff_mult = computation.difficulty_multiplier
        milestone_bonus = computation.milestone_bonus

        # Tally the milestone hit (engine already folded the bonus into points).
        if milestone_bonus:
            player.streak_milestone_bonus_total += milestone_bonus
            player.streak_milestones_hit += 1

        player.round_score = points
        player.round_score_breakdown = computation.breakdown
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
                # Timeout: the player never submitted this round, so they score
                # 0 for it and their streak breaks. On the FINAL round this also
                # means any wager they set is NOT resolved — wagers only apply on
                # submit (see scoring_engine.score_submission). A player who
                # times out KEEPS their current points and neither wins nor loses
                # the wager. This is the intended "timeout keeps stake" semantics
                # (#301, Markus' decision) — documented in the wager UI copy
                # (i18n wager.timeoutNote). Do NOT add a wager deduction here.
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

        # Feed the group-level difficulty calibrator (#40). Signal = the share
        # of *participating* players who answered correctly. Then advance the
        # target for the next round. No-op when not in "auto" mode. Rounds with
        # zero participants carry no signal (the calibrator ignores total<=0).
        #
        # #302: base the signal on players who actually SUBMITTED, not merely
        # connected. Counting every connected-but-idle tab (late joiners, AFK
        # phones) as wrong dragged the difficulty easier — the opposite of the
        # intended adaptation, and inconsistent with question_stats.record_round
        # below, which deliberately excludes timeouts.
        if self._calibrator is not None:
            participants = [
                p for p in self._player_registry.players.values() if p.submitted
            ]
            total = len(participants)
            correct = sum(
                1 for p in participants if player_correct.get(p.name, False)
            )
            self._calibrator.record_round(correct=correct, total=total)
            new_target = self._calibrator.next_target()
            _LOGGER.debug(
                "Auto-difficulty: round %d group %d/%d correct, "
                "avg=%.2f, next target=%s",
                self.round,
                correct,
                total,
                self._calibrator.average_rate() or 0.0,
                new_target.value,
            )

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

        # Clear the late-joiner flag now that this round (the one they
        # joined into) has been evaluated. The flag only exists to keep a
        # mid-round arrival from forcing the full timer on the round they
        # entered — from their *next* round on they're a full participant
        # and must count toward all_submitted(). Without this reset the flag
        # persisted for the rest of the game and late joiners were scored 0
        # (timeout) every round because all_submitted() never waited for
        # them. (#255.)
        for player in self._player_registry.players.values():
            player.joined_late = False

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
        if not self._phase_controller.pause(reason):
            return False
        self._notify_state_callbacks()
        return True

    def resume(self) -> bool:
        """Resume a paused game. Returns True if resume happened."""
        if not self._phase_controller.resume():
            return False
        self._notify_state_callbacks()
        return True

    def get_pause_reason(self) -> str | None:
        """Return the current pause reason (or None if not paused)."""
        return getattr(self, "_pause_reason", None)

    def end_game(self) -> dict[str, Any]:
        """End the game and transition to FINALE.

        Idempotent: re-entry while already in FINALE returns the cached
        finale payload without recomputing the podium/superlatives, firing
        a second ``game_ended`` broadcast, or recording analytics again.
        Two WS code paths (end-of-last-round + explicit end-game) could
        otherwise reach this and double-broadcast the finale + record the
        game twice in analytics. (#255.)
        """
        if self.phase == GamePhase.FINALE and self._finale_data is not None:
            return self._finale_data

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
        self._finale_data = finale_data

        # Record to analytics
        self._record_analytics()

        # Save question history so next game prioritises least-recently-shown.
        # The write is offloaded to an executor thread so end_game never
        # blocks the event loop on disk I/O (issue #222).
        self._flush_history()

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

    async def async_load_history(self) -> None:
        """Load persisted question history off the event loop (#222).

        Called once from ``async_setup_entry`` after construction. The
        blocking ``read_text`` of ``question_history.json`` runs in an
        executor thread. A no-op when no runtime is wired.
        """
        if self._runtime is None:
            return
        history_path = self._runtime.data_dir / "question_history.json"
        await self._question_bank.load_history_async(history_path, self._runtime)

    def _flush_history(self) -> None:
        """Persist question history without blocking the event loop (#222).

        When a runtime is available (HA or standalone), the blocking
        ``write_text`` of ``question_history.json`` is offloaded to an
        executor thread via a fire-and-forget task — mirroring how the
        per-question stats are saved in ``end_game``. Without a runtime
        (bare unit tests) it falls back to the synchronous flush so the
        behaviour and the on-disk result are unchanged.
        """
        bank = self._question_bank
        runtime = self._runtime

        # No runtime, or no running event loop (bare synchronous unit tests):
        # fall back to the synchronous write. The on-disk result is identical;
        # there is no loop to block in that path.
        if runtime is None:
            bank.flush_shown_history()
            return
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            bank.flush_shown_history()
            return

        async def _do_flush() -> None:
            try:
                await bank.flush_shown_history_async(runtime)
            except Exception:  # noqa: BLE001
                _LOGGER.exception("Failed to save question history")

        runtime.create_task(_do_flush())

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
                    started_at=(
                        int(self._game_start_time) if self._game_start_time else None
                    ),
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
        self._phase_controller.clear_timers()
        self._powerup_manager.reset()
        self._finale_podium = None
        self._finale_superlatives = None
        self._finale_data = None
        self.shuffle_map = []
        self.shuffled_answers = []
        self._timer_override = None
        self._calibrator = None
        self._lightning = None
        self._lightning_splash_pending = False

        for player in self._player_registry.players.values():
            player.reset_for_new_game()

        self._notify_state_callbacks()

    # ------------------------------------------------------------------
    # Lightning Round (issue #42)
    # ------------------------------------------------------------------
    #
    # Thin wrappers around a LightningRound instance. The mode's rules and
    # state live in game/lightning.py; this class only owns the reference
    # and the phase transition so the WS handler has one place to call.

    def start_lightning_round(
        self,
        *,
        category: str | None = None,
        categories: list[str] | None = None,
        difficulty: str | None = None,
        language: str | None = None,
    ) -> bool:
        """Begin a lightning round, reusing the current player roster.

        Returns True if it started, False if no questions were available.
        Allowed from LOBBY (standalone lightning), FINALE (after a game), or
        LIGHTNING_RECAP (the "play again" button after a lightning round —
        issue #294; this is the same "between rounds" situation as FINALE).
        A fresh LightningRound is built below and ``_lightning`` /
        ``_lightning_splash_pending`` are reassigned, so re-entry from
        LIGHTNING_RECAP starts cleanly. (#285 will later restructure
        lightning entry; this is a minimal fix for the dead-end.)
        """
        from .lightning import LightningRound  # local import — avoid cycle

        if self.phase not in (
            GamePhase.LOBBY,
            GamePhase.FINALE,
            GamePhase.LIGHTNING_RECAP,
        ):
            return False

        player_names = list(self._player_registry.players.keys())
        lr = LightningRound(
            self._question_bank,
            player_names,
            language=language or self.language,
            category=category if category is not None else self.category,
            categories=(
                categories
                if categories is not None
                else getattr(self, "categories", None)
            ),
            difficulty=difficulty,
        )
        if not lr.start():
            return False

        self._lightning = lr
        self.phase = GamePhase.LIGHTNING
        # Open on the intro splash (issue #201); the admin's Start control
        # calls begin_lightning_questions() to advance into question 1.
        self._lightning_splash_pending = True
        self._notify_state_callbacks()
        return True

    def begin_lightning_questions(self) -> bool:
        """Leave the intro splash and let the question loop start (issue #201).

        Returns True if the splash was pending and is now dismissed, False
        if there was nothing to dismiss (no active round / already started).
        """
        if self.phase != GamePhase.LIGHTNING or not self._lightning_splash_pending:
            return False
        self._lightning_splash_pending = False
        self._notify_state_callbacks()
        return True

    @property
    def lightning_splash_pending(self) -> bool:
        """True while the lightning intro splash is showing (issue #201)."""
        return self._lightning_splash_pending

    @property
    def lightning(self):
        """The active LightningRound, or None."""
        return self._lightning

    def finish_lightning_round(self) -> None:
        """Transition out of an active lightning round into its recap screen."""
        if self.phase == GamePhase.LIGHTNING:
            self.phase = GamePhase.LIGHTNING_RECAP
            self._flush_history()
            self._notify_state_callbacks()

    # ------------------------------------------------------------------
    # Power-ups
    # ------------------------------------------------------------------

    def use_powerup(
        self, player_id: str, target_id: str | None = None
    ) -> PowerUpEffect | str:
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
            if (
                not target_id
                or target_id == player_id
                or not target_player
                or not target_player.is_active
            ):
                # FREEZE: skip already-submitted players so the pause is actually
                # useful (freezing a locked-in opponent burns the power-up).
                # STEAL: the opposite — only a SUBMITTED target has a round_score
                # worth stealing; an unsubmitted target yields 0 stolen points
                # and burns the power-up for nothing (#254). So require submitted.
                opponents = [
                    name
                    for name, p in self._player_registry.players.items()
                    if name != player_id and p.is_active
                    and (held != PowerUpType.FREEZE or not p.submitted)
                    and (held != PowerUpType.STEAL or p.submitted)
                ]
                if not opponents:
                    return ERR_INVALID_ACTION
                target_id = random.choice(opponents)

        # Explicit-target submitted-check (random fallback already filters above).
        # FREEZE rejects submitted targets; STEAL rejects un-submitted targets.
        if target_id:
            target_check = self._player_registry.get_player(target_id)
            if held == PowerUpType.FREEZE and target_check and target_check.submitted:
                return ERR_INVALID_ACTION
            if (
                held == PowerUpType.STEAL
                and target_check
                and not target_check.submitted
            ):
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
                # Lockout, not a pause (#300): the target can't submit for the
                # freeze window, but their clock keeps running and the frozen
                # seconds count as elapsed (no speed-bonus refund).
                target_timer.freeze(FREEZE_DURATION)

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
            # Clamp the target's round_score at 0 before halving: on the final
            # round a wrong answer with a wager can drive round_score negative,
            # and a negative // 2 would invert the steal (target gains, source
            # loses). Treat a non-positive target round_score as nothing to
            # steal (#289).
            stolen = max(0, target_player.round_score) // 2
            target_player.round_score = max(0, target_player.round_score - stolen)
            target_player.score = max(0, target_player.score - stolen)
            source_player.round_score += stolen
            # Clamp the source total at 0 so a steal can never leave a player
            # with a permanently negative score (#289).
            source_player.score = max(0, source_player.score + stolen)
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

    def set_round_shuffle(
        self, shuffle_map: list[int], shuffled_answers: list[str]
    ) -> None:
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

    def ensure_player_shuffle(self, player_name: str) -> list[int]:
        """Return the player's shuffle, creating a fresh one if missing.

        Late joiners and reconnecting players who arrive mid-question never
        ran through the round-start per-player shuffle loop, so they have no
        entry in ``player_shuffles``. Without one, a reconnect snapshot would
        fall back to the canonical order — but ``submit_answer`` maps the
        tapped index through ``get_player_shuffle`` (issue #253). If we then
        also lazily create the shuffle here, the buttons the player rebuilds
        from the snapshot match the order their submit expects. Mirrors the
        round-start shuffle creation in ``_start_next_question``.
        """
        existing = self.player_shuffles.get(player_name)
        if existing:
            return existing
        if not self._current_question:
            return self.shuffle_map
        order = list(range(len(self._current_question.answers)))
        random.shuffle(order)
        self.player_shuffles[player_name] = order
        return order

    @property
    def round_duration(self) -> float:
        """Public accessor for current round duration."""
        return self._round_duration

    def get_player_timer(self, player_name: str):
        """Return the authoritative QuestionTimer for a player, or None.

        Exposed so the WebSocket handler can broadcast per-player remaining
        time (so time-boost and freeze are visible on the player's UI, #4).
        """
        return self._phase_controller.get_timer(player_name)

    def resolve_tick(self, player_names: list[str]):
        """Resolve one countdown-tick's per-player remaining + dashboard min.

        Thin delegate to the PhaseController so all timing logic lives there
        (#203). The WebSocket handler supplies the current player names and
        turns the returned :class:`TickResolution` into ``timer_tick`` wire
        messages; it owns the I/O, the controller owns the timing.
        """
        return self._phase_controller.resolve_tick(player_names)

    def all_timers_expired(self, player_names: list[str]) -> bool:
        """Whether every supplied player's timer has expired (#203).

        The countdown loop's stop condition, delegated to the PhaseController.
        """
        return self._phase_controller.all_timers_expired(player_names)

    def round_wall_clock_expired(self) -> bool:
        """Whether the round wall-clock has elapsed (#255).

        Fallback stop condition for the countdown loop when every player has
        disconnected mid-question and there are no live per-player timers left
        for ``all_timers_expired`` to break on. Delegated to the
        PhaseController.
        """
        return self._phase_controller.round_wall_clock_expired()

    def get_player_powerup(self, player_name: str):
        """Get the power-up held by a player."""
        return self._powerup_manager.get_powerup(player_name)

    def get_current_question(self) -> Question | None:
        """Return the current question, or None."""
        return self._current_question

    def get_leaderboard(self) -> list[dict[str, Any]]:
        """Return sorted leaderboard data — wire-identical to the live broadcast.

        Delegates to :func:`serialize_leaderboard` (the same helper the live
        ``game_state`` / ``round_summary`` broadcasts use) so the snapshot
        leaderboard carries the FULL client contract — ``submitted``,
        ``best_streak``, ``rounds_played``, ``powerups_used``, ``round_score``,
        ``correct`` — not just rank/name/score/streak (#297). Without those
        keys a reconnect-after-submit never re-locked the answer buttons
        (player-core.js reads ``leaderboard[].submitted``) and a FINALE
        reconnect showed 0 for BESTE SERIE / GESPIELTE RUNDEN / POWER-UPS.

        ``is_admin`` is likewise required so the reconnect client can show the
        admin "Start New Game" / "Next Round" controls; ``serialize_leaderboard``
        emits it. (Earlier hand-rolled versions of this method dropped fields
        and caused a chronic FINALE admin-lockout — see v1.1.4 notes.)
        """
        # Local import keeps the game-layer free of a module-level dependency
        # on the server layer (serializers only TYPE_CHECKING-imports game).
        from ..server.serializers import serialize_leaderboard

        return serialize_leaderboard(self.get_players())

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
            remaining = self._phase_controller.time_remaining_for_snapshot()
            snapshot["question"] = {
                "id": q.id,
                "text": q.question,
                "answers": [a.text for a in q.answers],
                "difficulty": q.difficulty,
                "category": q.category,
                "image_url": q.image_url,
                "time_limit": self._round_duration,
                "time_remaining": round(remaining, 1),
            }

        if self.phase == GamePhase.ANSWER_REVEAL and self._round_summary:
            s = self._round_summary
            q = s.question
            # Canonical (question-JSON) answer order, mirroring the
            # QUESTION_ACTIVE snapshot's ``question.answers``. A TV/dashboard
            # that (re)connects during the reveal has no live ``question``
            # block to render, so without these fields its question view was
            # blank (#296). The dashboard renders the unshuffled grid and
            # highlights ``correct_answer_index_original``.
            correct_idx_original = next(
                (i for i, a in enumerate(q.answers) if a.correct), -1
            )
            snapshot["round_summary"] = {
                "question_text": q.question,
                "category": q.category,
                "image_url": q.image_url,
                "answers": [a.text for a in q.answers],
                "correct_answer_index_original": correct_idx_original,
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
            awards = (
                self._finale_superlatives
                if self._finale_superlatives is not None
                else compute_superlatives(self.get_players())
            )
            if awards:
                snapshot["superlatives"] = [s.to_dict() for s in awards]

        if self.phase == GamePhase.LIGHTNING and self._lightning is not None:
            lr = self._lightning
            q = lr.current_question
            snapshot["lightning"] = {
                "index": lr.index,
                "num_questions": lr.num_questions,
                "time_remaining": round(lr.time_remaining(), 1),
                "seconds_per_question": lr.seconds_per_question,
                "leaderboard": lr.leaderboard(),
                # True while the intro splash ("Bolt Burst", #201) is still
                # showing and the first question hasn't been broadcast.
                "splash_pending": self._lightning_splash_pending,
            }
            if q is not None:
                # Canonical (admin/TV) answer order; players get their own
                # shuffle pushed via the lightning_question event.
                snapshot["lightning"]["question"] = {
                    "text": q.question,
                    "answers": [a.text for a in q.answers],
                    "category": q.category,
                    "image_url": q.image_url,
                }

        if self.phase == GamePhase.LIGHTNING_RECAP and self._lightning is not None:
            snapshot["lightning_recap"] = self._lightning.build_recap()

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
        # Named events (round_evaluated / game_ended) route to dedicated
        # broadcast handlers that build their own messages and re-fetch the
        # live state — they never read this payload's snapshot fields, so
        # building a full ``get_state_snapshot()`` (O(P log P) + a heavy dict)
        # here only to discard it is wasted work on every round/game end (#304).
        # Pass just the event marker; the default full-state handler fetches the
        # snapshot itself when it actually needs one.
        payload = {"event": event}
        coro = self._broadcast_callback(payload)
        if self._runtime is not None:
            self._runtime.create_task(coro)
        else:
            asyncio.ensure_future(coro)


