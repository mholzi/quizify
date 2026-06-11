"""Tests for the non-top-score end-of-game superlatives (#260 coverage gap).

The Top Score award already has dedicated coverage in
``test_highlights_top_score.py``; this module exercises the remaining
superlatives — Fastest Finger, Comeback King, Hot Streak, Most Accurate,
Buzzkill, Knowledge Expert — plus the ``MIN_PLAYERS_FOR_AWARDS`` / ``MIN_ROUNDS``
gating that short-circuits the whole computation.
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT))

from custom_components.quizify.game.highlights import (  # noqa: E402
    compute_superlatives,
)
from custom_components.quizify.game.player import PlayerSession  # noqa: E402


def _mk_player(name: str, rounds: int = 5) -> PlayerSession:
    """Build a PlayerSession with `rounds` rounds of history.

    The WebSocket is mocked (compute_superlatives never touches it). The caller
    sets only the fields the award under test reads; everything else keeps the
    dataclass defaults so the player contributes to nothing else. `round_history`
    is padded with all-"wrong" entries: this satisfies the MIN_ROUNDS gate (3)
    via length while keeping the correct-ratio at 0% so the *Most Accurate*
    award (which gates on >= 60%) never pre-empts the award actually under
    test by claiming a player first. Tests that exercise Most Accurate set
    `round_history` explicitly.
    """
    p = PlayerSession(name=name, ws=MagicMock())
    p.round_history = ["wrong"] * rounds
    return p


def _award(results: list, key: str):
    return next((r for r in results if r.award_key == key), None)


# ---------------------------------------------------------------------------
# Gating
# ---------------------------------------------------------------------------


def test_solo_game_gets_no_awards() -> None:
    """Below MIN_PLAYERS_FOR_AWARDS (2) the whole award set is suppressed."""
    assert compute_superlatives([_mk_player("Alice")]) == []


def test_too_few_rounds_gets_no_awards() -> None:
    """A game shorter than MIN_ROUNDS (3) yields no awards even with players."""
    players = [_mk_player("Alice", rounds=2), _mk_player("Bob", rounds=2)]
    assert compute_superlatives(players) == []


# ---------------------------------------------------------------------------
# Fastest Finger
# ---------------------------------------------------------------------------


def test_fastest_finger_lowest_avg_answer_time() -> None:
    """Fastest Finger goes to the lowest average correct-answer time; a player
    with fewer than 2 timed answers can't qualify."""
    alice = _mk_player("Alice")
    alice.answer_times = [1.0, 1.2, 1.1]  # avg ~1.1 → fastest
    bob = _mk_player("Bob")
    bob.answer_times = [4.0, 4.5]
    carol = _mk_player("Carol")
    carol.answer_times = [9.9]  # only one sample → ineligible

    award = _award(compute_superlatives([alice, bob, carol]),
                   "highlights.awards.fastestFinger")
    assert award is not None
    assert award.winner == "Alice"
    assert award.detail_params == {"avg": "1.1"}


# ---------------------------------------------------------------------------
# Comeback King
# ---------------------------------------------------------------------------


def test_comeback_king_rewards_second_half_improvement() -> None:
    """Comeback King fires for the biggest per-round-average improvement from
    the first half to the second; a flat scorer must NOT win it (the #255
    average-not-sum fix)."""
    # Per-round scores stay below TOP_SCORE_MIN (25) so the Top Score award
    # (which fires first) can't claim Riser before Comeback King is computed.
    riser = _mk_player("Riser")
    riser.round_scores = [0, 0, 20, 20]  # avg 0 → 20
    flat = _mk_player("Flat")
    flat.round_scores = [10, 10, 10, 10]  # no improvement

    results = compute_superlatives([riser, flat])
    award = _award(results, "highlights.awards.comebackKing")
    assert award is not None
    assert award.winner == "Riser"
    # Flat per-round scorer never wins a comeback trophy.
    assert "Flat" not in {
        r.winner for r in results if r.award_key == "highlights.awards.comebackKing"
    }


# ---------------------------------------------------------------------------
# Hot Streak
# ---------------------------------------------------------------------------


