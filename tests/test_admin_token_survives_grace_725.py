"""Regression tests for issue #725 (security).

The persisted admin token is the only credential behind
``views._is_admin_authenticated`` — the gate in front of analytics, the
all-time leaderboard, flags (player names plus free text), question stats,
presets and the TTS/house entity lists. ``__init__.py`` loads it at setup
precisely so no LAN client can seize admin after a restart.

The admin-disconnect grace timer used to undo that between sessions: when the
last ``?role=admin`` socket closed, ``_handle_disconnect`` armed
``schedule_admin_timeout()``, and 120 s later the token was set to ``None`` and
``admin_token.json`` deleted. From then on ``_grant_admin`` bootstrapped the
*first* ``?role=admin`` connection from any address and handed it the session
token — the exact takeover the persisted token (#140/#168) exists to close.

The fix: grace expiry no longer touches the credential. Clearing it is a
deliberate, HA-authenticated act via the ``quizify.reset_admin_session``
service.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT))

from custom_components.quizify.server.connection import ConnectionManager  # noqa: E402
from custom_components.quizify.server.websocket import (  # noqa: E402
    QuizifyWebSocketHandler,
)


class _FakeRuntime:
    def __init__(self, tmp_path: Path) -> None:
        self.data_dir = tmp_path

    async def run_in_executor(self, func, *args):  # noqa: ANN001, ANN002
        return func(*args)


class _FakePlayer:
    def __init__(self, *, is_admin: bool, connected: bool) -> None:
        self.is_admin = is_admin
        self.connected = connected


class _FakeGameState:
    def __init__(self, players: list[_FakePlayer]) -> None:
        self._players = players

    def get_players(self) -> list[_FakePlayer]:
        return self._players


def _request(remote: str = "192.168.1.66") -> MagicMock:
    """A minimal stand-in for the aiohttp request ``_grant_admin`` logs."""
    request = MagicMock()
    request.remote = remote
    return request


async def _expire_grace(conn: ConnectionManager) -> None:
    """Arm the admin grace with a zero wait and let it run to completion."""
    conn.ADMIN_SESSION_GRACE = 0
    conn.schedule_admin_timeout()
    await asyncio.gather(conn._admin_disconnect_task, return_exceptions=True)


class TestGraceExpiryKeepsTheCredential:
    @pytest.mark.asyncio
    async def test_token_survives_when_the_host_is_gone(self, tmp_path: Path) -> None:
        """Nobody is left in the room — the token must still not be dropped."""
        conn = ConnectionManager(_FakeRuntime(tmp_path), lambda: None)
        await conn.try_bootstrap_admin()
        token = conn._admin_session_token
        assert token is not None

        await _expire_grace(conn)

        assert conn._admin_session_token == token

    @pytest.mark.asyncio
    async def test_token_survives_when_the_admin_player_disconnected(
        self, tmp_path: Path
    ) -> None:
        """The host closed the tab mid-game; that is not a reason to de-auth."""
        gs = _FakeGameState([_FakePlayer(is_admin=True, connected=False)])
        conn = ConnectionManager(_FakeRuntime(tmp_path), lambda: gs)
        await conn.try_bootstrap_admin()
        token = conn._admin_session_token

        await _expire_grace(conn)

        assert conn._admin_session_token == token

    @pytest.mark.asyncio
    async def test_token_file_stays_on_disk(self, tmp_path: Path) -> None:
        """The file must survive too, or the next HA restart bootstraps fresh."""
        conn = ConnectionManager(_FakeRuntime(tmp_path), lambda: None)
        await conn.try_bootstrap_admin()
        token_file = tmp_path / "admin_token.json"
        assert token_file.exists()

        await _expire_grace(conn)

        assert token_file.exists(), (
            "admin_token.json was deleted on grace expiry — a restart would "
            "hand admin to the first LAN client that asks (#725)"
        )

        # And a fresh manager (i.e. the next HA start) loads the same token.
        reloaded = ConnectionManager(_FakeRuntime(tmp_path), lambda: None)
        await reloaded.async_load_admin_token()
        assert reloaded._admin_session_token == conn._admin_session_token


class TestNoBootstrapWindowAfterGrace:
    @pytest.mark.asyncio
    async def test_lan_client_cannot_seize_admin_after_the_grace(
        self, tmp_path: Path
    ) -> None:
        """The security property itself: no free bootstrap once the host left.

        A stranger on the LAN opens ``/quizify/admin`` (no token) two minutes
        after the host closed their tab. Before the fix ``_grant_admin``
        returned True for them and ``_handle_admin_connect`` handed over the
        session token.
        """
        runtime = _FakeRuntime(tmp_path)
        handler = QuizifyWebSocketHandler(
            runtime=runtime, game_state_provider=lambda: None
        )
        handler._conn = ConnectionManager(runtime, lambda: None)
        handler._conn.broadcast = AsyncMock()
        handler._conn.send = AsyncMock()

        # The host bootstrapped admin at some earlier point.
        await handler._conn.try_bootstrap_admin()
        host_token = handler._conn._admin_session_token
        assert host_token is not None

        # The host's last admin socket closed and the grace ran out.
        await _expire_grace(handler._conn)

        # A different LAN address asks for admin without presenting a token.
        granted = await handler._grant_admin("admin", None, _request("192.168.1.99"))
        assert granted is False, (
            "the grace period re-opened the admin bootstrap window for any "
            "LAN client (#725)"
        )

        # The host's own tab still authenticates with the token it kept.
        assert (
            await handler._grant_admin("admin", host_token, _request()) is True
        )

    @pytest.mark.asyncio
    async def test_reset_service_is_still_the_way_out(self, tmp_path: Path) -> None:
        """Clearing the credential stays possible — deliberately, via HA.

        ``quizify.reset_admin_session`` (registered in ``__init__.py``) calls
        exactly this; it is the documented recovery for a host who lost their
        browser copy of the token.
        """
        conn = ConnectionManager(_FakeRuntime(tmp_path), lambda: None)
        await conn.try_bootstrap_admin()
        token_file = tmp_path / "admin_token.json"
        assert token_file.exists()

        await conn.async_clear_admin_token()

        assert conn._admin_session_token is None
        assert not token_file.exists()
        # …and the next admin connection may bootstrap again.
        assert await conn.try_bootstrap_admin() is True
