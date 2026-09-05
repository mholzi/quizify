"""Regression tests for #746 — one owner for round-scoped task teardown.

Five round-scoped asyncio tasks lived as handler attributes and were
cancelled by hand at roughly twenty call sites. The resulting matrix was
asymmetric, and the asymmetry was the bug: ``_handle_reset_game``,
``_handle_play_again``, ``_handle_start_game`` and ``cleanup_game_tasks``
cancelled four of them, ``_advance_round`` two, and ``admin_action_end_game``
exactly one — the tick.

So the lightning loop outlived the finale. ``end_game()`` has no phase guard,
the loop's wait re-checked the phase only *after* a sleep (the all-answered
``break`` skipped the check), and ``lr.advance()`` then scored a question
after the podium had been broadcast and the analytics recorded. The
scoreboard moved after the end screen was up.

That is the same shape as #362, #407, #656 and #671 — four earlier rounds of
"a task was added, one path was forgotten". These tests therefore guard two
different things:

  * the teardown paths themselves, one test per path per task, because two
    triggers for the same transition need a test each (the lesson from
    #656/#657); and
  * the *registry*, so a sixth task cannot be forgotten at a twenty-first
    call site. ``test_every_handler_task_is_classified`` reads the handler's
    own source and fails if an ``asyncio.Task | None`` attribute appears in
    ``__init__`` without landing in one of the two scope tuples.
"""

from __future__ import annotations

import ast
import asyncio
import inspect
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT))

from custom_components.quizify.game.state import (  # noqa: E402
    GamePhase,
    QuizifyGameState,
)
from custom_components.quizify.server.connection import (  # noqa: E402
    ConnectionManager,
)
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
    h = QuizifyWebSocketHandler(
        runtime=_FakeRuntime(tmp_path), game_state_provider=lambda: game
    )
    h._conn = ConnectionManager(_FakeRuntime(tmp_path), lambda: game)
    h._conn.broadcast = AsyncMock()
    h._conn.broadcast_to_admins_and_dashboards = AsyncMock()
    h._conn.send = AsyncMock()
    return h


def _park(handler: QuizifyWebSocketHandler, attr: str) -> asyncio.Task:
    """Park a stand-in task on ``attr``.

    Deliberately not the real loop: these assert the cancel *wiring*, and a
    test that had to arrange a live lightning round to check a teardown path
    would stop testing the teardown the first time the round's own guards
    moved. The behavioural half lives in the end_game test further down.
    """

    async def _sleep_forever() -> None:
        await asyncio.sleep(3600)

    task = asyncio.ensure_future(_sleep_forever())
    setattr(handler, attr, task)
    return task


async def _assert_cancelled(
    handler: QuizifyWebSocketHandler, attr: str, task: asyncio.Task
) -> None:
    assert getattr(handler, attr) is None
    await asyncio.sleep(0)
    assert task.cancelled()


async def _assert_cancelled_even_if_replaced(
    handler: QuizifyWebSocketHandler, attr: str, task: asyncio.Task
) -> None:
    """For paths that tear down and then start a fresh game in one call.

    ``start_game`` and ``play_again`` run on into round 1, which arms a new
    timer tick — so the attribute is legitimately non-None afterwards. What
    must hold is that the OLD task died and did not survive as the live one.
    """
    await asyncio.sleep(0)
    assert task.cancelled()
    assert getattr(handler, attr) is not task


# Every round-scoped task, by the attribute the handler parks it on. Used to
# parametrize the teardown paths so a task added to the registry is asserted
# against every path at once, rather than only the one whose bug prompted it.
#
# Spelled out rather than read from ``_ROUND_SCOPED_TASKS`` on purpose: the
# behavioural tests below have to run — and fail — against a handler that has
# no registry yet, which is the whole point of a regression test.
# ``test_registry_matches_the_round_scoped_tasks`` ties the two together.
_ROUND_TASK_ATTRS = [
    "_timer_tick_task",
    "_wager_window_task",
    "_lightning_task",
    "_hot_seat_task",
    "_admin_pause_task",
]


# ---------------------------------------------------------------------------
# The bug: end_game left the lightning loop running
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("attr", _ROUND_TASK_ATTRS)
async def test_end_game_cancels_every_round_scoped_task(
    handler: QuizifyWebSocketHandler, game: QuizifyGameState, attr: str
) -> None:
    """The finale ends the round, so nothing round-scoped may survive it.

    Before #746 this passed only for ``_timer_tick_task`` (and the wager
    window it drags along); the lightning loop, the hot-seat loop and the
    deferred admin pause were all free to outlive the podium.
    """
    task = _park(handler, attr)
    await handler.admin_action_end_game(game)
    await _assert_cancelled(handler, attr, task)