def test_hot_streak_longest_run() -> None:
    """Hot Streak goes to the highest max_streak (>= 2)."""
    alice = _mk_player("Alice")
    alice.max_streak = 5
    bob = _mk_player("Bob")
    bob.max_streak = 3

    award = _award(compute_superlatives([alice, bob]),
                   "highlights.awards.hotStreak")
    assert award is not None
    assert award.winner == "Alice"
    assert award.detail_params == {"count": 5}


def test_hot_streak_skipped_when_streak_too_short() -> None:
    """A max streak of 1 is below the floor (>= 2) — no Hot Streak award."""
    alice = _mk_player("Alice")
    alice.max_streak = 1
    bob = _mk_player("Bob")
    bob.max_streak = 1

    award = _award(compute_superlatives([alice, bob]),
                   "highlights.awards.hotStreak")
    assert award is None


# ---------------------------------------------------------------------------
# Most Accurate
# ---------------------------------------------------------------------------


def test_most_accurate_requires_high_ratio() -> None:
    """Most Accurate awards the best correct ratio, but only when it clears
    MOST_ACCURATE_MIN_RATIO (0.60)."""
    sharp = _mk_player("Sharp")
    sharp.round_history = ["correct", "correct", "correct", "wrong"]  # 3/4 = 75%
    sloppy = _mk_player("Sloppy")
    sloppy.round_history = ["correct", "wrong", "wrong", "wrong"]  # 1/4 = 25%

    award = _award(compute_superlatives([sharp, sloppy]),
                   "highlights.awards.mostAccurate")
    assert award is not None
    assert award.winner == "Sharp"
    assert award.detail_params == {"correct": 3, "total": 4, "pct": 75}


def test_most_accurate_skipped_below_ratio_floor() -> None:
    """Nobody clears 60% correct → no Most Accurate trophy."""
    a = _mk_player("A")
    a.round_history = ["correct", "wrong", "wrong", "wrong"]  # 25%
    b = _mk_player("B")
    b.round_history = ["correct", "wrong", "wrong", "wrong"]  # 25%

    award = _award(compute_superlatives([a, b]),
                   "highlights.awards.mostAccurate")
    assert award is None


# ---------------------------------------------------------------------------
# Buzzkill + Knowledge Expert
# ---------------------------------------------------------------------------


def test_buzzkill_most_freezes_used() -> None:
    """Buzzkill goes to the player who froze others the most (>= 1)."""
    chiller = _mk_player("Chiller")
    chiller.freezes_used = 3
    other = _mk_player("Other")
    other.freezes_used = 0

    award = _award(compute_superlatives([chiller, other]),
                   "highlights.awards.buzzkill")
    assert award is not None
    assert award.winner == "Chiller"
    assert award.detail_params == {"count": 3}


def test_knowledge_expert_highest_hard_score() -> None:
    """Knowledge Expert goes to the highest hard-question score (> 0)."""
    brain = _mk_player("Brain")
    brain.hard_score = 40
    other = _mk_player("Other")
    other.hard_score = 0

    award = _award(compute_superlatives([brain, other]),
                   "highlights.awards.knowledgeExpert")
    assert award is not None
    assert award.winner == "Brain"
    assert award.detail_params == {"points": 40}


def test_no_player_holds_two_awards() -> None:
    """A single player can sweep at most one trophy even if they top several
    categories — the awarded-set guard removes them after the first."""
    star = _mk_player("Star")
    star.answer_times = [1.0, 1.0, 1.0]
    star.max_streak = 5
    star.freezes_used = 2
    star.hard_score = 30
    foil = _mk_player("Foil")
    foil.answer_times = [4.0, 4.0]
    foil.max_streak = 2
    foil.freezes_used = 1
    foil.hard_score = 5

    results = compute_superlatives([star, foil])
    by_winner: dict[str, list[str]] = {}
    for r in results:
        by_winner.setdefault(r.winner, []).append(r.award_key)
    for player, awards in by_winner.items():
        assert len(awards) == 1, f"{player} holds >1 award: {awards}"
