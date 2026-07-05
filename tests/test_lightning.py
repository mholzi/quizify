"""Tests for the Lightning Round mode (issue #42).

Covers the self-contained LightningRound engine (scoring, advance loop,
recap) and the QuizifyGameState hooks (phase transitions, snapshot,
power-up suppression). The fast async WS loop is exercised indirectly via
the engine's synchronous step methods, which is where all the rules live.
"""

from __future__ import annotations

import asyncio
import sys
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT))

from custom_components.quizify.game.lightning import (  # noqa: E402
    LIGHTNING_NUM_QUESTIONS,
    LIGHTNING_POINTS_PER_CORRECT,
    LIGHTNING_SECONDS_PER_QUESTION,
    LightningRound,
)
from custom_components.quizify.game.questions import QuestionBank  # noqa: E402
from custom_components.quizify.game.state import GamePhase, QuizifyGameState  # noqa: E402
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


def _fake_ws() -> MagicMock:
    ws = MagicMock()
    ws.closed = False
    return ws


@pytest.fixture
def bank() -> QuestionBank:
    qb = QuestionBank()
    qb.load_all_categories()
    return qb


@pytest.fixture
def state(tmp_path: Path) -> QuizifyGameState:
    return QuizifyGameState(runtime=_FakeRuntime(tmp_path), entry_id="test")


def _correct_shuffled_index(lr: LightningRound, name: str) -> int:
    """Return the player's shuffled button index for the correct answer."""
    q = lr.current_question
    assert q is not None
    correct_orig = next(i for i, a in enumerate(q.answers) if a.correct)
    order = lr._shuffles[name]
    return order.index(correct_orig)


def _wrong_shuffled_index(lr: LightningRound, name: str) -> int:
    q = lr.current_question
    assert q is not None
    wrong_orig = next(i for i, a in enumerate(q.answers) if not a.correct)
    order = lr._shuffles[name]
    return order.index(wrong_orig)


# ---------- Engine: defaults / rules ----------


class TestLightningRules:
    def test_defaults_match_decided_spec(self) -> None:
        assert LIGHTNING_NUM_QUESTIONS == 5
        assert LIGHTNING_SECONDS_PER_QUESTION == 15.0
        assert LIGHTNING_POINTS_PER_CORRECT == 10

    def test_start_builds_queue_and_arms_first(self, bank: QuestionBank) -> None:
        lr = LightningRound(bank, ["A", "B"], language="de")
        assert lr.start() is True
        assert lr.active is True
        assert lr.index == 0
        assert lr.current_question is not None
        assert lr.num_questions <= LIGHTNING_NUM_QUESTIONS

    def test_per_player_shuffles_are_independent(self, bank: QuestionBank) -> None:
        # With 2 players over the same question, shuffles are stored per name.
        lr = LightningRound(bank, ["A", "B"], language="de")
        lr.start()
        assert "A" in lr._shuffles and "B" in lr._shuffles


# ---------- Engine: scoring (flat, no bonus) ----------


class TestLightningScoring:
    def test_correct_answer_awards_flat_points(self, bank: QuestionBank) -> None:
        lr = LightningRound(bank, ["A"], language="de")
        lr.start()
        idx = _correct_shuffled_index(lr, "A")
        assert lr.record_answer("A", idx) is True
        lr.advance()
        assert lr.scores["A"] == LIGHTNING_POINTS_PER_CORRECT

    def test_wrong_answer_awards_nothing(self, bank: QuestionBank) -> None:
        lr = LightningRound(bank, ["A"], language="de")
        lr.start()
        idx = _wrong_shuffled_index(lr, "A")
        assert lr.record_answer("A", idx) is False
        lr.advance()
        assert lr.scores["A"] == 0

    def test_no_speed_bonus_two_correct_score_equal(self, bank: QuestionBank) -> None:
        lr = LightningRound(bank, ["fast", "slow"], language="de")
        lr.start()
        lr.record_answer("fast", _correct_shuffled_index(lr, "fast"))
        lr.record_answer("slow", _correct_shuffled_index(lr, "slow"))
        lr.advance()
        assert lr.scores["fast"] == lr.scores["slow"] == LIGHTNING_POINTS_PER_CORRECT

    def test_double_answer_rejected(self, bank: QuestionBank) -> None:
        lr = LightningRound(bank, ["A"], language="de")
        lr.start()
        idx = _correct_shuffled_index(lr, "A")
        assert lr.record_answer("A", idx) is True
        assert lr.record_answer("A", idx) is None  # second is rejected

    def test_out_of_range_index_rejected(self, bank: QuestionBank) -> None:
        lr = LightningRound(bank, ["A"], language="de")
        lr.start()
        assert lr.record_answer("A", 99) is None


