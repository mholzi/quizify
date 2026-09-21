"""Regression tests for issue #389 (P2 security) — inherited crown seizure.

Follow-up to #358. #358 gated the crown *transfer* to a DIFFERENT name behind
a valid admin token, but only on the ``is_admin: true`` claim path. #389 is the
silent-inheritance vector it missed: in LOBBY a disconnected player's slot can
be reclaimed by simply re-typing the same name, with no session token
(PlayerRegistry.add_player, the ``phase_value == "LOBBY"`` reconnect branch). If
that slot was the host's, the reclaimer inherits ``is_admin`` — and because a
plain player-form join carries no ``is_admin: true`` claim, the #358 crown block
never runs to vet it. An attacker who types the host's exact name during the
host's lobby reload / wifi blip would seize control with no token.

The fix strips the inherited crown at the ``_handle_join`` layer unless the join
proves ownership with a valid admin session token. It FAILS SOFT (never rejects
the join), consistent with #358. The legit host's admin-as-player tab always
re-sends ``is_admin: true`` on join (player-core.js), so it takes the #358 path
and keeps the crown; a token holder keeps it on this path too.
"""

from __future__ import annotations

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
from custom_components.quizify.server.connection import ConnectionManager  # noqa: E402
from custom_components.quizify.server.websocket import (  # noqa: E402
    QuizifyWebSocketHandler,
)


class _FakeRuntime:
    def __init__(self, tmp_path: Path) -> None:
        self.data_dir = tmp_path

    async def run_in_executor(self, func, *args):  # noqa: ANN001, ANN002
        return func(*args)


def _ws() -> MagicMock:
    ws = MagicMock()
    ws.closed = False
    ws.send_json = AsyncMock()
    return ws


async def _handler_with_token(game: QuizifyGameState):
    runtime = _FakeRuntime(game._runtime.data_dir)  # type: ignore[attr-defined]
    h = QuizifyWebSocketHandler(runtime=runtime, game_state_provider=lambda: game)
    h._conn = ConnectionManager(runtime, lambda: game)
    h._conn.broadcast = AsyncMock()
    h._conn.send = AsyncMock()
    await h._conn.try_bootstrap_admin()
    return h, h._conn._admin_session_token


def _make_game(tmp_path: Path) -> QuizifyGameState:
    return QuizifyGameState(runtime=_FakeRuntime(tmp_path), entry_id="test")


def _seat_stale_admin(game: QuizifyGameState, name: str = "Host") -> None:
    """Seat an admin player, then mark it disconnected (reload/wifi blip)."""
    game.add_player(name)
    admin = game.get_player(name)
    admin.is_admin = True
    admin.connected = False


class TestLobbyCrownInherit389:
    @pytest.mark.asyncio
    async def test_tokenless_name_rejoin_does_not_inherit_crown(
        self, tmp_path: Path
    ) -> None:
        # (a) Attacker re-joins the admin's EXACT name in LOBBY via the plain
        # player-join form (no ``is_admin`` claim, no token). The slot is
        # reclaimed but the inherited crown is stripped.
        game = _make_game(tmp_path)
        h, _tok = await _handler_with_token(game)
        _seat_stale_admin(game, "Host")

        await h._handle_join(_ws(), {"name": "Host"}, game)

        assert game.get_player("Host") is not None
        assert game.get_player("Host").connected is True
        assert game.get_player("Host").is_admin is False

    @pytest.mark.asyncio
    async def test_valid_token_name_rejoin_keeps_crown(
        self, tmp_path: Path
    ) -> None:
        # (b) The legit host re-joins their name WITH a valid admin token —
        # even on the token-less-claim (no ``is_admin``) path — and keeps the
        # crown.
        game = _make_game(tmp_path)
        h, token = await _handler_with_token(game)
        _seat_stale_admin(game, "Host")

        await h._handle_join(
            _ws(), {"name": "Host", "admin_token": token}, game
        )

        assert game.get_player("Host").is_admin is True

    @pytest.mark.asyncio
    async def test_bogus_token_name_rejoin_does_not_inherit_crown(
        self, tmp_path: Path
    ) -> None:
        # A wrong token is no proof — the inherited crown is still stripped.
        game = _make_game(tmp_path)
        h, _tok = await _handler_with_token(game)
        _seat_stale_admin(game, "Host")

        await h._handle_join(
            _ws(), {"name": "Host", "admin_token": "bogus"}, game
        )

        assert game.get_player("Host").is_admin is False

    @pytest.mark.asyncio
    async def test_normal_name_rejoin_unaffected(self, tmp_path: Path) -> None:
        # (c) A normal (non-admin) name rejoin in LOBBY is untouched: the slot
        # reconnects and stays a plain player.
        game = _make_game(tmp_path)
        h, _tok = await _handler_with_token(game)
        game.add_player("Alice")
        game.get_player("Alice").connected = False  # disconnected, non-admin

        await h._handle_join(_ws(), {"name": "Alice"}, game)

        assert game.get_player("Alice").connected is True
        assert game.get_player("Alice").is_admin is False

    @pytest.mark.asyncio
    async def test_legit_admin_selfjoin_redirect_keeps_crown(
        self, tmp_path: Path
    ) -> None:
        # Belt-and-suspenders: the admin-as-player redirect that DOES send
        # ``is_admin: true`` under the same name still keeps the crown via the
        # #358 path — the #389 strip must not fire for it.
        game = _make_game(tmp_path)
        h, _tok = await _handler_with_token(game)
        _seat_stale_admin(game, "Host")

        await h._handle_join(
            _ws(), {"name": "Host", "is_admin": True}, game
        )

        assert game.get_player("Host").is_admin is True

    @pytest.mark.asyncio
    async def test_ghost_reclaim_mid_game_does_not_inherit_crown(
        self, tmp_path: Path
    ) -> None:
        """The strip is not LOBBY-only (#985).

        Every other test here runs in the lobby, because that is the phase
        ``add_player`` lets a name-rejoin through. It is not the only one: a
        slot whose transport is already dead is reaped by ``_handle_join``
        and reclaimed via ``reclaim_by_name`` in ANY phase (#448/#646). So
        the host's ghost, mid-game, is reclaimable by name alone — and the
        reclaimer inherits ``is_admin`` with no ``is_admin: true`` claim to
        route the join through the #358 token gate.

        Pinned here because the branch reads as a lobby rule (its comment
        says "LOBBY name-rejoin") while it in fact guards the ghost path too;
        narrowing it to ``phase == LOBBY`` would reopen #389 mid-game.
        """
        game = _make_game(tmp_path)
        h, _tok = await _handler_with_token(game)

        host_ws = _ws()
        game.add_player("Host", h._conn.connection_id(host_ws))
        game.get_player("Host").is_admin = True

        game.start_game(language="de", num_rounds=3, difficulty="easy")
        game.start_next_question()
        assert game.phase != GamePhase.LOBBY

        # The host's socket died without a disconnect: the slot still reads
        # connected, so the join reaps it and treats the collision as a
        # reclaim rather than refusing with ERR_NAME_TAKEN.
        host_ws.closed = True

        await h._handle_join(_ws(), {"name": "Host"}, game)

        host = game.get_player("Host")
        assert host.connected is True, "the ghost slot was not reclaimed"
        assert host.is_admin is False
