"""Regression tests for issue #358 (P2 security) — crown seizure.

During the host's reload / wifi blip the real admin is momentarily
disconnected, so the single-admin guard (``has_other_admin`` counts only
*connected* admins) doesn't block a claim. The #207 crown-recovery branch then
demoted the stale admin and crowned the claimer — so any LAN client sending
``join {is_admin: true}`` under a new name could seize control mid-game.

The crown *transfer* from a stale admin to a different name now requires a
valid admin session token in the join, and fails SOFT (no error, no crown)
without one. First claims (no admin yet) and same-name reclaims stay
token-free (the documented Beatify trust trade-off).
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT))

from custom_components.quizify.game.state import QuizifyGameState  # noqa: E402
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
    game.add_player(name, _ws())
    admin = game.get_player(name)
    admin.is_admin = True
    admin.connected = False


class TestCrownSeizure358:
    @pytest.mark.asyncio
    async def test_no_token_cannot_seize_from_stale_admin(
        self, tmp_path: Path
    ) -> None:
        game = _make_game(tmp_path)
        h, _tok = await _handler_with_token(game)
        _seat_stale_admin(game)

        # Attacker joins under a NEW name claiming admin, WITHOUT a token.
        await h._handle_join(_ws(), {"name": "Attacker", "is_admin": True}, game)

        assert game.get_player("Attacker") is not None
        assert game.get_player("Attacker").is_admin is False
        # The stale host keeps the crown.
        assert game.get_player("Host").is_admin is True

    @pytest.mark.asyncio
    async def test_wrong_token_cannot_seize(self, tmp_path: Path) -> None:
        game = _make_game(tmp_path)
        h, _tok = await _handler_with_token(game)
        _seat_stale_admin(game)

        await h._handle_join(
            _ws(),
            {"name": "Attacker", "is_admin": True, "admin_token": "bogus"},
            game,
        )
        assert game.get_player("Attacker").is_admin is False
        assert game.get_player("Host").is_admin is True

    @pytest.mark.asyncio
    async def test_valid_token_transfers_crown(self, tmp_path: Path) -> None:
        game = _make_game(tmp_path)
        h, token = await _handler_with_token(game)
        _seat_stale_admin(game)

        # The legit host re-joins under a new name WITH the admin token.
        await h._handle_join(
            _ws(),
            {"name": "Host-again", "is_admin": True, "admin_token": token},
            game,
        )
        assert game.get_player("Host-again").is_admin is True
        assert game.get_player("Host").is_admin is False

    @pytest.mark.asyncio
    async def test_first_claim_needs_no_token(self, tmp_path: Path) -> None:
        # No admin exists yet → first claim is granted token-free (Beatify
        # trust trade-off, unchanged).
        game = _make_game(tmp_path)
        h, _tok = await _handler_with_token(game)

        await h._handle_join(_ws(), {"name": "Host", "is_admin": True}, game)
        assert game.get_player("Host").is_admin is True

    @pytest.mark.asyncio
    async def test_same_name_reclaim_needs_no_token(self, tmp_path: Path) -> None:
        # The admin's /admin -> /player redirect re-joining under the SAME name
        # is idempotent and stays token-free.
        game = _make_game(tmp_path)
        h, _tok = await _handler_with_token(game)
        _seat_stale_admin(game, "Host")

        await h._handle_join(_ws(), {"name": "Host", "is_admin": True}, game)
        assert game.get_player("Host").is_admin is True

    @pytest.mark.asyncio
    async def test_connected_admin_still_blocks_claim(
        self, tmp_path: Path
    ) -> None:
        # #208 invariant unchanged: a *connected* admin blocks any other claim,
        # token or not.
        game = _make_game(tmp_path)
        h, token = await _handler_with_token(game)
        game.add_player("Host", _ws())
        game.get_player("Host").is_admin = True  # connected admin

        await h._handle_join(
            _ws(),
            {"name": "Attacker", "is_admin": True, "admin_token": token},
            game,
        )
        assert game.get_player("Attacker").is_admin is False
        assert game.get_player("Host").is_admin is True
