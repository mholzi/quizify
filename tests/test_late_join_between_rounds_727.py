"""Regression tests for #727 — a between-rounds join is not a late join.

``add_player`` used to set ``joined_late = phase_value != "LOBBY"``, so joining
during ANSWER_REVEAL (or the wager window, or a Lightning / Hot Seat detour)
was flagged exactly like joining in the middle of a live question. The flag is
only cleared at the end of ``_do_evaluate_round``, so it survived into the
NEXT round: ``all_submitted()`` skipped the player, the round closed on the
other answers, and the newcomer was recorded as a ``"timeout"`` with 0 points
while their phone still showed a running clock.

Covered here:
- the phase split itself (``MID_QUESTION_PHASES``),
- the average-score seeding, which must still key off "joined after the
  lobby" and not off the narrowed mid-question flag,
- the end-to-end failure scenario from the issue.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT))

from custom_components.quizify.game.phase_controller import (  # noqa: E402
    MID_QUESTION_PHASES,
    GamePhase,
)
from custom_components.quizify.game.player_registry import (  # noqa: E402
    PlayerRegistry,
)
from custom_components.quizify.game.state import QuizifyGameState  # noqa: E402


def _fake_ws() -> MagicMock:
    ws = MagicMock()
    ws.closed = False
    return ws


class _Runtime:
    """Minimal runtime: runs scheduled coroutines on the live loop."""

    def __init__(self, tmp_path: Path) -> None:
        self.data_dir = tmp_path

    async def run_in_executor(self, func, *args):  # noqa: ANN001, ANN002
        return func(*args)

    def create_task(self, coro):  # noqa: ANN001
        return asyncio.ensure_future(coro)


# ---------- which phases count as "a question is in flight" ----------


class TestMidQuestionPhaseSplit:
    @pytest.mark.parametrize(
        "phase",
        [GamePhase.QUESTION_ACTIVE, GamePhase.PAUSED],
    )
    def test_join_during_live_question_is_late(self, phase: GamePhase) -> None:
        """A join while the answer clock runs (or is frozen mid-run) must stay
        flagged — that player genuinely cannot answer in time and must not
        hold up all_submitted()."""
        reg = PlayerRegistry()
        reg.add_player("Alice", _fake_ws(), "LOBBY", reg.get_average_score)
        ok, err = reg.add_player(
            "Newcomer", _fake_ws(), phase.value, reg.get_average_score
        )
        assert (ok, err) == (True, None)
        assert reg.players["Newcomer"].joined_late is True

    @pytest.mark.parametrize(
        "phase",
        [
            GamePhase.ANSWER_REVEAL,
            GamePhase.WAGER_ACTIVE,
            GamePhase.LIGHTNING,
            GamePhase.LIGHTNING_RECAP,
            GamePhase.HOT_SEAT_AUCTION,
            GamePhase.HOT_SEAT,
            GamePhase.HOT_SEAT_REVEAL,
            GamePhase.FINALE,
        ],
    )
    def test_join_between_rounds_is_not_late(self, phase: GamePhase) -> None:
        """No question is running in any of these phases: the joiner gets a
        full timer from begin_round like everybody else, so they must count
        toward all_submitted() from the very next round on (#727)."""
        reg = PlayerRegistry()
        reg.add_player("Alice", _fake_ws(), "LOBBY", reg.get_average_score)
        ok, err = reg.add_player(
            "Newcomer", _fake_ws(), phase.value, reg.get_average_score
        )
        assert (ok, err) == (True, None)
        assert reg.players["Newcomer"].joined_late is False

    def test_phase_set_is_an_allowlist(self) -> None:
        """Guard against a future phase silently inheriting the flag."""
        assert set(MID_QUESTION_PHASES) == {"QUESTION_ACTIVE", "PAUSED"}

    def test_between_rounds_joiner_is_not_blocking_yet_but_counts_next(
        self,
    ) -> None:
        """all_submitted() must wait for a player who joined at the reveal."""
        reg = PlayerRegistry()
        reg.add_player("Alice", _fake_ws(), "LOBBY", reg.get_average_score)
        reg.add_player("Dana", _fake_ws(), "ANSWER_REVEAL", reg.get_average_score)
        reg.players["Alice"].submitted = True
        assert reg.all_submitted() is False
        reg.players["Dana"].submitted = True
        assert reg.all_submitted() is True


# ---------- seeding still keys off "joined after the lobby" ----------


class TestAverageScoreSeedingUnchanged:
    @pytest.mark.parametrize(
        "phase_value",
        ["ANSWER_REVEAL", "WAGER_ACTIVE", "QUESTION_ACTIVE", "FINALE"],
    )
    def test_any_post_lobby_join_is_seeded_with_the_average(
        self, phase_value: str
    ) -> None:
        """Narrowing ``joined_late`` must not un-seed the newcomer's score:
        somebody joining at the reveal still missed every scored round."""
        reg = PlayerRegistry()
        ok, _ = reg.add_player("Dana", _fake_ws(), phase_value, lambda: 42)
        assert ok is True
        assert reg.players["Dana"].score == 42

    def test_lobby_join_still_starts_at_zero(self) -> None:
        reg = PlayerRegistry()
        ok, _ = reg.add_player("Dana", _fake_ws(), "LOBBY", lambda: 42)
        assert ok is True
        assert reg.players["Dana"].score == 0
        assert reg.players["Dana"].joined_late is False


# ---------- the scenario from the issue, end to end ----------


class TestJoinAtRevealPlaysTheNextRound:
    def test_reveal_joiner_is_not_scored_as_a_timeout(
        self, tmp_path: Path
    ) -> None:
        """A, B play; C joins during the reveal. The next round must not close
        on A+B alone, and C must not be recorded as a timeout while their own
        clock is still running."""
        gs = QuizifyGameState(runtime=_Runtime(tmp_path), entry_id="t")
        gs.add_player("Alice", _fake_ws())
        gs.add_player("Bob", _fake_ws())
        gs.start_game(language="de", num_rounds=5)
        gs.start_next_question()

        gs.submit_answer("Alice", 0)
        gs.submit_answer("Bob", 0)
        assert gs.phase is GamePhase.ANSWER_REVEAL

        # Carol joins between the two rounds.
        gs.add_player("Carol", _fake_ws())
        carol = gs.get_player("Carol")
        assert carol is not None
        assert carol.joined_late is False

        # Round 2: Alice + Bob answering must NOT close the round.
        gs.start_next_question()
        assert gs.phase is GamePhase.QUESTION_ACTIVE
        gs.submit_answer("Alice", 0)
        gs.submit_answer("Bob", 0)
        assert gs._player_registry.all_submitted() is False
        assert gs.phase is GamePhase.QUESTION_ACTIVE

        # Carol's tap is accepted (no ERR_ROUND_EXPIRED) and closes the round.
        result = gs.submit_answer("Carol", 0)
        assert not isinstance(result, str), f"got error: {result!r}"
        assert gs.phase is GamePhase.ANSWER_REVEAL

        # And she was never booked as a timeout for the round she played.
        assert "timeout" not in carol.round_history

    def test_mid_question_joiner_still_does_not_block(
        self, tmp_path: Path
    ) -> None:
        """The reason the flag exists is unchanged: a genuine mid-question
        join must still let the round close on the other players."""
        gs = QuizifyGameState(runtime=_Runtime(tmp_path), entry_id="t")
        gs.add_player("Alice", _fake_ws())
        gs.add_player("Bob", _fake_ws())
        gs.start_game(language="de", num_rounds=5)
        gs.start_next_question()

        gs.add_player("Carol", _fake_ws())
        carol = gs.get_player("Carol")
        assert carol is not None
        assert carol.joined_late is True

        gs.submit_answer("Alice", 0)
        gs.submit_answer("Bob", 0)
        assert gs.phase is GamePhase.ANSWER_REVEAL
