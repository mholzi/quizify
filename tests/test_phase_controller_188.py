"""Focused tests for the extracted PhaseController (issue #188).

The controller owns the game-flow phase value, the per-player question timers
and the round-timing bookkeeping, plus pause/resume. These tests exercise it in
isolation (no game state, scoring or registry) to lock in the behaviour the
extraction must preserve. End-to-end phase/timer behaviour through
QuizifyGameState is already covered by test_game_state / test_admin_redirect_pause
/ test_lightning; this file guards the unit itself.
"""

from __future__ import annotations

import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT))

from custom_components.quizify.game.phase_controller import (  # noqa: E402
    GamePhase,
    PhaseController,
)


def _make(players: list[str] | None = None) -> PhaseController:
    names = list(players or [])
    return PhaseController(players_fn=lambda: list(names))


class TestInitialState:
    def test_starts_in_lobby_with_no_timers(self) -> None:
        pc = _make()
        assert pc.phase == GamePhase.LOBBY
        assert pc.timers == {}
        assert pc.round_start_time is None
        assert pc.paused_from is None
        assert pc.pause_reason is None


class TestBeginRound:
    def test_creates_started_timer_per_player(self) -> None:
        pc = _make(["alice", "bob"])
        pc.begin_round(30.0)
        assert set(pc.timers) == {"alice", "bob"}
        assert pc.round_duration == 30.0
        assert pc.round_start_time is not None
        # Started timers count down from (near) the full duration.
        for t in pc.timers.values():
            assert 0 < t.get_remaining() <= 30.0

    def test_replaces_previous_round_timers(self) -> None:
        pc = _make(["alice"])
        pc.begin_round(30.0)
        first = pc.timers["alice"]
        pc.begin_round(20.0)
        assert pc.timers["alice"] is not first
        assert pc.round_duration == 20.0

    def test_enter_question_active_flips_phase(self) -> None:
        pc = _make(["alice"])
        pc.begin_round(30.0)
        pc.enter_question_active()
        assert pc.phase == GamePhase.QUESTION_ACTIVE


class TestLateJoiner:
    def test_late_joiner_gets_remaining_time_timer(self) -> None:
        pc = _make(["alice"])
        pc.begin_round(30.0)
        pc.enter_question_active()
        pc.add_late_joiner_timer("bob")
        assert "bob" in pc.timers
        # Tracks remaining round time, never below the 0.5s floor.
        assert 0.5 <= pc.timers["bob"].get_remaining() <= 30.0

    def test_no_op_outside_question_active(self) -> None:
        pc = _make(["alice"])
        # LOBBY — no round running.
        pc.add_late_joiner_timer("bob")
        assert "bob" not in pc.timers

    def test_does_not_replace_existing_timer(self) -> None:
        pc = _make(["alice"])
        pc.begin_round(30.0)
        pc.enter_question_active()
        existing = pc.timers["alice"]
        pc.add_late_joiner_timer("alice")
        assert pc.timers["alice"] is existing


class TestTimerLifecycle:
    def test_drop_and_clear(self) -> None:
        pc = _make(["alice", "bob"])
        pc.begin_round(30.0)
        pc.drop_timer("alice")
        assert "alice" not in pc.timers and "bob" in pc.timers
        pc.clear_timers()
        assert pc.timers == {}

    def test_get_timer(self) -> None:
        pc = _make(["alice"])
        pc.begin_round(30.0)
        assert pc.get_timer("alice") is pc.timers["alice"]
        assert pc.get_timer("ghost") is None


class TestPauseResume:
    def test_pause_only_from_question_active(self) -> None:
        pc = _make(["alice"])
        # LOBBY → no-op.
        assert pc.pause() is False
        assert pc.phase == GamePhase.LOBBY

    def test_pause_freezes_and_snapshots(self) -> None:
        pc = _make(["alice", "bob"])
        pc.begin_round(30.0)
        pc.enter_question_active()
        assert pc.pause(reason="admin_paused") is True
        assert pc.phase == GamePhase.PAUSED
        assert pc.paused_from == GamePhase.QUESTION_ACTIVE
        assert pc.pause_reason == "admin_paused"
        # Timers frozen (cleared) and remaining snapshotted per player.
        assert pc.timers == {}
        assert set(pc.paused_remaining) == {"alice", "bob"}
        assert all(v >= 0 for v in pc.paused_remaining.values())

    def test_resume_restores_to_question_active(self) -> None:
        pc = _make(["alice"])
        pc.begin_round(30.0)
        pc.enter_question_active()
        pc.pause()
        assert pc.resume() is True
        assert pc.phase == GamePhase.QUESTION_ACTIVE
        assert "alice" in pc.timers
        assert pc.paused_remaining == {}
        assert pc.pause_reason is None
        assert pc.paused_from is None

    def test_resume_only_from_paused(self) -> None:
        pc = _make(["alice"])
        assert pc.resume() is False

    def test_late_joiner_during_pause_gets_full_timer(self) -> None:
        # A player present only at resume (not in the pause snapshot) gets a
        # fresh full-round timer.
        names = ["alice"]
        pc = PhaseController(players_fn=lambda: list(names))
        pc.begin_round(30.0)
        pc.enter_question_active()
        pc.pause()
        names.append("carol")  # joined while paused
        pc.resume()
        assert "carol" in pc.timers
        # Full duration (allow for the tiny elapsed since start()).
        assert pc.timers["carol"].get_remaining() > 29.0


class TestSnapshotRemaining:
    def test_time_remaining_for_snapshot(self) -> None:
        pc = _make(["alice"])
        pc.begin_round(30.0)
        pc.enter_question_active()
        remaining = pc.time_remaining_for_snapshot()
        assert 0 < remaining <= 30.0

    def test_remaining_is_zero_when_no_round(self) -> None:
        pc = _make()
        # No round_start_time → elapsed treated as 0, full duration returned.
        assert pc.time_remaining_for_snapshot() == pc.round_duration