@pytest.mark.asyncio
async def test_ws_end_game_handler_cancels_the_lightning_loop(
    handler: QuizifyWebSocketHandler, game: QuizifyGameState
) -> None:
    """The admin's WS ``end_game`` reaches the same teardown as the service."""
    task = _park(handler, "_lightning_task")
    await handler._handle_end_game(_ws(), game)
    await _assert_cancelled(handler, "_lightning_task", task)


@pytest.mark.asyncio
async def test_lightning_question_is_not_scored_after_end_game(
    handler: QuizifyWebSocketHandler, game: QuizifyGameState
) -> None:
    """The behavioural half: the real loop, ended mid-question.

    Reproduces the exact gap. The wait loop re-checks the phase only after
    ``asyncio.sleep(step)``, so the all-answered ``break`` reached
    ``lr.advance()`` with no check at all — and there is no await between the
    two, so cancelling the task alone does not stop it either. The end_game
    here lands inside the tick broadcast, which is precisely where the host's
    tap lands on a live server.
    """
    game.add_player("A", _ws())
    assert game.start_lightning_round() is True
    lr = game.lightning
    lr.seconds_per_question = 30.0  # long: only all-answered ends the wait
    # One connected player who has "answered" — the short-circuit the loop
    # takes when the room is quicker than the clock.
    lr.all_connected_answered = lambda _connected: True
    lr.advance = MagicMock(return_value=True)

    handler.LIGHTNING_SPLASH_GRACE = 0.0  # keep the test sub-second

    ended: list[bool] = []

    async def _tick_then_end(*_args, **_kwargs) -> None:
        if not ended:
            ended.append(True)
            await handler.admin_action_end_game(game)

    handler._broadcast_lightning_tick = _tick_then_end

    handler._start_lightning_loop(game)
    for _ in range(20):
        await asyncio.sleep(0.02)
        if ended:
            break
    await asyncio.sleep(0.05)

    assert game.phase == GamePhase.FINALE
    assert lr.advance.called is False, (
        "a lightning question was scored after the finale — #746"
    )
    # And no recap frame after the podium.
    sent_types = [
        c.args[0].get("type")
        for c in handler._conn.broadcast.call_args_list
        if c.args and isinstance(c.args[0], dict)
    ]
    assert "lightning_recap" not in sent_types


# ---------------------------------------------------------------------------
# The other complete-teardown paths keep cancelling everything
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("attr", _ROUND_TASK_ATTRS)
async def test_reset_game_cancels_every_round_scoped_task(
    handler: QuizifyWebSocketHandler, game: QuizifyGameState, attr: str
) -> None:
    task = _park(handler, attr)
    await handler._handle_reset_game(_ws(), game)
    await _assert_cancelled(handler, attr, task)


@pytest.mark.asyncio
@pytest.mark.parametrize("attr", _ROUND_TASK_ATTRS)
async def test_cleanup_game_tasks_cancels_every_round_scoped_task(
    handler: QuizifyWebSocketHandler, attr: str
) -> None:
    task = _park(handler, attr)
    await handler.cleanup_game_tasks()
    await _assert_cancelled(handler, attr, task)


@pytest.mark.asyncio
@pytest.mark.parametrize("attr", _ROUND_TASK_ATTRS)
async def test_start_game_cancels_every_round_scoped_task(
    handler: QuizifyWebSocketHandler, game: QuizifyGameState, attr: str
) -> None:
    """Unconditional since #746 — a fresh start owes the new game a clean slate
    whatever phase it starts from, LOBBY included."""
    game.add_player("Anna", _ws())
    handler.START_REDIRECT_GRACE = 0.0
    task = _park(handler, attr)
    await handler._handle_start_game(_ws(), {"num_rounds": 3}, game)
    await _assert_cancelled_even_if_replaced(handler, attr, task)


@pytest.mark.asyncio
@pytest.mark.parametrize("attr", _ROUND_TASK_ATTRS)
async def test_play_again_cancels_every_round_scoped_task(
    handler: QuizifyWebSocketHandler, game: QuizifyGameState, attr: str
) -> None:
    game.add_player("Anna", _ws())
    game.start_game(num_rounds=3)
    handler.START_REDIRECT_GRACE = 0.0
    task = _park(handler, attr)
    await handler._handle_play_again(_ws(), game)
    await _assert_cancelled_even_if_replaced(handler, attr, task)


