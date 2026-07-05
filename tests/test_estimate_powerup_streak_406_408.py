"""Regression tests for the two estimate-round bugs #406 and #408.

#406 — power-ups that no-op on the estimate scoring path (JOKER, DOUBLE_POINTS,
STEAL) must be rejected up front so they aren't burned for nothing, and must
not be handed out on estimate rounds in the first place. FREEZE and TIME_BOOST
are pure timer effects that work identically on estimate rounds and stay usable.

#408 — the estimate evaluator recorded a non-exact guess as "wrong" yet still
grew the player's streak, letting a wrong guess step past a STREAK_MILESTONES
value without paying the milestone. Streak must only advance on an exact hit and
reset otherwise, so scoring and the recorded correctness agree.
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from custom_components.quizify.const import ERR_INVALID_ACTION  # noqa: E402
from custom_components.quizify.game.phase_controller import GamePhase  # noqa: E402
from custom_components.quizify.game.powerups import (  # noqa: E402
    PowerUpEffect,
    PowerUpType,
)
from custom_components.quizify.game.questions import (  # noqa: E402
    QUESTION_TYPE_ESTIMATE,
    Question,
)
from custom_components.quizify.game.state import QuizifyGameState  # noqa: E402


class _FakeRuntime:
    def __init__(self, tmp_path: Path) -> None:
        self.data_dir = tmp_path


def _fake_ws() -> MagicMock:
    ws = MagicMock()
    ws.closed = False
    return ws


def _make_estimate_question() -> Question:
    return Question(
        id="e-flow",
        question="How many bones?",
        answers=[],
        type=QUESTION_TYPE_ESTIMATE,
        estimate_answer=206,
        estimate_min=0,
        estimate_max=500,
        estimate_unit="bones",
        estimate_step=1,
    )


@pytest.fixture
def est_state(tmp_path: Path) -> QuizifyGameState:
    """A game state forced into an active estimate round with 3 players."""
    state = QuizifyGameState(runtime=_FakeRuntime(tmp_path), entry_id="test")
    state.add_player("Anna", _fake_ws())
    state.add_player("Tom", _fake_ws())
    state.add_player("Marina", _fake_ws())
    state.start_game(language="de", num_rounds=3, timer_duration=30)
    state.start_next_question()
    # Force the active question to a known estimate question.
    state._current_question = _make_estimate_question()
    for p in state._player_registry.players.values():
        p.reset_round()
    return state


# ---------------------------------------------------------------------------
# #406 — power-up gating on estimate rounds
# ---------------------------------------------------------------------------


class TestEstimatePowerupGate:
    @pytest.mark.parametrize(
        "powerup",
        [PowerUpType.JOKER, PowerUpType.DOUBLE_POINTS, PowerUpType.STEAL],
    )
    def test_noop_powerups_rejected_and_not_consumed(
        self, est_state: QuizifyGameState, powerup: PowerUpType
    ) -> None:
        est_state._powerup_manager._inventory["Anna"] = powerup
        # Tom is a valid, un-submitted opponent (would satisfy STEAL's
        # target resolution if the estimate gate weren't hit first).
        result = est_state.use_powerup("Anna", target_id="Tom")
        assert result == ERR_INVALID_ACTION
        # The once-per-game power-up survives for a later MC round.
        assert est_state._powerup_manager.has_powerup("Anna")
        assert est_state._powerup_manager.get_powerup("Anna") == powerup

    def test_freeze_still_works_on_estimate(
        self, est_state: QuizifyGameState
    ) -> None:
        est_state._powerup_manager._inventory["Anna"] = PowerUpType.FREEZE
        result = est_state.use_powerup("Anna", target_id="Tom")
        assert isinstance(result, PowerUpEffect)
        assert result.type == PowerUpType.FREEZE
        assert not est_state._powerup_manager.has_powerup("Anna")  # consumed

    def test_time_boost_still_works_on_estimate(
        self, est_state: QuizifyGameState
    ) -> None:
        est_state._powerup_manager._inventory["Anna"] = PowerUpType.TIME_BOOST
        result = est_state.use_powerup("Anna", target_id=None)
        assert isinstance(result, PowerUpEffect)
        assert result.type == PowerUpType.TIME_BOOST
        assert not est_state._powerup_manager.has_powerup("Anna")  # consumed

    def test_estimate_round_only_grants_timer_powerups(
        self, tmp_path: Path
    ) -> None:
        # Over many estimate rounds, the granted power-up is always one of the
        # two that actually work there — never a JOKER/DOUBLE/STEAL no-op.
        for _ in range(40):
            state = QuizifyGameState(
                runtime=_FakeRuntime(tmp_path), entry_id="test"
            )
            state.add_player("Solo", _fake_ws())
            state.start_game(language="de", num_rounds=3)
            # Force the next-served question to an estimate question so the
            # grant path sees is_estimate.
            state._question_bank.get_next_question = (  # type: ignore[method-assign]
                lambda *a, **k: _make_estimate_question()
            )
            state.start_next_question()
            granted = state._powerup_manager.get_powerup("Solo")
            assert granted in (PowerUpType.FREEZE, PowerUpType.TIME_BOOST)


# ---------------------------------------------------------------------------
# #408 — streak only advances on an exact estimate hit
# ---------------------------------------------------------------------------


class TestEstimateStreak:
    def test_non_exact_guess_resets_streak(
        self, est_state: QuizifyGameState
    ) -> None:
        anna = est_state._player_registry.get_player("Anna")
        anna.streak = 2  # coming off a 2-round MC streak
        # All three guess non-exactly → auto-evaluate.
        est_state.submit_guess("Anna", 210)  # 4 off, not exact
        est_state.submit_guess("Tom", 150)
        est_state.submit_guess("Marina", 240)
        assert est_state.phase == GamePhase.ANSWER_REVEAL
        # A non-exact guess is recorded "wrong" → streak must reset, not grow.
        assert anna.streak == 0
        assert anna.round_history[-1] == "wrong"

    def test_exact_guess_grows_streak(
        self, est_state: QuizifyGameState
    ) -> None:
        marina = est_state._player_registry.get_player("Marina")
        marina.streak = 2
        est_state.submit_guess("Anna", 210)
        est_state.submit_guess("Tom", 150)
        est_state.submit_guess("Marina", 206)  # exact
        assert est_state.phase == GamePhase.ANSWER_REVEAL
        # An exact hit is recorded "correct" → streak advances.
        assert marina.streak == 3
        assert marina.max_streak >= 3
        assert marina.round_history[-1] == "correct"

    def test_streak_and_recorded_result_agree(
        self, est_state: QuizifyGameState
    ) -> None:
        # For every guesser, a grown streak implies a "correct" record and a
        # reset streak implies "wrong" — the two must never disagree (#408).
        est_state.submit_guess("Anna", 206)  # exact
        est_state.submit_guess("Tom", 150)   # wrong
        est_state.submit_guess("Marina", 240)  # wrong
        for name in ("Anna", "Tom", "Marina"):
            p = est_state._player_registry.get_player(name)
            if p.round_history[-1] == "correct":
                assert p.streak >= 1
            else:
                assert p.streak == 0
