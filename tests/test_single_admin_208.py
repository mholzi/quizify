"""Regression test: issue #208 — two admins must never coexist in one game.

The bug (reproduced live during the v1.2.8 pre-release runthrough):
  - One player joins/becomes admin (e.g. "HostQA 👑").
  - From another context a second player joins with `is_admin: true`
    (e.g. "AdminQA"). The join handler unconditionally set
    `player_obj.is_admin = True`, so the lobby ended up with *two*
    crowned admins.

Expected single-admin invariant: at most one player carries the crown.
A second admin-claim by a *different* player while an admin already
exists is rejected — the original admin keeps the crown. A re-claim by
the *same* player (the admin's /admin→/player redirect re-joining under
the same name) stays idempotent and is still granted.
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


def _ws(closed: bool = False) -> MagicMock:
    ws = MagicMock()
    ws.closed = closed
    ws.send_json = AsyncMock()
    return ws


@pytest.fixture
def game(tmp_path: Path) -> QuizifyGameState:
    runtime = _FakeRuntime(tmp_path)
    return QuizifyGameState(runtime=runtime, entry_id="test")


@pytest.fixture
def handler(game: QuizifyGameState) -> QuizifyWebSocketHandler:
    runtime = _FakeRuntime(game._runtime.data_dir)  # type: ignore[attr-defined]
    h = QuizifyWebSocketHandler(
        runtime=runtime,
        game_state_provider=lambda: game,
    )
    h._conn = ConnectionManager(runtime, lambda: game)
    h._conn.broadcast = AsyncMock()
    return h


def _admins(game: QuizifyGameState) -> list[str]:
    return [p.name for p in game.get_players() if p.is_admin]


class TestSingleAdminInvariant:
    @pytest.mark.asyncio
    async def test_second_admin_claim_does_not_create_two_admins(
        self, handler: QuizifyWebSocketHandler, game: QuizifyGameState
    ) -> None:
        # First admin claims the crown.
        host_ws = _ws()
        await handler._handle_join(
            host_ws, {"name": "HostQA", "is_admin": True}, game
        )
        assert _admins(game) == ["HostQA"]

        # A different player tries to claim admin too.
        admin_ws = _ws()
        await handler._handle_join(
            admin_ws, {"name": "AdminQA", "is_admin": True}, game
        )

        # Exactly one admin — the original keeps the crown, the claim is rejected.
        assert _admins(game) == ["HostQA"]
        assert game.get_player("AdminQA") is not None
        assert game.get_player("AdminQA").is_admin is False

    @pytest.mark.asyncio
    async def test_same_player_readmin_is_idempotent(
        self, handler: QuizifyWebSocketHandler, game: QuizifyGameState
    ) -> None:
        # Admin self-joins, then re-joins under the same name (the
        # /admin → /player redirect path). Must stay admin, still single.
        ws1 = _ws()
        await handler._handle_join(ws1, {"name": "Host", "is_admin": True}, game)
        assert _admins(game) == ["Host"]

        ws2 = _ws()
        await handler._handle_join(ws2, {"name": "Host", "is_admin": True}, game)
        assert _admins(game) == ["Host"]

    @pytest.mark.asyncio
    async def test_non_admin_join_after_admin_stays_non_admin(
        self, handler: QuizifyWebSocketHandler, game: QuizifyGameState
    ) -> None:
        await handler._handle_join(_ws(), {"name": "Host", "is_admin": True}, game)
        await handler._handle_join(_ws(), {"name": "Bob"}, game)
        assert _admins(game) == ["Host"]
        assert game.get_player("Bob").is_admin is False

    def test_registry_has_other_admin_helper(
        self, game: QuizifyGameState
    ) -> None:
        game.add_player("Host", _ws())
        game.get_player("Host").is_admin = True
        # No other admin claim for the same name.
        assert game.has_other_admin("Host") is False
        # A different name would be blocked.
        assert game.has_other_admin("Intruder") is True
