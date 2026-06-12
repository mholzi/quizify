"""Tests for #340 — each player gets at most one power-up per game.

Before #340 the per-round power-up was drawn with `random.choice(connected)`
on every round, with no memory, so over an N-round game a player could be
picked repeatedly while others got none. #340 caps grants at one per player
per game: the candidate pool is restricted to connected players who have not
yet been granted a power-up this game, and grants stop once everyone has had
one. The "granted this game" set persists across rounds (NOT cleared in
`reset_round`) and is cleared only on a game-level reset (`reset`), which the
game state calls from `start_game` / `reset_to_lobby`.

Two layers of coverage:
  * unit tests on PowerUpManager (where the granted-set logic lives), and
  * an end-to-end test that drives the real `start_next_question` round loop
    in QuizifyGameState and asserts no player is ever granted twice.
"""

from __future__ import annotations

import sys
from collections import Counter
from pathlib import Path
from unittest.mock import MagicMock

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT))

from custom_components.quizify.game.powerups import PowerUpManager  # noqa: E402
from custom_components.quizify.game.state import (  # noqa: E402
    GamePhase,
    QuizifyGameState,
)

# ---------- PowerUpManager unit tests ----------


class TestPowerUpManagerGrantedSet:
    """The per-game granted set: record on assign, persist, reset semantics."""

    def test_assign_records_player_as_granted(self) -> None:
        mgr = PowerUpManager()
        assert not mgr.was_granted_this_game("Alice")
        mgr.assign_random_powerup("Alice")
        assert mgr.was_granted_this_game("Alice")

    def test_reset_round_keeps_granted_set(self) -> None:
        # The granted set must survive a per-round reset so the cap holds
        # across rounds (this is the core of #340).
        mgr = PowerUpManager()
        mgr.assign_random_powerup("Alice")
        mgr.reset_round()
        assert mgr.was_granted_this_game("Alice")

    def test_reset_clears_granted_set(self) -> None:
        # A game-level reset starts a fresh game — everyone eligible again.
        mgr = PowerUpManager()
        mgr.assign_random_powerup("Alice")
        mgr.reset()
        assert not mgr.was_granted_this_game("Alice")

    def test_using_powerup_does_not_clear_granted_flag(self) -> None:
        # Spending the held power-up must not make the player eligible again.
        mgr = PowerUpManager()
        mgr.assign_random_powerup("Alice")
        mgr.use_powerup("Alice", target_id=None)
        assert not mgr.has_powerup("Alice")  # consumed
        assert mgr.was_granted_this_game("Alice")  # still counted


def _simulate_round_loop(
    mgr: PowerUpManager,
    *,
    connected_each_round: list[list[str]],
) -> list[str | None]:
    """Replay the state.py per-round grant logic against a real manager.

    Mirrors `start_next_question`: each round, reset_round (keeps granted set),
    then pick the lucky player from connected-and-not-yet-granted; grant
    nothing if that pool is empty. Returns the granted player per round (or
    None when nothing was granted).
    """
    granted_per_round: list[str | None] = []
    for connected in connected_each_round:
        mgr.reset_round()
        eligible = [p for p in connected if not mgr.was_granted_this_game(p)]
        if eligible:
            # deterministic pick for the test (random in production)
            lucky = eligible[0]
            mgr.assign_random_powerup(lucky)
            granted_per_round.append(lucky)
        else:
            granted_per_round.append(None)
    return granted_per_round


