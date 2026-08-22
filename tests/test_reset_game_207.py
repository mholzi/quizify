"""Regression tests for issue #207 — reset_game must fully clear the
session AND actually deliver the reset signal to every connected client.

The pre-#207 bug: ``_handle_reset_game`` closed all player WebSockets
*before* broadcasting ``game_reset`` / ``game_state``. The host who
pressed reset is frequently an admin-as-player (joined the lobby on the
admin WS), so their own socket was closed first and the reset broadcast
never reached them — their UI stayed frozen on the stale lobby with the
old roster, even though the server had already wiped everything.

These tests pin:
  1. State-level: reset drops every player (incl. the admin) and returns
     the phase to LOBBY.
  2. Delivery-level: ``game_reset`` + ``game_state`` are broadcast while
     the player sockets are STILL OPEN, so real clients (admin + players)
     receive the signal and can return to their initial views.
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


def _ws() -> MagicMock:
    """A fake WebSocket whose .close() flips .closed to True, mirroring
    aiohttp behaviour so the broadcast skip-closed filter sees it close."""
    ws = MagicMock()
    ws.closed = False
    ws.send_json = AsyncMock()

    async def _close() -> None:
        ws.closed = True

    ws.close = AsyncMock(side_effect=_close)
    return ws


@pytest.fixture
def game(tmp_path: Path) -> QuizifyGameState:
    runtime = _FakeRuntime(tmp_path)
    return QuizifyGameState(runtime=runtime, entry_id="test")


@pytest.fixture
def handler(game: QuizifyGameState, tmp_path: Path):
    """Handler with a REAL ConnectionManager (so add_connection / the
    skip-closed broadcast filter run for real) but a broadcast recorder
    that snapshots *which still-open sockets* each message reaches."""
    runtime = _FakeRuntime(tmp_path)
    h = QuizifyWebSocketHandler(runtime=runtime, game_state_provider=lambda: game)
    h._conn = ConnectionManager(runtime, lambda: game)

    delivered: dict[int, list[str]] = {}

    async def _record(message: dict) -> None:
        msg_type = message.get("type")
        for ws in list(h._conn.connections):
            if not ws.closed:
                delivered.setdefault(id(ws), []).append(msg_type)

    h._conn.broadcast = _record  # type: ignore[assignment]
    h._delivered = delivered  # type: ignore[attr-defined]
    return h


@pytest.mark.asyncio
async def test_reset_clears_players_admin_and_phase(
    handler: QuizifyWebSocketHandler, game: QuizifyGameState
) -> None:
    """State invariant: after reset the registry is empty (admin included)
    and the phase is back at the initial LOBBY."""
    admin_ws = _ws()
    handler._conn.add_connection(admin_ws, is_admin=True, is_dashboard=False)
    game.add_player("Admin", admin_ws)
    game.get_player("Admin").is_admin = True
    game.add_player("Bob", _ws())
    game.add_player("Eve", _ws())
    game.start_game(language="de", num_rounds=3, difficulty="easy")
    game.start_next_question()
    assert game.phase == GamePhase.QUESTION_ACTIVE
    assert len(game.get_players()) == 3

    await handler._handle_reset_game(admin_ws, game)

    assert game.phase == GamePhase.LOBBY
    assert game.get_players() == []
    assert game.get_player("Admin") is None

    snapshot = game.get_state_snapshot()
    assert snapshot["phase"] == "LOBBY"
    assert snapshot["players"] == []


@pytest.mark.asyncio
async def test_reset_signal_reaches_admin_as_player_before_socket_closes(
    handler: QuizifyWebSocketHandler, game: QuizifyGameState
) -> None:
    """The #207 regression. The host is an admin-as-player in the lobby:
    their player slot lives on the same WS as their admin connection.
    Reset MUST broadcast game_reset + game_state to that socket while it
    is still open — otherwise the admin tab never returns to setup."""
    admin_ws = _ws()
    handler._conn.add_connection(admin_ws, is_admin=True, is_dashboard=False)
    game.add_player("Admin", admin_ws)
    game.get_player("Admin").is_admin = True

    bob_ws = _ws()
    handler._conn.add_connection(bob_ws, is_admin=False, is_dashboard=False)
    game.add_player("Bob", bob_ws)

    assert game.phase == GamePhase.LOBBY

    await handler._handle_reset_game(admin_ws, game)

    delivered = handler._delivered  # type: ignore[attr-defined]
    # Both the admin-as-player tab AND the player phone must have received
    # the reset signal followed by the fresh (empty) game_state.
    assert delivered.get(id(admin_ws)) == ["game_reset", "game_state"], (
        "admin-as-player did not receive the reset broadcast before its "
        "socket was closed (issue #207)"
    )
    assert delivered.get(id(bob_ws)) == ["game_reset", "game_state"], (
        "player phone did not receive the reset broadcast (issue #207)"
    )
    # Stale sockets are still ultimately closed (cleanup of dead phones).
    assert admin_ws.close.await_count == 1
    assert bob_ws.close.await_count == 1


@pytest.mark.asyncio
async def test_reset_during_active_game_delivers_to_all_players(
    handler: QuizifyWebSocketHandler, game: QuizifyGameState
) -> None:
    """Reset mid-game (QUESTION_ACTIVE) must still deliver the reset to
    every connected player so their phones leave the question view."""
    admin_ws = _ws()
    handler._conn.add_connection(admin_ws, is_admin=True, is_dashboard=False)
    game.add_player("Admin", admin_ws)
    game.get_player("Admin").is_admin = True

    p1_ws = _ws()
    p2_ws = _ws()
    handler._conn.add_connection(p1_ws, is_admin=False, is_dashboard=False)
    handler._conn.add_connection(p2_ws, is_admin=False, is_dashboard=False)
    game.add_player("P1", p1_ws)
    game.add_player("P2", p2_ws)
    game.start_game(language="de", num_rounds=3, difficulty="easy")
    game.start_next_question()
    assert game.phase == GamePhase.QUESTION_ACTIVE

    await handler._handle_reset_game(admin_ws, game)

    delivered = handler._delivered  # type: ignore[attr-defined]
    for ws in (admin_ws, p1_ws, p2_ws):
        assert delivered.get(id(ws)) == ["game_reset", "game_state"]
    assert game.phase == GamePhase.LOBBY
    assert game.get_players() == []


# ---------------------------------------------------------------------------
# Auth-path regression (#207 reopened): the original tests above call
# ``_handle_reset_game`` DIRECTLY, so they never exercised the admin-only
# auth guard in ``_handle_message`` — which is exactly where the reopened
# bug lived. Pressing reset routed through ``reset_game`` → the guard
# rejected the legitimate host → the client silently swallowed the
# refusal → nothing happened server-side ("no reset processing
# at all"). Root cause: the #209 single-admin invariant compared admin
# slots by NAME only, so when the host's /admin → /player redirect
# re-joined under a disambiguated name ("Host 2") while the stale "Host"
# admin slot lingered, the host's crown claim was rejected; once the stale
# slot was pruned NOBODY held the crown and every admin-only action failed.
# These tests drive the real dispatch + auth path.
# ---------------------------------------------------------------------------


def _auth_handler(game: QuizifyGameState, tmp_path: Path) -> QuizifyWebSocketHandler:
    """Handler whose broadcast is a no-op recorder — we only assert on
    server-side STATE changes here, driving the real ``_handle_message``
    dispatch + auth guard (not the broadcast-ordering of the tests above)."""
    runtime = _FakeRuntime(tmp_path)
    h = QuizifyWebSocketHandler(runtime=runtime, game_state_provider=lambda: game)
    h._conn = ConnectionManager(runtime, lambda: game)
    h._get_game_state = lambda: game  # type: ignore[assignment]

    sent: dict[int, list[dict]] = {}

    async def _noop_broadcast(message: dict) -> None:
        return None

    async def _send(ws, message: dict) -> None:
        sent.setdefault(id(ws), []).append(message)

    h._conn.broadcast = _noop_broadcast  # type: ignore[assignment]
    h._conn.send = _send  # type: ignore[assignment]
    h._sent = sent  # type: ignore[attr-defined]
    return h


@pytest.mark.asyncio
async def test_legit_admin_reset_authorized_and_clears(
    game: QuizifyGameState, tmp_path: Path
) -> None:
    """A legitimate admin's reset_game (routed through _handle_message) is
    authorized, wipes all players incl. the admin, and returns to LOBBY."""
    h = _auth_handler(game, tmp_path)
    admin_ws = _ws()
    h._conn.add_connection(admin_ws, is_admin=True, is_dashboard=False)
    game.add_player("Host", admin_ws)
    game.get_player("Host").is_admin = True
    game.add_player("Bob", _ws())
    game.start_game(language="de", num_rounds=3, difficulty="easy")
    game.start_next_question()
    assert game.phase == GamePhase.QUESTION_ACTIVE

    # WS-level admin tab.
    await h._handle_message(admin_ws, {"type": "reset_game"}, is_admin=True)

    assert game.phase == GamePhase.LOBBY
    assert game.get_players() == []
    # No refusal was sent to the host.
    sent = h._sent  # type: ignore[attr-defined]
    errs = [m for m in sent.get(id(admin_ws), []) if m.get("type") == "error"]
    assert errs == []


@pytest.mark.asyncio
async def test_orphaned_crown_host_reset_authorized(
    game: QuizifyGameState, tmp_path: Path
) -> None:
    """The reopened #207 scenario: the legitimate host is on a player WS
    (post /admin → /player redirect) with is_admin False, and NO connected
    admin holds the crown. Reset MUST still be authorized (escape hatch) so
    the host can recover — previously it was silently rejected."""
    h = _auth_handler(game, tmp_path)
    host_ws = _ws()  # player-role WS, NOT a WS-level admin
    h._conn.add_connection(host_ws, is_admin=False, is_dashboard=False)
    game.add_player("Host 2", host_ws)  # disambiguated, non-admin slot
    game.add_player("Bob", _ws())
    assert game.get_admin() is None  # crown is orphaned

    await h._handle_message(host_ws, {"type": "reset_game"}, is_admin=False)

    sent = h._sent  # type: ignore[attr-defined]
    errs = [m for m in sent.get(id(host_ws), []) if m.get("type") == "error"]
    assert errs == [], "orphaned-crown host was wrongly rejected (#207)"
    assert game.phase == GamePhase.LOBBY
    assert game.get_players() == []


@pytest.mark.asyncio
async def test_non_admin_reset_rejected_while_live_admin_present(
    game: QuizifyGameState, tmp_path: Path
) -> None:
    """A non-admin player cannot reset while a CONNECTED admin holds the
    crown — the escape hatch only opens when the crown is orphaned."""
    h = _auth_handler(game, tmp_path)
    admin_ws = _ws()
    h._conn.add_connection(admin_ws, is_admin=True, is_dashboard=False)
    game.add_player("Host", admin_ws)
    game.get_player("Host").is_admin = True

    rogue_ws = _ws()
    h._conn.add_connection(rogue_ws, is_admin=False, is_dashboard=False)
    game.add_player("Rogue", rogue_ws)

    await h._handle_message(rogue_ws, {"type": "reset_game"}, is_admin=False)

    sent = h._sent  # type: ignore[attr-defined]
    errs = [m for m in sent.get(id(rogue_ws), []) if m.get("type") == "error"]
    assert errs and errs[0]["code"] == "ADMIN_REQUIRED"
    # Game untouched: both players still present, crown still on the host.
    assert len(game.get_players()) == 2
    assert game.get_admin() is not None and game.get_admin().name == "Host"


@pytest.mark.asyncio
async def test_host_reclaims_crown_from_stale_slot_on_rejoin(
    game: QuizifyGameState, tmp_path: Path
) -> None:
    """#209 crown-recovery: when the host re-joins under a disambiguated
    name while the stale (disconnected) old admin slot lingers, the host
    must (re)acquire the single admin crown — and the stale slot must be
    demoted so exactly one admin exists (single-admin invariant #208).

    #358: a crown transfer to a *different* name now requires the admin
    session token (which the real host holds and the frontend sends), so the
    attacker path is closed. The legit host carries it here."""
    h = _auth_handler(game, tmp_path)
    # Seed the persisted admin token directly (avoids the executor-backed
    # store); the real host holds this and the frontend now sends it (#358).
    token = "recovery-token"
    h._conn._admin_session_token = token

    # Old admin slot (the /quizify/admin tab joined-as-player), now stale:
    old_ws = _ws()
    game.add_player("Host", old_ws)
    game.get_player("Host").is_admin = True
    game.get_player("Host").connected = False  # admin WS closing on redirect

    # Host re-joins on a fresh player WS; the lingering "Host" slot forces
    # the disambiguated "Host 2" name, carrying is_admin: true + the token.
    new_ws = _ws()
    h._conn.add_connection(new_ws, is_admin=False, is_dashboard=False)
    await h._handle_message(
        new_ws,
        {
            "type": "join",
            "name": "Host 2",
            "is_admin": True,
            "admin_token": token,
        },
        is_admin=False,
    )

    new_player = game.get_player("Host 2")
    assert new_player is not None
    assert new_player.is_admin, "host failed to reclaim crown (#207/#209)"
    # Single-admin invariant: stale slot demoted, exactly one admin.
    admins = [p for p in game.get_players() if p.is_admin]
    assert len(admins) == 1
    assert admins[0].name == "Host 2"


@pytest.mark.asyncio
async def test_live_admin_blocks_second_admin_claim(
    game: QuizifyGameState, tmp_path: Path
) -> None:
    """Single-admin invariant (#208) still holds: a DIFFERENT player cannot
    seize the crown while a CONNECTED admin already holds it."""
    h = _auth_handler(game, tmp_path)
    admin_ws = _ws()
    game.add_player("Host", admin_ws)
    game.get_player("Host").is_admin = True  # connected admin

    rogue_ws = _ws()
    h._conn.add_connection(rogue_ws, is_admin=False, is_dashboard=False)
    await h._handle_message(
        rogue_ws, {"type": "join", "name": "Rogue", "is_admin": True}, is_admin=False
    )

    assert game.get_player("Rogue") is not None
    assert not game.get_player("Rogue").is_admin, "second admin was wrongly granted"
    admins = [p for p in game.get_players() if p.is_admin]
    assert len(admins) == 1 and admins[0].name == "Host"
