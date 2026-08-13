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
import random
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


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
    """
    from custom_components.quizify.game.questions import QuestionBank

    original = QuestionBank.get_next_question

    def _mixed_draw_skips_estimates(
        self, category=None, difficulty=None
    ):  # noqa: ANN001, ANN202
        question = original(self, category=category, difficulty=difficulty)
        if category is not None:
            return question
        for _ in range(50):
            if question is None or question.answers:
                return question
            question = original(self, category=category, difficulty=difficulty)
        return question

    QuestionBank.get_next_question = _mixed_draw_skips_estimates
    try:
        yield
    finally:
        QuestionBank.get_next_question = original


@pytest.fixture(autouse=True)
def _fresh_event_loop():
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        yield
    finally:
        try:
            loop.close()
        finally:
            asyncio.set_event_loop(asyncio.new_event_loop())


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