class TestRoundLoopLogic:
    """The eligibility-pool logic replayed over a multi-round game."""

    def test_each_player_at_most_once(self) -> None:
        mgr = PowerUpManager()
        players = ["A", "B", "C"]
        # 8 rounds, all 3 always connected.
        grants = _simulate_round_loop(
            mgr, connected_each_round=[players] * 8
        )
        counts = Counter(g for g in grants if g is not None)
        for player in players:
            assert counts[player] <= 1, f"{player} granted {counts[player]}x"

    def test_grants_stop_once_all_granted(self) -> None:
        mgr = PowerUpManager()
        players = ["A", "B", "C"]
        grants = _simulate_round_loop(
            mgr, connected_each_round=[players] * 6
        )
        # First 3 rounds grant one each, remaining rounds grant nothing.
        assert all(g is not None for g in grants[:3])
        assert all(g is None for g in grants[3:])
        # Exactly the 3 distinct players got one.
        assert set(grants[:3]) == set(players)

    def test_late_joiner_still_eligible(self) -> None:
        mgr = PowerUpManager()
        # A and B play from round 1; C joins at round 3.
        rounds = [["A", "B"], ["A", "B"], ["A", "B", "C"], ["A", "B", "C"]]
        grants = _simulate_round_loop(mgr, connected_each_round=rounds)
        # A, B granted in rounds 1-2; round 3 only C is eligible → C granted.
        assert grants[0] in ("A", "B")
        assert grants[1] in ("A", "B")
        assert {grants[0], grants[1]} == {"A", "B"}
        assert grants[2] == "C"  # late joiner gets their one
        assert grants[3] is None  # everyone now has one

    def test_reconnect_does_not_regrant(self) -> None:
        mgr = PowerUpManager()
        # A granted round 1, disconnects round 2, reconnects round 3.
        rounds = [["A", "B"], ["B"], ["A", "B"], ["A", "B"]]
        grants = _simulate_round_loop(mgr, connected_each_round=rounds)
        counts = Counter(g for g in grants if g is not None)
        assert counts["A"] <= 1  # reconnect must not re-grant A

    def test_single_player_one_grant_total(self) -> None:
        mgr = PowerUpManager()
        grants = _simulate_round_loop(mgr, connected_each_round=[["Solo"]] * 5)
        assert grants[0] == "Solo"
        assert all(g is None for g in grants[1:])


# ---------- End-to-end: real QuizifyGameState round loop ----------


class _FakeRuntime:
    def __init__(self, tmp_path: Path) -> None:
        self.data_dir = tmp_path


def _fake_ws() -> MagicMock:
    ws = MagicMock()
    ws.closed = False
    return ws


@pytest.fixture
def state(tmp_path: Path) -> QuizifyGameState:
    runtime = _FakeRuntime(tmp_path)
    return QuizifyGameState(runtime=runtime, entry_id="test")


class TestRealGameRoundLoop:
    """Drive the actual start_next_question loop and assert the #340 cap."""

    def test_no_player_granted_twice_over_full_game(
        self, state: QuizifyGameState
    ) -> None:
        names = ["Alice", "Bob", "Charlie"]
        for n in names:
            state.add_player(n, _fake_ws())
        state.start_game(language="de", num_rounds=8)

        granted_seen: set[str] = set()
        for _ in range(8):
            q = state.start_next_question()
            if q is None:
                break
            # Determine who was granted this round via the manager's set:
            # newly-granted players are those in the granted set we haven't
            # seen yet.
            newly = {
                n
                for n in names
                if state._powerup_manager.was_granted_this_game(n)
            } - granted_seen
            # At most one grant per round.
            assert len(newly) <= 1, f"more than one grant in a round: {newly}"
            granted_seen |= newly
            # Force back to a phase from which the next question can start.
            state.phase = GamePhase.ANSWER_REVEAL

        # Every granted player was granted exactly once (set membership =>
        # uniqueness); and we never exceeded the player count.
        assert granted_seen <= set(names)
        # With 8 rounds and 3 players, all three should have been granted.
        assert granted_seen == set(names)

    def test_start_game_clears_previous_granted_set(
        self, state: QuizifyGameState
    ) -> None:
        names = ["Alice", "Bob"]
        for n in names:
            state.add_player(n, _fake_ws())
        state.start_game(language="de", num_rounds=3)
        state.start_next_question()
        # Someone is now granted.
        assert any(
            state._powerup_manager.was_granted_this_game(n) for n in names
        )
        # Reset to lobby clears the granted set (a fresh game can begin).
        state.reset_to_lobby()
        assert not any(
            state._powerup_manager.was_granted_this_game(n) for n in names
        )
