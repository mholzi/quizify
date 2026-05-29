"""Tests for the Top Score end-of-game award (issue #150)."""

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


def _mk_player(name: str, round_scores: list[int] | None = None) -> PlayerSession:
    """Build a PlayerSession with just the fields highlights cares about.

    The WebSocket is mocked — compute_superlatives never touches it. All other
    PlayerSession dataclass fields keep their defaults (empty lists, 0
    counters), so the player only contributes to the awards we set.

    `round_history` mirrors `round_scores` length because the awards function
    early-exits when `max_rounds < MIN_ROUNDS` (3 rounds). The history string
    doesn't matter for Top Score — it's only used by Most Accurate.
    """
    p = PlayerSession(name=name, ws=MagicMock())
    if round_scores is not None:
        p.round_scores = list(round_scores)
        # Awards function gates on len(round_history) — pad to match scores so
        # the MIN_ROUNDS=3 check passes without affecting Top Score logic.
        p.round_history = ["correct"] * len(round_scores)
    return p


def test_top_score_awarded_to_highest_single_round_scorer() -> None:
    """The player whose best single-round score is highest wins Top Score —
    distinct from cumulative champion (podium) and Hot Streak (sequence)."""
    players = [
        _mk_player("Alice", [10, 12, 15, 11, 14]),     # max 15
        _mk_player("Bob", [8, 9, 10, 47, 6]),          # max 47 ← winner
        _mk_player("Carol", [20, 18, 22, 19, 16]),     # max 22
    ]
    results = compute_superlatives(players)
    top_score = next(
        (r for r in results if r.award_key == "highlights.awards.topScore"),
        None,
    )
    assert top_score is not None, "Top Score award should fire"
    assert top_score.winner == "Bob"
    assert top_score.detail_params == {"points": 47, "round": 4}


def test_top_score_tie_break_prefers_earliest_round() -> None:
    """When two players hit the same max single-round score, the player
    who reached it earlier in the game wins the trophy."""
    players = [
        _mk_player("Alice", [12, 10, 30, 8, 9]),       # max 30 in round 3
        _mk_player("Bob", [8, 9, 11, 12, 30]),         # max 30 in round 5
    ]
    results = compute_superlatives(players)
    top_score = next(
        (r for r in results if r.award_key == "highlights.awards.topScore"),
        None,
    )
    assert top_score is not None
    assert top_score.winner == "Alice"  # earlier round wins tie
    assert top_score.detail_params == {"points": 30, "round": 3}


def test_top_score_skipped_when_below_floor() -> None:
    """A sleepy game (everyone's best round under 25 pts) gets no trophy —
    don't hand out hollow awards."""
    players = [
        _mk_player("Alice", [5, 8, 10, 12, 7]),    # max 12
        _mk_player("Bob", [6, 9, 11, 8, 10]),      # max 11
    ]
    results = compute_superlatives(players)
    top_score = next(
        (r for r in results if r.award_key == "highlights.awards.topScore"),
        None,
    )
    assert top_score is None


def test_top_score_skipped_with_empty_round_scores() -> None:
    """Players who haven't played a single round can't qualify."""
    players = [
        _mk_player("Alice", []),
        _mk_player("Bob", []),
    ]
    results = compute_superlatives(players)
    top_score = next(
        (r for r in results if r.award_key == "highlights.awards.topScore"),
        None,
    )
    assert top_score is None


def test_top_score_does_not_duplicate_other_awards() -> None:
    """No player can hold two awards. Since Top Score fires first in the
    chain, the Top Score winner is removed from the pool before any later
    award (Fastest Finger, Comeback King, …) picks its candidate."""
    # Alice's single-round max (50) is the highest → wins Top Score.
    # She also has the fastest avg answer time, but that award now goes
    # to the next-fastest non-awarded player.
    alice = _mk_player("Alice", [10, 12, 50, 11, 14])
    alice.answer_times = [1.0, 1.1, 1.2, 1.3, 1.4]
    bob = _mk_player("Bob", [8, 9, 11, 30, 6])
    bob.answer_times = [5.0, 5.1, 5.2, 5.3, 5.4]
    carol = _mk_player("Carol", [25, 30, 22, 19, 16])
    carol.answer_times = [3.0, 3.1, 3.2, 3.3, 3.4]
    results = compute_superlatives([alice, bob, carol])
    award_by_winner: dict[str, list[str]] = {}
    for r in results:
        award_by_winner.setdefault(r.winner, []).append(r.award_key)
    # No double-awarding: each player's list has at most one entry.
    for player, awards in award_by_winner.items():
        assert len(awards) == 1, (
            f"{player} should hold at most one award, got {awards}"
        )
    # Alice wins Top Score.
    assert "highlights.awards.topScore" in award_by_winner.get("Alice", [])
