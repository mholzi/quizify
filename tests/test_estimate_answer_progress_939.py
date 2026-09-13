"""Estimate rounds move the answer counter too (issue #939).

``_handle_submit_answer`` acked an estimate guess and returned before it ever
called ``_mark_progress_dirty()`` — the only two callers sat on the
multiple-choice path. ``answer_progress`` is the only feeder of the submission
tracker on the phone, the TV and the host page, so on every estimate question
the room read ``0/N answered`` until the reveal. The serializer already counted
guesses (#835); only the trigger was missing.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT))

from custom_components.quizify.game.questions import (  # noqa: E402
    QUESTION_TYPE_ESTIMATE,
    Question,
)
from custom_components.quizify.game.state import QuizifyGameState  # noqa: E402
from custom_components.quizify.server.connection import (  # noqa: E402
    ConnectionManager,
)
from custom_components.quizify.server.websocket import (  # noqa: E402
    QuizifyWebSocketHandler,
)


class _FakeRuntime:
    def __init__(self, tmp_path: Path) -> None:
        self.data_dir = tmp_path

    def create_task(self, coro):  # noqa: ANN001
        return asyncio.ensure_future(coro)

    async def run_in_executor(self, func, *args):  # noqa: ANN001, ANN002
        return func(*args)


def _ws() -> MagicMock:
    ws = MagicMock()
    ws.closed = False
    ws.send_json = AsyncMock()
    ws.close = AsyncMock()
    return ws


@pytest.fixture
def game(tmp_path: Path) -> QuizifyGameState:
    return QuizifyGameState(runtime=_FakeRuntime(tmp_path), entry_id="test")


@pytest.fixture
def handler(game: QuizifyGameState, tmp_path: Path) -> QuizifyWebSocketHandler:
    runtime = _FakeRuntime(tmp_path)
    h = QuizifyWebSocketHandler(runtime=runtime, game_state_provider=lambda: game)
    h._conn = ConnectionManager(runtime, lambda: game)
    h._get_game_state = lambda: game  # type: ignore[assignment]
    h._conn.send = AsyncMock()  # type: ignore[method-assign]
    h._conn.send_error = AsyncMock()  # type: ignore[method-assign]
    h._conn.broadcast = AsyncMock()  # type: ignore[method-assign]
    h._REACTION_FLUSH_WINDOW = 0.01
    return h


def _seat(h: QuizifyWebSocketHandler, game: QuizifyGameState, name: str) -> MagicMock:
    ws = _ws()
    h._conn.add_connection(ws, is_admin=False, is_dashboard=False)
    game.add_player(name, h._conn.connection_id(ws))
    return ws


def _start_estimate_round(game: QuizifyGameState) -> None:
    game.start_game(language="en", num_rounds=3, timer_duration=30)
    game.start_next_question()
    game._current_question = Question(
        id="e-939",
        question="How many bones?",
        answers=[],
        type=QUESTION_TYPE_ESTIMATE,
        estimate_answer=206,
        estimate_min=0,
        estimate_max=500,
        estimate_unit="bones",
        estimate_step=1,
    )
    for p in game._player_registry.players.values():
        p.reset_round()


def _progress_frames(h: QuizifyWebSocketHandler) -> list[dict]:
    return [
        call.args[0]
        for call in h._conn.broadcast.call_args_list  # type: ignore[attr-defined]
        if call.args and call.args[0].get("type") == "answer_progress"
    ]


async def _flush(h: QuizifyWebSocketHandler) -> None:
    """Await the flush the handler armed — no fixed sleep (#918)."""
    task = h._progress_flush_task
    assert task is not None, "no flush armed — the guess never marked progress"
    await task


@pytest.mark.asyncio
async def test_a_solo_guess_sends_one_answer_progress_frame(
    handler: QuizifyWebSocketHandler, game: QuizifyGameState
) -> None:
    anna_ws = _seat(handler, game, "Anna")
    _seat(handler, game, "Ben")
    _seat(handler, game, "Cleo")
    _start_estimate_round(game)

    await handler._handle_submit_answer(anna_ws, {"guess": 200}, game)
    await _flush(handler)

    frames = _progress_frames(handler)
    assert len(frames) == 1
    assert (frames[0]["submitted"], frames[0]["total"]) == (1, 3)
    anna = next(e for e in frames[0]["players"] if e["name"] == "Anna")
    assert anna["submitted"] is True


@pytest.mark.asyncio
async def test_a_team_guess_sends_one_answer_progress_frame(
    handler: QuizifyWebSocketHandler, game: QuizifyGameState
) -> None:
    """A team guess marks nobody submitted (#602) — the frame must still go."""
    anna_ws = _seat(handler, game, "Anna")
    _seat(handler, game, "Dan")
    _seat(handler, game, "Ben")
    game.create_team("Sofa", "Anna")
    game.join_team(game.get_team_of("Anna")["team_id"], "Dan")
    game.create_team("Sessel", "Ben")
    _start_estimate_round(game)

    await handler._handle_submit_answer(anna_ws, {"guess": 200}, game)
    await _flush(handler)

    frames = _progress_frames(handler)
    assert len(frames) == 1
    assert (frames[0]["submitted"], frames[0]["total"]) == (1, 2)
    sofa = next(e for e in frames[0]["players"] if e["name"] == "Sofa")
    assert sofa["submitted"] is True


@pytest.mark.asyncio
async def test_a_refused_guess_does_not_mark_progress(
    handler: QuizifyWebSocketHandler, game: QuizifyGameState
) -> None:
    """Nothing changed, so the room is not told anything changed."""
    anna_ws = _seat(handler, game, "Anna")
    _seat(handler, game, "Ben")
    _start_estimate_round(game)
    await handler._handle_submit_answer(anna_ws, {"guess": 200}, game)
    await _flush(handler)
    handler._conn.broadcast.reset_mock()  # type: ignore[attr-defined]

    # A second guess from a solo player is ALREADY_SUBMITTED.
    await handler._handle_submit_answer(anna_ws, {"guess": 300}, game)
    await _flush(handler)

    handler._conn.send_error.assert_awaited()  # type: ignore[attr-defined]
    assert _progress_frames(handler) == []
