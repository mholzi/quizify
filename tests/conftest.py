"""Shared pytest fixtures.

Test isolation for asyncio: some tests run a coroutine via ``asyncio.run()``
(which, on teardown, sets the current event loop to ``None``) or otherwise
leave the loop closed. On Python 3.9 a subsequent test whose setup calls
``asyncio.get_event_loop()`` then raises "There is no current event loop in
thread 'MainThread'". This autouse fixture gives every test a fresh, open loop
and disposes it afterwards, so test order can never leak a broken loop between
modules (regression seen after merging parallel feature PRs, 2026-06-09).
"""

from __future__ import annotations

import asyncio
import contextlib
import inspect
import random
import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


@pytest.fixture(scope="session", autouse=True)
def _warm_the_dns_resolver():
    """Spawn aiohttp's resolver thread before anyone is counting (#740).

    When ``aiodns`` is installed — Home Assistant depends on it —
    ``aiohttp.resolver.DefaultResolver`` is ``AsyncResolver``, and the first
    one ever constructed leaves behind a permanent pycares daemon thread named
    ``_run_safe_shutdown_loop``. Whichever test happens to open the first
    ``TestClient`` therefore appears to leak a thread.

    Today's harness allow-lists that name explicitly; the
    pytest-homeassistant-custom-component that ships with the declared HA floor
    (2024.12) predates the allow-list and fails the test instead. Creating the
    resolver once, at session start, puts the thread in place before any test's
    "threads before" snapshot is taken — which is also simply more honest than
    charging it to an arbitrary test.
    """
    with contextlib.suppress(Exception):  # pragma: no cover - env dependent
        from aiohttp.resolver import DefaultResolver

        async def _warm() -> None:
            resolver = DefaultResolver()
            try:
                await resolver.resolve("127.0.0.1", 80)
            finally:
                await resolver.close()

        asyncio.run(_warm())
    asyncio.set_event_loop(asyncio.new_event_loop())
    yield


@pytest.fixture(autouse=True)
def _seed_random():
    """Make the global ``random`` deterministic per test.

    Several game mechanics draw from the module-level ``random`` (joker
    removing a random wrong answer, freeze/steal picking a random opponent,
    player-order shuffles, streak-eligible question selection). The tests
    don't seed it, so a full ``pytest tests/`` run — whose collection order
    differs from running a single class — advances the RNG to a different
    point and intermittently flips outcomes (e.g. a joker landing a None
    remove-index). Reseed before every test so results depend only on the
    test, never on suite order.
    """
    random.seed(0)
    yield


@pytest.fixture(autouse=True)
def _mixed_draw_serves_multiple_choice():
    """Keep estimate questions out of *mixed* draws (``category=None``).

    Dozens of tests start a game over all packs and then read
    ``question.answers`` to find the correct index. An estimate question
    (#275) has none, so those tests only ever passed because the seeded
    shuffle above never happened to serve one — never because the assumption
    held. Estimates have been in the mixed pool since 1.4.0, and #566 is
    adding five to every themed pack, so each new pack pair moves the draw
    and tips over a different handful of them.

    Rather than let content changes break scoring tests, mixed draws serve
    multiple choice here. Two things stay untouched on purpose: a draw that
    *names* a category returns exactly what that pack holds, and the estimate
    tests inject their question into ``_current_question`` directly, so
    nothing that actually exercises the mechanic is hidden by this.

    The filter sits in ``_build_queue`` rather than in the draw itself,
    because skipping at serve time means an extra draw, and an extra draw
    advances ``_queue_index`` — which #350 asserts to the number.
    """
    from custom_components.quizify.game.questions import QuestionBank

    original = QuestionBank._build_queue

    def _build_mixed_queue_without_estimates(
        self, category=None, difficulty=None, language=None, categories=None
    ):  # noqa: ANN001, ANN202
        original(self, category, difficulty, language, categories)
        if category is None and not categories:
            self._queue = [q for q in self._queue if q.answers]

    QuestionBank._build_queue = _build_mixed_queue_without_estimates
    try:
        yield
    finally:
        QuestionBank._build_queue = original


@pytest.fixture(autouse=True)
def _fresh_event_loop(request):
    """Give every *synchronous* test a fresh, open loop.

    Only synchronous tests. An ``async def`` test is driven by pytest-asyncio,
    which owns the loop it runs on and hands the same loop to the harness
    fixtures (``hass``, ``http_hass``) that ran during setup. Installing a
    different loop here used to be invisible because pytest-asyncio 1.x sets
    its own loop back afterwards — but on the pytest-asyncio 0.24 that ships
    with the declared HA floor (2024.12) it wins, and the test body then awaits
    on a loop the fixture's executor futures were never attached to:
    42 tests died with "attached to a different loop" (#740). Leaving async
    tests alone is correct on every version: their loop is not ours to swap.
    """
    if inspect.iscoroutinefunction(request.function):
        yield
        return
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        yield
    finally:
        try:
            loop.close()
        finally:
            asyncio.set_event_loop(asyncio.new_event_loop())


_COMPONENT_ROOT = str(Path(__file__).resolve().parent.parent / "custom_components")


def _is_component_task(task: asyncio.Task) -> bool:
    """True when the task is running one of *our* coroutines."""
    code = getattr(task.get_coro(), "cr_code", None)
    return code is not None and code.co_filename.startswith(_COMPONENT_ROOT)


