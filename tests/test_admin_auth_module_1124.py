"""Pin the admin auth path after it moved to ``server/admin_auth.py`` (#1124).

Part 3 of #1124 moved ``_grant_admin``, ``_handle_admin_connect``,
``_is_authorized_admin`` and ``_is_reset_authorized`` out of
``QuizifyWebSocketHandler``. The handler keeps one-line delegates under the old
names. These tests pin the rules through both entry points, so the delegate
and the module function cannot drift apart:

- a ``role=admin`` socket without a token, or with a wrong one, is not admin
  once a token exists;
- the right token grants admin;
- the token is only honoured for ``role=admin``;
- a rejected token is never written to the log;
- the bootstrap still grants exactly once;
- player-as-admin and the ``reset_game`` escape hatch keep their rules.
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT))

from custom_components.quizify.server import admin_auth  # noqa: E402
from custom_components.quizify.server.connection import ConnectionManager  # noqa: E402
from custom_components.quizify.server.websocket import (  # noqa: E402
    QuizifyWebSocketHandler,
)

_WS_LOGGER = "custom_components.quizify.server.websocket"


class _FakeRuntime:
    def __init__(self, tmp_path: Path) -> None:
        self.data_dir = tmp_path
        self.hass = None

    async def run_in_executor(self, func, *args):  # noqa: ANN001, ANN002
        return func(*args)


class _Player:
    def __init__(self, *, is_admin: bool, connected: bool = True) -> None:
        self.is_admin = is_admin
        self.connected = connected


class _GameState:
    def __init__(self, admin: _Player | None = None) -> None:
        self._admin = admin

    def get_admin(self) -> _Player | None:
        return self._admin


def _request(remote: str = "192.168.1.50") -> MagicMock:
    request = MagicMock()
    request.remote = remote
    return request


@pytest.fixture
def handler(tmp_path: Path) -> QuizifyWebSocketHandler:
    runtime = _FakeRuntime(tmp_path)
    h = QuizifyWebSocketHandler(runtime=runtime, game_state_provider=lambda: None)
    h._conn = ConnectionManager(runtime, lambda: None)
    h._conn.broadcast = AsyncMock()
    h._conn.send = AsyncMock()
    return h


async def _issued_token(handler: QuizifyWebSocketHandler) -> str:
    """Bootstrap once, the way the host's first tab does, and return the token."""
    assert await handler._grant_admin("admin", None, _request("192.168.1.2"))
    token = handler._conn._admin_session_token
    assert token
    return token


async def _grant_via_module(h, role, token, request):  # noqa: ANN001, ANN202
    return await admin_auth.grant_admin(h, role, token, request)


async def _grant_via_delegate(h, role, token, request):  # noqa: ANN001, ANN202
    return await h._grant_admin(role, token, request)


_GRANTS = pytest.mark.parametrize(
    "grant", [_grant_via_module, _grant_via_delegate], ids=["module", "delegate"]
)


# ---------------------------------------------------------------------------
# grant_admin
# ---------------------------------------------------------------------------


@_GRANTS
@pytest.mark.asyncio
async def test_no_token_is_rejected_once_a_token_exists(handler, grant) -> None:
    await _issued_token(handler)
    assert await grant(handler, "admin", None, _request()) is False
    assert await grant(handler, "admin", "", _request()) is False


@_GRANTS
@pytest.mark.asyncio
async def test_wrong_token_is_rejected(handler, grant) -> None:
    token = await _issued_token(handler)
    assert await grant(handler, "admin", "not-the-token", _request()) is False
    # Same length, one character off: still a different token.
    near = token[:-1] + ("0" if token[-1] != "0" else "1")
    assert await grant(handler, "admin", near, _request()) is False


@_GRANTS
@pytest.mark.asyncio
async def test_right_token_is_accepted(handler, grant) -> None:
    token = await _issued_token(handler)
    assert await grant(handler, "admin", token, _request()) is True


@_GRANTS
@pytest.mark.asyncio
async def test_token_only_counts_for_role_admin(handler, grant) -> None:
    token = await _issued_token(handler)
    for role in (None, "", "player", "dashboard", "Admin"):
        assert await grant(handler, role, token, _request()) is False


@_GRANTS
@pytest.mark.asyncio
async def test_bootstrap_grants_exactly_once(handler, grant) -> None:
    assert await grant(handler, "admin", None, _request("192.168.1.2")) is True
    assert await grant(handler, "admin", None, _request("192.168.1.3")) is False


@pytest.mark.asyncio
async def test_rejected_token_is_not_logged(handler, caplog) -> None:
    token = await _issued_token(handler)
    wrong = "wrong-token-value-1124"
    with caplog.at_level(logging.DEBUG, logger=_WS_LOGGER):
        assert await handler._grant_admin("admin", wrong, _request()) is False
        assert await handler._grant_admin("admin", token, _request()) is True

    text = "\n".join(r.getMessage() for r in caplog.records)
    assert "INVALID token rejected" in text
    assert wrong not in text
    assert token not in text
    # The lines still come from the old logger name (#1124).
    assert {r.name for r in caplog.records} >= {_WS_LOGGER}


# ---------------------------------------------------------------------------
# handle_admin_connect
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_admin_connect_sends_the_token_only_to_that_socket(handler) -> None:
    token = await _issued_token(handler)
    ws = object()
    handler._snapshot = lambda gs: {"phase": "LOBBY"}

    await handler._handle_admin_connect(ws, _GameState())

    handler._conn.send.assert_awaited_once()
    sent_ws, frame = handler._conn.send.await_args.args
    assert sent_ws is ws
    assert frame["type"] == "game_state"
    assert frame["admin_session_token"] == token
    handler._conn.broadcast.assert_not_awaited()


# ---------------------------------------------------------------------------
# is_authorized_admin / is_reset_authorized
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("ws_admin", "player", "expected"),
    [
        (True, None, True),
        (False, None, False),
        (False, _Player(is_admin=False), False),
        (False, _Player(is_admin=True), True),
    ],
)
def test_is_authorized_admin(handler, ws_admin, player, expected) -> None:
    handler._player_for_ws = lambda gs, ws: player
    gs = _GameState()
    assert admin_auth.is_authorized_admin(handler, object(), ws_admin, gs) is expected
    assert handler._is_authorized_admin(object(), ws_admin, gs) is expected


def test_reset_refused_for_a_guest_while_a_host_is_connected(handler) -> None:
    handler._player_for_ws = lambda gs, ws: _Player(is_admin=False)
    gs = _GameState(admin=_Player(is_admin=True, connected=True))
    assert handler._is_reset_authorized(object(), False, gs) is False
    assert admin_auth.is_reset_authorized(handler, object(), False, gs) is False


def test_reset_allowed_for_a_guest_when_nobody_hosts(handler) -> None:
    handler._player_for_ws = lambda gs, ws: _Player(is_admin=False)
    gs = _GameState(admin=_Player(is_admin=True, connected=False))
    assert handler._is_reset_authorized(object(), False, gs) is True


def test_reset_always_allowed_for_the_admin(handler) -> None:
    handler._player_for_ws = lambda gs, ws: None
    gs = _GameState(admin=_Player(is_admin=True, connected=True))
    assert handler._is_reset_authorized(object(), True, gs) is True
