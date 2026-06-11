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

import pytest


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
# pytest-socket, which blocks ALL socket use by default — including asyncio's
# internal selector socketpair, which breaks the sync asyncio.run() tests. Keep
# sockets enabled (we don't rely on network-blocking). Guarded so the base test
# run without pytest-socket installed is unaffected.
try:  # pragma: no cover - only active when pytest-socket is installed (CI)
    import pytest as _pytest
    import pytest_socket as _pytest_socket

    @_pytest.fixture(autouse=True)
    def _quizify_enable_sockets():
        _pytest_socket.enable_socket()
        yield
except ImportError:  # pytest-socket absent (base local run) — nothing to do
    pass