@pytest.fixture(autouse=True)
def _cancel_component_background_tasks():
    """Run the teardown these unit tests never had (#740).

    Eight places in the integration arm a background timer — the roster,
    progress and reaction debounces, the round-timer tick, the wager window,
    the disconnect grace, the admin-session grace, the question-stats save.
    Every one of them is cancelled in production: by ``_cancel_roster_flush``,
    by ``_handle_disconnect``, by ``async_flush`` on config-entry unload. The
    tests below construct the handler bare, trigger the timer and end — nobody
    unloads anything, so the task is simply left pending.

    That went unnoticed for as long as it did because the old
    ``_fresh_event_loop`` above swapped the loop before
    pytest-homeassistant-custom-component's ``verify_cleanup`` looked at it, so
    the check counted tasks on an empty loop. With the swap gone it sees the
    real one and fails 78 tests. This fixture supplies the missing unload:
    cancel what our own modules left running, and only that. Tasks belonging to
    Home Assistant, aiohttp or the harness are untouched, so ``verify_cleanup``
    still gates everything it was there to gate — including the integration's
    real unload path, which ``test_unload_detaches_consumers_605``,
    ``test_ws_route_survives_reload_606`` and ``test_question_stats_flush_588``
    assert directly.
    """
    yield
    try:
        loop = asyncio.get_event_loop()
    except RuntimeError:  # pragma: no cover - no loop left to clean
        return
    if not all(
        callable(getattr(loop, name, None))
        for name in ("is_closed", "is_running", "run_until_complete")
    ):
        # A test replaced ``asyncio.get_event_loop`` with a stub clock
        # (test_performance_169) and monkeypatch has not undone it yet. There
        # is no loop to inspect, and nothing of ours was scheduled on one.
        return
    if loop.is_closed() or loop.is_running():
        return
    pending = [
        task
        for task in asyncio.all_tasks(loop)
        if not task.done() and _is_component_task(task)
    ]
    if not pending:
        return
    for task in pending:
        task.cancel()
    loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))


# pytest-homeassistant-custom-component (a CI-only test dep, #271) pulls in
# pytest-socket, which blocks ALL socket use by default — socket *creation*
# (asyncio's internal socketpair) AND *connect* to non-localhost. Our tests
# don't rely on network-blocking, so we re-enable sockets for every test.
#
# This used to attach pytest-socket's `enable_socket` MARKER to every collected
# item. That stopped working on 2026-07-21 without a single line of our code
# changing: re-running the last green main build (060ae50, green on 2026-07-15)
# failed with 23 SocketBlockedError in exactly the tests that spin a real
# aiohttp TestServer. The only environment difference was the GitHub runner
# image (20260705.232.1 → 20260714.240.1); every relevant package version
# (Python 3.13.14, pytest 9.0.0, pytest-socket 0.7.0, pytest-homeassistant-
# custom-component 0.13.316, aiohttp, homeassistant) was identical.
#
# A marker only helps if pytest-socket's own setup hook wins the ordering race
# against everything else that touches the guard, and that ordering is not ours
# to control. So enable the socket explicitly instead, at the two points where
# we *can* be last: a `trylast` setup hook (runs after every other plugin's
# runtest_setup) and an autouse fixture (runs immediately before the test body).
# Both are no-ops when pytest-socket is absent, so a base run without the HA
# test harness is unaffected.
try:  # pragma: no cover - depends on the CI-only test dependency
    import pytest_socket
except ImportError:  # pragma: no cover
    pytest_socket = None


@pytest.hookimpl(trylast=True)
def pytest_runtest_setup(item):  # noqa: ARG001, D401
    if pytest_socket is not None:
        pytest_socket.enable_socket()


@pytest.fixture(autouse=True)
def _sockets_enabled():
    """Re-assert socket access right before the test body runs."""
    if pytest_socket is not None:
        pytest_socket.enable_socket()
    yield


# ---------------------------------------------------------------------------
# Shared source-slicing helper (#811)
#
# Several tests assert on the *code* inside www/*.js, www/*.html or a Python
# module. A fix that explains itself in a comment then quotes the very thing it
# removed, and a raw text search reads the explanation as the code — the trap
# hit in #622, #625, #626 and, from the other side, in #811: a slice anchored on
# `ws.onclose` landed on a CSS comment 2150 lines above the handler, so the
# assertion was satisfied by the guarded line existing *anywhere* on the page.
#
# The helper lived twice (test_end_screen_standing_624.py,
# test_preset_modals_626.py). It lives here now so the next source-slicing test
# does not re-implement it a third time.
# ---------------------------------------------------------------------------

_BLOCK_COMMENT = re.compile(r"/\*.*?\*/", re.S)
_LINE_COMMENT = re.compile(r"^\s*//.*$", re.M)
_HTML_COMMENT = re.compile(r"<!--.*?-->", re.S)


def without_comments(source: str) -> str:
    """Strip comments so assertions look at declarations, never at prose.

    Handles the three comment syntaxes that appear in the files these tests
    slice: ``/* … */`` (CSS and JS), a whole-line ``//`` (JS), and
    ``<!-- … -->`` (HTML). The line-comment pattern is anchored at the start of
    a line on purpose — a bare ``re.sub("//.*")`` would eat the rest of any line
    containing a URL.

    Deliberately not a parser. It is enough to keep a comment from standing in
    for the code it describes, which is the only failure mode it exists for.
    """
    source = _BLOCK_COMMENT.sub("", source)
    source = _HTML_COMMENT.sub("", source)
    return _LINE_COMMENT.sub("", source)


# Import it as ``from tests.conftest import without_comments`` — pytest 9 loads
# this file as the module ``tests.conftest``, and the ``sys.path`` insert above
# puts the repo root in front, so that spelling resolves under both the current
# pytest and the older one on the HA-floor leg.