# ---------------------------------------------------------------------------
# The deliberate subsets stay subsets
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pause_freezes_the_clock_and_nothing_else(
    handler: QuizifyWebSocketHandler, game: QuizifyGameState
) -> None:
    """A pause is not a teardown: the round is still live, so the deferred
    admin-disconnect pause and the detour loops must survive it."""
    game.add_player("Anna", _ws())
    game.start_game(num_rounds=3)
    game.start_next_question()
    tick = _park(handler, "_timer_tick_task")
    pending = _park(handler, "_admin_pause_task")

    assert await handler.admin_action_pause(game) is True

    await asyncio.sleep(0)
    assert tick.cancelled()
    assert handler._admin_pause_task is pending
    assert not pending.cancelled()
    pending.cancel()


@pytest.mark.asyncio
async def test_advance_out_of_lightning_recap_keeps_the_admin_pause(
    handler: QuizifyWebSocketHandler, game: QuizifyGameState
) -> None:
    """``_advance_round`` settles ONE detour. Cancelling the whole round here
    would swallow a genuine host disconnect that is still counting down."""
    game.add_player("Anna", _ws())
    game.start_game(num_rounds=3)
    assert game.start_lightning_round(auto=True) is True
    game.finish_lightning_round()
    assert game.phase == GamePhase.LIGHTNING_RECAP

    loop_task = _park(handler, "_lightning_task")
    pending = _park(handler, "_admin_pause_task")

    assert await handler._advance_round(game) is None

    await asyncio.sleep(0)
    assert loop_task.cancelled()
    assert handler._admin_pause_task is pending
    assert not pending.cancelled()
    pending.cancel()
    handler._cancel_timer_tick()


# ---------------------------------------------------------------------------
# The registry itself — the guard that makes a sixth omission unwritable
# ---------------------------------------------------------------------------


def _task_attributes_declared_in_init() -> set[str]:
    """Every ``self._x: asyncio.Task | None`` assigned in the handler's init."""
    source_file = inspect.getsourcefile(QuizifyWebSocketHandler)
    assert source_file is not None
    tree = ast.parse(Path(source_file).read_text(encoding="utf-8"))

    found: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.ClassDef) or node.name != "QuizifyWebSocketHandler":
            continue
        for item in node.body:
            if not isinstance(item, ast.FunctionDef) or item.name != "__init__":
                continue
            for stmt in ast.walk(item):
                if not isinstance(stmt, ast.AnnAssign):
                    continue
                target = stmt.target
                if (
                    isinstance(target, ast.Attribute)
                    and isinstance(target.value, ast.Name)
                    and target.value.id == "self"
                    and ast.unparse(stmt.annotation) == "asyncio.Task | None"
                ):
                    found.add(target.attr)
    return found


def test_every_handler_task_is_classified() -> None:
    """A new background task must declare its scope, not just a call site.

    This is the test that retires the #362/#407/#656/#671/#746 pattern. Adding
    ``self._foo_task: asyncio.Task | None = None`` and wiring one cancel call
    by hand now fails here, with the two tuples as the only place to put it —
    and both are walked by teardown code, so classifying it is also enough to
    tear it down everywhere.
    """
    declared = _task_attributes_declared_in_init()
    classified = {a for a, _ in QuizifyWebSocketHandler._ROUND_SCOPED_TASKS} | {
        a for a, _ in QuizifyWebSocketHandler._CONNECTION_SCOPED_TASKS
    }

    assert declared, "the AST walk found no task attributes — it has gone stale"
    assert declared == classified, (
        "unclassified handler task(s): "
        f"{sorted(declared - classified)}; stale registry entries: "
        f"{sorted(classified - declared)}. Add each task to "
        "_ROUND_SCOPED_TASKS (dies with the round) or "
        "_CONNECTION_SCOPED_TASKS (survives it)."
    )


def test_registry_matches_the_round_scoped_tasks() -> None:
    """Ties the hand-written list above back to the registry it stands in for."""
    assert [
        attr for attr, _c in QuizifyWebSocketHandler._ROUND_SCOPED_TASKS
    ] == _ROUND_TASK_ATTRS


def test_every_registered_canceller_exists_and_is_callable() -> None:
    """The registries are strings; this is what keeps them honest."""
    for attr, canceller in (
        *QuizifyWebSocketHandler._ROUND_SCOPED_TASKS,
        *QuizifyWebSocketHandler._CONNECTION_SCOPED_TASKS,
    ):
        fn = getattr(QuizifyWebSocketHandler, canceller, None)
        assert callable(fn), f"{canceller} (for {attr}) is not a handler method"
        params = list(inspect.signature(fn).parameters)
        assert params == ["self"], f"{canceller} must take no arguments but self"
