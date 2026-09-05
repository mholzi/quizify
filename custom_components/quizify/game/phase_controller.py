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
from dataclasses import dataclass, field
from enum import Enum

from ..const import DEFAULT_ROUND_DURATION, WAGER_WINDOW_DURATION
from .timer import QuestionTimer

_LOGGER = logging.getLogger(__name__)

# Cadence of the per-question countdown broadcast, in seconds. Owned here
# (rather than in the connection layer) so all timing constants live with the
# timing logic. Behaviour-preserving: this is the same 0.5s the tick loop has
# always slept between broadcasts.
TICK_INTERVAL = 0.5


@dataclass
class TickResolution:
    """Pure timing result for one broadcast iteration of the countdown loop.

    Produced by :meth:`PhaseController.resolve_tick`; consumed by the
    connection layer, which turns it into ``timer_tick`` wire messages. Holds no
    websocket / connection state — only player names and their authoritative
    remaining seconds, plus the value dashboards/admins show (the minimum across
    all timed players so the shared TV countdown is consistent).
    """

    # (player_name, remaining_seconds) for every player that currently has a
    # timer, in the order the names were supplied. Remaining is clamped at 0.
    per_player: list[tuple[str, float]] = field(default_factory=list)
    # Minimum remaining across ``per_player`` (0.0 when nobody has a timer) —
    # the value broadcast to dashboards and pure-admin connections.
    dashboard_remaining: float = 0.0


class GamePhase(str, Enum):  # noqa: UP042 — StrEnum changes str()/serialization
    """Game phase states."""

    LOBBY = "LOBBY"
    # WAGER_ACTIVE (#656) — the final round's betting window. The question is
    # loaded, but its text has not been sent and no timer is running: players
    # see the category and place their bet on that alone, Jeopardy-style.
    # Closing the window arms the timers and flips to QUESTION_ACTIVE.
    #
    # It is a real phase rather than a flag on QUESTION_ACTIVE because
    # everything that reads QUESTION_ACTIVE would be wrong here: the tick
    # loop's "connected players, no timers" fallback (#586) would end the
    # round instantly, and the reconnect snapshot would hand out the very
    # question text the window exists to withhold.
    WAGER_ACTIVE = "WAGER_ACTIVE"
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
    # Hot Seat auction (issue #616) — a second self-contained detour. The
    # chair is bought, not assigned: HOT_SEAT_AUCTION is the sealed bidding
    # window, HOT_SEAT is the single question the winner answers alone while
    # everyone else may stake on the outcome, and HOT_SEAT_REVEAL shows the
    # bids, the answer and the point swings. The rules live in
    # game/hot_seat.py, not in this class.
    HOT_SEAT_AUCTION = "HOT_SEAT_AUCTION"
    HOT_SEAT = "HOT_SEAT"
    HOT_SEAT_REVEAL = "HOT_SEAT_REVEAL"
    # PAUSED — admin-triggered pause during QUESTION_ACTIVE. Timer is
    # frozen; resume returns to QUESTION_ACTIVE with the remaining time
    # the player had before pause. Used both for explicit "Pause" button
    # and for graceful host-disconnect handling.
    PAUSED = "PAUSED"


