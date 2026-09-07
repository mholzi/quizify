"""The game layer keeps no socket any more (#882).

``PlayerSession`` used to hold the live ``aiohttp.web.WebSocketResponse``, and
three separate decisions read ``ws.closed`` through it: whether a player counts
as active, whether a name-collision is a reload or a stranger, and whether the
all-submitted early reveal may fire. That put the transport inside the game
model — the one thing ``game/drivers/protocols.py`` says must never happen —
and it is why nearly every test in this suite carried a ``.closed``-bearing
mock just to drive pure scoring logic.

The registry is now keyed by an opaque ``connection_id``; the id → socket map
lives only in ``ConnectionManager``, and ``connected`` is a plain flag the
server layer sets. What is pinned here is that the behaviour which depended on
the old arrangement still holds:

* a reconnect maps back to the *same* player, score and all;
* a disconnect marks that player not connected;
* a socket that dies without a disconnect is still spotted — by the server
  layer asking the connection manager, not by the game model asking the socket.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT))

from custom_components.quizify.game.player import PlayerSession  # noqa: E402
from custom_components.quizify.game.player_registry import (  # noqa: E402
    PlayerRegistry,
)
from custom_components.quizify.game.state import (  # noqa: E402
    GamePhase,
    QuizifyGameState,
)
from custom_components.quizify.server.connection import (  # noqa: E402
    ConnectionManager,
)
from custom_components.quizify.server.websocket import (  # noqa: E402
    QuizifyWebSocketHandler,
)

_GAME_DIR = _REPO_ROOT / "custom_components" / "quizify" / "game"


class _FakeRuntime:
    def __init__(self, tmp_path: Path) -> None:
        self.data_dir = tmp_path

    def create_task(self, coro):  # noqa: ANN001, ANN202
        import asyncio  # noqa: PLC0415

        return asyncio.ensure_future(coro)

    async def run_in_executor(self, func, *args):  # noqa: ANN001, ANN002, ANN202
        return func(*args)


def _ws(*, closed: bool = False) -> MagicMock:
    ws = MagicMock()
    ws.closed = closed
    ws.send_json = AsyncMock()
    ws.send_str = AsyncMock()
    ws.close = AsyncMock()
    return ws


@pytest.fixture
def game(tmp_path: Path) -> QuizifyGameState:
    return QuizifyGameState(runtime=_FakeRuntime(tmp_path), entry_id="test")


@pytest.fixture
def handler(game: QuizifyGameState) -> QuizifyWebSocketHandler:
    runtime = _FakeRuntime(game._runtime.data_dir)  # type: ignore[attr-defined]
    h = QuizifyWebSocketHandler(runtime=runtime, game_state_provider=lambda: game)
    h._conn = ConnectionManager(runtime, lambda: game)
    h._conn.broadcast = AsyncMock()  # type: ignore[method-assign]
    h._conn.broadcast_to_admins_and_dashboards = AsyncMock()  # type: ignore[method-assign]
    return h


def _connect(handler: QuizifyWebSocketHandler, *, admin: bool = False) -> MagicMock:
    ws = _ws()
    handler._conn.add_connection(ws, is_admin=admin, is_dashboard=False)
    return ws


# ---------------------------------------------------------------------------
# The boundary itself
# ---------------------------------------------------------------------------


def test_the_game_package_imports_no_transport() -> None:
    """The rule ``game/drivers/protocols.py`` states, now actually true.

    Before #882 three modules under ``game/`` imported ``aiohttp`` and typed on
    ``web.WebSocketResponse``. Nothing enforced the rule the drivers were
    designed around, so it drifted.
    """
    imports_transport = re.compile(
        r"^\s*(?:from|import)\s+aiohttp\b|web\.WebSocketResponse", re.MULTILINE
    )
    offenders = {
        path.relative_to(_REPO_ROOT).as_posix()
        for path in _GAME_DIR.rglob("*.py")
        if imports_transport.search(path.read_text("utf-8"))
    }
    assert offenders == set(), (
        f"game/ imports the transport again: {sorted(offenders)}"
    )


def test_a_player_exists_without_a_socket() -> None:
    """A ``PlayerSession`` is pure game state.

    This is the line 97 test files used to pay for: building a player meant
    building a socket mock with a ``.closed`` attribute first.
    """
    player = PlayerSession(name="Alice")

    assert player.connection_id is None
    assert player.connected is True
    assert player.is_active is True


def test_the_registry_is_keyed_by_the_connection_not_the_socket() -> None:
    registry = PlayerRegistry()
    ok, err = registry.add_player("Alice", "conn-1", "LOBBY", lambda: 0)
    assert ok and err is None

    assert registry.get_player_by_connection("conn-1") is registry.players["Alice"]
    assert registry.get_player_by_connection("conn-unknown") is None
    assert registry.get_player_by_connection(None) is None


def test_removing_a_player_drops_the_connection_index() -> None:
    """No stale key can point at a player who left."""
    registry = PlayerRegistry()
    registry.add_player("Alice", "conn-1", "LOBBY", lambda: 0)
    registry.remove_player("Alice")

    assert registry.get_player_by_connection("conn-1") is None


# ---------------------------------------------------------------------------
# Reconnect: the same player comes back
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_reconnect_maps_back_to_the_same_player(
    handler: QuizifyWebSocketHandler, game: QuizifyGameState
) -> None:
    """A token reconnect on a NEW socket must land on the SAME session.

    Not a copy with the same name — the same object, with the score it earned.
    The old handle must stop resolving, or the registry would answer two
    sockets with one player and the next disconnect would take the live one
    down.
    """
    first_ws = _connect(handler)
    await handler._handle_join(first_ws, {"name": "Alice"}, game)

    alice = game.get_player("Alice")
    alice.score = 42
    first_id = alice.connection_id
    assert game.get_player_by_connection(first_id) is alice

    token = handler._conn.create_session_token("Alice")
    await handler._handle_disconnect(first_ws, was_admin=False)
    assert alice.connected is False

    second_ws = _connect(handler)
    await handler._handle_reconnect(second_ws, {"session_token": token}, game)

    returned = game.get_player("Alice")
    assert returned is alice, "the reconnect built a new session"
    assert returned.score == 42
    assert returned.connected is True

    second_id = returned.connection_id
    assert second_id != first_id
    assert game.get_player_by_connection(second_id) is alice
    assert game.get_player_by_connection(first_id) is None, (
        "the abandoned handle still resolves to a live player"
    )
    assert handler._conn.socket_for(second_id) is second_ws


@pytest.mark.asyncio
async def test_a_message_on_the_new_socket_is_attributed_to_that_player(
    handler: QuizifyWebSocketHandler, game: QuizifyGameState
) -> None:
    """The reconnect is only real if the next tap scores for the right person."""
    first_ws = _connect(handler)
    await handler._handle_join(first_ws, {"name": "Alice"}, game)
    token = handler._conn.create_session_token("Alice")
    await handler._handle_disconnect(first_ws, was_admin=False)

    second_ws = _connect(handler)
    await handler._handle_reconnect(second_ws, {"session_token": token}, game)

    game.start_game(language="de", num_rounds=3, difficulty="easy")
    game.start_next_question()
    question = game.get_current_question()
    shuffle = game.ensure_player_shuffle("Alice")
    correct = next(i for i, a in enumerate(question.answers) if a.correct)
    tapped = shuffle.index(correct)

    await handler._handle_submit_answer(
        second_ws, {"answer_index": tapped}, game
    )

    assert game.get_player("Alice").submitted is True
    assert game.get_player("Alice").score > 0


# ---------------------------------------------------------------------------
# Disconnect: the flag the server layer now owns
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_disconnect_marks_the_player_not_connected(
    handler: QuizifyWebSocketHandler, game: QuizifyGameState
) -> None:
    """``connected`` is the single source of truth, so it has to be set.

    It used to be double-checked against ``ws.closed``, which quietly covered
    for anyone who forgot to clear it. Nothing covers for that now.
    """
    ws = _connect(handler)
    await handler._handle_join(ws, {"name": "Alice"}, game)
    alice = game.get_player("Alice")
    assert alice.connected is True
    assert alice.is_active is True

    await handler._handle_disconnect(ws, was_admin=False)

    assert alice.connected is False
    assert alice.is_active is False
    # The row survives for the roster and the grace period — only the flag moved.
    assert game.get_player("Alice") is alice


@pytest.mark.asyncio
async def test_a_disconnect_leaves_no_other_player_marked_down(
    handler: QuizifyWebSocketHandler, game: QuizifyGameState
) -> None:
    """One socket closing must take exactly one player with it."""
    alice_ws = _connect(handler)
    bob_ws = _connect(handler)
    await handler._handle_join(alice_ws, {"name": "Alice"}, game)
    await handler._handle_join(bob_ws, {"name": "Bob"}, game)

    await handler._handle_disconnect(alice_ws, was_admin=False)

    assert game.get_player("Alice").connected is False
    assert game.get_player("Bob").connected is True


# ---------------------------------------------------------------------------
# The ghost: a socket that died without a disconnect
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_reload_reclaims_the_slot_instead_of_spawning_a_ghost(
    handler: QuizifyWebSocketHandler, game: QuizifyGameState
) -> None:
    """#448/#646, preserved across the move.

    The phone reloads: its new socket sends ``join`` before aiohttp has run the
    old socket's ``finally``. The old slot still says ``connected = True``. The
    registry can no longer look at the socket to tell, so the server layer
    reaps it first and reports the collision as a reclaim.
    """
    old_ws = _connect(handler)
    await handler._handle_join(old_ws, {"name": "Alice"}, game)
    alice = game.get_player("Alice")
    alice.score = 17

    old_ws.closed = True  # dead transport, disconnect handler not run yet
    assert alice.connected is True

    fresh_ws = _connect(handler)
    await handler._handle_join(fresh_ws, {"name": "Alice"}, game)

    names = [p.name for p in game.get_players()]
    assert names == ["Alice"], f"a ghost was spawned: {names}"
    assert game.get_player("Alice") is alice
    assert alice.score == 17
    assert alice.connected is True
    assert handler._conn.socket_for(alice.connection_id) is fresh_ws


@pytest.mark.asyncio
async def test_a_live_duplicate_is_still_renamed(
    handler: QuizifyWebSocketHandler, game: QuizifyGameState
) -> None:
    """The other half: two real people, one name, two open sockets."""
    first_ws = _connect(handler)
    await handler._handle_join(first_ws, {"name": "Charlie"}, game)

    second_ws = _connect(handler)
    await handler._handle_join(second_ws, {"name": "Charlie"}, game)

    names = sorted(p.name for p in game.get_players())
    assert names == ["Charlie", "Charlie 2"]


@pytest.mark.asyncio
async def test_a_ghost_does_not_hold_the_room_on_the_full_timer(
    handler: QuizifyWebSocketHandler, game: QuizifyGameState
) -> None:
    """The all-submitted early reveal, which ``is_active`` guarded before.

    Bob's phone is gone but his disconnect has not landed. Alice answers. The
    round must reveal rather than wait out Bob's clock.
    """
    alice_ws = _connect(handler)
    bob_ws = _connect(handler)
    await handler._handle_join(alice_ws, {"name": "Alice"}, game)
    await handler._handle_join(bob_ws, {"name": "Bob"}, game)
    game.start_game(language="de", num_rounds=3, difficulty="easy")
    game.start_next_question()

    bob_ws.closed = True  # dead, disconnect handler not run yet

    question = game.get_current_question()
    shuffle = game.ensure_player_shuffle("Alice")
    correct = next(i for i, a in enumerate(question.answers) if a.correct)
    await handler._handle_submit_answer(
        alice_ws, {"answer_index": shuffle.index(correct)}, game
    )

    assert game.get_player("Bob").connected is False
    assert game.phase == GamePhase.ANSWER_REVEAL


def test_reaping_never_guesses_about_a_connection_it_cannot_see(
    handler: QuizifyWebSocketHandler, game: QuizifyGameState
) -> None:
    """An unknown handle is unknown, not dead.

    A player restored from a snapshot, or built by a test, has no socket at
    all. Treating "no socket" as "socket closed" would mark the whole room
    disconnected the first time anyone sent a message.
    """
    game.add_player("Alice")  # no transport whatsoever
    open_ws = _connect(handler)
    game.add_player("Bob", handler._conn.connection_id(open_ws))
    dead_ws = _connect(handler)
    game.add_player("Cara", handler._conn.connection_id(dead_ws))
    dead_ws.closed = True

    reaped = handler._reap_closed_connections(game)

    assert reaped == {"Cara"}
    assert game.get_player("Alice").connected is True
    assert game.get_player("Bob").connected is True
    assert game.get_player("Cara").connected is False


# ---------------------------------------------------------------------------
# The map itself
# ---------------------------------------------------------------------------


def test_the_connection_manager_is_the_only_place_a_socket_is_found(
    handler: QuizifyWebSocketHandler
) -> None:
    ws = _connect(handler)
    cid = handler._conn.connection_id(ws)

    assert handler._conn.connection_id(ws) == cid, "the id is not stable"
    assert handler._conn.socket_for(cid) is ws
    assert handler._conn.is_connection_open(cid) is True
    assert handler._conn.is_connection_dead(cid) is False

    ws.closed = True
    assert handler._conn.is_connection_open(cid) is False
    assert handler._conn.is_connection_dead(cid) is True


def test_forgetting_a_connection_drops_both_directions(
    handler: QuizifyWebSocketHandler
) -> None:
    """Bounded by the number of *live* sockets, not by every socket ever seen.

    The lesson of #310: a map that only ever grows is a leak on a host that
    runs for months.
    """
    ws = _connect(handler)
    cid = handler._conn.connection_id(ws)
    handler._conn.forget_connection(ws)

    assert handler._conn.socket_for(cid) is None
    assert handler._conn.is_connection_dead(cid) is False  # unknown, not dead
    assert handler._conn._id_by_ws == {}
    assert handler._conn._ws_by_id == {}
