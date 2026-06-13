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


# ---------------------------------------------------------------------------
# Estimate / closest-guess scoring (#275)
# ---------------------------------------------------------------------------
#
# Estimate questions are scored by *closeness*, not right/wrong: players are
# ranked by the absolute distance between their guess and the true value, the
# closest player gets the full award, and the rest scale down by rank. The
# award is built on the same BASE_POINTS scale the MC path uses so an estimate
# round sits naturally alongside multiple-choice rounds on the leaderboard
# (rather than introducing a parallel, wildly different point magnitude).
#
# Formula (per round, applied to players who actually guessed):
#   * Rank players by |guess - answer| ascending; equal distance ⇒ same rank
#     (standard competition ranking — two players tied for closest both rank 1).
#   * award(rank) = round(BASE_ESTIMATE_POINTS * RANK_DECAY ** (rank - 1)) but
#     never below ESTIMATE_MIN_POINTS, so every participant scores *something*
#     ("nobody is mathematically out" — the editorial point of the mode).
#   * difficulty multiplier (easy 1.0 / medium 1.5 / hard 2.0) applies, same as
#     MC, so a hard estimate is worth more.
#   * an EXACT hit (distance == 0) adds ESTIMATE_EXACT_BONUS on top.
#   * non-answerers (no guess submitted) score 0.
#
# Tied players share the rank *and the award for that rank* (they are equally
# close — neither is "more first" than the other).

# Full award for the single closest guess (pre-difficulty). ~5x a strong MC
# round so the estimate-round drama reads as a meaningful swing without
# dwarfing a whole game of MC rounds.
BASE_ESTIMATE_POINTS = 50
# Each rank step keeps this fraction of the previous rank's award.
ESTIMATE_RANK_DECAY = 0.7
# Floor so even the worst guess that still participated earns a little.
ESTIMATE_MIN_POINTS = 5
# Flat bonus for landing exactly on the true value.
ESTIMATE_EXACT_BONUS = 25


def calculate_estimate_scores(
    guesses: dict[str, float],
    answer: float,
    difficulty: Difficulty = Difficulty.MEDIUM,
) -> dict[str, dict]:
    """Score an estimate round by closeness (#275).

    ``guesses`` maps player-name -> submitted numeric guess (already clamped to
    the question range by the caller). ``answer`` is the true value. Players who
    did not guess are simply absent from ``guesses`` and score 0 — the caller
    handles them.

    Returns a mapping player-name -> dict with:
        * ``distance``       — abs(guess - answer)
        * ``rank``           — 1-based competition rank (ties share a rank)
        * ``points``         — awarded points (difficulty-scaled, exact bonus)
        * ``exact``          — True if the guess hit the answer exactly

    Deterministic and side-effect free, mirroring ``calculate_round_score``.
    """
    if not guesses:
        return {}

    diff_mult = DIFFICULTY_MULTIPLIERS.get(difficulty, 1.0)

    # Distance per player, then sort ascending so the closest is first.
    distances = {name: abs(g - answer) for name, g in guesses.items()}
    ordered = sorted(distances.items(), key=lambda kv: kv[1])

    results: dict[str, dict] = {}
    prev_distance: float | None = None
    rank = 0
    for i, (name, dist) in enumerate(ordered):
        # Competition ranking: equal distance ⇒ identical rank (and award).
        if prev_distance is None or dist != prev_distance:
            rank = i + 1
            prev_distance = dist

        base = BASE_ESTIMATE_POINTS * (ESTIMATE_RANK_DECAY ** (rank - 1))
        points = max(ESTIMATE_MIN_POINTS, int(round(base * diff_mult)))

        exact = dist == 0
        if exact:
            points += ESTIMATE_EXACT_BONUS

        results[name] = {
            "distance": dist,
            "rank": rank,
            "points": points,
            "exact": exact,
        }

    return results


def calculate_podium(players: list[PlayerSession]) -> list[PlayerSession]:
    """Return the top 3 players sorted by score (descending)."""
    sorted_players = sorted(players, key=lambda p: p.score, reverse=True)
    return sorted_players[:3]
