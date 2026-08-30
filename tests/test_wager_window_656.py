"""Regression tests for #656: the final round's betting window.

Reported by an external player: "the timer to answer the question is already
running while you're still thinking about how much you want to wager."

Two things were wrong, and only one of them was in the report:

1. ``question_started`` carried the question text AND ``timer_duration`` in one
   message, and the phone built the wager panel out of that same message — so
   the answer clock drained while the table argued about the stake.
2. The question was readable while betting. A player who knew the answer could
   stake everything at no risk, which is not a bet.

The fix gives betting its own phase, WAGER_ACTIVE: category only, no answer
timer, closing when every connected player has locked in or the window's own
deadline passes. Only then is the question sent and the round clock armed.
"""

from __future__ import annotations

import asyncio
import re
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
from custom_components.quizify.server.round_message_builder import (  # noqa: E402
    RoundMessageBuilder,
)
from custom_components.quizify.server.serializers import (  # noqa: E402
    serialize_wager_window,
)

_WWW = _REPO_ROOT / "custom_components" / "quizify" / "www"


class _FakeRuntime:
    def __init__(self, tmp_path: Path) -> None:
        self.data_dir = tmp_path


def _fake_ws() -> MagicMock:
    ws = MagicMock()
    ws.closed = False
    return ws


def _mc_question() -> Question:
    return Question(
        id="mc-final",
        question="Which planet is closest to the sun?",
        answers=[
            Answer(text="Mercury", correct=True),
            Answer(text="Venus", correct=False),
            Answer(text="Mars", correct=False),
        ],
        category="Science",
        difficulty="easy",
    )


def _estimate_question() -> Question:
    return Question(
        id="est-final",
        question="How many bones in an adult body?",
        answers=[],
        type=QUESTION_TYPE_ESTIMATE,
        estimate_answer=206,
        estimate_min=0,
        estimate_max=500,
        estimate_unit="bones",
        estimate_step=1,
    )


def _game(tmp_path: Path, *, rounds: int, players: tuple[str, ...] = ("Anna", "Tom")):
    state = QuizifyGameState(runtime=_FakeRuntime(tmp_path), entry_id="t")
    for name in players:
        state.add_player(name, _fake_ws())
    state.start_game(language="de", num_rounds=rounds, timer_duration=30)
    return state


class _Conn:
    """Stub connection recording what went to phones vs. to host/TV."""

    def __init__(self) -> None:
        self.sent: list[dict] = []
        self.errors: list[tuple] = []
        self.to_hosts: list[dict] = []
        self.broadcasts: list[dict] = []

    async def send(self, ws, message):  # noqa: ANN001
        self.sent.append(message)

    async def send_error(self, ws, code, msg):  # noqa: ANN001
        self.errors.append((code, msg))

    async def broadcast_to_admins_and_dashboards(  # noqa: ANN001
        self, message, dashboard_message=None
    ):
        self.to_hosts.append(message)

    async def broadcast(self, message):  # noqa: ANN001
        self.broadcasts.append(message)


def _handler():
    from custom_components.quizify.server.websocket import (  # noqa: PLC0415
        QuizifyWebSocketHandler,
    )

    handler = QuizifyWebSocketHandler.__new__(QuizifyWebSocketHandler)
    conn = _Conn()
    handler._conn = conn
    handler._round_messages = RoundMessageBuilder()
    handler._wager_window_task = None
    handler._timer_tick_task = None
    # Optional collaborators the question fan-out consults; None = not wired,
    # which is what a standalone dev server looks like.
    handler._tts_announcer = None
    handler._event_emitter = None
    return handler, conn


# ---------------------------------------------------------------------------
# The phase itself
# ---------------------------------------------------------------------------


class TestWindowOpens:
    def test_mc_final_parks_in_the_window_with_no_clock(self, tmp_path: Path) -> None:
        """The heart of #656: on the final round nothing is counting down.

        Not "a shorter timer" or "a paused timer" — no timer object exists and
        no round start has been stamped, so there is no clock that could be
        draining while the table decides.
        """
        state = _game(tmp_path, rounds=1)
        state.start_next_question()

        assert state.phase is GamePhase.WAGER_ACTIVE
        assert state._phase_controller.timers == {}
        assert state._phase_controller.round_start_time is None
        # The wall-clock fallback must not be able to evaluate a round that
        # has not started.
        assert state._phase_controller.round_wall_clock_expired() is False
        assert state.wager_window_remaining() > 0

    def test_non_final_round_is_untouched(self, tmp_path: Path) -> None:
        state = _game(tmp_path, rounds=3)
        state.start_next_question()

        assert state.phase is GamePhase.QUESTION_ACTIVE
        assert set(state._phase_controller.timers) == {"Anna", "Tom"}

    def test_estimate_final_skips_the_window(self, tmp_path: Path) -> None:
        """An estimate final scores without ever reading ``player.wager``
        (#353), so opening a window there would collect bets nothing settles."""
        state = _game(tmp_path, rounds=1)
        state.start_next_question()
        state._current_question = _estimate_question()

        assert state._needs_wager_window(_estimate_question()) is False
        assert state._needs_wager_window(_mc_question()) is True


