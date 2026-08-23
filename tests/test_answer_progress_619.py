"""The room can see who it is waiting for (issue #619).

Every part of this existed and none of it was connected: ``player.html`` has a
``#submission-tracker``, ``renderSubmissionTracker()`` was fully written, and its
only caller ``updateGameView()`` was never invoked by anything. The TV handled a
``leaderboard_update`` message that had zero senders. The server broadcast
nothing at all when an answer landed.

Meanwhile the round really does end early once everyone has answered — so when
one guest dawdles, the whole room watches a timer with no idea who it is waiting
for. That is the actual cost: not a missing pixel, a missing "Papa, tap
something".

Two design points are pinned here because both were decisions, not defaults:

* the payload shape follows the renderer that already exists (``name`` /
  ``submitted`` / ``connected``), not the plain name list the issue proposed —
  that renderer draws initial circles and colours each one, and could not have
  drawn a list of strings;
* the broadcast is coalesced through the same window as the roster (#453).
  Sending per accepted answer would re-create the O(N²) fan-out #453 removed,
  on a hotter path: a room of eight taps eight times per round.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT))

from custom_components.quizify.game.state import QuizifyGameState  # noqa: E402
from custom_components.quizify.server.connection import (  # noqa: E402
    ConnectionManager,
)
from custom_components.quizify.server.serializers import (  # noqa: E402
    serialize_answer_progress,
)
from custom_components.quizify.server.websocket import (  # noqa: E402
    QuizifyWebSocketHandler,
)

_WWW = _REPO_ROOT / "custom_components" / "quizify" / "www"


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


@pytest.fixture
def game(tmp_path: Path) -> QuizifyGameState:
    return QuizifyGameState(runtime=_FakeRuntime(tmp_path), entry_id="test")


@pytest.fixture
def handler(game: QuizifyGameState) -> QuizifyWebSocketHandler:
    runtime = _FakeRuntime(game._runtime.data_dir)  # type: ignore[attr-defined]
    h = QuizifyWebSocketHandler(runtime=runtime, game_state_provider=lambda: game)
    h._conn = ConnectionManager(runtime, lambda: game)
    h._conn.broadcast = AsyncMock()
    h._REACTION_FLUSH_WINDOW = 0.02
    return h


# --- payload ---------------------------------------------------------------


def test_the_payload_matches_what_the_renderer_reads(game: QuizifyGameState) -> None:
    """``renderSubmissionTracker`` predates any sender and reads these keys."""
    game.add_player("Anna", _ws())
    game.add_player("Ben", _ws())
    game.get_player("Anna").submit_answer(1, 0.0)

    payload = serialize_answer_progress(game.get_players())

    assert payload["type"] == "answer_progress"
    assert payload["submitted"] == 1
    assert payload["total"] == 2
    assert {e["name"] for e in payload["players"]} == {"Anna", "Ben"}
    for entry in payload["players"]:
        assert set(entry) == {"name", "submitted", "connected"}


def test_the_payload_carries_no_scores(game: QuizifyGameState) -> None:
    """This goes out mid-question.

    A live score next to each name would say who just answered correctly — the
    same class of leak as #604, arriving through a different door.
    """
    game.add_player("Anna", _ws())
    game.get_player("Anna").submit_answer(1, 0.0)

    entry = serialize_answer_progress(game.get_players())["players"][0]

    assert "score" not in entry
    assert "streak" not in entry


# --- coalescing ------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_tap_storm_is_one_broadcast(
    handler: QuizifyWebSocketHandler, game: QuizifyGameState
) -> None:
    """Five simultaneous taps must not be five room-wide frames."""
    for name in ("A", "B", "C", "D", "E"):
        game.add_player(name, _ws())

    for _ in range(5):
        handler._mark_progress_dirty()
    await asyncio.sleep(handler._REACTION_FLUSH_WINDOW * 4)

    progress_frames = [
        call.args[0]
        for call in handler._conn.broadcast.call_args_list
        if call.args and call.args[0].get("type") == "answer_progress"
    ]
    assert len(progress_frames) == 1
    assert progress_frames[0]["total"] == 5


@pytest.mark.asyncio
async def test_the_frame_reflects_the_room_at_flush_time(
    handler: QuizifyWebSocketHandler, game: QuizifyGameState
) -> None:
    """Serialized on flush, not on the first tap of the window.

    Otherwise the frame describes the room as it was when the window opened,
    which is exactly the state the room already saw.
    """
    game.add_player("Anna", _ws())
    game.add_player("Ben", _ws())

    handler._mark_progress_dirty()
    game.get_player("Anna").submit_answer(0, 0.0)
    game.get_player("Ben").submit_answer(1, 0.0)
    await asyncio.sleep(handler._REACTION_FLUSH_WINDOW * 4)

    frame = next(
        call.args[0]
        for call in handler._conn.broadcast.call_args_list
        if call.args and call.args[0].get("type") == "answer_progress"
    )
    assert frame["submitted"] == 2


@pytest.mark.asyncio
async def test_cleanup_cancels_the_pending_flush(
    handler: QuizifyWebSocketHandler, game: QuizifyGameState
) -> None:
    """A teardown with a flush in flight must not fire into a dead game."""
    game.add_player("Anna", _ws())
    handler._mark_progress_dirty()
    assert handler._progress_flush_task is not None

    handler._cancel_progress_flush()

    assert handler._progress_flush_task is None
    assert handler._progress_dirty is False


# --- client wiring ---------------------------------------------------------


def test_the_phone_routes_the_message_to_the_dead_renderer() -> None:
    source = (_WWW / "js" / "player-core.js").read_text("utf-8")

    assert "case 'answer_progress':" in source
    assert "game.renderSubmissionTracker(msg.players)" in source


def test_the_routing_reached_the_shipped_bundle() -> None:
    """player.html loads the bundle; editing the module alone ships nothing."""
    bundle = (_WWW / "js" / "player.bundle.js").read_text("utf-8")

    assert "case 'answer_progress':" in bundle


def test_the_tv_shows_a_count_and_clears_it_between_rounds() -> None:
    """A stale "5/5" over a fresh question would be worse than no counter."""
    source = (_WWW / "dashboard.html").read_text("utf-8")

    assert "function handleAnswerProgress(" in source
    assert "case 'answer_progress':" in source
    question_started = source.split("case 'question_started':", 1)[1][:400]
    assert "answerProgress" in question_started


def test_the_all_submitted_state_finally_has_a_rule() -> None:
    """The class was toggled from the start and styled by nothing, so the one
    moment the row is worth looking at looked like every other moment."""
    css = (_WWW / "css" / "styles.css").read_text("utf-8")

    assert ".submission-tracker.all-submitted" in css
    assert "prefers-reduced-motion" in css
