"""Regression tests for #353: final-round wager on an estimate question.

An estimate final scores through ``submit_guess`` / ``_evaluate_estimate_round``,
which never read ``player.wager`` — only the MC path's ``ScoringEngine`` resolves
wagers. Before the fix ``_handle_submit_wager`` ACKed the wager anyway, so the
player got a confirmed ``wager_accepted`` for a bet that had NO scoring effect,
and ``serialize_question_for_player`` still advertised the wager UI
(``is_final_round=True``) on estimate finals.

The fix:
- ``_handle_submit_wager`` rejects the wager with an error (no ``wager_accepted``,
  no stored ``player.wager``) when the current question is an estimate.
- ``serialize_question_for_player`` suppresses ``is_final_round`` for estimate
  questions so a compliant client never offers the wager affordance.
- MC-final wagers keep working exactly as before.
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from custom_components.quizify.game.questions import (  # noqa: E402
    QUESTION_TYPE_ESTIMATE,
    Answer,
    Question,
)
from custom_components.quizify.game.state import (  # noqa: E402
    GamePhase,
    QuizifyGameState,
)
from custom_components.quizify.server.serializers import (  # noqa: E402
    serialize_question_for_player,
)


class _FakeRuntime:
    def __init__(self, tmp_path: Path) -> None:
        self.data_dir = tmp_path


def _fake_ws() -> MagicMock:
    ws = MagicMock()
    ws.closed = False
    return ws


def _estimate_question() -> Question:
    return Question(
        id="e-final",
        question="How many bones?",
        answers=[],
        type=QUESTION_TYPE_ESTIMATE,
        estimate_answer=206,
        estimate_min=0,
        estimate_max=500,
        estimate_unit="bones",
        estimate_step=1,
    )


def _mc_question() -> Question:
    return Question(
        id="m-final",
        question="What is 1+1?",
        answers=[
            Answer(text="2", correct=True),
            Answer(text="3", correct=False),
            Answer(text="4", correct=False),
        ],
    )


class _Conn:
    """Stub connection capturing sends + errors from the wager handler."""

    def __init__(self) -> None:
        self.sent: list[dict] = []
        self.errors: list[tuple] = []
        self.to_hosts: list[dict] = []

    async def send(self, ws, message):  # noqa: ANN001
        self.sent.append(message)

    async def send_error(self, ws, code, msg):  # noqa: ANN001
        self.errors.append((code, msg))

    async def broadcast_to_admins_and_dashboards(  # noqa: ANN001
        self, message, dashboard_message=None
    ):
        # #656: an accepted wager updates the host/TV lock-in tally.
        self.to_hosts.append(message)


def _final_round_state(
    tmp_path: Path, question: Question
) -> QuizifyGameState:
    """A game forced onto its (single) final round with ``question`` active."""
    state = QuizifyGameState(runtime=_FakeRuntime(tmp_path), entry_id="test")
    state.add_player("Anna", _fake_ws())
    state.add_player("Tom", _fake_ws())
    state.start_game(language="de", num_rounds=1, timer_duration=30)
    state.start_next_question()
    state._current_question = question
    # #656: put the state in the phase the real flow would reach for THIS
    # question. start_next_question ran against the pack's own (MC) question
    # and opened a betting window; an estimate final never opens one, so arm
    # the round straight away when we swap an estimate question in.
    if question.is_estimate:
        state.arm_round_timers()
    for p in state._player_registry.players.values():
        p.reset_round()
    return state


def _make_handler():
    from custom_components.quizify.server.websocket import (  # noqa: PLC0415
        QuizifyWebSocketHandler,
    )

    from custom_components.quizify.server.round_message_builder import (  # noqa: PLC0415
        RoundMessageBuilder,
    )

    handler = QuizifyWebSocketHandler.__new__(QuizifyWebSocketHandler)
    conn = _Conn()
    handler._conn = conn
    # #656: accepting a wager now also builds the host/TV progress payload.
    handler._round_messages = RoundMessageBuilder()
    return handler, conn


# ---------------------------------------------------------------------------
# Handler: estimate final rejects, MC final still works
# ---------------------------------------------------------------------------


class TestWagerHandler:
    @pytest.mark.asyncio
    async def test_estimate_final_wager_rejected(self, tmp_path: Path) -> None:
        """A wager on an estimate final is rejected: an error is returned,
        no ``wager_accepted`` is sent, and no wager is stored on the player."""
        from custom_components.quizify.server.websocket import (  # noqa: PLC0415
            ERR_INVALID_ACTION,
        )

        state = _final_round_state(tmp_path, _estimate_question())
        assert state.phase is GamePhase.QUESTION_ACTIVE
        assert state.round == state.total_rounds

        handler, conn = _make_handler()
        ws = state.get_player("Anna").ws
        await handler._handle_submit_wager(ws, {"wager": 50}, state)

        # Rejected with an error, never ACKed.
        assert len(conn.errors) == 1
        assert conn.errors[0][0] == ERR_INVALID_ACTION
        assert all(m.get("type") != "wager_accepted" for m in conn.sent)
        # The bet was NOT stored → no scoring effect.
        assert state.get_player("Anna").wager in (None, 0)

    @pytest.mark.asyncio
    async def test_estimate_wager_has_no_scoring_effect(
        self, tmp_path: Path
    ) -> None:
        """After the rejected wager, the estimate round scores purely by
        closeness — the (dropped) wager changes nothing."""
        state = _final_round_state(tmp_path, _estimate_question())
        handler, _conn = _make_handler()
        anna = state.get_player("Anna")

        # Anna tries to wager, then everyone guesses.
        await handler._handle_submit_wager(anna.ws, {"wager": 100}, state)
        assert anna.wager in (None, 0)

        state.submit_guess("Anna", 210)   # 4 off → closest
        state.submit_guess("Tom", 150)    # 56 off
        # All submitted → auto-evaluated into the reveal via the estimate path.
        assert state.phase is GamePhase.ANSWER_REVEAL
        est = state.get_round_summary().estimate
        assert est["winner"] == "Anna"
        assert anna.round_score > 0
        # Wager never re-appeared / was never applied.
        assert anna.wager in (None, 0)

    @pytest.mark.asyncio
    async def test_mc_final_wager_still_accepted(self, tmp_path: Path) -> None:
        """An MC final wager keeps working exactly as before: ACKed with
        ``wager_accepted`` and stored on the player."""
        state = _final_round_state(tmp_path, _mc_question())
        # #656: an MC final sits in the betting window — that is where a
        # wager is now accepted, and the only place it is.
        assert state.phase is GamePhase.WAGER_ACTIVE
        assert state.round == state.total_rounds

        handler, conn = _make_handler()
        anna = state.get_player("Anna")
        await handler._handle_submit_wager(anna.ws, {"wager": 40}, state)

        assert conn.errors == []
        accepted = [m for m in conn.sent if m.get("type") == "wager_accepted"]
        assert len(accepted) == 1
        assert accepted[0]["wager"] == 40
        assert anna.wager == 40


# ---------------------------------------------------------------------------
# Serializer: wager UI withheld on estimate finals, kept for MC finals
# ---------------------------------------------------------------------------


class TestFinalRoundSerialization:
    def test_estimate_final_hides_wager_ui(self) -> None:
        """``is_final_round`` is forced False for an estimate question so the
        client never renders the (unresolvable) wager UI — even when the caller
        passes ``is_final_round=True``."""
        payload = serialize_question_for_player(
            question=_estimate_question(),
            shuffled_answers=[],
            round_num=1,
            total_rounds=1,
            timer_duration=30,
            is_final_round=True,
            player_score=90,
        )
        assert payload["is_final_round"] is False
        assert payload["question_type"] == "estimate"

    def test_mc_final_advertises_wager_ui(self) -> None:
        """An MC final still carries ``is_final_round=True`` — no regression."""
        payload = serialize_question_for_player(
            question=_mc_question(),
            shuffled_answers=["2", "3", "4"],
            round_num=1,
            total_rounds=1,
            timer_duration=30,
            is_final_round=True,
            player_score=90,
        )
        assert payload["is_final_round"] is True
        assert payload["question_type"] == "multiple_choice"
