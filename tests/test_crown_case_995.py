"""The crown decision reads a name the way the registry does (#995).

``PlayerRegistry.get_player`` matches case-insensitively: a host stored as
``Host`` who reconnects and types ``HOST`` gets their own slot back. The crown
decision around that lookup compared case-sensitively — ``has_other_admin``
and the stale-admin comparison in ``_resolve_admin_claim`` — so the two
disagreed about who the person in front of them was. The host reclaimed their
own slot and the decision then read that same slot as "a different player":

* ``Admin claim rejected for HOST: a different player already holds the
  single admin slot`` — about the host's own slot;
* and with the #208 guard out of the way, the same mismatch sends the claim
  into the #358 crown-*transfer* gate, which denies the host the crown they
  are already holding ("stale admin Host keeps the crown", one slot, two
  names).

The crown value survived both of those by luck: the slot that is refused is
the slot that already wears the crown, so ``is_admin`` was never written.
That is exactly why this is worth pinning — the refusal is real, it is one
edit away from costing the host the crown, and the log said so every time.

What is deliberately NOT changed here: the #389 inherited-crown strip. It
carries no name comparison at all. A tokenless name-rejoin of the host's slot
loses the crown whatever case was typed, because nothing on that path
distinguishes the host from someone who guessed the host's name.
"""

from __future__ import annotations

import logging
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

_WS_LOGGER = "custom_components.quizify.server.websocket"


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


def _make_game(tmp_path: Path) -> QuizifyGameState:
    return QuizifyGameState(runtime=_FakeRuntime(tmp_path), entry_id="test")


async def _handler_with_token(game: QuizifyGameState):
    runtime = _FakeRuntime(game._runtime.data_dir)  # type: ignore[attr-defined]
    h = QuizifyWebSocketHandler(runtime=runtime, game_state_provider=lambda: game)
    h._conn = ConnectionManager(runtime, lambda: game)
    h._conn.broadcast = AsyncMock()
    h._conn.send = AsyncMock()
    await h._conn.try_bootstrap_admin()
    return h, h._conn._admin_session_token


def _seat_stale_admin(game: QuizifyGameState, name: str = "Host") -> None:
    """Seat an admin player, then mark it disconnected (reload/wifi blip)."""
    game.add_player(name)
    admin = game.get_player(name)
    admin.is_admin = True
    admin.connected = False


def _refusals(caplog: pytest.LogCaptureFixture) -> list[str]:
    """The crown decision's "you are not who you say you are" warnings."""
    return [
        r.getMessage()
        for r in caplog.records
        if r.name == _WS_LOGGER
        and (
            "Admin claim rejected" in r.getMessage()
            or "Crown transfer denied" in r.getMessage()
        )
    ]


class TestCrownIsCaseInsensitive995:
    @pytest.mark.asyncio
    async def test_case_variant_self_reclaim_keeps_the_crown(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        """The host types ``HOST``, gets the ``Host`` slot, keeps the crown.

        The host's admin-as-player tab always re-sends ``is_admin: true`` on
        join (player-core.js), so this is the shape the real reconnect has.
        """
        game = _make_game(tmp_path)
        h, _tok = await _handler_with_token(game)
        _seat_stale_admin(game, "Host")

        with caplog.at_level(logging.WARNING, logger=_WS_LOGGER):
            await h._handle_join(_ws(), {"name": "HOST", "is_admin": True}, game)

        host = game.get_player("HOST")
        assert host is not None
        assert host.connected is True, "the host's own slot was not reclaimed"
        assert host.is_admin is True
        # The decision recognised the host as themselves rather than refusing
        # them their own slot. This is what fails without the fix.
        assert _refusals(caplog) == []

    @pytest.mark.asyncio
    async def test_the_stored_spelling_is_untouched(self, tmp_path: Path) -> None:
        """Only the comparison folds case; nothing re-spells the slot (#603).

        The registry key stays the name the player first typed, so the
        session token issued under it and every later lookup still resolve.
        """
        game = _make_game(tmp_path)
        h, _tok = await _handler_with_token(game)
        _seat_stale_admin(game, "Host")

        await h._handle_join(_ws(), {"name": "HOST", "is_admin": True}, game)

        names = list(game._player_registry.players)  # noqa: SLF001
        assert names == ["Host"], "the crown decision re-spelled the slot"
        assert game.get_player("Host").name == "Host"

    @pytest.mark.asyncio
    async def test_a_different_player_still_cannot_inherit_the_crown(
        self, tmp_path: Path
    ) -> None:
        """#389 is untouched: a tokenless name-rejoin still loses the crown.

        The strip is the guard that makes the lobby's name-rejoin safe, and
        it must not soften because the comparison above it learned to fold
        case — nothing here can tell the host from a guest who typed the
        host's name.
        """
        game = _make_game(tmp_path)
        h, _tok = await _handler_with_token(game)
        _seat_stale_admin(game, "Host")

        # Plain player-join form: no ``is_admin`` claim, no token, and a
        # casing the stored slot does not use.
        await h._handle_join(_ws(), {"name": "HOST"}, game)

        host = game.get_player("Host")
        assert host.connected is True, "the slot was not reclaimed"
        assert host.is_admin is False

    @pytest.mark.asyncio
    async def test_a_different_name_still_cannot_seize_the_crown(
        self, tmp_path: Path
    ) -> None:
        """#358 is untouched: a genuinely different name needs a token."""
        game = _make_game(tmp_path)
        h, _tok = await _handler_with_token(game)
        _seat_stale_admin(game, "Host")

        await h._handle_join(_ws(), {"name": "Attacker", "is_admin": True}, game)

        assert game.get_player("Attacker").is_admin is False
        assert game.get_player("Host").is_admin is True

    @pytest.mark.asyncio
    async def test_a_connected_admin_still_blocks_a_second_claim(
        self, tmp_path: Path
    ) -> None:
        """#208 is untouched: the live host keeps the single admin slot."""
        game = _make_game(tmp_path)
        h, _tok = await _handler_with_token(game)
        game.add_player("Host")
        game.get_player("Host").is_admin = True  # connected

        await h._handle_join(_ws(), {"name": "Guest", "is_admin": True}, game)

        assert game.get_player("Guest").is_admin is False
        assert game.get_player("Host").is_admin is True

    def test_registry_reads_one_player_as_one_player(self, tmp_path: Path) -> None:
        """``has_other_admin`` now answers on the same fold as ``get_player``."""
        game = _make_game(tmp_path)
        game.add_player("Host")
        game.get_player("Host").is_admin = True

        assert game.get_player("HOST") is game.get_player("Host")
        assert game.has_other_admin("HOST") is False
        assert game.has_other_admin("Intruder") is True