# ---------- Engine: advance / all-answered / recap ----------


class TestLightningFlow:
    def test_runs_full_loop_to_finish(self, bank: QuestionBank) -> None:
        lr = LightningRound(bank, ["A"], language="de", num_questions=5)
        lr.start()
        steps = 0
        while True:
            lr.record_answer("A", _correct_shuffled_index(lr, "A"))
            if not lr.advance():
                break
            steps += 1
            assert steps < 50  # guard against infinite loop
        assert lr.finished is True
        assert lr.active is False
        assert lr.scores["A"] == 5 * LIGHTNING_POINTS_PER_CORRECT

    def test_all_connected_answered_short_circuits(self, bank: QuestionBank) -> None:
        lr = LightningRound(bank, ["A", "B"], language="de")
        lr.start()
        assert lr.all_connected_answered(["A", "B"]) is False
        lr.record_answer("A", _correct_shuffled_index(lr, "A"))
        assert lr.all_connected_answered(["A", "B"]) is False
        lr.record_answer("B", _correct_shuffled_index(lr, "B"))
        assert lr.all_connected_answered(["A", "B"]) is True

    def test_miss_recorded_when_no_answer(self, bank: QuestionBank) -> None:
        lr = LightningRound(bank, ["A"], language="de", num_questions=2)
        lr.start()
        lr.advance()  # no answer to Q1
        recap = lr.build_recap()
        assert recap["questions"][0]["results"]["A"] == "miss"
        assert lr.scores["A"] == 0

    def test_recap_has_per_question_grid(self, bank: QuestionBank) -> None:
        lr = LightningRound(bank, ["A", "B"], language="de", num_questions=3)
        lr.start()
        # A answers everything right, B everything wrong.
        for _ in range(3):
            lr.record_answer("A", _correct_shuffled_index(lr, "A"))
            lr.record_answer("B", _wrong_shuffled_index(lr, "B"))
            lr.advance()
        recap = lr.build_recap()
        assert recap["num_questions"] == 3
        assert len(recap["questions"]) == 3
        for row in recap["questions"]:
            assert row["results"]["A"] == "correct"
            assert row["results"]["B"] == "wrong"
            assert row["correct_answer"]
            # The recap surfaces B's wrong pick (for "You said X"), and never
            # the correct answer as a wrong pick.
            assert "A" not in row["chosen"]  # correct answerer has no wrong pick
            assert row["chosen"].get("B")
            assert row["chosen"]["B"] != row["correct_answer"]
        # Leaderboard sorted, A first.
        lb = recap["leaderboard"]
        assert lb[0]["name"] == "A"
        assert lb[0]["score"] == 3 * LIGHTNING_POINTS_PER_CORRECT
        assert lb[1]["score"] == 0

    def test_late_joiner_can_score(self, bank: QuestionBank) -> None:
        lr = LightningRound(bank, ["A"], language="de", num_questions=2)
        lr.start()
        lr.advance()  # Q1 over
        lr.add_player("Late")
        lr.record_answer("Late", _correct_shuffled_index(lr, "Late"))
        lr.advance()
        assert lr.scores["Late"] == LIGHTNING_POINTS_PER_CORRECT


