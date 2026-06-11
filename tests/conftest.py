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
# pytest-socket, which blocks ALL socket use by default — socket *creation*
# (asyncio's internal socketpair) AND *connect* to non-localhost. Our tests
# don't rely on network-blocking, so mark every collected test with
# pytest-socket's own `enable_socket` marker, which reliably overrides the
# block per-test. Guarded so the base run without pytest-socket is unaffected.
def pytest_collection_modifyitems(items):  # noqa: D401
    try:
        import pytest_socket  # noqa: F401
    except ImportError:
        return
    import pytest as _pytest
    for item in items:
        item.add_marker(_pytest.mark.enable_socket)
