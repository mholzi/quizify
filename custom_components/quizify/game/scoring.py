"""Scoring engine for Quizify."""

from __future__ import annotations

from .player import PlayerSession
from .types import DIFFICULTY_MULTIPLIERS, Difficulty

BASE_POINTS = 10
MAX_SPEED_BONUS = 5
MAX_STREAK_MULTIPLIER_STACKS = 5
STREAK_MULTIPLIER_PER_STACK = 0.1

# Discrete bonus points awarded once when a player's streak hits exactly
# the milestone count (not every round above it). Sits on top of the
# smooth streak multiplier so the player feels both: a gradual climb in
# round score AND a celebratory spike at 3/5/10/etc. Same scale Beatify
# uses — proven not to feel grindy in their A/B tests.
STREAK_MILESTONES: dict[int, int] = {
    3: 20,
    5: 50,
    10: 100,
    15: 150,
    20: 250,
    25: 400,
}


def get_streak_multiplier(streak: int) -> float:
    """Return the streak multiplier: 1.0 + min(streak, 5) * 0.1."""
    return 1.0 + min(streak, MAX_STREAK_MULTIPLIER_STACKS) * STREAK_MULTIPLIER_PER_STACK


def get_streak_milestone_bonus(streak: int) -> int:
    """Bonus points if ``streak`` is exactly a milestone, else 0.

    Awarded once per hit — the caller must only invoke this for the round
    in which the streak transitioned to ``streak`` (not every subsequent
    round above the milestone).
    """
    return STREAK_MILESTONES.get(streak, 0)


def calculate_round_score(
    correct: bool,
    elapsed: float,
    time_limit: float,
    difficulty: Difficulty,
    streak: int,
    double_points_active: bool = False,
) -> int:
    """Calculate points earned for a single round.

    Scoring formula (correct answers only):
        base        = 10
        speed_bonus = up to 5 (linear decay over time_limit)
        difficulty  = easy x1.0, medium x1.5, hard x2.0
        streak      = 1.0 + min(streak, 5) * 0.1
        double      = x2 if active

    Returns 0 for incorrect answers.
    """
    if not correct:
        return 0

    # Speed bonus: linearly decays from MAX_SPEED_BONUS to 0 over time_limit
    time_fraction = max(0.0, 1.0 - elapsed / time_limit) if time_limit > 0 else 0.0
    speed_bonus = MAX_SPEED_BONUS * time_fraction

    # Difficulty multiplier
    diff_mult = DIFFICULTY_MULTIPLIERS.get(difficulty, 1.0)

    # Streak multiplier
    streak_mult = get_streak_multiplier(streak)

    score = (BASE_POINTS + speed_bonus) * diff_mult * streak_mult

    if double_points_active:
        score *= 2

    return int(score)


def calculate_podium(players: list[PlayerSession]) -> list[PlayerSession]:
    """Return the top 3 players sorted by score (descending)."""
    sorted_players = sorted(players, key=lambda p: p.score, reverse=True)
    return sorted_players[:3]
