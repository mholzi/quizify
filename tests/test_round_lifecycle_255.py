"""Regression tests for the round-lifecycle & scoring edge-cases in #255.

Covers:
- HIGH: ``joined_late`` is cleared at the end of the round the player joined
  into, so from their next round on they count toward ``all_submitted()``.
- MEDIUM: a round where every player has disconnected still evaluates once the
  round wall-clock has elapsed (no infinite tick-loop spin).
- MEDIUM: ``end_game()`` is idempotent — a second call records analytics once
  and emits a single finale broadcast, reusing the cached payload.
- LOW: Comeback King is not a false-positive on flat per-round scores with an
  odd round count.
- LOW: a wager submitted after the answer is rejected with an error.
"""

from __future__ import annotations

import asyncio
import sys
import time
from pathlib import Path
from unittest.mock import MagicMock

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT))

from custom_components.quizify.game.highlights import (  # noqa: E402
    compute_superlatives,
)
from custom_components.quizify.game.phase_controller import (  # noqa: E402
    PhaseController,
)
from custom_components.quizify.game.player import PlayerSession  # noqa: E402
from custom_components.quizify.game.state import (  # noqa: E402
    GamePhase,
    QuizifyGameState,
)


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


# ---------- HIGH: joined_late cleared after the joined round ----------


class TestLateJoinerFlagCleared:
    def test_late_joiner_counts_from_their_second_round(
        self, tmp_path: Path
    ) -> None:
        """A player who joins mid-round is excluded from all_submitted() for
        that one round, but once it evaluates the flag is cleared and they
        block the *next* round's early-reveal until they answer too — instead
        of being silently scored 0 (timeout) every remaining round."""
        gs = QuizifyGameState(runtime=_Runtime(tmp_path), entry_id="t")
        gs.add_player("Alice", _fake_ws())
        gs.add_player("Bob", _fake_ws())
        gs.start_game(language="de", num_rounds=5)
        gs.start_next_question()

        # Charlie joins mid-round 1 → flagged late.
        gs.add_player("Charlie", _fake_ws())
        assert gs.get_player("Charlie").joined_late is True

        # Round 1: only the original two answer; the late joiner doesn't block.
        gs.submit_answer("Alice", 0)
        gs.submit_answer("Bob", 0)
        assert gs.phase is not GamePhase.QUESTION_ACTIVE

        # The flag was cleared by the round evaluation.
        assert gs.get_player("Charlie").joined_late is False

        # Round 2: Charlie is now a full participant — Alice+Bob answering is
        # NOT enough to close the round; it stays open until Charlie answers.
        gs.start_next_question()
        gs.submit_answer("Alice", 0)
        gs.submit_answer("Bob", 0)
        assert gs.phase is GamePhase.QUESTION_ACTIVE
        assert gs._player_registry.all_submitted() is False

        gs.submit_answer("Charlie", 0)
        assert gs.phase is not GamePhase.QUESTION_ACTIVE


# ---------- MEDIUM: all-disconnect round still evaluates ----------


class TestAllDisconnectEvaluates:
    def test_round_wall_clock_expired_after_duration(self) -> None:
        """With no live per-player timers (everyone disconnected), the round
        wall-clock is the fallback break condition for the tick loop."""
        pc = PhaseController(players_fn=lambda: [])
        # Before a round starts there is no wall-clock to expire.
        assert pc.round_wall_clock_expired() is False

        pc.begin_round(round_duration=10.0)
        assert pc.round_wall_clock_expired() is False

        # Simulate the wall-clock having fully elapsed.
        pc.round_start_time = time.monotonic() - 11.0
        assert pc.round_wall_clock_expired() is True

    def test_all_timers_expired_needs_a_live_timer(self) -> None:
        """all_timers_expired() can't break when there are no timers left —
        which is exactly why round_wall_clock_expired() exists as a fallback."""
        pc = PhaseController(players_fn=lambda: [])
        pc.begin_round(round_duration=10.0)
        # No connected players → empty list → never "all expired".
        assert pc.all_timers_expired([]) is False
        # And the wall-clock is the path that actually ends the round.
        pc.round_start_time = time.monotonic() - 11.0
        assert pc.round_wall_clock_expired() is True

    def test_state_delegates_wall_clock(self, tmp_path: Path) -> None:
        gs = QuizifyGameState(runtime=_Runtime(tmp_path), entry_id="t")
        gs.add_player("Alice", _fake_ws())
        gs.start_game(language="de", num_rounds=3)
        gs.start_next_question()
        # Force the shared round wall-clock past the duration.
        gs._phase_controller.round_start_time = time.monotonic() - 9999.0
        assert gs.round_wall_clock_expired() is True


# ---------- MEDIUM: end_game idempotency ----------


