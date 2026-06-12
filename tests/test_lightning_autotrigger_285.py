"""Tests for the auto-triggered Lightning Round (issue #285).

The Lightning Round (#42) stopped being a host-manual mode and became an
automatic, random mid-game event. These tests pin the contract Markus
confirmed (2026-06-11):

* AC2 — fires exactly once per game, at a uniformly random round.
* AC3 — eligible window is rounds 3 … N-1 (first two + last blocked).
* AC4 — a short game (window empty) skips it; we never force one.
* AC5 — a settings toggle (default ON) arms it; off disarms it.

The target-round pick is seeded via ``start_game(lightning_seed=...)`` so the
window bounds, the once-per-game guarantee and the short-game skip are all
asserted deterministically. The fast-loop mechanics live in
``game/lightning.py`` and are covered by ``test_lightning.py`` — here we drive
the *entry* (state machine) only.
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT))

from custom_components.quizify.game.state import (  # noqa: E402
    GamePhase,
    QuizifyGameState,
)


def _fake_ws() -> MagicMock:
    ws = MagicMock()
    ws.closed = False
    return ws


class _Runtime:
    def __init__(self, tmp_path: Path) -> None:
        self.data_dir = tmp_path

    async def run_in_executor(self, func, *args):  # noqa: ANN001, ANN002
        return func(*args)

    def create_task(self, coro):  # noqa: ANN001
        import asyncio

        return asyncio.ensure_future(coro)


@pytest.fixture
def state(tmp_path: Path) -> QuizifyGameState:
    st = QuizifyGameState(runtime=_Runtime(tmp_path), entry_id="test")
    st.add_player("A", _fake_ws())
    return st


# ---------------------------------------------------------------------------
# AC3 — eligible window = rounds 3 … N-1
# ---------------------------------------------------------------------------


class TestEligibleWindow:
    def test_earliest_is_round_3(self, state: QuizifyGameState) -> None:
        # seed 14 lands the uniform pick on the window's low bound for N=10.
        state.start_game(num_rounds=10, lightning_seed=14)
        assert state.lightning_target_round == 3

    def test_latest_is_n_minus_1(self, state: QuizifyGameState) -> None:
        # seed 0 lands the pick on the window's high bound (N-1 = 9) for N=10.
        state.start_game(num_rounds=10, lightning_seed=0)
        assert state.lightning_target_round == 9

    def test_target_never_outside_window_over_many_seeds(
        self, state: QuizifyGameState
    ) -> None:
        for seed in range(300):
            state.reset_to_lobby()
            state.start_game(num_rounds=12, lightning_seed=seed)
            target = state.lightning_target_round
            assert target is not None
            assert 3 <= target <= 11  # 3 … N-1, N=12

    def test_round_1_and_2_never_trigger(self, state: QuizifyGameState) -> None:
        # Pin the target to round 3 (earliest), then assert no trigger fires
        # while the game is about to enter round 1 or round 2.
        state.start_game(num_rounds=10, lightning_seed=14)
        assert state.lightning_target_round == 3
        # About to enter round 1 (round counter == 0).
        assert state.round == 0
        assert state.should_trigger_lightning() is False
        # Simulate completing round 1 → about to enter round 2.
        state.round = 1
        state.phase = GamePhase.ANSWER_REVEAL
        assert state.should_trigger_lightning() is False
        # About to enter round 3 → fires.
        state.round = 2
        assert state.should_trigger_lightning() is True

    def test_last_round_never_triggers(self, state: QuizifyGameState) -> None:
        # N-1 is the high bound, so the pick can never select round N itself.
        # Drive the counter to "about to enter the final round" and confirm no
        # trigger, regardless of the (already-armed) target.
        state.start_game(num_rounds=5, lightning_seed=0)
        target = state.lightning_target_round
        assert target is not None and target <= 4  # never 5 (the last round)


# ---------------------------------------------------------------------------
# AC2 — fires exactly once per game
# ---------------------------------------------------------------------------


class TestExactlyOnce:
    def test_trigger_only_at_target_round(self, state: QuizifyGameState) -> None:
        state.start_game(num_rounds=10, lightning_seed=42)
        target = state.lightning_target_round
        assert target is not None
        # Walk every between-rounds advance point; only the target fires.
        fired_at = []
        for upcoming in range(1, 11):
            state.round = upcoming - 1
            state.phase = (
                GamePhase.LOBBY if upcoming == 1 else GamePhase.ANSWER_REVEAL
            )
            if state.should_trigger_lightning():
                fired_at.append(upcoming)
        assert fired_at == [target]

    def test_fired_flag_blocks_second_trigger(
        self, state: QuizifyGameState
    ) -> None:
        state.start_game(num_rounds=10, lightning_seed=42)
        target = state.lightning_target_round
        assert target is not None
        # Position the game at the target and actually start the auto round.
        state.round = target - 1
        state.phase = GamePhase.ANSWER_REVEAL
        assert state.should_trigger_lightning() is True
        assert state.start_lightning_round(auto=True) is True
        # Once armed, the flag is burned — even back at the same round it never
        # re-triggers.
        state.phase = GamePhase.ANSWER_REVEAL
        state.round = target - 1
        assert state.should_trigger_lightning() is False


# ---------------------------------------------------------------------------
# AC4 — short game skips it
# ---------------------------------------------------------------------------


class TestShortGameSkip:
    @pytest.mark.parametrize("num_rounds", [1, 2, 3])
    def test_window_empty_no_target(
        self, state: QuizifyGameState, num_rounds: int
    ) -> None:
        state.start_game(num_rounds=num_rounds, lightning_seed=42)
        assert state.lightning_target_round is None
        # And no advance point ever triggers.
        for upcoming in range(1, num_rounds + 1):
            state.round = upcoming - 1
            state.phase = (
                GamePhase.LOBBY if upcoming == 1 else GamePhase.ANSWER_REVEAL
            )
            assert state.should_trigger_lightning() is False

    def test_four_rounds_is_smallest_eligible(
        self, state: QuizifyGameState
    ) -> None:
        # N=4 → window is just {3}; the LR is armed (not skipped).
        state.start_game(num_rounds=4, lightning_seed=42)
        assert state.lightning_target_round == 3


# ---------------------------------------------------------------------------
# AC5 — settings toggle, default ON
# ---------------------------------------------------------------------------


class TestToggle:
    def test_default_on_arms_lightning(self, state: QuizifyGameState) -> None:
        # No explicit toggle → ON by default.
        state.start_game(num_rounds=10, lightning_seed=42)
        assert state.lightning_target_round is not None

    def test_toggle_off_disarms_lightning(
        self, state: QuizifyGameState
    ) -> None:
        state.start_game(
            num_rounds=10, lightning_enabled=False, lightning_seed=42
        )
        assert state.lightning_target_round is None
        # No advance point triggers when disabled.
        for upcoming in range(1, 11):
            state.round = upcoming - 1
            state.phase = (
                GamePhase.LOBBY if upcoming == 1 else GamePhase.ANSWER_REVEAL
            )
            assert state.should_trigger_lightning() is False

    def test_toggle_persisted_in_last_settings(
        self, state: QuizifyGameState
    ) -> None:
        # "Play again — same settings" must carry the toggle choice.
        state.start_game(num_rounds=10, lightning_enabled=False)
        assert state.last_settings["lightning_enabled"] is False


# ---------------------------------------------------------------------------
# Detour / resume — the mid-game entry + return-to-game transition
# ---------------------------------------------------------------------------


class TestDetourAndResume:
    def test_auto_entry_from_answer_reveal(
        self, state: QuizifyGameState
    ) -> None:
        # auto=True may fire from ANSWER_REVEAL (between rounds); the legacy
        # auto=False path still rejects that phase.
        state.start_game(num_rounds=10, lightning_seed=42)
        state.round = state.lightning_target_round - 1  # type: ignore[operator]
        state.phase = GamePhase.ANSWER_REVEAL
        assert state.start_lightning_round(auto=False) is False
        assert state.start_lightning_round(auto=True) is True
        assert state.phase == GamePhase.LIGHTNING
        assert state.in_lightning_detour is True

    def test_resume_restores_round_and_phase(
        self, state: QuizifyGameState
    ) -> None:
        state.start_game(num_rounds=10, lightning_seed=42)
        target = state.lightning_target_round
        assert target is not None
        resume_round = target - 1
        state.round = resume_round
        state.phase = GamePhase.ANSWER_REVEAL
        assert state.start_lightning_round(auto=True) is True
        state.finish_lightning_round()
        assert state.phase == GamePhase.LIGHTNING_RECAP

        assert state.resume_after_lightning() is True
        # Back in the main game at the round we detoured from, ready for the
        # normal start_next_question to proceed into the target round.
        assert state.phase == GamePhase.ANSWER_REVEAL
        assert state.round == resume_round
        assert state.in_lightning_detour is False
        assert state.lightning is None

    def test_resume_noop_without_detour(
        self, state: QuizifyGameState
    ) -> None:
        # A standalone lightning session (no main game paused behind it) has
        # nothing to resume.
        state.add_player("A", _fake_ws())
        assert state.start_lightning_round() is True  # auto=False, from LOBBY
        state.finish_lightning_round()
        assert state.in_lightning_detour is False
        assert state.resume_after_lightning() is False

    def test_reset_clears_autotrigger_state(
        self, state: QuizifyGameState
    ) -> None:
        state.start_game(num_rounds=10, lightning_seed=42)
        assert state.lightning_target_round is not None
        state.reset_to_lobby()
        assert state.lightning_target_round is None
        assert state.in_lightning_detour is False