# ---------- GameState hooks ----------


class TestGameStateLightning:
    def test_start_from_lobby(self, state: QuizifyGameState) -> None:
        state.add_player("A", _fake_ws())
        assert state.start_lightning_round() is True
        assert state.phase == GamePhase.LIGHTNING
        assert state.lightning is not None

    def test_start_from_finale(self, state: QuizifyGameState) -> None:
        state.add_player("A", _fake_ws())
        state.start_game(num_rounds=1, language="de")
        state.end_game()
        assert state.phase == GamePhase.FINALE
        assert state.start_lightning_round() is True
        assert state.phase == GamePhase.LIGHTNING

    def test_cannot_start_mid_question(self, state: QuizifyGameState) -> None:
        state.add_player("A", _fake_ws())
        state.start_game(num_rounds=3, language="de")
        state.start_next_question()
        assert state.phase == GamePhase.QUESTION_ACTIVE
        assert state.start_lightning_round() is False

    def test_finish_transitions_to_recap(self, state: QuizifyGameState) -> None:
        state.add_player("A", _fake_ws())
        state.start_lightning_round()
        state.finish_lightning_round()
        assert state.phase == GamePhase.LIGHTNING_RECAP

    def test_start_again_from_recap(self, state: QuizifyGameState) -> None:
        # Regression for #294: the recap "play again" button sends
        # start_lightning while the phase is LIGHTNING_RECAP. Previously the
        # guard only allowed LOBBY/FINALE, so the host got an error toast and
        # the button was a dead-end. From RECAP it must start a fresh round.
        state.add_player("A", _fake_ws())
        state.start_lightning_round()
        first_round = state.lightning
        state.finish_lightning_round()
        assert state.phase == GamePhase.LIGHTNING_RECAP

        assert state.start_lightning_round() is True
        assert state.phase == GamePhase.LIGHTNING
        assert state.lightning is not None
        # A genuinely fresh round, with the intro splash re-armed (#201).
        assert state.lightning is not first_round
        assert state.lightning_splash_pending is True

    def test_snapshot_exposes_lightning(self, state: QuizifyGameState) -> None:
        state.add_player("A", _fake_ws())
        state.start_lightning_round()
        snap = state.get_state_snapshot()
        assert snap["phase"] == "LIGHTNING"
        assert "lightning" in snap
        assert "question" in snap["lightning"]
        assert snap["lightning"]["num_questions"] >= 1

    def test_splash_pending_after_start(self, state: QuizifyGameState) -> None:
        # The intro splash (#201) is up between start and the admin's Start.
        state.add_player("A", _fake_ws())
        state.start_lightning_round()
        assert state.lightning_splash_pending is True
        snap = state.get_state_snapshot()
        assert snap["lightning"]["splash_pending"] is True

    def test_begin_questions_clears_splash(self, state: QuizifyGameState) -> None:
        state.add_player("A", _fake_ws())
        state.start_lightning_round()
        assert state.begin_lightning_questions() is True
        assert state.lightning_splash_pending is False
        snap = state.get_state_snapshot()
        assert snap["lightning"]["splash_pending"] is False

    def test_begin_questions_idempotent(self, state: QuizifyGameState) -> None:
        state.add_player("A", _fake_ws())
        state.start_lightning_round()
        assert state.begin_lightning_questions() is True
        # Second call is a no-op (splash already dismissed).
        assert state.begin_lightning_questions() is False

    def test_begin_questions_noop_without_round(
        self, state: QuizifyGameState
    ) -> None:
        # No active lightning round → nothing to dismiss.
        assert state.begin_lightning_questions() is False

    def test_snapshot_exposes_recap(self, state: QuizifyGameState) -> None:
        state.add_player("A", _fake_ws())
        state.start_lightning_round()
        state.finish_lightning_round()
        snap = state.get_state_snapshot()
        assert snap["phase"] == "LIGHTNING_RECAP"
        assert "lightning_recap" in snap

    def test_reset_clears_lightning(self, state: QuizifyGameState) -> None:
        state.add_player("A", _fake_ws())
        state.start_lightning_round()
        state.reset_to_lobby()
        assert state.lightning is None
        assert state.phase == GamePhase.LOBBY

    def test_powerups_not_assigned_in_lightning(self, state: QuizifyGameState) -> None:
        # Power-ups are assigned in the normal start_next_question path only;
        # lightning never calls it. Assert no power-up is held after start.
        state.add_player("A", _fake_ws())
        state.start_lightning_round()
        assert state.get_player_powerup("A") is None


