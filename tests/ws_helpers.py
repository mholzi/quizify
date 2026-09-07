"""Test-side plumbing for the opaque connection ids introduced by #882.

Before #882 a player *was* its socket: ``PlayerSession.ws`` held the
``aiohttp`` response object, so a test could hand ``game.add_player`` a
``MagicMock`` and later drive a handler with ``game.get_player("A").ws``.
That is exactly the coupling the issue removes — the game layer now stores an
opaque ``connection_id`` and the id → socket map lives in
``server.connection.ConnectionManager``.

Tests that only exercise game logic need none of this: ``game.add_player(name)``
now works with no socket at all, which is the point. What lives here is for the
handful of tests that genuinely drive the *server* layer and therefore still
need a socket on one end and a player on the other:

``seat_player``
    join a player and bind them to a fake socket in one step.
``socket_of``
    the inverse lookup, replacing the old ``player.ws``.
``FakeConnection``
    the id ↔ socket half of ``ConnectionManager``, for the stub connection
    objects several test modules hand-roll instead of using the real one.
"""

from __future__ import annotations

import uuid
from typing import Any
from unittest.mock import AsyncMock, MagicMock


def fake_ws(*, closed: bool = False) -> MagicMock:
    """A socket-shaped mock good enough for the server layer."""
    ws = MagicMock()
    ws.closed = closed
    ws.send_json = AsyncMock()
    ws.send_str = AsyncMock()
    ws.close = AsyncMock()
    return ws


class FakeConnection:
    """The ``connection_id`` half of ``ConnectionManager``, for stubs.

    Mix into a hand-rolled ``_Conn`` test double so it can translate between
    sockets and the opaque ids the game layer stores. ``send_to_player``
    deliberately routes through ``send`` even when the player has no socket
    bound, because a stub is a recorder: a test that never binds a socket
    still wants to see what would have gone out.
    """

    # Built lazily rather than in ``__init__``: the stubs this is mixed into
    # define their own constructor and mostly do not chain to super().

    @property
    def _id_maps(self) -> tuple[dict[int, str], dict[str, Any]]:
        maps = self.__dict__.get("_conn_id_maps")
        if maps is None:
            maps = ({}, {})
            self.__dict__["_conn_id_maps"] = maps
        return maps

    def connection_id(self, ws: Any) -> str:
        by_ws, by_id = self._id_maps
        cid = by_ws.get(id(ws))
        if cid is None:
            cid = uuid.uuid4().hex
            by_ws[id(ws)] = cid
            by_id[cid] = ws
        return cid

    def socket_for(self, connection_id: str | None) -> Any:
        if not connection_id:
            return None
        return self._id_maps[1].get(connection_id)

    def is_connection_open(self, connection_id: str | None) -> bool:
        ws = self.socket_for(connection_id)
        return ws is not None and not ws.closed

    def is_connection_dead(self, connection_id: str | None) -> bool:
        ws = self.socket_for(connection_id)
        return ws is not None and bool(ws.closed)

    def forget_connection(self, ws: Any) -> None:
        by_ws, by_id = self._id_maps
        cid = by_ws.pop(id(ws), None)
        if cid is not None:
            by_id.pop(cid, None)

    async def send_to_player(self, player: Any, message: dict) -> None:
        await self.send(self.socket_for(player.connection_id), message)  # type: ignore[attr-defined]


def seat_player(conn: Any, game: Any, name: str, ws: Any = None) -> Any:
    """Join *name* on a fresh (or given) socket and return that socket."""
    if ws is None:
        ws = fake_ws()
    add = getattr(conn, "add_connection", None)
    if add is not None:
        add(ws, is_admin=False, is_dashboard=False)
    game.add_player(name, conn.connection_id(ws))
    return ws


def bind(conn: Any, game: Any, name: str, ws: Any) -> Any:
    """Point an already-joined *name* at *ws* (the reconnect move)."""
    add = getattr(conn, "add_connection", None)
    if add is not None:
        add(ws, is_admin=False, is_dashboard=False)
    player = game.get_player(name)
    game.bind_player_connection(player, conn.connection_id(ws))
    return ws


def socket_of(conn: Any, player: Any) -> Any:
    """The socket *player* is bound to — the old ``player.ws``."""
    return conn.socket_for(player.connection_id)


def ws_of(conn: Any, game: Any, name: str) -> Any:
    """The socket for player *name*, binding a fresh one if there is none.

    The drop-in replacement for the old ``game.get_player(name).ws`` in tests
    that only need *a* socket that routes back to that player.
    """
    player = game.get_player(name)
    ws = socket_of(conn, player)
    if ws is None:
        ws = bind(conn, game, name, fake_ws())
    return ws
