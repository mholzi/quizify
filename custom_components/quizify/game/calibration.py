"""Group-level adaptive difficulty calibration (#40).

The game serves one shared question to every connected player per round, at a
single difficulty for that round. When the host picks the ``"auto"`` difficulty
mode, this module nudges the difficulty of *upcoming* questions up or down based
on the whole group's recent correct-rate:

* If the table is acing it (high correct-rate sustained over a few rounds),
  lean harder.
* If the table is struggling, lean easier.

Design goals (all deliberately conservative):

* **Group-level only.** One signal per round (the group's correct-rate), one
  shared difficulty for the next round. No per-player identity, no per-player
  difficulty streams — that personalised variant is deferred to #186.
* **Bounded.** Difficulty only ever steps by one level at a time
  (easy <-> medium <-> hard); never jumps easy -> hard in a single move.
* **Smooth.** A step requires the *averaged* correct-rate over the recent
  window to clear a threshold, not a single lucky/unlucky round. This avoids
  wild swings from one easy or one brutal question.
* **No-op without signal.** Until at least ``min_rounds`` rounds with at least
  one participating player have been observed, the target difficulty never
  moves. Rounds where nobody answered contribute no signal.

The calibrator is pure with respect to the question bank: it only decides a
target ``Difficulty``; the bank is responsible for actually serving a question
at (or near) that difficulty.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field

from .types import Difficulty

# Ordered difficulty ladder. Index position == relative hardness.
_LADDER: list[Difficulty] = [Difficulty.EASY, Difficulty.MEDIUM, Difficulty.HARD]

# Default tuning. Chosen to be forgiving: you need a clearly dominant
# performance over several rounds before the difficulty moves, and the move
# is a single rung.
DEFAULT_WINDOW = 3          # rounds of history averaged for the signal
DEFAULT_MIN_ROUNDS = 2      # min observed rounds before any nudge
DEFAULT_STEP_UP = 0.80      # avg correct-rate above this -> harder
DEFAULT_STEP_DOWN = 0.40    # avg correct-rate below this -> easier


def _clamp_index(idx: int) -> int:
    """Clamp a ladder index into the valid range."""
    return max(0, min(len(_LADDER) - 1, idx))


@dataclass
class GroupCalibrator:
    """Tracks the group's recent correct-rate and proposes a target difficulty.

    Usage::

        cal = GroupCalibrator(start=Difficulty.MEDIUM)
        target = cal.current_target            # before any rounds: the start
        ...
        cal.record_round(correct=3, total=4)   # after each evaluated round
        target = cal.next_target()             # difficulty for the next round
    """

    start: Difficulty = Difficulty.MEDIUM
    window: int = DEFAULT_WINDOW
    min_rounds: int = DEFAULT_MIN_ROUNDS
    step_up_threshold: float = DEFAULT_STEP_UP
    step_down_threshold: float = DEFAULT_STEP_DOWN

    # Internal state.
    _target: Difficulty = field(init=False)
    _rates: deque[float] = field(init=False)
    _observed: int = field(init=False, default=0)

    def __post_init__(self) -> None:
        if self.start not in _LADDER:
            self.start = Difficulty.MEDIUM
        # window must be >= 1 so the deque always holds at least the last round.
        if self.window < 1:
            self.window = 1
        self._target = self.start
        self._rates = deque(maxlen=self.window)

    # ------------------------------------------------------------------
    # Reads
    # ------------------------------------------------------------------

    @property
    def current_target(self) -> Difficulty:
        """The difficulty the *next* round should use, given history so far."""
        return self._target

    @property
    def observed_rounds(self) -> int:
        """Number of rounds with signal observed so far."""
        return self._observed

    def average_rate(self) -> float | None:
        """Mean correct-rate over the recent window, or None if no signal."""
        if not self._rates:
            return None
        return sum(self._rates) / len(self._rates)

    # ------------------------------------------------------------------
    # Writes
    # ------------------------------------------------------------------

    def record_round(self, correct: int, total: int) -> None:
        """Feed one evaluated round's group result.

        ``correct`` = number of participating players who answered correctly.
        ``total``   = number of participating players (those who answered or
        timed out). Rounds with ``total <= 0`` carry no signal and are ignored
        (e.g. a round nobody was present for).
        """
        if total <= 0:
            return
        rate = max(0.0, min(1.0, correct / total))
        self._rates.append(rate)
        self._observed += 1

    def next_target(self) -> Difficulty:
        """Recompute and return the target difficulty for the upcoming round.

        Bounded (single rung), smooth (uses the windowed average), and a no-op
        until ``min_rounds`` rounds of signal have accumulated.
        """
        # Not enough signal yet -> never move.
        if self._observed < self.min_rounds:
            return self._target

        avg = self.average_rate()
        if avg is None:
            return self._target

        idx = _LADDER.index(self._target)
        if avg >= self.step_up_threshold:
            idx = _clamp_index(idx + 1)
        elif avg <= self.step_down_threshold:
            idx = _clamp_index(idx - 1)
        # else: comfortable band -> hold.

        self._target = _LADDER[idx]
        return self._target