# ---------- WS handler: end-to-end fast loop ----------


def _handler(state: QuizifyGameState) -> QuizifyWebSocketHandler:
    runtime = _FakeRuntime(state._runtime.data_dir)  # type: ignore[attr-defined]
    h = QuizifyWebSocketHandler(runtime=runtime, game_state_provider=lambda: state)
    h._conn = ConnectionManager(runtime, lambda: state)
    h._conn.broadcast = AsyncMock()
    h._conn.broadcast_to_admins_and_dashboards = AsyncMock()
    h._conn.send = AsyncMock()
    return h


class TestLightningWsLoop:
    @pytest.mark.asyncio
    async def test_full_loop_advances_and_reaches_recap(
        self, state: QuizifyGameState
    ) -> None:
        """Drive the actual WS fast loop with a short window so a 2-question
        round runs to the recap without us answering. Verifies: auto-advance
        on timeout, no reveal between, and a final lightning_recap broadcast."""
        state.add_player("A", _fake_ws())
        h = _handler(state)
        assert state.start_lightning_round() is True
        # Make the loop snappy: 2 questions, ~0.15s each.
        state.lightning._questions = state.lightning._questions[:2]
        state.lightning.num_questions = 2
        state.lightning.seconds_per_question = 0.15

        h._start_lightning_loop(state)
        # Wait for the loop to finish (grace 1.0s + 2 × ~0.15s + margin).
        for _ in range(40):
            await asyncio.sleep(0.1)
            if state.phase == GamePhase.LIGHTNING_RECAP:
                break
        h._cancel_lightning_loop()

        assert state.phase == GamePhase.LIGHTNING_RECAP
        # A lightning_recap message must have been broadcast.
        sent_types = [
            c.args[0].get("type")
            for c in h._conn.broadcast.call_args_list
            if c.args and isinstance(c.args[0], dict)
        ]
        assert "lightning_recap" in sent_types

    @pytest.mark.asyncio
    async def test_all_answered_short_circuits_window(
        self, state: QuizifyGameState
    ) -> None:
        """If the single connected player answers, the question advances
        before its (long) window elapses."""
        state.add_player("A", _fake_ws())
        h = _handler(state)
        state.start_lightning_round()
        lr = state.lightning
        lr._questions = lr._questions[:1]
        lr.num_questions = 1
        lr.seconds_per_question = 30.0  # long — only all-answered ends it

        h._start_lightning_loop(state)
        await asyncio.sleep(1.2)  # past the grace, question is live
        # Answer correctly via the handler path.
        idx = _correct_shuffled_index(lr, "A")
        await h._handle_lightning_answer(
            state.get_player("A").ws, {"answer_index": idx}, state
        )
        # Tell the handler "A" is the only connected player by patching
        # get_players to return the connected stub (already connected).
        for _ in range(20):
            await asyncio.sleep(0.1)
            if state.phase == GamePhase.LIGHTNING_RECAP:
                break
        h._cancel_lightning_loop()

        assert state.phase == GamePhase.LIGHTNING_RECAP
        assert lr.scores["A"] == LIGHTNING_POINTS_PER_CORRECT

    @pytest.mark.asyncio
    async def test_stale_index_answer_rejected_after_advance(
        self, state: QuizifyGameState
    ) -> None:
        """#405: a lightning tap stamped with the previous question's index —
        landing after the loop's advance() armed the next Q with a fresh clock
        and new shuffles — is dropped, so it cannot occupy the player's single
        answer slot on the new question. The real answer to the new question,
        correctly stamped, is then still accepted and scored."""
        state.add_player("A", _fake_ws())
        h = _handler(state)
        assert state.start_lightning_round() is True
        lr = state.lightning
        ws = state.get_player("A").ws

        # Player is on Q0 and holds that index; capture a pick for it.
        stale_index = lr.index  # 0
        stale_pick = _correct_shuffled_index(lr, "A")

        # Timeout advance: Q1 is armed with a fresh (unexpired) clock + shuffles.
        assert lr.advance() is True
        assert lr.index == stale_index + 1
        assert lr.time_remaining() > 0  # new window is genuinely open

        # The stale tap (still stamped with the OLD index) must be dropped and
        # must NOT be recorded against the new question.
        await h._handle_lightning_answer(
            ws, {"answer_index": stale_pick, "index": stale_index}, state
        )
        assert "A" not in lr._answers.get(lr.index, {})

        # A's real answer to the new question (correctly stamped) is accepted.
        real_pick = _correct_shuffled_index(lr, "A")
        await h._handle_lightning_answer(
            ws, {"answer_index": real_pick, "index": lr.index}, state
        )
        assert "A" in lr._answers[lr.index]
        lr.advance()  # score the new question
        assert lr.scores["A"] == LIGHTNING_POINTS_PER_CORRECT

    @pytest.mark.asyncio
    async def test_indexless_legacy_answer_gated_by_window(
        self, state: QuizifyGameState
    ) -> None:
        """#405 backward compat: an index-less tap (older client) is accepted
        while the current window is open, but dropped once it has expired — the
        exact spot where a stale index-less tap would otherwise land wrong."""
        state.add_player("A", _fake_ws())
        h = _handler(state)
        state.start_lightning_round()
        lr = state.lightning
        ws = state.get_player("A").ws

        # Window open → index-less tap is accepted (legacy path preserved).
        await h._handle_lightning_answer(
            ws, {"answer_index": _correct_shuffled_index(lr, "A")}, state
        )
        assert "A" in lr._answers[lr.index]

        # New question, then force its window closed → index-less tap dropped.
        assert lr.advance() is True
        lr._question_start = time.monotonic() - lr.seconds_per_question - 1.0
        await h._handle_lightning_answer(
            ws, {"answer_index": _correct_shuffled_index(lr, "A")}, state
        )
        assert "A" not in lr._answers.get(lr.index, {})


