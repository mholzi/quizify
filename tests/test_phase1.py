"""Tests for Phase 1 hardening ported from Beatify.

Covers:
- PlayerSession.is_active (ghost-WS detection)
- PlayerRegistry stale-WS rejoin (browser-reload race)
- PlayerRegistry.all_submitted uses is_active
- PlayerRegistry.get_average_score uses rounds_played
- QuizifyGameState.leader property
- State callbacks fire on phase changes
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT))

from custom_components.quizify.game.player import PlayerSession  # noqa: E402
from custom_components.quizify.game.player_registry import PlayerRegistry  # noqa: E402
from custom_components.quizify.game.state import GamePhase, QuizifyGameState  # noqa: E402


class _FakeRuntime:
    def __init__(self, tmp_path: Path) -> None:
        self.data_dir = tmp_path


def _ws(closed: bool = False) -> MagicMock:
    ws = MagicMock()
    ws.closed = closed
    return ws


# ---------- is_active ----------


class TestIsActive:
    def test_connected_and_open_ws_is_active(self) -> None:
        p = PlayerSession(name="A", ws=_ws(closed=False))
        assert p.is_active is True

    def test_disconnected_is_not_active(self) -> None:
        p = PlayerSession(name="A", ws=_ws(closed=False), connected=False)
        assert p.is_active is False

    def test_closed_ws_is_not_active_even_if_connected_flag_true(self) -> None:
        # The ghost case: _handle_disconnect hasn't run yet so `connected` is
        # still True, but the underlying socket is dead.
        p = PlayerSession(name="A", ws=_ws(closed=True))
        assert p.connected is True
        assert p.is_active is False

    def test_none_ws_is_not_active(self) -> None:
        p = PlayerSession(name="A", ws=None)  # type: ignore[arg-type]
        assert p.is_active is False


# ---------- Stale-WS rejoin ----------


class TestStaleWsRejoin:
    def test_rejoin_when_old_ws_closed_succeeds(self) -> None:
        reg = PlayerRegistry()
        old_ws = _ws(closed=False)
        ok, err = reg.add_player("Alice", old_ws, "LOBBY", reg.get_average_score)
        assert ok and err is None

        # Simulate the browser reload race: old WS is dead, but disconnect
        # hasn't fired yet so connected is still True.
        old_ws.closed = True

        new_ws = _ws(closed=False)
        ok2, err2 = reg.add_player("Alice", new_ws, "LOBBY", reg.get_average_score)
        assert ok2 is True
        assert err2 is None
        assert reg.players["Alice"].ws is new_ws

    def test_rejoin_when_old_ws_open_rejects(self) -> None:
        reg = PlayerRegistry()
        old_ws = _ws(closed=False)
        reg.add_player("Bob", old_ws, "LOBBY", reg.get_average_score)

        # Old WS still open → genuine dual-tab attempt; name should be taken.
        new_ws = _ws(closed=False)
        ok, err = reg.add_player("Bob", new_ws, "LOBBY", reg.get_average_score)
        assert ok is False
        assert err is not None  # ERR_NAME_TAKEN


# ---------- all_submitted uses is_active ----------


class TestAllSubmitted:
    def test_ghost_player_does_not_block_early_reveal(self) -> None:
        reg = PlayerRegistry()
        alice_ws = _ws(closed=False)
        bob_ws = _ws(closed=False)
        reg.add_player("Alice", alice_ws, "LOBBY", reg.get_average_score)
        reg.add_player("Bob", bob_ws, "LOBBY", reg.get_average_score)

        # Alice answers
        reg.players["Alice"].submitted = True
        # Bob has not answered; mark his WS dead but leave connected=True
        # (the ghost case).
        bob_ws.closed = True

        # Without the is_active fix this would return False (Bob blocks).
        assert reg.all_submitted() is True

    def test_late_joiner_does_not_block(self) -> None:
        reg = PlayerRegistry()
        reg.add_player("Alice", _ws(), "LOBBY", reg.get_average_score)
        reg.players["Alice"].submitted = True

        # Late joiner — mid-round add.
        reg.add_player("Late", _ws(), "QUESTION_ACTIVE", reg.get_average_score)
        assert reg.players["Late"].joined_late is True

        assert reg.all_submitted() is True


# ---------- get_average_score uses rounds_played ----------


class TestAverageScore:
    def test_excludes_players_without_completed_rounds(self) -> None:
        reg = PlayerRegistry()
        reg.add_player("A", _ws(), "LOBBY", reg.get_average_score)
        reg.add_player("B", _ws(), "LOBBY", reg.get_average_score)

        reg.players["A"].score = 100
        reg.players["A"].rounds_played = 2
        # B is a late joiner who hasn't played a round; should not count.
        reg.players["B"].score = 0
        reg.players["B"].rounds_played = 0

        # Old impl would average (100 + 0) / 2 = 50.
        # New impl averages over scored players only: 100 / 1 = 100.
        assert reg.get_average_score() == 100

    def test_returns_zero_when_no_one_has_played(self) -> None:
        reg = PlayerRegistry()
        reg.add_player("A", _ws(), "LOBBY", reg.get_average_score)
        assert reg.get_average_score() == 0


# ---------- leader property ----------


class TestLeader:
    def test_leader_none_when_no_players(self, tmp_path: Path) -> None:
        gs = QuizifyGameState(runtime=_FakeRuntime(tmp_path), entry_id="t")
        assert gs.leader is None

    def test_leader_is_top_scorer(self, tmp_path: Path) -> None:
        gs = QuizifyGameState(runtime=_FakeRuntime(tmp_path), entry_id="t")
        gs.add_player("Alice", _ws())
        gs.add_player("Bob", _ws())
        gs.players["Alice"].score = 50
        gs.players["Bob"].score = 80
        assert gs.leader is not None
        assert gs.leader.name == "Bob"


# ---------- State callbacks (HA sensor push) ----------


class TestStateCallbacks:
    def test_callback_fires_on_add_player(self, tmp_path: Path) -> None:
        gs = QuizifyGameState(runtime=_FakeRuntime(tmp_path), entry_id="t")
        calls = []
        gs.register_state_callback(lambda: calls.append("tick"))
        gs.add_player("Alice", _ws())
        assert calls == ["tick"]

    def test_callback_fires_on_remove_player(self, tmp_path: Path) -> None:
        gs = QuizifyGameState(runtime=_FakeRuntime(tmp_path), entry_id="t")
        gs.add_player("Alice", _ws())
        calls = []
        gs.register_state_callback(lambda: calls.append("tick"))
        gs.remove_player("Alice")
        assert calls == ["tick"]

    def test_unregister_stops_callback(self, tmp_path: Path) -> None:
        gs = QuizifyGameState(runtime=_FakeRuntime(tmp_path), entry_id="t")
        calls: list[str] = []

        def cb() -> None:
            calls.append("x")

        gs.register_state_callback(cb)
        gs.unregister_state_callback(cb)
        gs.add_player("Alice", _ws())
        assert calls == []

    def test_failing_callback_does_not_break_others(self, tmp_path: Path) -> None:
        gs = QuizifyGameState(runtime=_FakeRuntime(tmp_path), entry_id="t")
        good_calls: list[str] = []

        def bad() -> None:
            raise RuntimeError("boom")

        gs.register_state_callback(bad)
        gs.register_state_callback(lambda: good_calls.append("ok"))
        # Should not raise, and the good callback should still fire.
        gs.add_player("Alice", _ws())
        assert good_calls == ["ok"]
