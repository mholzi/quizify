"""Game state management for Quizify."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import random
import secrets
import time
from collections.abc import Callable, Coroutine
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
    ERR_TEAM_LOCKED,
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
    calculate_estimate_scores,
    calculate_podium,
)
from .scoring_engine import ScoringEngine, wager_loss
from .team import ANSWER_CHANGE_LOCK_SECONDS, Team, TeamRegistry
from .timer import QuestionTimer
from .types import TIME_LIMITS, Difficulty

if TYPE_CHECKING:
    from aiohttp import web

    from ..analytics import QuizifyAnalytics
    from ..question_stats import QuestionStatsService
    from ..runtime import Runtime
    from .hot_seat import HotSeatRound
    from .lightning import LightningRound

_LOGGER = logging.getLogger(__name__)

# GamePhase moved to phase_controller (issue #188) but is re-exported here so
# the many `from .state import GamePhase` / `from .game.state import GamePhase`
# call sites across the integration keep working unchanged.
__all__ = [
    "AnswerResult",
    "GamePhase",
    "QuizifyGameState",
    "RoundSummary",
    "TeamAnswerAck",
]


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
class TeamAnswerAck:
    """A tap that set the team's answer rather than scoring a player (#365).

    Returned instead of :class:`AnswerResult` in team mode: nothing is scored
    at tap time, because the answer standing when the clock stops is the one
    that counts. ``lock_seconds`` lets the client grey the buttons for exactly
    as long as the model will refuse the next change.
    """

    team_id: str
    answer_index: int
    set_by: str
    lock_seconds: float


@dataclass
class RoundSummary:
    """Summary of a completed round."""

    question: Question
    correct_answer: Answer
    fun_fact: str
    results: list[AnswerResult] = field(default_factory=list)
    leaderboard: list[dict[str, Any]] = field(default_factory=list)
    # Estimate-round reveal data (#275). None for multiple-choice rounds.
    # When set, carries the true value, range/unit, and every player's guess +
    # distance + awarded points so the number-line reveal can be rendered on
    # both the player and TV screens. See ``build_estimate_reveal``.
    estimate: dict[str, Any] | None = None


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
        # Teams (#365). Empty unless somebody forms one in the lobby — the mode
        # is opt-in per game and costs nothing while unused.
        self._team_registry = TeamRegistry()
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

        # Memoized round-summary message dict (#414), keyed on
        # ``(game_id, round)``. ``RoundMessageBuilder.build_round_summary`` is
        # recipient-independent but was recomputed on every call — once for the
        # live broadcast and again for every join/reconnect/get_state during
        # ANSWER_REVEAL (O(P) each, O(P²) under a reconnect storm). Build it
        # once per round and reuse; invalidated in ``start_next_question`` and
        # ``reset_to_lobby``.
        self._round_summary_msg: dict[str, Any] | None = None
        self._round_summary_msg_key: tuple[str | None, int] | None = None

        # Optional admin-chosen timer override (seconds). When set,
        # overrides the difficulty-derived TIME_LIMITS lookup in
        # start_next_question. Cleared on reset_to_lobby/end_game.
        self._timer_override: int | None = None

        # Last-game settings snapshot for "Play again — same settings".
        # None until a game has been started at least once.
        self._last_settings: dict[str, Any] | None = None

        # Broadcast callback — set by websocket layer
        self._broadcast_callback: (
            Callable[[dict[str, Any]], Coroutine[Any, Any, None]] | None
        ) = None

        # State-change observers (HA sensor entities subscribe here so they
        # can push updates without polling). Pure callbacks, no async.
        self._state_callbacks: list[Callable[[], None]] = []

        # Analytics / stats service (injected from __init__.py)
        self._stats_service: QuizifyAnalytics | None = None
        # Per-question stats sink (optional; standalone tests skip it).
        self._question_stats: QuestionStatsService | None = None
        self._game_start_time: float | None = None

        # Cached finale data (computed once in end_game, cleared in reset_to_lobby)
        self._finale_podium: list | None = None
        self._finale_superlatives: list | None = None
        self._finale_data: dict[str, Any] | None = None

        # Active LightningRound (issue #42), or None when not in a lightning
        # round. Owns its own questions/scores/recap; this class only holds
        # the reference + phase so the WS layer can route to it.
        self._lightning: LightningRound | None = None

        # True between start_lightning_round() and begin_lightning_questions():
        # the intro splash ("Bolt Burst", issue #201) is on screen and the
        # first question has not been broadcast yet. For the auto-triggered
        # flow (#285) the WS loop advances out of it on a grace timer rather
        # than a host tap.
        self._lightning_splash_pending: bool = False

        # Auto-trigger bookkeeping (issue #285). The Lightning Round is no
        # longer a host-manual mode — it fires on its own exactly once per
        # game at a uniformly random round inside the eligible window
        # (rounds 3 … N-1). Picked up front in start_game() so the pick is a
        # single seedable draw the tests can pin.
        #   * _lightning_enabled  — the settings toggle (default ON).
        #   * _lightning_target_round — the 1-based round the LR fires BEFORE,
        #     or None when disabled / the window is empty (short game).
        #   * _lightning_fired — guards the once-per-game guarantee.
        #   * _round_to_resume — while in the LIGHTNING/LIGHTNING_RECAP
        #     detour, the normal round the game returns to afterwards.
        self._lightning_enabled: bool = True
        self._lightning_target_round: int | None = None
        self._lightning_fired: bool = False
        self._round_to_resume: int = 0
        # Injectable RNG for the target-round pick so tests are deterministic
        # without monkeypatching the module-global ``random``. Defaults to the
        # shared module RNG in production.
        self._lightning_rng: random.Random = random.Random()

        # Hot Seat auction (#616) — the second self-contained detour, armed
        # exactly like the Lightning Round above and deliberately never in the
        # same round as it (see _pick_hot_seat_round).
        self._hot_seat: HotSeatRound | None = None
        self._hot_seat_enabled: bool = True
        self._hot_seat_target_round: int | None = None
        self._hot_seat_fired: bool = False
        self._hot_seat_round_to_resume: int = 0
        self._hot_seat_rng: random.Random = random.Random()

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

    def set_stats_services(
        self,
        stats_service: QuizifyAnalytics | None,
        question_stats: QuestionStatsService | None,
    ) -> None:
        """Inject the analytics + per-question stats sinks.

        Public wiring entry point called from ``__init__.py`` so setup no
        longer assigns the private ``_stats_service`` / ``_question_stats``
        attributes across the module boundary (#364). Either may be ``None``
        (standalone tests / dev server), matching the pre-wired defaults.
        """
        self._stats_service = stats_service
        self._question_stats = question_stats

    @property
    def stats_service(self) -> QuizifyAnalytics | None:
        """Read-only access to the injected analytics sink.

        Same #364 posture as ``set_stats_services``: collaborators that need
        to *read* all-time numbers (the lobby standing line, #371) go through
        this property instead of reaching for ``_stats_service``. ``None``
        wherever analytics was never wired (dev server, most tests).
        """
        return self._stats_service

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
    # Teams (#365)
    # ------------------------------------------------------------------

    @property
    def team_registry(self) -> TeamRegistry:
        """The game's teams. Empty unless somebody formed one in the lobby."""
        return self._team_registry

    @property
    def team_mode(self) -> bool:
        """True once at least one team exists.

        Deliberately derived rather than stored: there is no separate switch to
        forget to clear, and a game where everybody left their team falls back
        to ordinary play on its own.
        """
        return self._team_registry.is_active

    def create_team(self, name: str, founder: str) -> dict | None:
        """Open a team in the lobby. Returns the team's wire shape.

        Lobby only — teams are fixed from the start of the game
        (Markus, 2026-08-12), so a request arriving mid-round is refused here
        rather than handled halfway.
        """
        if self.phase != GamePhase.LOBBY:
            return None
        if self._player_registry.get_player(founder) is None:
            return None
        team = self._team_registry.create(name, founder)
        return team.to_dict()

    def join_team(self, team_id: str, player_name: str) -> dict | None:
        """Move a player into an existing team (lobby only)."""
        if self.phase != GamePhase.LOBBY:
            return None
        if self._player_registry.get_player(player_name) is None:
            return None
        team = self._team_registry.join(team_id, player_name)
        return team.to_dict() if team else None

    def leave_team(self, player_name: str) -> bool:
        """Leave the current team (lobby only). Empty teams dissolve."""
        if self.phase != GamePhase.LOBBY:
            return False
        if self._team_registry.get_by_member(player_name) is None:
            return False
        self._team_registry.leave(player_name)
        return True

    def get_team_of(self, player_name: str) -> dict | None:
        team = self._team_registry.get_by_member(player_name)
        return team.to_dict() if team else None

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
        # A player who leaves also leaves their team; a team whose last member
        # goes is dissolved rather than left standing empty (#365).
        self._team_registry.remove_player(name)
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
        self._team_registry.reset()
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
        lightning_enabled: bool = True,
        lightning_seed: int | None = None,
        hot_seat_enabled: bool = True,
        hot_seat_seed: int | None = None,
    ) -> dict[str, Any]:
        """Start a new game session.

        Pass ``categories`` (list of slugs) for multi-category mode.
        Pass ``category`` (single slug) for single-category mode.
        Pass neither for mixed (all packs).

        ``lightning_enabled`` (the settings toggle, default ON) controls
        whether the auto Lightning Round (#285) is armed for this game.
        ``lightning_seed`` lets tests pin the random target-round draw; it is
        never passed in production.

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

        # Arm the auto Lightning Round (#285). Pick the target round once, up
        # front, so it is a single seedable draw — the WS round-advance path
        # consults should_trigger_lightning() and fires when reached.
        self._lightning_enabled = lightning_enabled
        self._lightning_fired = False
        self._round_to_resume = 0
        if lightning_seed is not None:
            self._lightning_rng = random.Random(lightning_seed)
        self._lightning_target_round = self._pick_lightning_round(num_rounds)

        # Arm the Hot Seat auction (#616), same shape, same draw-once rule.
        self._hot_seat_enabled = hot_seat_enabled
        self._hot_seat_fired = False
        self._hot_seat_round_to_resume = 0
        if hot_seat_seed is not None:
            self._hot_seat_rng = random.Random(hot_seat_seed)
        self._hot_seat_target_round = self._pick_hot_seat_round(num_rounds)

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
            "lightning_enabled": lightning_enabled,
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
        # picker in the admin UI lets the host pick 20/30/45/180s up front
        # (the 180 s option came from #506: reading a question plus four
        # answers takes small kids well over 45 s).
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
        # Teams clear their standing answer with the players (#365) — the lock
        # must not survive into the next question either.
        self._team_registry.reset_round()
        self._powerup_manager.reset_round()

        # Stamp round timing + create+start per-player timers (PhaseController).
        self._phase_controller.begin_round(round_duration)

        # Randomly assign a power-up to one player who has not yet received one
        # this game (#340 — at most one power-up per player per game). The
        # granted set persists across rounds and is cleared only on a
        # game-level reset (start_game / reset_to_lobby). Once every connected
        # player has had one, no power-up is granted this round.
        eligible = [
            p
            for p in self._player_registry.players.values()
            if p.connected
            and not self._powerup_manager.was_granted_this_game(p.name)
        ]
        if eligible:
            lucky_player = random.choice(eligible)
            # On estimate rounds only hand out power-ups that actually do
            # something there (#406): JOKER/DOUBLE_POINTS/STEAL no-op on the
            # estimate scoring path, so restrict the pool to FREEZE/TIME_BOOST.
            allowed_types = (
                [PowerUpType.FREEZE, PowerUpType.TIME_BOOST]
                if question.is_estimate
                else None
            )
            powerup = self._powerup_manager.assign_random_powerup(
                lucky_player.name, allowed_types=allowed_types
            )
            _LOGGER.debug(
                "Power-up %s assigned to %s", powerup.value, lucky_player.name
            )
        else:
            _LOGGER.debug(
                "No eligible players for power-up this round "
                "(all connected players already granted)"
            )

        self._phase_controller.enter_question_active()
        self._round_summary = None
        # Invalidate the memoized round-summary message (#414): a new round is
        # live, so the previous round's cached dict must not be served.
        self._round_summary_msg = None
        self._round_summary_msg_key = None

        _LOGGER.info(
            "Round %d/%d started: %s (%.0fs)",
            self.round,
            self.total_rounds,
            question.id,
            self._round_duration,
        )
        self._notify_state_callbacks()
        return question

    def submit_answer(
        self,
        player_id: str,
        answer_index: int,
        *,
        elapsed_override: float | None = None,
        _settling_team: bool = False,
    ) -> AnswerResult | TeamAnswerAck | str:
        """Submit a player's answer for the current round.

        Returns AnswerResult on success, or an error code string.

        ``_settling_team`` is the one internal caller (#365): when a round
        closes, the team's standing answer is scored through this same path on
        behalf of the member who set it, with ``elapsed_override`` carrying the
        time of that member's *last* tap. The round clock has expired by then,
        so the phase, expiry and freeze guards below are skipped for that call
        — they exist to police live taps, and this is the settlement.
        """
        if not _settling_team and self.phase != GamePhase.QUESTION_ACTIVE:
            return ERR_GAME_NOT_STARTED

        player = self._player_registry.get_player(player_id)
        if player is None:
            return ERR_NOT_IN_GAME

        if player.submitted:
            return ERR_ALREADY_SUBMITTED

        timer = self._timers.get(player_id)
        if not _settling_team and timer and timer.is_expired():
            return ERR_ROUND_EXPIRED

        # Freeze lockout (#300): a frozen target cannot submit until the freeze
        # window expires. The round clock keeps running underneath (the freeze
        # does NOT pause get_remaining), so the lockout costs the target real
        # answer time. Reject the submission while still frozen; once the window
        # passes the player may submit normally (if the timer hasn't expired).
        if not _settling_team and timer and timer.is_frozen():
            _LOGGER.debug(
                "Rejecting submit from frozen player %s (%.1fs of freeze left)",
                player_id,
                timer.frozen_remaining(),
            )
            return ERR_FROZEN

        # Team mode (#365): the tap sets the *team's* answer instead of the
        # player's. Nobody is marked submitted here — every member may keep
        # changing it until the clock stops, and the answer that stands then is
        # the one that scores, exactly once, in _do_evaluate_round.
        team = (
            None
            if _settling_team
            else self._team_registry.get_by_member(player.name)
        )
        if team is not None:
            if not team.set_answer(answer_index, player.name):
                return ERR_TEAM_LOCKED
            self._notify_state_callbacks()
            return TeamAnswerAck(
                team_id=team.team_id,
                answer_index=answer_index,
                set_by=player.name,
                lock_seconds=ANSWER_CHANGE_LOCK_SECONDS,
            )

        # Record submission
        elapsed = (
            elapsed_override
            if elapsed_override is not None
            else (timer.get_elapsed() if timer else 0.0)
        )
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

        # #450: accumulate, don't assign. A STEAL (or reaction bonus) can land
        # BEFORE the source submits, adding to round_score while round_score was
        # zeroed by reset_round at round start. A plain `= points` here wiped
        # those pre-submit deltas from round_score even though `score` kept
        # them, so the reveal breakdown, round_scores history, the Top Score
        # superlative, and a later STEAL against this player (which halves
        # round_score) all under-counted. `+= points` folds this round's earned
        # points on top of any pre-submit steal/bonus delta. Submit is guarded
        # by the `player.submitted` early-return above, so this runs exactly
        # once per round — no double-count. (The estimate path can't hit this:
        # STEAL is rejected on estimate rounds, #406.)
        player.round_score += points
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
        #
        # NOT during team settlement (#601). `_settle_team_answers` calls this
        # method once per team while the round is already being evaluated, and
        # `_round_summary` is not set until that evaluation finishes — so the
        # guard inside `evaluate_round()` is still open and the round would
        # re-enter `_do_evaluate_round` from the middle of its own settlement
        # loop. Measured consequence: a team scored twice for one round and
        # its history grew three entries. The settlement caller evaluates the
        # round itself; it never needs this path.
        if not _settling_team and self._player_registry.all_submitted():
            self.evaluate_round()

        return result

    def submit_guess(self, player_id: str, guess: float) -> str | None:
        """Submit a player's numeric guess for an estimate round (#275).

        Parallel to ``submit_answer`` but for ``type == "estimate"`` questions:
        there is no per-answer correctness or immediate scoring — closeness is
        ranked across all players at round evaluation. The guess is clamped to
        the question's [min, max] range, recorded on the player, and the player
        is marked submitted. Returns an error-code string on failure, or None on
        success. The round auto-evaluates once everyone has submitted, same as
        the MC path.
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
        if timer and timer.is_frozen():
            return ERR_FROZEN

        question = self._current_question
        if question is None:
            return ERR_GAME_NOT_STARTED
        if not question.is_estimate:
            # Wrong message for the question type — reject so a client bug
            # can't silently mis-submit an MC round as a guess.
            return ERR_INVALID_ACTION

        # Clamp the guess into the valid range (defence-in-depth; the slider
        # already constrains it client-side).
        q_min = question.estimate_min if question.estimate_min is not None else guess
        q_max = question.estimate_max if question.estimate_max is not None else guess
        clamped = max(q_min, min(q_max, float(guess)))

        # Team mode (#602): the guess belongs to the *team*, exactly as a tap
        # does on a multiple-choice round. Nobody is marked submitted here —
        # every member may keep re-guessing until the clock stops, and the
        # number standing then is the one that scores, once, in
        # `_settle_team_guesses`. Before this, estimate rounds ignored teams
        # completely: the points landed on the member's individual score,
        # which is invisible in team mode, and the team finished on zero.
        team = self._team_registry.get_by_member(player.name)
        if team is not None:
            if not team.set_guess(clamped, player.name):
                return ERR_TEAM_LOCKED
            self._notify_state_callbacks()
            return None

        elapsed = timer.get_elapsed() if timer else 0.0
        player.current_guess = clamped
        player.submitted = True
        player.submission_time = time.time()
        player.last_elapsed = elapsed

        # `state.all_submitted()`, not the registry directly (#602): the public
        # accessor is the one that knows a team round always runs to the clock.
        # Reaching past it ended estimate rounds on the first guess from each
        # member, so the "the answer standing at the buzzer counts" rule did
        # not apply to estimates either.
        if self.all_submitted():
            self.evaluate_round()

        return None

    def evaluate_round(self) -> RoundSummary | None:
        """Manually trigger round evaluation (e.g. when timer expires)."""
        if self.phase != GamePhase.QUESTION_ACTIVE:
            return None
        # Guard against double evaluation (race between timer tick and all_submitted)
        if self._round_summary is not None:
            return self._round_summary
        return self._do_evaluate_round()

    def _pick_team_carrier(
        self, team: Team, setter: str | None
    ) -> PlayerSession | None:
        """The member who carries a team's response into the scoring path.

        Normally the member who set it. If they left between the tap and the
        buzzer the response still stands for the team, so it is handed to
        another member rather than dropping a round the team did answer.

        Connected members are preferred over disconnected ones (#601). The
        earlier version took the first member who had not submitted, which
        could be someone whose phone had simply locked — and during the
        re-entrant evaluation that bug allowed, handing the answer to that
        member scored the same round a second time.
        """
        player = self._player_registry.get_player(setter) if setter else None
        if player is not None and not player.submitted:
            return player
        candidates = [
            p
            for name in team.members
            if (p := self._player_registry.get_player(name)) is not None
            and not p.submitted
        ]
        if not candidates:
            return None
        # Fall back to a disconnected member only when nobody is left online:
        # the team did answer, and losing the round because every phone went
        # dark would punish the wrong thing.
        return next((p for p in candidates if p.connected), candidates[0])

    def _settle_team_answers(self) -> None:
        """Score each team's standing answer once, through the player path.

        The member who set the answer carries it: their submission is scored
        with the team's streak and with the elapsed time of their *last* tap,
        and the result is written back to the team. Members who did not set it
        are left untouched — they neither score nor count as a timeout, because
        in team mode there is nothing individual left to reward or punish.

        A team that never answered breaks its streak and records a timeout,
        the same shape a player's missed round takes.
        """
        round_started = self._round_start_time or 0.0
        for team in self._team_registry.all_teams():
            if team.current_answer is None or team.answer_by is None:
                team.streak = 0
                team.round_history.append("timeout")
                team.round_scores.append(0)
                team.rounds_played += 1
                continue
            player = self._pick_team_carrier(team, team.answer_by)
            if player is None:
                team.streak = 0
                team.round_history.append("timeout")
                team.round_scores.append(0)
                team.rounds_played += 1
                continue

            elapsed = max(0.0, (team.answered_at or round_started) - round_started)
            # The scoring engine reads the player's streak; in team mode the
            # streak belongs to the team, so lend it for the call and take the
            # updated value back.
            player.streak = team.streak
            result = self.submit_answer(
                player.name,
                team.current_answer,
                elapsed_override=elapsed,
                _settling_team=True,
            )
            if isinstance(result, AnswerResult):
                team.score += result.points_earned
                team.streak = result.new_streak
                team.last_answer_correct = result.correct
                team.last_elapsed = elapsed
                team.round_score = result.points_earned
                team.round_score_breakdown = dict(player.round_score_breakdown)
                team.round_history.append("correct" if result.correct else "wrong")
                # Award tallies, kept in the same shape a player keeps them so
                # the awards can be computed by the same code (#365).
                team.round_scores.append(result.points_earned)
                team.rounds_played += 1
                if result.correct:
                    team.answer_times.append(elapsed)
                    if (question := self._current_question) is not None and (
                        question.difficulty == Difficulty.HARD.value
                    ):
                        team.hard_score += result.points_earned
                if team.streak > team.max_streak:
                    team.max_streak = team.streak

    def _settle_team_guesses(self) -> dict[str, str]:
        """Hand each team's standing guess to the member who set it (#602).

        The estimate path ranks *participants* by closeness to the true value.
        In team mode the participant is the team — so rather than teach
        ``calculate_estimate_scores`` about teams, the team's guess is carried
        into the ranking by one of its members, the same shape
        ``_settle_team_answers`` uses for multiple choice. The ranking then
        contains one entry per team plus any solo players, and the scoring
        function needs no change at all.

        Returns ``{team_id: carrier_name}`` so the results can be copied back
        onto the teams once the per-player pass has run.
        """
        round_started = self._round_start_time or 0.0
        carriers: dict[str, str] = {}
        for team in self._team_registry.all_teams():
            if team.current_guess is None or team.guess_by is None:
                continue
            player = self._pick_team_carrier(team, team.guess_by)
            if player is None:
                continue
            player.current_guess = team.current_guess
            player.submitted = True
            player.submission_time = time.time()
            player.last_elapsed = max(
                0.0, (team.guessed_at or round_started) - round_started
            )
            carriers[team.team_id] = player.name
        return carriers

    def _apply_estimate_results_to_teams(
        self, carriers: dict[str, str], scores: dict[str, dict]
    ) -> None:
        """Copy each carrier's estimate result onto their team (#602).

        The mirror of the bookkeeping block in ``_settle_team_answers``: the
        team takes the points, the streak, the history entry and the award
        tallies, in the same shape a player keeps them, so the awards can be
        computed by the same code. A team that never guessed records a
        timeout, which is what a missed round looks like everywhere else.
        """
        for team in self._team_registry.all_teams():
            carrier_name = carriers.get(team.team_id)
            player = (
                self._player_registry.get_player(carrier_name)
                if carrier_name
                else None
            )
            entry = scores.get(carrier_name) if carrier_name else None
            if player is None or entry is None:
                team.streak = 0
                team.round_history.append("timeout")
                team.round_scores.append(0)
                team.rounds_played += 1
                continue

            points = player.round_score
            exact = bool(entry["exact"])
            team.score += points
            if team.score < 0:
                team.score = 0
            # Streak only advances on an exact hit, matching the per-player
            # rule (#408) — a near miss is recorded as "wrong", and growing a
            # streak on it would step past a milestone that was never paid.
            team.streak = team.streak + 1 if exact else 0
            if team.streak > team.max_streak:
                team.max_streak = team.streak
            team.last_answer_correct = exact
            team.last_elapsed = player.last_elapsed
            team.round_score = points
            team.round_score_breakdown = dict(player.round_score_breakdown)
            team.round_history.append("correct" if exact else "wrong")
            team.round_scores.append(points)
            team.rounds_played += 1
            if exact:
                team.answer_times.append(player.last_elapsed)
                if (question := self._current_question) is not None and (
                    question.difficulty == Difficulty.HARD.value
                ):
                    team.hard_score += points

    def _collect_team_powerup_stats(self) -> None:
        """Roll each team's members' power-up usage up to the team (#365).

        Power-ups are handed to people and spent by people; the award that
        reads them ("Buzzkill") belongs to the team. Summing at award time
        rather than tracking a second counter keeps one source of truth — a
        member who left the game took their freezes with them, which is the
        same thing that happens to their points.
        """
        for team in self._team_registry.all_teams():
            team.freezes_used = sum(
                player.freezes_used
                for name in team.members
                if (player := self._player_registry.get_player(name)) is not None
            )

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

        # Estimate rounds (#275) are scored by closeness across all players,
        # not per-answer correctness. They build their own RoundSummary and
        # short-circuit the MC correctness/scoring path below — but still share
        # the downstream phase transition + broadcast (handled in the helper).
        if question.is_estimate:
            return self._evaluate_estimate_round(question)

        # Team mode (#365): settle each team's standing answer before the
        # per-player pass below reads `submitted`. Exactly one member is scored
        # per team — the one whose tap stands — so the team earns one score per
        # round, which is what "a team behaves like a single player" means.
        if self.team_mode:
            self._settle_team_answers()

        correct_answer = self._question_bank.get_correct_answer(question)

        # Build per-player correctness from cached `last_answer_correct`
        # set during submit_answer (#145). No second validate_answer call,
        # and no `current_answer or -1` bug that misclassified answer
        # index 0 as a timeout.
        player_correct: dict[str, bool] = {}
        for player in self._player_registry.players.values():
            if not player.submitted:
                # Timeout: the player never submitted this round, so they score
                # 0 for it and their streak breaks.
                #
                # On the FINAL round the wager is resolved anyway, as a loss
                # (#653). This reverses #301, where a timeout left the stake
                # untouched so a sleeping phone cost nothing. The Hot Seat
                # auction (#616) cannot inherit that rule — there the stake buys
                # the right to answer alone, so sitting the question out would
                # mean taking the round for free — and two settlement rules side
                # by side would be worse than either. Both are strict now.
                player.streak = 0
                if self.round == self.total_rounds:
                    loss = wager_loss(player.score, player.wager)
                    if loss:
                        # round_score, not a literal -loss: a pre-submit STEAL
                        # (#472) may already have folded points in here, and the
                        # reveal reports points_earned=round_score.
                        player.round_score -= loss
                        player.score = max(0, player.score - loss)
                player.record_round_result("timeout")
                # Record round_score, not a literal 0 (#472). For a genuine
                # timeout this is 0 (reset_round zeroed it and the player never
                # submitted), but a pre-submit STEAL can have folded points into
                # this player's round_score before they timed out. The reveal's
                # AnswerResult below already reports points_earned=round_score,
                # so the round_scores history and Top-Score aggregation must
                # match — appending 0 here under-counted that stolen amount.
                player.round_scores.append(player.round_score)
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

    def _evaluate_estimate_round(self, question: Question) -> RoundSummary:
        """Evaluate an estimate round by closeness (#275).

        Ranks every player who submitted a guess by absolute distance to the
        true value, awards difficulty-scaled, rank-decayed points (closest =
        full, exact = bonus), applies them to player scores, and builds a
        RoundSummary whose ``estimate`` block carries the number-line reveal
        data. Non-guessers score 0 and break their streak, same as an MC
        timeout. Mirrors the MC path's downstream phase transition + broadcast.
        """
        answer_val = (
            question.estimate_answer
            if question.estimate_answer is not None
            else 0.0
        )
        try:
            diff_enum = Difficulty(question.difficulty)
        except ValueError:
            diff_enum = Difficulty.MEDIUM

        # Team mode (#602): settle each team's standing guess onto a member
        # before the ranking is built, so the ranking sees one entry per team
        # (plus any solo players) instead of every individual member.
        carriers = self._settle_team_guesses() if self.team_mode else {}

        guesses: dict[str, float] = {
            p.name: p.current_guess
            for p in self._player_registry.players.values()
            if p.submitted and p.current_guess is not None
        }
        scores = calculate_estimate_scores(guesses, answer_val, diff_enum)

        # Apply scoring + per-round bookkeeping to every player.
        for player in self._player_registry.players.values():
            entry = scores.get(player.name)
            if entry is None:
                # No guess this round → score 0, streak breaks (mirrors MC
                # timeout). A player who guessed but somehow isn't in ``scores``
                # can't happen, but this branch also covers the not-submitted
                # case cleanly.
                player.streak = 0
                player.record_round_result("timeout")
                player.round_score = 0
                player.round_score_breakdown = {}
                player.round_scores.append(0)
            else:
                points = entry["points"]
                # Closeness scoring has no streak/speed concept; surface the
                # estimate-specific breakdown so the reveal can show distance.
                # Streak only advances on an EXACT hit — a non-exact guess is
                # recorded as "wrong" below, so growing the streak on it would
                # let a wrong guess step past a STREAK_MILESTONES value without
                # ever paying the milestone (only MC awards them) (#408). Keep
                # streak bookkeeping in agreement with the recorded correctness.
                if entry["exact"]:
                    player.streak += 1
                    if player.streak > player.max_streak:
                        player.max_streak = player.streak
                else:
                    player.streak = 0
                player.round_score = points
                player.round_score_breakdown = {
                    "speed_bonus": 0,
                    "streak_bonus": 0,
                    "difficulty_multiplier": 1.0,
                    "double_points": False,
                    "estimate_distance": entry["distance"],
                    "estimate_rank": entry["rank"],
                    "estimate_exact": entry["exact"],
                }
                player.score += points
                if player.score < 0:
                    player.score = 0
                if diff_enum == Difficulty.HARD:
                    player.hard_score += points
                player.last_answer_correct = entry["exact"]
                player.record_round_result(
                    "correct" if entry["exact"] else "wrong"
                )
                player.round_scores.append(points)
            player.rounds_played += 1

        if self.team_mode:
            self._apply_estimate_results_to_teams(carriers, scores)

        # Build per-player results (connected only).
        results: list[AnswerResult] = []
        for player in self._player_registry.players.values():
            if not player.connected:
                continue
            entry = scores.get(player.name)
            results.append(
                AnswerResult(
                    player_id=player.name,
                    correct=bool(entry and entry["exact"]),
                    points_earned=player.round_score,
                    new_streak=player.streak,
                    new_total=player.score,
                )
            )

        leaderboard = self.get_leaderboard()

        # A synthetic "correct answer" so the rest of the pipeline (which reads
        # ``RoundSummary.correct_answer.text``) keeps working for estimate
        # rounds. The text is the true value with its unit.
        answer_text = self._format_estimate_value(question, answer_val)
        correct_answer = Answer(text=answer_text, correct=True)

        estimate_block = self._build_estimate_reveal(question, answer_val, scores)

        self._round_summary = RoundSummary(
            question=question,
            correct_answer=correct_answer,
            fun_fact=question.fun_fact,
            results=results,
            leaderboard=leaderboard,
            estimate=estimate_block,
        )

        self.phase = GamePhase.ANSWER_REVEAL

        # Per-question stats: estimate rounds record an "exact-or-not" signal +
        # elapsed so the stat pipeline stays populated. Closeness has no clean
        # correct/wrong, so an exact hit counts as correct.
        if self._question_stats is not None:
            try:
                submitted_results = [
                    (bool(scores.get(p.name, {}).get("exact")), p.last_elapsed)
                    for p in self._player_registry.players.values()
                    if p.submitted
                ]
                self._question_stats.record_round(question.id, submitted_results)
            except Exception:  # noqa: BLE001
                _LOGGER.exception("Failed to record estimate question stats")

        for player in self._player_registry.players.values():
            player.joined_late = False

        _LOGGER.info(
            "Estimate round %d evaluated (answer=%s, %d guesses), "
            "transitioning to ANSWER_REVEAL",
            self.round,
            answer_val,
            len(guesses),
        )
        self._fire_broadcast("round_evaluated")

        return self._round_summary

    @staticmethod
    def _format_estimate_value(question: Question, value: float) -> str:
        """Format an estimate value for display (drop trailing .0, add unit)."""
        text = str(int(value)) if value == int(value) else str(value)
        unit = question.estimate_unit
        return f"{text} {unit}".strip() if unit else text

    def _build_estimate_reveal(
        self,
        question: Question,
        answer_val: float,
        scores: dict[str, dict],
    ) -> dict[str, Any]:
        """Assemble the number-line reveal block for an estimate round (#275).

        Carries the true value, the slider range/unit, and every CONNECTED
        player's guess + distance + awarded points + rank, plus the winner
        (rank 1). Both the player reveal and the TV dashboard render the
        number line from this. The closest player(s) are rank 1; ties share it.
        """
        guesses_out: list[dict[str, Any]] = []
        winner_name = ""
        best_rank: int | None = None
        for player in self._player_registry.players.values():
            if not player.connected:
                continue
            entry = scores.get(player.name)
            if entry is None or player.current_guess is None:
                guesses_out.append({
                    "player_name": player.name,
                    "color": player.color,
                    "guess": None,
                    "distance": None,
                    "points": 0,
                    "rank": None,
                    "exact": False,
                    "no_guess": True,
                })
                continue
            rank = entry["rank"]
            if best_rank is None or rank < best_rank:
                best_rank = rank
                winner_name = player.name
            guesses_out.append({
                "player_name": player.name,
                "color": player.color,
                "guess": player.current_guess,
                "distance": entry["distance"],
                "points": entry["points"],
                "rank": rank,
                "exact": entry["exact"],
                "no_guess": False,
            })

        return {
            "answer": answer_val,
            "answer_text": self._format_estimate_value(question, answer_val),
            "min": question.estimate_min,
            "max": question.estimate_max,
            "unit": question.estimate_unit,
            "step": question.estimate_step,
            "guesses": guesses_out,
            "winner": winner_name,
        }

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

        # Cache podium and superlatives once so get_state_snapshot() can reuse
        # them. In team mode the participants are the teams (#365) — the podium
        # and the awards are computed by the same code, one rung up.
        self._collect_team_powerup_stats()
        participants = self.get_ranked_participants()
        self._finale_podium = calculate_podium(participants)
        self._finale_superlatives = compute_superlatives(participants)

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
            # Bind to a local so the narrowed (non-None) type survives into the
            # nested coroutine closure (mypy can't assume the attribute stays
            # non-None across the await).
            question_stats = self._question_stats

            async def _save_qs() -> None:
                try:
                    await question_stats.save_if_dirty()
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

        # Bind the narrowed (non-None) values to locals so they survive into
        # the nested coroutine closure below (mypy re-widens self.* across the
        # await boundary).
        stats_service = self._stats_service
        game_id = self.game_id

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
                await stats_service.record_game(
                    game_id=game_id,
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
                return
            # #624: only NOW does the all-time table include the game that just
            # finished. The finale is broadcast long before this — deliberately,
            # so a slow disk never delays the end screen — so a standing sent
            # with the finale would be the one from BEFORE this game and would
            # contradict the podium the player is looking at.
            #
            # Hence a second, later event. A player who has already closed the
            # tab simply never receives it, which is the correct outcome.
            self._fire_broadcast("analytics_recorded")

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
        # Drop the memoized round-summary message (#414) with the rest of the
        # round state so a fresh game never serves a stale summary.
        self._round_summary_msg = None
        self._round_summary_msg_key = None
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
        # Auto-trigger bookkeeping (#285) — re-armed by the next start_game.
        self._lightning_target_round = None
        self._lightning_fired = False
        self._round_to_resume = 0
        self._hot_seat = None
        self._hot_seat_target_round = None
        self._hot_seat_fired = False
        self._hot_seat_round_to_resume = 0

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

    def _pick_lightning_round(self, num_rounds: int) -> int | None:
        """Pick the round the auto Lightning Round fires before (#285).

        The eligible window is rounds ``3 … num_rounds-1`` (1-based): the
        first two rounds are blocked so the game establishes itself, and the
        final round is blocked so the Lightning Round never replaces the
        climactic last question. Returns a uniformly-random round in that
        window, or ``None`` when the toggle is off OR the window is empty
        (``num_rounds < 4`` — a short game simply skips the Lightning Round,
        we never force one).
        """
        if not self._lightning_enabled:
            return None
        low, high = 3, num_rounds - 1
        if high < low:
            return None  # window empty → short game, no Lightning Round
        return self._lightning_rng.randint(low, high)

    @property
    def lightning_target_round(self) -> int | None:
        """The round the auto Lightning Round fires before, or None (#285)."""
        return self._lightning_target_round

    def should_trigger_lightning(self) -> bool:
        """True iff the auto Lightning Round should fire right now (#285).

        Consulted by the WS round-advance path BEFORE it starts the next
        normal question. Fires exactly once per game, when the game is about
        to enter the pre-picked target round (``self.round`` counts completed
        rounds, so ``round + 1`` is the round we are about to start). Guarded
        by ``_lightning_fired`` for the once-per-game guarantee and only from
        a between-rounds phase.
        """
        if self._lightning_fired or self._lightning_target_round is None:
            return False
        if self.phase not in (GamePhase.LOBBY, GamePhase.ANSWER_REVEAL):
            return False
        return self.round + 1 == self._lightning_target_round

    def start_lightning_round(
        self,
        *,
        category: str | None = None,
        categories: list[str] | None = None,
        difficulty: str | None = None,
        language: str | None = None,
        auto: bool = False,
    ) -> bool:
        """Begin a lightning round, reusing the current player roster.

        Returns True if it started, False if no questions were available or
        the phase is wrong.

        ``auto=True`` is the #285 mid-game auto path: it remembers the
        in-progress round so the game can resume the normal flow after the
        recap, marks the once-per-game flag, and is allowed to fire from
        ANSWER_REVEAL (between two normal rounds). ``auto=False`` keeps the
        legacy entry phases (LOBBY/FINALE/LIGHTNING_RECAP) used by tests and
        any remaining standalone callers.
        """
        from .lightning import LightningRound  # local import — avoid cycle

        if auto:
            if self.phase not in (GamePhase.LOBBY, GamePhase.ANSWER_REVEAL):
                return False
        elif self.phase not in (
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
            # A team answers lightning together, like a normal round (#552).
            # The roster is a snapshot: teams are frozen from the start of the
            # game, so the detour cannot be joined or left mid-round.
            teams=self._team_registry.to_list(),
        )
        # Reserve the questions the main game still owes the host (#544). Only
        # the auto path detours out of a running game; the lobby/finale entries
        # have no remaining rounds to protect.
        reserve = max(0, self.total_rounds - self.round) if auto else 0
        if not lr.start(reserve=reserve):
            return False

        if auto:
            # Remember the normal round we detoured out of so the game resumes
            # there after the recap, and burn the once-per-game flag.
            self._round_to_resume = self.round
            self._lightning_fired = True

        self._lightning = lr
        self.phase = GamePhase.LIGHTNING
        # Open on the intro splash (issue #201). In the auto flow (#285) the
        # WS loop advances out of it on a grace timer (no host tap);
        # begin_lightning_questions() still performs the transition.
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

    @property
    def in_lightning_detour(self) -> bool:
        """True while the auto Lightning Round is interrupting a live game (#285).

        Distinguishes the mid-game auto detour (a normal game is paused in the
        background, ``_round_to_resume`` > 0) from a standalone lightning
        session started from the lobby. The WS layer uses this to decide
        whether the recap's advance returns to the main game or to the lobby.
        """
        return self._round_to_resume > 0

    def resume_after_lightning(self) -> bool:
        """Return to the paused main game after the auto Lightning recap (#285).

        Flips LIGHTNING_RECAP back to ANSWER_REVEAL and restores the round
        counter so the next ``start_next_question`` proceeds into the
        originally-scheduled round. Returns True if a detour was active, False
        otherwise (a standalone lightning session has no main game to resume).
        """
        if self.phase != GamePhase.LIGHTNING_RECAP or not self.in_lightning_detour:
            return False
        self.round = self._round_to_resume
        self._round_to_resume = 0
        self._lightning = None
        self._lightning_splash_pending = False
        self.phase = GamePhase.ANSWER_REVEAL
        self._notify_state_callbacks()
        return True

    # ------------------------------------------------------------------
    # Hot Seat auction (issue #616)
    # ------------------------------------------------------------------
    #
    # Thin wrappers around a HotSeatRound, mirroring the Lightning block
    # above: the rules live in game/hot_seat.py, this class owns only the
    # reference and the phase transitions so the WS handler has one caller.

    def _pick_hot_seat_round(self, num_rounds: int) -> int | None:
        """Pick the round the Hot Seat auction fires before (#616).

        Same window as the Lightning Round — rounds ``3 … num_rounds-1`` — for
        the same two reasons: the game should establish itself first, and the
        last question stays the climax rather than being replaced by a detour.

        The draw additionally avoids the Lightning target. Two detours in one
        round would mean the second fires immediately after the first's recap,
        with no normal question between them; the round would read as broken
        even though both modes behaved correctly.
        """
        if not self._hot_seat_enabled:
            return None
        low, high = 3, num_rounds - 1
        if high < low:
            return None  # window empty → short game, no auction
        candidates = [
            r for r in range(low, high + 1) if r != self._lightning_target_round
        ]
        if not candidates:
            # A 4-round game has a one-round window that Lightning already
            # owns. Lightning was armed first, so it keeps it.
            return None
        return candidates[self._hot_seat_rng.randrange(len(candidates))]

    @property
    def hot_seat_target_round(self) -> int | None:
        """The round the Hot Seat auction fires before, or None (#616)."""
        return self._hot_seat_target_round

    @property
    def hot_seat(self):
        """The active HotSeatRound, or None."""
        return self._hot_seat

    @property
    def in_hot_seat_detour(self) -> bool:
        """True while the auction is interrupting a live game (#616)."""
        return self._hot_seat_round_to_resume > 0

    def should_trigger_hot_seat(self) -> bool:
        """True iff the Hot Seat auction should fire right now (#616).

        Consulted by the WS round-advance path before it starts the next
        normal question, exactly like ``should_trigger_lightning``. Fires once
        per game, only from a between-rounds phase.
        """
        if self._hot_seat_fired or self._hot_seat_target_round is None:
            return False
        if self.phase not in (GamePhase.LOBBY, GamePhase.ANSWER_REVEAL):
            return False
        return self.round + 1 == self._hot_seat_target_round

    def start_hot_seat_auction(self) -> bool:
        """Open the sealed bidding window. False means "skip, play on".

        False is a normal outcome, not an error: too few players to hold an
        auction, or no question the main game can spare (#544). Either way the
        once-per-game flag is burned so the attempt is not retried every round.
        """
        from .hot_seat import HotSeatRound  # local import — avoid cycle

        if self.phase not in (GamePhase.LOBBY, GamePhase.ANSWER_REVEAL):
            return False

        scores = {
            name: p.score for name, p in self._player_registry.players.items()
        }
        hs = HotSeatRound(
            self._question_bank,
            scores,
            language=self.language,
            category=self.category,
            categories=getattr(self, "categories", None),
            difficulty=self.difficulty,
        )
        # Same reservation rule as Lightning (#544): never spend a question the
        # main game still owes a later round.
        reserve = max(0, self.total_rounds - self.round)
        self._hot_seat_fired = True
        if not hs.start(reserve=reserve):
            return False

        self._hot_seat = hs
        self._hot_seat_round_to_resume = self.round
        self.phase = GamePhase.HOT_SEAT_AUCTION
        self._notify_state_callbacks()
        return True

    def close_hot_seat_auction(self) -> str | None:
        """Award the chair and move into the question. Returns the winner.

        None means nobody bid above zero — the driver treats that as "no
        auction happened" and resumes the normal round, because a chair
        nobody wanted is not a round worth playing.
        """
        if self.phase != GamePhase.HOT_SEAT_AUCTION or self._hot_seat is None:
            return None
        winner = self._hot_seat.resolve_auction()
        if winner is None:
            return None
        self.phase = GamePhase.HOT_SEAT
        self._notify_state_callbacks()
        return winner

    def finish_hot_seat(self) -> dict[str, int]:
        """Settle the round and move to the reveal. Returns the point deltas.

        Applying the deltas is this method's job rather than the caller's, so
        an unanswered question cannot quietly skip settlement — that is the
        exact failure this mode had to design against (#653).
        """
        if self._hot_seat is None:
            return {}
        deltas = self._hot_seat.settle()
        for name, delta in deltas.items():
            player = self._player_registry.players.get(name)
            if player is None:
                continue
            player.score += delta
            if player.score < 0:
                player.score = 0
        if self.phase == GamePhase.HOT_SEAT:
            self.phase = GamePhase.HOT_SEAT_REVEAL
            self._flush_history()
            self._notify_state_callbacks()
        return deltas

    def resume_after_hot_seat(self) -> bool:
        """Return to the paused main game after the reveal (#616).

        Mirrors ``resume_after_lightning``: flips back to ANSWER_REVEAL and
        restores the round counter so the next advance lands on the
        originally-scheduled round.
        """
        if self.phase != GamePhase.HOT_SEAT_REVEAL or not self.in_hot_seat_detour:
            return False
        self.round = self._hot_seat_round_to_resume
        self._hot_seat_round_to_resume = 0
        self._hot_seat = None
        self.phase = GamePhase.ANSWER_REVEAL
        self._notify_state_callbacks()
        return True

    def abort_hot_seat(self) -> None:
        """Drop an auction that found no bidder and return to the main game."""
        if self._hot_seat is None:
            return
        self.round = self._hot_seat_round_to_resume or self.round
        self._hot_seat_round_to_resume = 0
        self._hot_seat = None
        if self.phase in (GamePhase.HOT_SEAT_AUCTION, GamePhase.HOT_SEAT):
            self.phase = GamePhase.ANSWER_REVEAL
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

        # Estimate-round no-op gate (#406). On estimate rounds the scoring path
        # (_evaluate_estimate_round) applies points later at reveal, never reads
        # is_double_points_active, and the question carries no MC answers. So
        # STEAL reads a still-zero target.round_score (steals 0), DOUBLE_POINTS
        # is silently ignored, and JOKER has an empty question.answers to prune —
        # all three would burn the once-per-game power-up for nothing. Reject
        # them so the inventory survives for a later MC round. FREEZE/TIME_BOOST
        # are pure timer effects that behave identically on estimate rounds and
        # stay usable.
        if question.is_estimate and held in (
            PowerUpType.JOKER,
            PowerUpType.DOUBLE_POINTS,
            PowerUpType.STEAL,
        ):
            return ERR_INVALID_ACTION

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
            # Subtract instead of clamping to 0: `stolen` is already bounded by
            # max(0, round_score) // 2, so the subtraction can never drive a
            # positive round_score below 0, and a negative round_score (wrong
            # final-round wager) yields stolen == 0, making this a true no-op.
            # The old `max(0, round_score - stolen)` rewrote a negative
            # round_score to 0 while stealing nothing, so at reveal
            # _do_evaluate_round reported points_earned == 0 and history logged
            # 0 even though the total had already dropped by the wager
            # (reveal/history desync, #484).
            target_player.round_score -= stolen
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

    def has_live_timers(self, player_names: list[str]) -> bool:
        """Whether any supplied player still holds a per-player timer (#586).

        Pairs with ``round_wall_clock_expired`` in the countdown loop: the
        wall-clock fallback applies exactly when no supplied player has a
        timer. Delegated to the PhaseController.
        """
        return self._phase_controller.has_live_timers(player_names)

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

        return serialize_leaderboard(self.get_ranked_participants())

    def get_ranked_participants(self) -> list[Any]:
        """Whoever the ranking is about: teams in team mode, else players (#365).

        Both answer to the same attribute names, so every leaderboard call site
        can take this without caring which it got — that is the whole reason
        the dashboard, reveal and finale need no team-specific rendering path.
        """
        if not self.team_mode:
            return self.get_players()
        # A player who joined no team is a team of one, not an error state
        # (Markus, 2026-08-12) — so they keep their own row next to the teams
        # rather than dropping out of the ranking. Found by playing it: with
        # teams returned alone, the one guest who stayed solo vanished from the
        # leaderboard, the TV and the podium.
        teams: list[Any] = list(self._team_registry.all_teams())
        solo = [
            p
            for p in self.get_players()
            if self._team_registry.get_by_member(p.name) is None
        ]
        return teams + solo

    def get_round_summary(self) -> RoundSummary | None:
        """Return the last round summary."""
        return self._round_summary

    def all_submitted(self) -> bool:
        """Whether every active, non-late participant has submitted (#412).

        Public accessor over the registry check so the WS layer can re-test the
        all-answered condition (e.g. after the last unanswered player drops
        mid-question) without reaching into ``_player_registry``.
        """
        if self.team_mode:
            # Never, in team mode (#365). The rule is "the answer standing when
            # the clock stops is the team's answer", so the round has to run to
            # the timer — ending it the moment every team has *an* answer would
            # close it on the first tap and there would be nothing left to
            # re-decide. Found by playing it: with one team the round ended
            # before the second member had looked up.
            return False
        return self._player_registry.all_submitted()

    def get_cached_round_summary_msg(
        self, key: tuple[str | None, int]
    ) -> dict[str, Any] | None:
        """Return the memoized round-summary message for ``key`` (#414).

        ``key`` is ``(game_id, round)``. Returns ``None`` on a miss; callers
        must build the dict and store it via ``store_round_summary_msg``. The
        returned dict is shared — treat it read-only or ``dict()``-copy before
        mutating.
        """
        if self._round_summary_msg_key == key:
            return self._round_summary_msg
        return None

    def store_round_summary_msg(
        self, key: tuple[str | None, int], msg: dict[str, Any]
    ) -> None:
        """Memoize the round-summary message ``msg`` under ``key`` (#414)."""
        self._round_summary_msg_key = key
        self._round_summary_msg = msg

    def invalidate_round_summary_msg(self) -> None:
        """Drop the memoized round-summary message (#449).

        The #414 memo caches the full round-summary msg (incl. the serialized
        leaderboard) keyed on ``(game_id, round)``. A reveal-time reaction
        bonus (#416) mutates recipient scores via ``add_reaction_bonus`` while
        that memo is still live, so any join/reconnect/get_state during the
        same ANSWER_REVEAL would otherwise serve a STALE pre-bonus leaderboard.
        Call this after applying reaction bonuses so the next build
        re-serializes the fresh scores.
        """
        self._round_summary_msg = None
        self._round_summary_msg_key = None

    def get_finale_podium(self) -> list | None:
        """Return the finale podium cached by ``end_game`` (#415), or None."""
        return self._finale_podium

    def get_finale_superlatives(self) -> list | None:
        """Return the finale superlatives cached by ``end_game`` (#415)."""
        return self._finale_superlatives

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
            # Always present, empty in an ordinary game (#365). A reconnecting
            # phone has to be able to tell "no teams" from "teams not sent" —
            # otherwise a member who drops mid-game comes back without their
            # team indicator and believes they are playing alone.
            "teams": self._team_registry.to_list(),
        }

        if self.phase == GamePhase.QUESTION_ACTIVE and self._current_question:
            q = self._current_question
            # Calculate time remaining for mid-round joiners
            remaining = self._phase_controller.time_remaining_for_snapshot()
            # Canonical-shuffle order (#521), matching the live
            # ``question_started`` payload. Emitting question-JSON order here
            # meant a dashboard reconnecting mid-question rebuilt its grid
            # unshuffled — and most packs keep the correct answer first in the
            # file. ``shuffled_answers`` is empty only before the first
            # question of a game, where the fallback is the same list anyway.
            snapshot["question"] = {
                "id": q.id,
                "text": q.question,
                "answers": (
                    list(self.shuffled_answers)
                    if len(self.shuffled_answers) == len(q.answers)
                    else [a.text for a in q.answers]
                ),
                "difficulty": q.difficulty,
                "category": q.category,
                "image_url": q.image_url,
                # #434: a dashboard that reconnects mid-question needs both the
                # style and the remaining time below to resume the blur at the
                # right point instead of snapping to sharp.
                "reveal_style": q.reveal_style,
                "time_limit": self._round_duration,
                "time_remaining": round(remaining, 1),
            }
            # Estimate questions (#275) carry slider metadata instead of
            # answers so a reconnecting player rebuilds the slider, not the
            # 3-answer grid.
            if q.is_estimate:
                snapshot["question"]["question_type"] = q.type
                snapshot["question"]["estimate"] = {
                    "min": q.estimate_min,
                    "max": q.estimate_max,
                    "unit": q.estimate_unit,
                    "step": q.estimate_step,
                }

        if self.phase == GamePhase.ANSWER_REVEAL and self._round_summary:
            s = self._round_summary
            q = s.question
            # Round-shuffle answer order, mirroring the QUESTION_ACTIVE
            # snapshot's ``question.answers``. A TV/dashboard that (re)connects
            # during the reveal has no live ``question`` block to render, so
            # without these fields its question view was blank (#296).
            # Both the order and the highlight index moved from question-JSON
            # order to the round shuffle in #521 — in JSON order most packs
            # keep the correct answer first, which put it on tile A every
            # round. ``correct_answer_index_original`` stays in the payload for
            # clients cached from before that change.
            correct_idx_original = next(
                (i for i, a in enumerate(q.answers) if a.correct), -1
            )
            order = self.shuffle_map
            if len(order) == len(q.answers) and sorted(order) == list(
                range(len(q.answers))
            ):
                reveal_answers = [q.answers[i].text for i in order]
                if correct_idx_original >= 0:
                    correct_idx_display = order.index(correct_idx_original)
                else:
                    correct_idx_display = -1
            else:
                # No usable shuffle (pre-first-question, or a malformed map):
                # a mis-ordered grid is worse than an unshuffled one.
                reveal_answers = [a.text for a in q.answers]
                correct_idx_display = correct_idx_original
            snapshot["round_summary"] = {
                "question_text": q.question,
                "category": q.category,
                "image_url": q.image_url,
                "answers": reveal_answers,
                "correct_answer_index": correct_idx_display,
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
            # Estimate reveal data (#275) so a reconnect during the reveal
            # rebuilds the number line instead of an empty answer grid.
            if s.estimate is not None:
                snapshot["round_summary"]["question_type"] = q.type
                snapshot["round_summary"]["estimate"] = s.estimate

        if self.phase == GamePhase.FINALE:
            # Use cached values computed once in end_game()
            podium = self._finale_podium or calculate_podium(
                self.get_ranked_participants()
            )
            snapshot["podium"] = [
                {"name": p.name, "score": p.score, "rank": i + 1}
                for i, p in enumerate(podium)
            ]
            awards = (
                self._finale_superlatives
                if self._finale_superlatives is not None
                else compute_superlatives(self.get_ranked_participants())
            )
            if awards:
                snapshot["superlatives"] = [s.to_dict() for s in awards]

        if self.phase == GamePhase.LIGHTNING and self._lightning is not None:
            lr = self._lightning
            lq = lr.current_question
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
            if lq is not None:
                # Canonical (admin/TV) answer order; players get their own
                # shuffle pushed via the lightning_question event.
                snapshot["lightning"]["question"] = {
                    "text": lq.question,
                    "answers": [a.text for a in lq.answers],
                    "category": lq.category,
                    "image_url": lq.image_url,
                }

        if self.phase == GamePhase.LIGHTNING_RECAP and self._lightning is not None:
            snapshot["lightning_recap"] = self._lightning.build_recap()

        return snapshot

    # ------------------------------------------------------------------
    # Broadcast / event dispatch
    # ------------------------------------------------------------------

    def set_broadcast_callback(
        self, callback: Callable[[dict[str, Any]], Coroutine[Any, Any, None]]
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