# ---------- WS handler: #285 auto-trigger entry + resume ----------


class TestLightningAutoEntry:
    @pytest.mark.asyncio
    async def test_start_next_question_detours_into_auto_lightning(
        self, state: QuizifyGameState
    ) -> None:
        """When the game is about to enter the pre-picked target round, the
        normal advance path detours into the auto Lightning Round instead of a
        normal question."""
        state.add_player("A", _fake_ws())
        h = _handler(state)
        state.start_game(num_rounds=10, lightning_seed=42)
        target = state.lightning_target_round
        assert target is not None
        # Position the game just before the target round.
        state.round = target - 1
        state.phase = GamePhase.ANSWER_REVEAL
        assert state.should_trigger_lightning() is True

        # The advance detours; phase becomes LIGHTNING (splash up).
        await h._start_next_question(state)
        assert state.phase == GamePhase.LIGHTNING
        assert state.lightning is not None
        assert state.in_lightning_detour is True
        h._cancel_lightning_loop()

    @pytest.mark.asyncio
    async def test_recap_advance_resumes_main_game(
        self, state: QuizifyGameState
    ) -> None:
        """The host's normal next_question after the mid-game lightning recap
        resumes the paused main game at the originally-scheduled round."""
        state.add_player("A", _fake_ws())
        h = _handler(state)
        state.start_game(num_rounds=10, lightning_seed=42)
        target = state.lightning_target_round
        assert target is not None
        resume_round = target - 1
        state.round = resume_round
        state.phase = GamePhase.ANSWER_REVEAL
        assert state.start_lightning_round(auto=True) is True
        state.finish_lightning_round()
        assert state.phase == GamePhase.LIGHTNING_RECAP

        # next_question from the recap → resume + start the target round.
        await h._handle_next_question(state.get_player("A").ws, state)
        assert state.phase == GamePhase.QUESTION_ACTIVE
        assert state.round == target  # the originally-scheduled round now ran
        h._cancel_timer_tick()


