"""LightningController — the Lightning Round detour's orchestration (#1108).

Extracted from ``QuizifyGameState`` (issue #1108), in the same shape as
:class:`~custom_components.quizify.game.hot_seat_controller.HotSeatController`.
The mode's *rules* live in ``lightning.py`` (:class:`LightningRound`, issue
#42); this module owns the glue that stayed behind in ``state.py``: arming the
auto target round (#285), the once-per-game guard, the intro splash (#201),
and the phase transitions into the round, its recap and back to the paused
main game.

It holds a reference to the owning ``QuizifyGameState`` because a detour reads
the roster and the question bank and flips the game's phase and round counter.
``QuizifyGameState`` keeps every public name it had (``lightning``,
``start_lightning_round``, ``resume_after_lightning`` …) as a thin delegate.
"""

from __future__ import annotations

import random
from typing import TYPE_CHECKING

from .phase_controller import GamePhase

if TYPE_CHECKING:  # pragma: no cover - typing only
    from .lightning import LightningRound
    from .state import QuizifyGameState


class LightningController:
    """Owns the Lightning Round's arming, splash and phase transitions."""

    def __init__(self, game: QuizifyGameState) -> None:
        """Bind to the owning game; nothing is armed until ``arm``."""
        self._game = game
        # Active LightningRound (issue #42), or None when not in a lightning
        # round. Owns its own questions/scores/recap; this controller only
        # holds the reference + phase so the WS layer can route to it.
        self._round: LightningRound | None = None

        # True between start_lightning_round() and begin_lightning_questions():
        # the intro splash ("Bolt Burst", issue #201) is on screen and the
        # first question has not been broadcast yet. For the auto-triggered
        # flow (#285) the WS loop advances out of it on a grace timer rather
        # than a host tap.
        self._splash_pending: bool = False

        # Auto-trigger bookkeeping (issue #285). The Lightning Round is no
        # longer a host-manual mode — it fires on its own exactly once per
        # game at a uniformly random round inside the eligible window
        # (rounds 3 … N-1). Picked up front in start_game() so the pick is a
        # single seedable draw the tests can pin.
        #   * _enabled  — the settings toggle (default ON).
        #   * _target_round — the 1-based round the LR fires BEFORE,
        #     or None when disabled / the window is empty (short game).
        #   * _fired — guards the once-per-game guarantee.
        #   * _round_to_resume — while in the LIGHTNING/LIGHTNING_RECAP
        #     detour, the normal round the game returns to afterwards.
        self._enabled: bool = True
        self._target_round: int | None = None
        self._fired: bool = False
        self._round_to_resume: int = 0
        # Injectable RNG for the target-round pick so tests are deterministic
        # without monkeypatching the module-global ``random``. Defaults to the
        # shared module RNG in production.
        self._rng: random.Random = random.Random()

    def arm(self, num_rounds: int, *, enabled: bool, seed: int | None) -> None:
        """Arm the auto Lightning Round for a new game (#285).

        Pick the target round once, up front, so it is a single seedable draw
        — the WS round-advance path consults should_trigger_lightning() and
        fires when reached.
        """
        self._enabled = enabled
        self._fired = False
        self._round_to_resume = 0
        if seed is not None:
            self._rng = random.Random(seed)
        self._target_round = self._pick_lightning_round(num_rounds)

    def reset(self) -> None:
        """Forget the round and its arming — re-armed by the next start_game."""
        self._round = None
        self._splash_pending = False
        self._target_round = None
        self._fired = False
        self._round_to_resume = 0

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
        if not self._enabled:
            return None
        low, high = 3, num_rounds - 1
        if high < low:
            return None  # window empty → short game, no Lightning Round
        return self._rng.randint(low, high)

    @property
    def lightning_target_round(self) -> int | None:
        """The round the auto Lightning Round fires before, or None (#285)."""
        return self._target_round

    def should_trigger_lightning(self) -> bool:
        """True iff the auto Lightning Round should fire right now (#285).

        Consulted by the WS round-advance path BEFORE it starts the next
        normal question. Fires exactly once per game, when the game is about
        to enter the pre-picked target round (``self._game.round`` counts completed
        rounds, so ``round + 1`` is the round we are about to start). Guarded
        by ``_lightning_fired`` for the once-per-game guarantee and only from
        a between-rounds phase.
        """
        if self._fired or self._target_round is None:
            return False
        if self._game.phase not in (GamePhase.LOBBY, GamePhase.ANSWER_REVEAL):
            return False
        return self._game.round + 1 == self._target_round

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
            if self._game.phase not in (GamePhase.LOBBY, GamePhase.ANSWER_REVEAL):
                return False
        elif self._game.phase not in (
            GamePhase.LOBBY,
            GamePhase.FINALE,
            GamePhase.LIGHTNING_RECAP,
        ):
            return False

        player_names = list(self._game.players.keys())
        lr = LightningRound(
            self._game.question_bank,
            player_names,
            language=language or self._game.language,
            category=category if category is not None else self._game.category,
            categories=(
                categories if categories is not None else self._game.categories
            ),
            difficulty=difficulty,
            # A team answers lightning together, like a normal round (#552).
            # The roster is a snapshot: teams are frozen from the start of the
            # game, so the detour cannot be joined or left mid-round.
            teams=self._game.team_registry.to_list(),
        )
        # Reserve the questions the main game still owes the host (#544). Only
        # the auto path detours out of a running game; the lobby/finale entries
        # have no remaining rounds to protect.
        reserve = max(0, self._game.total_rounds - self._game.round) if auto else 0
        if not lr.start(reserve=reserve):
            return False

        if auto:
            # Remember the normal round we detoured out of so the game resumes
            # there after the recap, and burn the once-per-game flag.
            self._round_to_resume = self._game.round
            self._fired = True

        self._round = lr
        self._game.phase = GamePhase.LIGHTNING
        # Open on the intro splash (issue #201). In the auto flow (#285) the
        # WS loop advances out of it on a grace timer (no host tap);
        # begin_lightning_questions() still performs the transition.
        self._splash_pending = True
        self._game._notify_state_callbacks()
        return True

    def begin_lightning_questions(self) -> bool:
        """Leave the intro splash and let the question loop start (issue #201).

        Returns True if the splash was pending and is now dismissed, False
        if there was nothing to dismiss (no active round / already started).
        """
        if self._game.phase != GamePhase.LIGHTNING or not self._splash_pending:
            return False
        self._splash_pending = False
        self._game._notify_state_callbacks()
        return True

    @property
    def lightning_splash_pending(self) -> bool:
        """True while the lightning intro splash is showing (issue #201)."""
        return self._splash_pending

    @property
    def lightning(self) -> LightningRound | None:
        """The active LightningRound, or None."""
        return self._round

    def finish_lightning_round(self) -> None:
        """Transition out of an active lightning round into its recap screen."""
        if self._game.phase == GamePhase.LIGHTNING:
            self._game.phase = GamePhase.LIGHTNING_RECAP
            # Mid-game: persist, but keep this game's shown set (#1019).
            self._game._flush_history(end_of_game=False)
            self._game._notify_state_callbacks()

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
        if (
            self._game.phase != GamePhase.LIGHTNING_RECAP
            or not self.in_lightning_detour
        ):
            return False
        self._game.round = self._round_to_resume
        self._round_to_resume = 0
        self._round = None
        self._splash_pending = False
        self._game.phase = GamePhase.ANSWER_REVEAL
        self._game._notify_state_callbacks()
        return True