class TestWindowCloses:
    def test_arming_starts_the_round(self, tmp_path: Path) -> None:
        state = _game(tmp_path, rounds=1)
        state.start_next_question()

        assert state.arm_round_timers() is True
        assert state.phase is GamePhase.QUESTION_ACTIVE
        assert set(state._phase_controller.timers) == {"Anna", "Tom"}
        assert state._phase_controller.round_start_time is not None

    def test_second_arm_is_a_no_op(self, tmp_path: Path) -> None:
        """The window has two closing triggers — the last bet arriving and the
        deadline elapsing — and they can land in either order. The loser of
        that race must do nothing, not restart the round with fresh timers."""
        state = _game(tmp_path, rounds=1)
        state.start_next_question()
        assert state.arm_round_timers() is True
        first_start = state._phase_controller.round_start_time

        assert state.arm_round_timers() is False
        assert state._phase_controller.round_start_time == first_start

    def test_missing_wagers_drive_the_early_close(self, tmp_path: Path) -> None:
        state = _game(tmp_path, rounds=1)
        state.start_next_question()

        assert sorted(state.players_missing_wager()) == ["Anna", "Tom"]
        state.get_player("Anna").wager = 50
        assert state.players_missing_wager() == ["Tom"]
        state.get_player("Tom").wager = 0
        assert state.players_missing_wager() == []

    def test_a_disconnected_player_cannot_hold_the_window(
        self, tmp_path: Path
    ) -> None:
        """Waiting on a phone that has left the room would strand everyone
        else until the deadline."""
        state = _game(tmp_path, rounds=1)
        state.start_next_question()
        state.get_player("Tom").connected = False
        state.get_player("Anna").wager = 25

        assert state.players_missing_wager() == []


# ---------------------------------------------------------------------------
# What the window is allowed to say
# ---------------------------------------------------------------------------


class TestPayloadWithholdsTheQuestion:
    def test_wager_window_payload_carries_no_question(self) -> None:
        payload = serialize_wager_window(
            question=_mc_question(),
            round_num=5,
            total_rounds=5,
            window_duration=20,
            player_score=92,
        )

        assert payload["type"] == "wager_window"
        assert payload["category"] == "Science"
        assert payload["player_score"] == 92
        # The bug in one assertion: nothing in this message may let a player
        # bet on a question they can already read.
        assert "question_text" not in payload
        assert "answers" not in payload
        assert "Mercury" not in str(payload)

    def test_snapshot_withholds_the_question(self, tmp_path: Path) -> None:
        """A phone that drops mid-window must not come back knowing what it is
        betting on — the reconnect path is the obvious way to leak it."""
        state = _game(tmp_path, rounds=1)
        state.start_next_question()
        question_text = state.get_current_question().question

        snapshot = state.get_state_snapshot()

        assert snapshot["phase"] == "WAGER_ACTIVE"
        assert "question" not in snapshot
        assert question_text not in str(snapshot)
        assert snapshot["wager"]["window_remaining"] > 0
        assert snapshot["wager"]["player_count"] == 2

    def test_host_tally_never_carries_the_amounts(self, tmp_path: Path) -> None:
        """A bet the TV gives away is not a bet."""
        state = _game(tmp_path, rounds=1)
        state.start_next_question()
        state.get_player("Anna").wager = 77

        payload = RoundMessageBuilder().build_wager_progress(state)

        assert payload["locked_in"] == 1
        assert payload["player_count"] == 2
        assert payload["waiting_on"] == ["Tom"]
        assert "77" not in str(payload)


# ---------------------------------------------------------------------------
# The handler
# ---------------------------------------------------------------------------


