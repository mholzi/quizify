"""ScoringEngine — pure round-score computation for Quizify.

Extracted from ``QuizifyGameState.submit_answer`` (issue #184, splitting the
game-server God-objects). This module owns the *pure* arithmetic that turns a
single submitted answer into points + a client-facing breakdown:

  * base / speed / difficulty / streak scoring (delegates to ``scoring``)
  * the streak-milestone spike (3/5/10/... → discrete bonus)
  * the final-round wager override (Jeopardy-style bet)

It is deliberately side-effect free: it never mutates a ``PlayerSession`` and
never touches game state. ``submit_answer`` keeps ownership of all mutation
(streak counters, score accumulation, milestone tallies) and simply applies the
``ScoreComputation`` this engine returns. Behaviour is byte-for-byte identical
to the previous inline block — the same numbers in the same order — so the
message protocol and scoring are unchanged.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .scoring import (
    BASE_POINTS,
    MAX_SPEED_BONUS,
    calculate_round_score,
    get_streak_milestone_bonus,
    get_streak_multiplier,
)
from .types import DIFFICULTY_MULTIPLIERS, Difficulty


@dataclass
class ScoreComputation:
    """The result of scoring a single submitted answer.

    Carries everything ``submit_answer`` needs to apply to the player and to
    build the outgoing ``AnswerResult``. No mutation happens in here — the
    caller owns applying ``points`` to the running score and bumping the
    milestone tallies via ``milestone_bonus``.
    """

    points: int
    speed_bonus: int = 0
    streak_bonus: int = 0
    difficulty_multiplier: float = 1.0
    double_points: bool = False
    # Set to the absolute wager points when a final-round wager replaced the
    # normal scoring; None otherwise. Surfaced in the breakdown.
    wager_used: int | None = None
    # Discrete milestone spike already folded into ``points`` — surfaced
    # separately so the caller can tally it and the client can celebrate.
    milestone_bonus: int = 0

    @property
    def breakdown(self) -> dict[str, Any]:
        """The ``round_score_breakdown`` dict the client renders."""
        return {
            "speed_bonus": self.speed_bonus,
            "streak_bonus": self.streak_bonus,
            "difficulty_multiplier": self.difficulty_multiplier,
            "double_points": self.double_points,
            "wager": self.wager_used,
            "milestone_bonus": self.milestone_bonus,
        }


class ScoringEngine:
    """Computes the points + breakdown for one submitted answer.

    Stateless: a single shared instance is safe. The method mirrors the exact
    sequence the inline ``submit_answer`` block used so the result is identical.
    """

    def score_submission(
        self,
        *,
        correct: bool,
        elapsed: float,
        round_duration: float,
        difficulty: Difficulty,
        streak: int,
        double_points_active: bool,
        is_final_round: bool,
        wager: int | None,
        score_before_wager: int,
    ) -> ScoreComputation:
        """Compute points and breakdown for one answer.

        Args mirror the inputs the previous inline block read:

          * ``streak`` is the player's streak *after* this round's update
            (i.e. already incremented for a correct answer / zeroed for wrong).
          * ``score_before_wager`` is the player's running score *before* this
            round's points are added — the wager bets against that figure.

        Returns a :class:`ScoreComputation`; the caller applies it.
        """
        points = calculate_round_score(
            correct=correct,
            elapsed=elapsed,
            time_limit=round_duration,
            difficulty=difficulty,
            streak=streak,
            double_points_active=double_points_active,
        )

        # Calculate breakdown for client display.
        speed_bonus = 0
        streak_bonus = 0
        diff_mult = DIFFICULTY_MULTIPLIERS.get(difficulty, 1.0)
        if correct:
            time_fraction = (
                max(0.0, 1.0 - elapsed / round_duration)
                if round_duration > 0
                else 0.0
            )
            speed_bonus = int(MAX_SPEED_BONUS * time_fraction)
            streak_mult = get_streak_multiplier(streak)
            streak_bonus = int((BASE_POINTS + speed_bonus) * diff_mult * (streak_mult - 1.0))

        # Wager override (Jeopardy-style final round). On the final round, if
        # the player submitted a wager (0-100% of their pre-round score), it
        # REPLACES the normal scoring: right answer adds the wager value, wrong
        # subtracts. Speed/streak/difficulty multipliers are ignored — the
        # wager IS the bet. We bet against the score BEFORE points were added.
        wager_used: int | None = None
        if is_final_round and wager is not None:
            bank = max(0, score_before_wager)
            wager_pts = int(bank * wager / 100)
            wager_used = wager_pts
            if correct:
                points = wager_pts
            else:
                # Lose the wager — but never go below zero so a player can't be
                # priced out of an existing rematch flow.
                points = -min(wager_pts, bank)
            # Clear bonuses since the wager overrides them.
            speed_bonus = 0
            streak_bonus = 0

        # Streak milestone bonus — discrete spike awarded the round the streak
        # EXACTLY equals a milestone value (3, 5, 10, 15, 20, 25). Wager rounds
        # skip this so the bet stays the only thing in play.
        milestone_bonus = 0
        if correct and wager_used is None:
            milestone_bonus = get_streak_milestone_bonus(streak)
            if milestone_bonus:
                points += milestone_bonus

        return ScoreComputation(
            points=points,
            speed_bonus=speed_bonus,
            streak_bonus=streak_bonus,
            difficulty_multiplier=diff_mult,
            double_points=double_points_active,
            wager_used=wager_used,
            milestone_bonus=milestone_bonus,
        )
