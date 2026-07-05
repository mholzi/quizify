"""Regression tests for the be-a-ws-state-r4 backend batch.

Four independent fixes, bundled here because they share the
server/websocket.py + game/state.py + game/lightning.py surface:

* #448 — _handle_join's auto-rename loop gated on the raw ``connected`` flag
         renamed a rejoiner to "Name 2" even when the old slot was a stale
         connected-but-closed ghost, making PlayerRegistry.add_player's
         same-name reclaim unreachable (duplicate ghost, score 0). Fixed by
         gating on ``is_active`` (connected AND ws open).
* #450 — submit_answer overwrote ``round_score`` with a plain assignment,
         wiping any pre-submit STEAL / reaction-bonus delta from round_score
         while ``score`` kept it. Fixed by accumulating (``+= points``).
* #453 — every join/leave broadcast a full roster to every socket (O(N²) on a
         room-wide blip). Now coalesced through a ~150ms flush window into one
         ``player_joined`` / ``player_left`` per window.
* #455 — get_state_snapshot rebuilt the immutable lightning recap on every
         join/reconnect during LIGHTNING_RECAP. Now memoized behind
         ``LightningRound.finished``.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT))

from custom_components.quizify.game.lightning import LightningRound  # noqa: E402
from custom_components.quizify.game.powerups import (  # noqa: E402
    PowerUpEffect,
    PowerUpType,
)
from custom_components.quizify.game.questions import QuestionBank  # noqa: E402
from custom_components.quizify.game.state import (  # noqa: E402
    GamePhase,
    QuizifyGameState,
)
from custom_components.quizify.server.connection import ConnectionManager  # noqa: E402
from custom_components.quizify.server.websocket import (  # noqa: E402
    QuizifyWebSocketHandler,
)


class _FakeRuntime:
    def __init__(self, tmp_path: Path) -> None:
        self.data_dir = tmp_path

    def create_task(self, coro):
        return asyncio.ensure_future(coro)

    async def run_in_executor(self, func, *args):
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, func, *args)


def _ws(closed: bool = False) -> MagicMock:
    ws = MagicMock()
    ws.closed = closed
    ws.send_json = AsyncMock()
    return ws


def _correct_index(game: QuizifyGameState) -> int:
    q = game._current_question
    assert q is not None
    return next(i for i, a in enumerate(q.answers) if a.correct)


@pytest.fixture
def game(tmp_path: Path) -> QuizifyGameState:
    return QuizifyGameState(runtime=_FakeRuntime(tmp_path), entry_id="test")


@pytest.fixture
def handler(game: QuizifyGameState) -> QuizifyWebSocketHandler:
    runtime = _FakeRuntime(game._runtime.data_dir)  # type: ignore[attr-defined]
    h = QuizifyWebSocketHandler(runtime=runtime, game_state_provider=lambda: game)
    h._conn = ConnectionManager(runtime, lambda: game)
    h._conn.broadcast = AsyncMock()
    h._conn.send = AsyncMock()
    h._conn.send_error = AsyncMock()
    # Fast coalescing window so the roster tests don't crawl.
    h._REACTION_FLUSH_WINDOW = 0.02
    return h


async def _drain_roster(handler: QuizifyWebSocketHandler) -> None:
    """Let the coalescing flush window elapse and the task finish."""
    task = handler._roster_flush_task
    if task is not None:
        await asyncio.wait_for(asyncio.shield(task), timeout=2.0)
    # A tiny extra beat for any re-armed tail pass to settle.
    await asyncio.sleep(handler._REACTION_FLUSH_WINDOW * 2)


# ---------------------------------------------------------------------------
# #448 — stale connected slot must be reclaimed, not renamed
# ---------------------------------------------------------------------------


class TestJoinReclaimStaleSlot:
    @pytest.mark.asyncio
    async def test_stale_ghost_reclaimed_not_duplicated(
        self, handler: QuizifyWebSocketHandler, game: QuizifyGameState
    ) -> None:
        """A reload leaves the old slot connected=True with a CLOSED ws. A
        rejoin under the SAME name must reclaim that slot (keeping score), not
        rename to "Alice 2" and spawn a score-0 ghost."""
        alice_ws = _ws()
        bob_ws = _ws()
        game.add_player("Alice", alice_ws)
        game.add_player("Bob", bob_ws)
        game.start_game(num_rounds=3, language="de")
        game.start_next_question()

        # Alice answers correctly → she has a real score.
        game.submit_answer("Alice", _correct_index(game))
        alice = game.get_player("Alice")
        assert alice is not None
        assert alice.score > 0
        earned = alice.score

        # Simulate the reload: her tab's ws dies but _handle_disconnect hasn't
        # fired yet → stale connected flag with a closed ws.
        alice_ws.closed = True
        assert alice.connected is True
        assert not alice.is_active  # the case the fix keys on

        # Alice rejoins with a fresh ws under the same name.
        fresh_ws = _ws()
        await handler._handle_join(fresh_ws, {"name": "Alice"}, game)

        # No ghost: still exactly one Alice, no "Alice 2", score preserved.
        names = [p.name for p in game.get_players()]
        assert names.count("Alice") == 1
        assert "Alice 2" not in names
        reclaimed = game.get_player("Alice")
        assert reclaimed is not None
        assert reclaimed.score == earned
        assert reclaimed.ws is fresh_ws
        assert reclaimed.is_active

    @pytest.mark.asyncio
    async def test_live_duplicate_still_renamed(
        self, handler: QuizifyWebSocketHandler, game: QuizifyGameState
    ) -> None:
        """A genuinely LIVE duplicate (old ws still open) must still get the
        "Name 2" suffix — the fix must not collapse two real players."""
        first_ws = _ws()
        game.add_player("Charlie", first_ws)
        assert game.get_player("Charlie").is_active

        second_ws = _ws()
        await handler._handle_join(second_ws, {"name": "Charlie"}, game)

        names = [p.name for p in game.get_players()]
        assert "Charlie" in names
        assert "Charlie 2" in names


# ---------------------------------------------------------------------------
# #450 — pre-submit STEAL delta must survive the thief's own submit
# ---------------------------------------------------------------------------


class TestStealRoundScoreAccumulates:
    def test_steal_before_submit_not_wiped_by_submit(
        self, game: QuizifyGameState
    ) -> None:
        thief_ws, victim_ws = _ws(), _ws()
        game.add_player("Thief", thief_ws)
        game.add_player("Victim", victim_ws)
        game.start_game(num_rounds=3, language="de")
        game.start_next_question()

        # Victim submits correctly → has a round_score worth stealing.
        game.submit_answer("Victim", _correct_index(game))
        victim = game.get_player("Victim")
        assert victim.round_score > 1

        # Thief steals from the (submitted) victim BEFORE answering.
        game._powerup_manager._inventory["Thief"] = PowerUpType.STEAL
        effect = game.use_powerup("Thief", "Victim")
        assert isinstance(effect, PowerUpEffect)
        thief = game.get_player("Thief")
        stolen = thief.round_score
        assert stolen > 0

        # Thief now answers correctly. round_score must ACCUMULATE the earned
        # points on top of the stolen delta — not overwrite it.
        result = game.submit_answer("Thief", _correct_index(game))
        points = result.points_earned

        assert thief.round_score == stolen + points
        # The actual bug symptom: a plain assignment left round_score == points.
        assert thief.round_score > points
        # round_score stays consistent with score (thief started at 0).
        assert thief.round_score == thief.score

    def test_no_steal_round_score_equals_points(
        self, game: QuizifyGameState
    ) -> None:
        """Sanity: with no pre-submit delta, ``+= points`` from a zeroed
        round_score is identical to the old behaviour."""
        a_ws = _ws()
        game.add_player("Solo", a_ws)
        game.start_game(num_rounds=3, language="de")
        game.start_next_question()

        result = game.submit_answer("Solo", _correct_index(game))
        solo = game.get_player("Solo")
        assert solo.round_score == result.points_earned


# ---------------------------------------------------------------------------
# #472 — a pre-submit STEAL by a player who then times out must be recorded
#        in round_scores history (gap left by #450)
# ---------------------------------------------------------------------------


class TestStealThenTimeoutRecordsHistory:
    def test_timeout_after_steal_records_stolen_round_score(
        self, game: QuizifyGameState
    ) -> None:
        """A thief who steals then never submits must have the stolen amount
        recorded in ``round_scores`` — the timeout branch used to append a
        literal 0 while the AnswerResult reported ``points_earned=round_score``,
        under-counting the Top-Score / history aggregation."""
        thief_ws, victim_ws = _ws(), _ws()
        game.add_player("Thief", thief_ws)
        game.add_player("Victim", victim_ws)
        game.start_game(num_rounds=3, language="de")
        game.start_next_question()

        # Victim submits correctly → has a round_score worth stealing.
        game.submit_answer("Victim", _correct_index(game))
        victim = game.get_player("Victim")
        assert victim.round_score > 1

        # Thief steals BEFORE answering, then never submits (timeout).
        game._powerup_manager._inventory["Thief"] = PowerUpType.STEAL
        effect = game.use_powerup("Thief", "Victim")
        assert isinstance(effect, PowerUpEffect)
        thief = game.get_player("Thief")
        stolen = thief.round_score
        assert stolen > 0
        assert not thief.submitted

        # Timer expires → round evaluated with the thief still unsubmitted.
        summary = game.evaluate_round()
        assert summary is not None

        # History must carry the stolen delta, not a literal 0.
        assert thief.round_scores[-1] == stolen
        # And it must agree with the reveal's AnswerResult (which reports
        # points_earned=round_score) for the same player.
        thief_result = next(r for r in summary.results if r.player_id == "Thief")
        assert thief_result.points_earned == stolen
        assert thief.round_scores[-1] == thief_result.points_earned

    def test_genuine_timeout_records_zero(self, game: QuizifyGameState) -> None:
        """Sanity: a plain timeout with no pre-submit delta still records 0,
        since reset_round zeroed round_score."""
        game.add_player("Idle", _ws())
        game.start_game(num_rounds=3, language="de")
        game.start_next_question()

        idle = game.get_player("Idle")
        assert not idle.submitted

        game.evaluate_round()
        assert idle.round_scores[-1] == 0


# ---------------------------------------------------------------------------
# #453 — roster broadcasts coalesced through the flush window
# ---------------------------------------------------------------------------


class TestRosterCoalescing:
    @pytest.mark.asyncio
    async def test_burst_of_joins_collapses_to_one_broadcast(
        self, handler: QuizifyWebSocketHandler, game: QuizifyGameState
    ) -> None:
        game.add_player("A", _ws())
        game.add_player("B", _ws())
        game.add_player("C", _ws())

        # Three roster changes inside one window.
        handler._mark_roster_dirty("player_joined")
        handler._mark_roster_dirty("player_joined")
        handler._mark_roster_dirty("player_joined")

        await _drain_roster(handler)

        # ONE broadcast, not three — carrying the full current roster.
        handler._conn.broadcast.assert_awaited_once()
        msg = handler._conn.broadcast.await_args.args[0]
        assert msg["type"] == "player_joined"
        assert {p["name"] for p in msg["players"]} == {"A", "B", "C"}

    @pytest.mark.asyncio
    async def test_mixed_window_types_by_last_event_list_authoritative(
        self, handler: QuizifyWebSocketHandler, game: QuizifyGameState
    ) -> None:
        """A join then a leave in one window collapse to a single frame typed
        by the LAST event, but the players list is always the live roster."""
        game.add_player("A", _ws())
        game.add_player("B", _ws())
        handler._mark_roster_dirty("player_joined")
        handler._mark_roster_dirty("player_left")

        await _drain_roster(handler)

        handler._conn.broadcast.assert_awaited_once()
        msg = handler._conn.broadcast.await_args.args[0]
        assert msg["type"] == "player_left"
        assert {p["name"] for p in msg["players"]} == {"A", "B"}

    @pytest.mark.asyncio
    async def test_raising_flush_is_logged_not_swallowed(
        self,
        handler: QuizifyWebSocketHandler,
        game: QuizifyGameState,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """#474: the roster-flush task carries the same _log_task_exception
        done-callback as every other fire-and-forget task here. If broadcast
        raises inside the flush (after _roster_dirty was cleared), the crash is
        surfaced in the log instead of only appearing at GC time."""
        game.add_player("A", _ws())
        handler._conn.broadcast = AsyncMock(side_effect=RuntimeError("boom"))

        handler._mark_roster_dirty("player_joined")
        task = handler._roster_flush_task
        assert task is not None
        # The done-callback must be attached (mirrors lightning/timer/pause).
        assert handler._log_task_exception in [
            cb for cb, _ctx in task._callbacks  # type: ignore[attr-defined]
        ]

        with caplog.at_level("ERROR"):
            with pytest.raises(RuntimeError):
                await asyncio.wait_for(asyncio.shield(task), timeout=2.0)
            # Let the done-callback run.
            await asyncio.sleep(0)

        assert any(
            "Unhandled exception in background task" in r.message
            for r in caplog.records
        )

    @pytest.mark.asyncio
    async def test_join_handler_defers_roster_broadcast(
        self, handler: QuizifyWebSocketHandler, game: QuizifyGameState
    ) -> None:
        """_handle_join no longer broadcasts the roster synchronously — the
        joining player still gets their game_state, the roster goes out once
        the window flushes."""
        await handler._handle_join(_ws(), {"name": "Zoe"}, game)
        # Nothing broadcast yet (still inside the window / task pending).
        handler._conn.broadcast.assert_not_awaited()

        await _drain_roster(handler)

        handler._conn.broadcast.assert_awaited_once()
        msg = handler._conn.broadcast.await_args.args[0]
        assert msg["type"] == "player_joined"
        assert any(p["name"] == "Zoe" for p in msg["players"])


# ---------------------------------------------------------------------------
# #455 — lightning recap memoized once finished
# ---------------------------------------------------------------------------


@pytest.fixture
def bank() -> QuestionBank:
    qb = QuestionBank()
    qb.load_all_categories()
    return qb


def _run_to_finish(lr: LightningRound) -> None:
    steps = 0
    while True:
        q = lr.current_question
        if q is not None:
            correct_orig = next(i for i, a in enumerate(q.answers) if a.correct)
            order = lr._shuffles["A"]
            lr.record_answer("A", order.index(correct_orig))
        if not lr.advance():
            break
        steps += 1
        assert steps < 50


class TestLightningRecapCache:
    def test_recap_memoized_after_finish(self, bank: QuestionBank) -> None:
        lr = LightningRound(bank, ["A"], language="de", num_questions=3)
        assert lr.start() is True
        _run_to_finish(lr)
        assert lr.finished is True

        first = lr.build_recap()
        second = lr.build_recap()
        # Same dict object handed back — no rebuild per join/reconnect.
        assert first is second

    def test_cached_recap_immune_to_later_mutation(
        self, bank: QuestionBank
    ) -> None:
        """Once cached, a stray score mutation can't leak into the payload —
        the recap is immutable post-finish by contract."""
        lr = LightningRound(bank, ["A"], language="de", num_questions=3)
        lr.start()
        _run_to_finish(lr)
        first = lr.build_recap()
        first_scores = {row["name"]: row["score"] for row in first["leaderboard"]}

        lr.scores["A"] = 9999  # would change leaderboard on a fresh build
        again = lr.build_recap()
        again_scores = {row["name"]: row["score"] for row in again["leaderboard"]}
        assert again_scores == first_scores

    def test_prefinish_recap_not_cached(self, bank: QuestionBank) -> None:
        """A build before the round finishes must NOT be frozen in — the
        cache only arms once ``finished`` is set."""
        lr = LightningRound(bank, ["A"], language="de", num_questions=3)
        lr.start()
        # Mid-round build (unusual, but must stay live).
        lr.build_recap()
        assert lr._recap_cache is None
        assert lr.finished is False

    def test_start_invalidates_stale_cache(self, bank: QuestionBank) -> None:
        """A reused instance's start() drops any prior recap cache."""
        lr = LightningRound(bank, ["A"], language="de", num_questions=3)
        lr.start()
        _run_to_finish(lr)
        lr.build_recap()
        assert lr._recap_cache is not None

        # Re-arm the same instance for a new round.
        assert lr.start() is True
        assert lr._recap_cache is None

    def test_snapshot_reuses_cached_recap(self, tmp_path: Path) -> None:
        """get_state_snapshot at LIGHTNING_RECAP reuses the cached recap dict
        rather than rebuilding it each call."""
        state = QuizifyGameState(runtime=_FakeRuntime(tmp_path), entry_id="test")
        state.add_player("A", _ws())
        state.start_game(num_rounds=3, language="de", lightning_enabled=True)
        # Drive a standalone lightning round to its recap.
        lr = LightningRound(state._question_bank, ["A"], language="de", num_questions=3)
        assert lr.start() is True
        _run_to_finish(lr)
        state._lightning = lr
        state.phase = GamePhase.LIGHTNING_RECAP

        snap1 = state.get_state_snapshot()
        snap2 = state.get_state_snapshot()
        assert snap1["lightning_recap"] is snap2["lightning_recap"]