class TestHandler:
    @pytest.mark.asyncio
    async def test_wager_accepted_inside_the_window(self, tmp_path: Path) -> None:
        state = _game(tmp_path, rounds=1)
        state.start_next_question()
        handler, conn = _handler()
        anna = state.get_player("Anna")

        await handler._handle_submit_wager(anna.ws, {"wager": 40}, state)

        assert anna.wager == 40
        assert conn.errors == []
        assert [m for m in conn.sent if m.get("type") == "wager_accepted"]
        # Tom has not bet, so the window stays open.
        assert state.phase is GamePhase.WAGER_ACTIVE

    @pytest.mark.asyncio
    async def test_wager_refused_once_the_question_is_out(
        self, tmp_path: Path
    ) -> None:
        """The exploit #656 closes: with the question on screen, a player who
        knows the answer could previously stake everything at no risk."""
        state = _game(tmp_path, rounds=1)
        state.start_next_question()
        state.arm_round_timers()  # window closed, question live
        handler, conn = _handler()
        anna = state.get_player("Anna")

        await handler._handle_submit_wager(anna.ws, {"wager": 100}, state)

        assert anna.wager is None
        assert not [m for m in conn.sent if m.get("type") == "wager_accepted"]

    @pytest.mark.asyncio
    async def test_last_bet_closes_the_window_and_sends_the_question(
        self, tmp_path: Path
    ) -> None:
        state = _game(tmp_path, rounds=1)
        state.start_next_question()
        handler, conn = _handler()

        await handler._handle_submit_wager(
            state.get_player("Anna").ws, {"wager": 10}, state
        )
        assert state.phase is GamePhase.WAGER_ACTIVE
        await handler._handle_submit_wager(
            state.get_player("Tom").ws, {"wager": 20}, state
        )

        # Everyone in → the round starts without waiting out the deadline.
        assert state.phase is GamePhase.QUESTION_ACTIVE
        assert set(state._phase_controller.timers) == {"Anna", "Tom"}
        questions = [m for m in conn.sent if m.get("type") == "question_started"]
        assert len(questions) == 2  # one per player, own shuffle
        assert questions[0]["timer_duration"] == 30

    @pytest.mark.asyncio
    async def test_deadline_closes_the_window_and_sends_the_question(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The deadline is the guarantee that a table which never bets still
        gets its question.

        It caught a real failure on the live server: ``_close_wager_window``
        clears the deadline first, and the deadline task is one of its two
        callers — so cancelling blindly killed the coroutine mid-close and the
        final round sat on the betting screen forever with the countdown at
        zero. Every unit test passed, because they all closed the window from
        the *other* trigger.
        """
        from custom_components.quizify.server import websocket as ws_mod  # noqa: PLC0415

        monkeypatch.setattr(ws_mod, "WAGER_WINDOW_DURATION", 0.05)
        state = _game(tmp_path, rounds=1)
        question = state.start_next_question()
        handler, conn = _handler()

        await handler._open_wager_window(state, question)
        assert state.phase is GamePhase.WAGER_ACTIVE
        await asyncio.sleep(0.3)

        assert state.phase is GamePhase.QUESTION_ACTIVE
        assert [m for m in conn.sent if m.get("type") == "question_started"]
        # Nobody bet — that is allowed, and costs nothing.
        assert state.get_player("Anna").wager is None

    @pytest.mark.asyncio
    async def test_host_skip_closes_the_window(self, tmp_path: Path) -> None:
        """Mid-window, skip means "stop waiting for bets" — the question has
        not been asked yet, so there is nothing to abandon. Without this the
        host's skip fell through to the advance path, which refuses
        WAGER_ACTIVE, and the window would have been cancelled with nothing
        to restart it."""
        state = _game(tmp_path, rounds=1)
        state.start_next_question()
        handler, _conn = _handler()

        await handler._handle_admin_skip(state.get_player("Anna").ws, state)

        assert state.phase is GamePhase.QUESTION_ACTIVE

    @pytest.mark.asyncio
    async def test_opening_the_window_sends_no_question(
        self, tmp_path: Path
    ) -> None:
        state = _game(tmp_path, rounds=1)
        question = state.start_next_question()
        handler, conn = _handler()

        await handler._open_wager_window(state, question)
        handler._cancel_wager_window()  # don't leave a live deadline in a test

        windows = [m for m in conn.sent if m.get("type") == "wager_window"]
        assert len(windows) == 2
        assert not [m for m in conn.sent if m.get("type") == "question_started"]
        assert question.question not in str(conn.sent)
        assert conn.to_hosts[0]["type"] == "wager_progress"
        assert conn.to_hosts[0]["window_duration"] == 20


# ---------------------------------------------------------------------------
# Client: the gate that must NOT come back
# ---------------------------------------------------------------------------


class TestClientSource:
    """The old client disabled the answer buttons until a wager was in.

    Left in place next to a real betting window, that gate locks a player who
    let the window lapse out of answering at all — they never submitted a
    wager, so their buttons would stay dead for the whole final round.
    """

    def test_answer_buttons_are_not_gated_on_the_wager(self) -> None:
        src = (_WWW / "js" / "player-game.js").read_text("utf-8")

        assert "_wagerGate" not in src
        assert not re.search(r"btn\.disabled\s*=\s*_wager", src)

    def test_bundle_carries_the_window_renderer(self) -> None:
        """player.bundle.js is generated — a stale one ships a phone that
        cannot render the window at all."""
        bundle = (_WWW / "js" / "player.bundle.js").read_text("utf-8")

        assert "renderWagerWindow" in bundle
        assert "wager_window" in bundle

    def test_every_language_carries_the_window_strings(self) -> None:
        import json  # noqa: PLC0415

        for lang in ("de", "en", "es"):
            data = json.loads((_WWW / "i18n" / f"{lang}.json").read_text("utf-8"))
            assert "questionPending" in data["wager"], lang
            assert "waitingForOthers" in data["wager"], lang
            assert "hostWindowTitle" in data["wager"], lang
            assert "hostProgress" in data["wager"], lang
            assert "titleWager" in data["page"], lang