# ---------- Shared-queue preservation + auto difficulty (#350) ----------


class TestLightningSharedQueue350:
    """Regression for #350 (P0).

    The lightning round used to ``reset()`` and consume the *shared* game
    queue. That (a) filtered on ``difficulty="auto"`` — a mode, not a
    per-question tag — yielding an EMPTY queue that ended auto-difficulty
    games right at the lightning round, and (b) destroyed the main game's
    queue position/ordering. The round now builds a private pool and never
    disturbs the bank's ``_queue``/``_queue_index``.
    """

    def test_start_with_auto_difficulty_finds_questions(
        self, bank: QuestionBank
    ) -> None:
        # difficulty="auto" must span all difficulties, not filter on a tag
        # no question carries (which used to empty the queue).
        lr = LightningRound(bank, ["A", "B"], language="de", difficulty="auto")
        assert lr.start() is True
        assert lr.num_questions > 0
        assert lr.current_question is not None

    def test_start_preserves_main_game_queue(self, bank: QuestionBank) -> None:
        # Simulate a main game mid-flight: build the queue and serve 2 rounds.
        bank.reset(language="de")
        served_ids = set()
        for _ in range(2):
            q = bank.get_next_question()
            assert q is not None
            bank.record_shown(q.id)
            served_ids.add(q.id)
        index_before = bank._queue_index
        assert index_before == 2

        lr = LightningRound(bank, ["A"], language="de")
        assert lr.start() is True
        lightning_ids = {q.id for q in lr._questions}

        # The already-served prefix (and thus the index) is untouched.
        assert bank._queue_index == index_before
        assert {q.id for q in bank._queue[:index_before]} == served_ids
        # Lightning never re-shows a question the main game already served…
        assert lightning_ids.isdisjoint(served_ids)
        # …and its picks are claimed out of the pending queue so the resumed
        # main game can't show them either.
        assert lightning_ids.isdisjoint(bank.remaining_queue_ids())
        # The main game still has fresh questions to serve.
        nxt = bank.get_next_question()
        assert nxt is not None
        assert nxt.id not in lightning_ids

    @pytest.mark.asyncio
    async def test_auto_lightning_does_not_end_auto_difficulty_game(
        self, state: QuizifyGameState
    ) -> None:
        # Full path: an auto-difficulty game whose auto lightning fires must
        # enter the lightning phase with real questions — not fall through the
        # "no questions" fallback that used to end the game.
        state.add_player("A", _fake_ws())
        h = _handler(state)
        state.start_game(num_rounds=10, difficulty="auto", lightning_seed=42)
        assert state.difficulty == "auto"
        target = state.lightning_target_round
        assert target is not None

        state.round = target - 1
        state.phase = GamePhase.ANSWER_REVEAL
        await h._start_auto_lightning(state)
        h._cancel_lightning_loop()

        assert state.phase == GamePhase.LIGHTNING
        assert state.lightning is not None
        assert state.lightning.num_questions > 0
        # The main game's queue survived the detour and can still serve.
        lightning_ids = {q.id for q in state.lightning._questions}
        assert lightning_ids.isdisjoint(state._question_bank.remaining_queue_ids())
        assert state._question_bank.get_next_question() is not None