# Phases in which a main-round question is genuinely in flight, i.e. the answer
# clock for the current round is running (or frozen mid-run). Someone joining
# during one of these cannot possibly answer that question in time, so they are
# flagged ``joined_late`` and excluded from ``all_submitted()`` until the round
# they walked into has been evaluated.
#
# This is deliberately an allowlist rather than "everything except LOBBY"
# (#727): every phase NOT named here is a between-rounds phase where the next
# question has not started yet, and a joiner there gets a full timer from
# ``begin_round`` like everybody else.
#
#   * ANSWER_REVEAL — the previous round is already scored.
#   * WAGER_ACTIVE (#656) — the final round's betting window. No timer runs;
#     ``begin_round`` builds one for every player present when the window
#     closes, this joiner included.
#   * LIGHTNING / LIGHTNING_RECAP (#42) and HOT_SEAT_AUCTION / HOT_SEAT /
#     HOT_SEAT_REVEAL (#616) — self-contained detours with their own rosters
#     and scoring. Neither reads ``joined_late``, and the main round they hand
#     back to has not begun.
#   * FINALE — the game is over. The flag would only survive into a
#     "play again" round.
#
# PAUSED belongs here because ``PhaseController.pause`` only ever enters it
# from QUESTION_ACTIVE: a paused game is a live question with its clocks
# frozen, and ``resume`` hands the joiner the round's shared remaining (#702).
MID_QUESTION_PHASES: frozenset[str] = frozenset(
    {GamePhase.QUESTION_ACTIVE.value, GamePhase.PAUSED.value}
)


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

        # Final-round betting window (#656). Its own clock, deliberately kept
        # apart from the round timing above: the round has not started while
        # this one runs. None outside WAGER_ACTIVE.
        self.wager_window_start: float | None = None
        self.wager_window_duration: float = WAGER_WINDOW_DURATION

        # Pause bookkeeping.
        self.paused_from: GamePhase | None = None
        self.paused_remaining: dict[str, float] = {}
        self.pause_reason: str | None = None
        # Per-player elapsed at pause + the monotonic instant we paused, so
        # resume() can preserve both the frozen remaining AND the elapsed
        # (speed bonus) and advance the round wall-clock by the pause duration
        # (#295). round_start_time alone is wall-clock and would otherwise keep
        # running through the pause, desyncing it from the frozen per-player
        # timers (a late joiner post-resume would get a ~0.5s timer, etc.).
        self.paused_elapsed: dict[str, float] = {}
        # Per-player freeze-lockout remaining at pause (#451). A freeze is a
        # submit LOCKOUT with its own end-time (QuestionTimer._frozen_until);
        # QuestionTimer.resumed() hardcodes it to None, so without snapshotting
        # + re-applying it a target paused mid-lockout would resume fully
        # UNFROZEN and the server would accept a submit the client still shows
        # as frozen. Only players actually frozen at pause get an entry.
        self.paused_frozen: dict[str, float] = {}
        self.paused_at: float | None = None

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
            timer = self._late_joiner_timer()
            if timer is not None:
                self.timers[name] = timer

    def _late_joiner_timer(self) -> QuestionTimer | None:
        """Build a timer on the round's SHARED wall-clock (#355, #702).

        Used for anyone who was not present when the round began: someone who
        joins mid-question, and (since #702) someone who joins while the game
        is paused. Returns None when there is no round to hang a clock on.

        A plain ``QuestionTimer(remaining).start()`` would make get_elapsed()
        count from JOIN, so a player joining with 5s left and answering in 2s
        scores time_fraction ≈ 0.93 (near-max speed bonus) — beating an on-time
        player answering at the same wall-clock instant (≈ 0.17). Seeding
        elapsed = now - round_start_time (the exact pause/resume trick) makes
        the late joiner's get_elapsed() identical to an on-time player's, so
        speed scoring is consistent. The remaining carries a 0.5s floor as the
        late-join grace, so they can still answer the in-flight question.
        """
        if self.round_start_time is None:
            return None
        elapsed = time.monotonic() - self.round_start_time
        remaining = max(0.5, self.round_duration - elapsed)
        return QuestionTimer.resumed(remaining=remaining, elapsed=elapsed)

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

    def begin_wager_window(
        self, round_duration: float, window_duration: float
    ) -> None:
        """Open the final round's betting window (#656).

        Records the duration the round *will* run for without stamping
        ``round_start_time`` or building a single timer — nothing is counting
        down towards the answer yet. That is the whole point: the window has
        its own clock (``wager_window_remaining``), and the round proper only
        starts when the caller follows up with ``begin_round``.

        Leaving ``round_start_time`` at None also keeps
        ``round_wall_clock_expired`` False for the duration of the window, so
        no fallback can evaluate a round that has not started.
        """
        self.round_duration = round_duration
        self.round_start_time = None
        self.timers.clear()
        self.wager_window_duration = window_duration
        self.wager_window_start = time.monotonic()
        self.phase = GamePhase.WAGER_ACTIVE

    def wager_window_remaining(self) -> float:
        """Seconds left in the betting window, clamped at zero.

        Feeds the reconnect snapshot so a phone that drops mid-window comes
        back to the countdown the rest of the room is watching, rather than a
        fresh 20 seconds.
        """
        if self.wager_window_start is None:
            return 0.0
        elapsed = time.monotonic() - self.wager_window_start
        return max(0.0, self.wager_window_duration - elapsed)

    def enter_question_active(self) -> None:
        """Flip the phase to QUESTION_ACTIVE."""
        self.wager_window_start = None
        self.phase = GamePhase.QUESTION_ACTIVE

    def get_timer(self, name: str) -> QuestionTimer | None:
        """Return the authoritative timer for a player, or None."""
        return self.timers.get(name)

    def resolve_tick(self, player_names: list[str]) -> TickResolution:
        """Resolve the authoritative remaining time for one countdown tick.

        Reads each player's :class:`QuestionTimer` ONCE (an O(1) dict lookup
        each) and returns the per-player remaining plus the dashboard minimum.
        Players without a timer (not yet joined this round) are skipped. The
        result carries no connection state — the caller decides which of these
        players are connected and turns the numbers into ``timer_tick``
        messages. Behaviour matches the previous inline ``tick_state``
        comprehension exactly: same order, same ``max(0.0, …)`` clamp, same
        ``min(..., default=0.0)`` dashboard value.
        """
        per_player = [
            (name, max(0.0, timer.get_remaining()))
            for name in player_names
            if (timer := self.timers.get(name)) is not None
        ]
        dashboard_remaining = min(
            (remaining for _, remaining in per_player), default=0.0
        )
        return TickResolution(
            per_player=per_player,
            dashboard_remaining=dashboard_remaining,
        )

    def all_timers_expired(self, player_names: list[str]) -> bool:
        """Whether every supplied player has an expired timer.

        The countdown loop's stop condition: the round ends once all
        *connected* players' timers have run out. A player without a timer is
        ignored (not treated as expired) — that guards the late-joiner /
        admin-self-join window where a connected player exists before their
        per-player timer does. Returns False when no supplied player has a timer
        yet (nothing to expire), matching the previous inline check that
        required at least one live timer before it could break.
        """
        timers = [
            timer
            for name in player_names
            if (timer := self.timers.get(name)) is not None
        ]
        return bool(timers) and all(timer.is_expired() for timer in timers)

    def has_live_timers(self, player_names: list[str]) -> bool:
        """Whether any of *player_names* currently holds a per-player timer.

        The countdown loop pairs this with ``round_wall_clock_expired`` to
        decide whether the wall-clock fallback applies. ``all_timers_expired``
        can only break the loop once at least one supplied player has a timer,
        so the loop needs a way to recognise the opposite state — connected
        players, no timers — and fall back to the shared round clock (#586).
        """
        return any(self.timers.get(name) is not None for name in player_names)

    def round_wall_clock_expired(self) -> bool:
        """Whether the round's wall-clock has run out, regardless of timers.

        Fallback stop condition for the countdown loop: when every player has
        disconnected mid-question there are no connected per-player timers left
        to expire, so ``all_timers_expired`` (which needs at least one live
        timer) can never break the loop and it spins until the admin force-ends
        the game. This compares the shared round wall-clock — the same
        ``round_duration - (now - round_start_time)`` used for snapshots — so
        the round still evaluates (and the admin can advance) once the nominal
        duration has elapsed even with zero connected players. (#255.)

        Returns False before a round has started (no ``round_start_time``).
        """
        if self.round_start_time is None:
            return False
        return self.time_remaining_for_snapshot() <= 0.0

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
        # Snapshot remaining AND elapsed time per player and freeze timers in
        # place. On resume we rebuild timers that preserve BOTH (so the speed
        # bonus, which scores off elapsed, isn't inflated — #295/#254).
        self.paused_remaining = {}
        self.paused_elapsed = {}
        self.paused_frozen = {}
        for name, timer in self.timers.items():
            self.paused_remaining[name] = max(0.0, timer.get_remaining())
            self.paused_elapsed[name] = max(0.0, timer.get_elapsed())
            # Preserve an active freeze lockout across the pause (#451):
            # QuestionTimer.resumed() rebuilds a fully-unfrozen timer, so we
            # snapshot the remaining lockout here and re-apply it in resume().
            frozen_left = timer.frozen_remaining()
            if frozen_left > 0.0:
                self.paused_frozen[name] = frozen_left
        self.timers.clear()
        # Remember when we paused so resume() can shift the round wall-clock by
        # the exact pause duration, keeping round_start_time in lock-step with
        # the frozen per-player remaining (#295).
        self.paused_at = time.monotonic()
        self.phase = GamePhase.PAUSED
        _LOGGER.info("Game paused (reason=%s)", reason)
        return True

    def resume(self) -> bool:
        """Resume a paused game. Returns True if resume happened."""
        if self.phase != GamePhase.PAUSED:
            return False
        # Advance the round wall-clock by the pause duration FIRST, so
        # round_start_time stays in lock-step with the frozen per-player
        # remaining (#295). Without this shift a long pause leaves
        # round_start_time far in the past: time_remaining_for_snapshot() and
        # round_wall_clock_expired() read stale, and a player joining
        # post-resume gets add_late_joiner_timer ≈ 0.5s (instantly expired).
        if self.paused_at is not None and self.round_start_time is not None:
            self.round_start_time += time.monotonic() - self.paused_at
        # Restore timers with the remaining time AND elapsed they had at pause
        # (QuestionTimer.resumed preserves both, so the speed bonus measured
        # from get_elapsed() isn't inflated — #295/#254). Someone who joined
        # while the game was paused is in no snapshot and gets the round's
        # shared remaining instead (#702).
        full = self.round_duration
        for name in self._players_fn():
            if name in self.paused_remaining:
                timer = QuestionTimer.resumed(
                    remaining=self.paused_remaining[name],
                    elapsed=self.paused_elapsed.get(name, 0.0),
                )
                # Re-apply the freeze lockout that was active at pause (#451).
                # resumed() builds an unfrozen timer, so a target frozen when
                # the game paused would otherwise resume able to submit.
                frozen_left = self.paused_frozen.get(name, 0.0)
                if frozen_left > 0.0:
                    timer.freeze(frozen_left)
            else:
                # Joined during the pause (#702). A fresh full clock made the
                # newcomer outlast everybody else — the tick loop only ends
                # once every connected timer has expired, and all_submitted()
                # ignores late joiners, so the whole room sat at 0:00 waiting
                # for one phone that had up to a full round left. The mid-
                # question join path already hands out the shared remaining;
                # this is the same rule for the paused path.
                late = self._late_joiner_timer()
                if late is None:  # no round wall-clock to hang it on
                    late = QuestionTimer(full)
                    late.start()
                timer = late
            self.timers[name] = timer
        self.paused_remaining = {}
        self.paused_elapsed = {}
        self.paused_frozen = {}
        self.paused_at = None
        self.pause_reason = None
        self.phase = self.paused_from or GamePhase.QUESTION_ACTIVE
        self.paused_from = None
        _LOGGER.info("Game resumed")
        return True