class TestEndGameIdempotent:
    @pytest.mark.asyncio
    async def test_double_end_game_records_analytics_once(
        self, tmp_path: Path
    ) -> None:
        """A re-entry into end_game() while already in FINALE must not record
        the game to analytics a second time, and must reuse the cached payload."""
        gs = QuizifyGameState(runtime=_Runtime(tmp_path), entry_id="t")

        record_calls: list[dict] = []

        class _Stats:
            async def record_game(self, **kwargs):  # noqa: ANN003
                record_calls.append(kwargs)

        gs._stats_service = _Stats()
        gs.add_player("Alice", _fake_ws())
        gs.add_player("Bob", _fake_ws())
        gs.start_game(language="de", num_rounds=2)
        gs.start_next_question()

        first = gs.end_game()
        second = gs.end_game()

        # Same cached payload object returned on re-entry.
        assert second is first
        assert gs.phase is GamePhase.FINALE

        # Let the scheduled record_game coroutine(s) run.
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        assert len(record_calls) == 1

    @pytest.mark.asyncio
    async def test_double_end_game_single_finale_broadcast(
        self, tmp_path: Path
    ) -> None:
        """The game_ended state event (→ finale broadcast) fires exactly once
        across two end_game() calls."""
        gs = QuizifyGameState(runtime=_Runtime(tmp_path), entry_id="t")

        events: list[str] = []

        async def _broadcast(payload: dict) -> None:
            events.append(payload.get("event", ""))

        gs.set_broadcast_callback(_broadcast)
        gs.add_player("Alice", _fake_ws())
        gs.add_player("Bob", _fake_ws())
        gs.start_game(language="de", num_rounds=2)
        gs.start_next_question()

        gs.end_game()
        gs.end_game()

        await asyncio.sleep(0)
        await asyncio.sleep(0)
        assert events.count("game_ended") == 1


# ---------- LOW: Comeback King false-positive on odd rounds ----------


def _mk_player(name: str, round_scores: list[int]) -> PlayerSession:
    p = PlayerSession(name=name, ws=MagicMock())
    p.round_scores = list(round_scores)
    p.round_history = ["correct"] * len(round_scores)
    return p


class TestComebackKingOddRounds:
    def test_flat_odd_round_scores_no_comeback(self) -> None:
        """Five identical per-round scores must NOT trigger Comeback King.

        With an odd round count the second half held one extra round, so the
        raw-sum comparison (sum(scores[2:]) > sum(scores[:2])) falsely fired."""
        players = [
            _mk_player("Flat", [10, 10, 10, 10, 10]),  # 5 rounds, all equal
            _mk_player("AlsoFlat", [5, 5, 5, 5, 5]),
        ]
        awards = compute_superlatives(players)
        names = {a.award for a in awards}
        assert "Comeback King" not in names

    def test_genuine_comeback_still_awarded(self) -> None:
        """A real second-half surge still earns the award (no regression).

        ``TopDog`` carries the single highest round so it claims Top Score
        first (awards are exclusive), leaving Comeback King free for the player
        whose per-round average genuinely climbs in the back half."""
        players = [
            _mk_player("TopDog", [50, 5, 5, 5, 5]),
            _mk_player("Steady", [10, 10, 10, 10, 10]),
            _mk_player("Riser", [0, 5, 5, 20, 20]),  # weak start, strong finish
        ]
        awards = compute_superlatives(players)
        comeback = [a for a in awards if a.award == "Comeback King"]
        assert len(comeback) == 1
        assert comeback[0].winner == "Riser"


# ---------- LOW: wager after answer rejected ----------


class TestWagerAfterAnswerRejected:
    @pytest.mark.asyncio
    async def test_wager_rejected_when_already_submitted(
        self, tmp_path: Path
    ) -> None:
        """A wager arriving after the player has locked in their answer never
        reaches the stake.

        #255 guarded this with an explicit error, because back then the wager
        UI lived *inside* the live question. #656 moved betting into its own
        phase that closes before the question is sent, so the same attempt is
        now refused one step earlier — by the phase gate, silently. What #255
        actually protects is unchanged and asserted below: the stake on an
        already-locked answer cannot be mutated.
        """
        from custom_components.quizify.server.websocket import (  # noqa: PLC0415
            QuizifyWebSocketHandler,
        )

        gs = QuizifyGameState(runtime=_Runtime(tmp_path), entry_id="t")
        ws = _fake_ws()
        gs.add_player("Alice", ws)
        # A second player keeps the round in QUESTION_ACTIVE after Alice
        # answers (so the wager phase-guard doesn't short-circuit first).
        gs.add_player("Bob", _fake_ws())
        # Final round (round == total_rounds) so the wager path is reachable.
        gs.start_game(language="de", num_rounds=1)
        gs.start_next_question()
        # #656: the final round opens in the betting window. Close it so the
        # question is live and Alice can lock in an answer.
        assert gs.arm_round_timers() is True
        gs.submit_answer("Alice", 0)
        assert gs.get_player("Alice").submitted is True
        assert gs.phase is GamePhase.QUESTION_ACTIVE

        # Build a handler with a stubbed connection so we can capture errors.
        handler = QuizifyWebSocketHandler.__new__(QuizifyWebSocketHandler)
        sent_errors: list[tuple] = []

        class _Conn:
            async def send_error(self, ws, code, msg):  # noqa: ANN001
                sent_errors.append((code, msg))

        handler._conn = _Conn()

        await handler._handle_submit_wager(ws, {"wager": 50}, gs)

        # The stale wager was not applied — the point of #255.
        assert gs.get_player("Alice").wager in (None, 0)
        # Refused by the closed window, so no error frame is produced.
        assert sent_errors == []
