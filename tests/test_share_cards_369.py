"""Tests for issue #369 — shareable end-of-game result cards.

The finale payload carries a ``share`` block: one entry per player with the
per-round outcome sequence, so the phone can render a score slip and hand the
text to ``navigator.share``.

What these pin down is mostly honesty of the card. A shared result gets posted
into a group chat, so every number on it is a public claim:

* a late joiner must not be shown as having played rounds they missed
* tied players must not be handed 1st/2nd by sort order
* a timeout must not be laundered into a wrong answer
* power-ups must not be placed on specific rounds — the game never records
  which round they were spent in
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT))

from custom_components.quizify.game.player import PlayerSession  # noqa: E402
from custom_components.quizify.server.serializers import (  # noqa: E402
    build_share_payload,
    serialize_finale,
)


def _player(name: str, score: int, history: list[str], powerups: int = 0):
    p = PlayerSession(name=name, ws=MagicMock())
    p.score = score
    p.round_history = list(history)
    p.powerups_used = powerups
    return p


# ---------------------------------------------------------------------------
# Shape
# ---------------------------------------------------------------------------


def test_finale_always_carries_a_share_block() -> None:
    """The client renders unconditionally; it must never probe for the key."""
    players = [_player("Alice", 10, ["correct"])]
    msg = serialize_finale(players, players)
    assert "share" in msg
    assert msg["share"]["players"][0]["name"] == "Alice"


def test_packs_flow_through_and_default_to_empty() -> None:
    msg = serialize_finale([], [], packs=["geographie", "musik"])
    assert msg["share"]["packs"] == ["geographie", "musik"]
    # No packs (mixed category) → empty list, and the card omits the line
    # rather than inventing a pack name.
    assert serialize_finale([], [])["share"]["packs"] == []


# ---------------------------------------------------------------------------
# Honesty of the numbers
# ---------------------------------------------------------------------------


def test_round_history_is_passed_through_verbatim() -> None:
    """Three outcomes survive to the client, timeout included."""
    hist = ["correct", "wrong", "timeout", "correct"]
    share = build_share_payload([_player("Alice", 30, hist)])
    entry = share["players"][0]
    assert entry["results"] == hist
    assert entry["rounds"] == 4
    assert entry["correct"] == 2  # timeout is NOT counted as correct


def test_timeout_is_not_folded_into_wrong() -> None:
    """'I ran out of time' and 'I answered wrong' are different stories."""
    share = build_share_payload([_player("Alice", 0, ["timeout", "wrong"])])
    results = share["players"][0]["results"]
    assert results[0] == "timeout"
    assert results[1] == "wrong"
    assert results[0] != results[1]


def test_late_joiner_reports_only_the_rounds_they_played() -> None:
    """A 2-round history in an 8-round game must stay 2, not be padded."""
    early = _player("Alice", 80, ["correct"] * 8)
    late = _player("Bob", 10, ["correct", "wrong"])
    share = build_share_payload([early, late])
    by_name = {e["name"]: e for e in share["players"]}
    assert by_name["Bob"]["rounds"] == 2
    assert len(by_name["Bob"]["results"]) == 2
    assert by_name["Alice"]["rounds"] == 8


def test_tied_players_share_a_rank() -> None:
    """Equal scores must not be handed 1st/2nd by sort order — the card is
    posted publicly and would misstate the outcome."""
    a = _player("Alice", 50, ["correct"])
    b = _player("Bob", 50, ["correct"])
    c = _player("Cara", 10, ["wrong"])
    share = build_share_payload([a, b, c])
    ranks = {e["name"]: e["rank"] for e in share["players"]}
    assert ranks["Alice"] == 1
    assert ranks["Bob"] == 1
    assert ranks["Cara"] == 3  # competition ranking: no rank 2 handed out
    assert all(e["total_players"] == 3 for e in share["players"])


def test_powerups_are_a_count_not_a_round_marker() -> None:
    """The game never records WHICH round a power-up was spent in, so the
    payload must not imply a position. It carries a count and nothing else."""
    share = build_share_payload([_player("Alice", 40, ["correct"] * 4, powerups=2)])
    entry = share["players"][0]
    assert entry["powerups"] == 2
    # No per-round power-up marker anywhere in the round sequence.
    assert set(entry["results"]) <= {"correct", "wrong", "timeout"}


def test_rank_order_is_by_score_regardless_of_input_order() -> None:
    share = build_share_payload([
        _player("Low", 5, ["wrong"]),
        _player("High", 90, ["correct"]),
        _player("Mid", 40, ["correct"]),
    ])
    assert [e["name"] for e in share["players"]] == ["High", "Mid", "Low"]
    assert [e["rank"] for e in share["players"]] == [1, 2, 3]


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


def test_player_who_never_played_a_round_gets_an_empty_strip() -> None:
    """Joined the lobby, game ended. The client hides the card on rounds == 0
    rather than sharing an empty brag."""
    share = build_share_payload([_player("Ghost", 0, [])])
    entry = share["players"][0]
    assert entry["rounds"] == 0
    assert entry["results"] == []


def test_empty_game_produces_an_empty_player_list() -> None:
    share = build_share_payload([])
    assert share["players"] == []
    assert share["packs"] == []


@pytest.mark.parametrize("n", [1, 5, 20])
def test_total_players_matches_the_actual_field(n: int) -> None:
    players = [_player(f"P{i}", 100 - i, ["correct"]) for i in range(n)]
    share = build_share_payload(players)
    assert all(e["total_players"] == n for e in share["players"])


def test_results_list_is_a_copy_not_the_live_history() -> None:
    """Mutating the payload must not reach back into game state."""
    p = _player("Alice", 10, ["correct"])
    share = build_share_payload([p])
    share["players"][0]["results"].append("wrong")
    assert p.round_history == ["correct"]
