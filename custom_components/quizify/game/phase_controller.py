"""PhaseController — game-flow phase state machine + per-question timing.

Extracted from ``QuizifyGameState`` (issue #188, continuing the God-object
split started in #184/#187). This module owns the *mechanical* core of the
game flow:

  * the authoritative :class:`GamePhase` value and its transitions
  * the per-player :class:`QuestionTimer` map and the round-timing bookkeeping
    (``round_start_time``, ``round_duration``)
  * pause / resume — freezing the per-player timers, remembering the phase to
    resume back into, and the pause reason

It deliberately does **not** know about scoring, the player registry, the
question bank, calibration or analytics. Those orchestration concerns stay in
``QuizifyGameState``, which drives this controller through narrow primitives
and reads/writes its state via delegating properties. Behaviour is identical to
the previous inline implementation — same phase transitions, same timing, same
pause/resume snapshot/restore, same message flow.

The controller needs to enumerate the current player names when (re)building
timers; it receives that through a ``players_fn`` callback rather than holding a
reference to the registry, keeping the dependency one-directional.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from enum import Enum

from ..const import DEFAULT_ROUND_DURATION
from .timer import QuestionTimer

_LOGGER = logging.getLogger(__name__)


class GamePhase(str, Enum):
    """Game phase states."""

    LOBBY = "LOBBY"
    QUESTION_ACTIVE = "QUESTION_ACTIVE"
    ANSWER_REVEAL = "ANSWER_REVEAL"
    FINALE = "FINALE"
    # Lightning Round (issue #42) — a self-contained fast bonus mode the
    # host can trigger after/instead of the normal game. LIGHTNING runs the
    # rapid 10-question loop (no inter-question reveal); LIGHTNING_RECAP is
    # the end screen showing totals + the per-question right/wrong grid.
    # The mode's logic lives in game/lightning.py, not in this class.
    LIGHTNING = "LIGHTNING"
    LIGHTNING_RECAP = "LIGHTNING_RECAP"
    # PAUSED — admin-triggered pause during QUESTION_ACTIVE. Timer is
    # frozen; resume returns to QUESTION_ACTIVE with the remaining time
    # the player had before pause. Used both for explicit "Pause" button
    # and for graceful host-disconnect handling.
    PAUSED = "PAUSED"


class PhaseController:
    """Owns the game phase + per-question timer mechanics.

    A single instance is held by each :class:`QuizifyGameState`. The host class
    delegates its ``phase`` and timing attributes here and calls the primitives
    below at the appropriate points in the game flow.
    """

    def __init__(self, players_fn: Callable[[], list[str]]) -> None:
        """Initialize.

        ``players_fn`` returns the current player names (registry keys), used
        when (re)building per-player timers.
        """
        self._players_fn = players_fn

        self.phase: GamePhase = GamePhase.LOBBY

        # Per-question timing.
        self.timers: dict[str, QuestionTimer] = {}  # player_id → timer
        self.round_start_time: float | None = None
        self.round_duration: float = DEFAULT_ROUND_DURATION

        # Pause bookkeeping.
        self.paused_from: GamePhase | None = None
        self.paused_remaining: dict[str, float] = {}
        self.pause_reason: str | None = None

    # ------------------------------------------------------------------
    # Timer mechanics
    # ------------------------------------------------------------------

    def add_late_joiner_timer(self, name: str) -> None:
        """Give a mid-round joiner a timer tracking the round's remaining time.

        Without this the tick loop's "all timers missing or expired → break"
        condition treats them as already done and the round evaluates ~1s after
        start when the late-joiner is the ONLY connected player (the
        admin-self-join + redirect flow). Late joiners can also still answer the
        in-flight question this way.
        """
        if (
            self.phase == GamePhase.QUESTION_ACTIVE
            and self.round_start_time is not None
            and name not in self.timers
        ):
            elapsed = time.monotonic() - self.round_start_time
            remaining = max(0.5, self.round_duration - elapsed)
            timer = QuestionTimer(remaining)
            timer.start()
            self.timers[name] = timer

    def drop_timer(self, name: str) -> None:
        """Drop a single player's timer (player removed)."""
        self.timers.pop(name, None)

    def clear_timers(self) -> None:
        """Drop every timer (full player wipe / reset)."""
        self.timers.clear()

    def begin_round(self, round_duration: float) -> None:
        """Open a fresh round: set the duration, stamp the start time and
        create+start a per-player timer for every current player.

        Transition to QUESTION_ACTIVE is left to ``enter_question_active`` so
        the caller controls ordering relative to its own per-round resets.
        """
        self.round_duration = round_duration
        self.round_start_time = time.monotonic()
        self.timers.clear()
        for name in self._players_fn():
            timer = QuestionTimer(self.round_duration)
            timer.start()
            self.timers[name] = timer

    def enter_question_active(self) -> None:
        """Flip the phase to QUESTION_ACTIVE."""
        self.phase = GamePhase.QUESTION_ACTIVE

    def get_timer(self, name: str) -> QuestionTimer | None:
        """Return the authoritative timer for a player, or None."""
        return self.timers.get(name)

    def time_remaining_for_snapshot(self) -> float:
        """Remaining time for a mid-round joiner's state snapshot.

        Mirrors the wall-clock calculation used when serializing
        QUESTION_ACTIVE: ``round_duration - (now - round_start_time)`` clamped
        at zero.
        """
        elapsed = (
            time.monotonic() - self.round_start_time
            if self.round_start_time
            else 0.0
        )
        return max(0.0, self.round_duration - elapsed)

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
        self.paused_from = GamePhase.QUESTION_ACTIVE
        self.pause_reason = reason
        # Snapshot remaining time per player and freeze timers in place.
        # On resume we'll create fresh timers with the saved remaining.
        self.paused_remaining = {}
        for name, timer in self.timers.items():
            self.paused_remaining[name] = max(0.0, timer.get_remaining())
        self.timers.clear()
        self.phase = GamePhase.PAUSED
        _LOGGER.info("Game paused (reason=%s)", reason)
        return True

    def resume(self) -> bool:
        """Resume a paused game. Returns True if resume happened."""
        if self.phase != GamePhase.PAUSED:
            return False
        # Restore timers with the remaining time they had at pause.
        # Late-joiners during PAUSED won't be in paused_remaining and
        # get a fresh full-round timer here.
        full = self.round_duration
        for name in self._players_fn():
            remaining = self.paused_remaining.get(name, full)
            timer = QuestionTimer(remaining)
            timer.start()
            self.timers[name] = timer
        self.paused_remaining = {}
        self.pause_reason = None
        self.phase = self.paused_from or GamePhase.QUESTION_ACTIVE
        self.paused_from = None
        _LOGGER.info("Game resumed")
        return True
