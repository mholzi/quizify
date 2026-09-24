"""Per-game room code in the join link (issue #1016).

Quizify's routes are registered on HA's raw aiohttp router, so no HA login
protects them — not even over Nabu Casa Remote UI, whose tunnel reaches every
route. Before #1016 the join link was the bare ``/quizify/player`` and anyone
who once held it could join any later game from the internet; the
``flag-question`` and ``pack-submit`` POSTs needed nothing at all.

These tests pin the gate:

* a fresh WebSocket ``join`` needs the game's current room code;
* a player reconnecting with a session token does not;
* the host (WS admin socket, or a join carrying the admin token) does not;
* ``reset_game`` rotates the code, and the host screens are told the new one;
* the television is only told the code on the LAN or with a valid ``?room=``;
* ``flag-question`` needs the room code (or admin token) and answers a
  non-object body with 400 instead of a 500;
* ``pack-submit`` and ``pack-submit/request`` need the admin token.

They run with ``room_code_gate``, which switches off the conftest shim that
hands older join tests the current code.
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT))

from custom_components.quizify.const import (  # noqa: E402
    ERR_ROOM_CODE_INVALID,
    ERR_SUBMIT_UNAUTHORIZED,
)
from custom_components.quizify.game.room_code import (  # noqa: E402
    ROOM_CODE_ALPHABET,
    ROOM_CODE_LENGTH,
    join_path,
)
from custom_components.quizify.game.state import QuizifyGameState  # noqa: E402
from custom_components.quizify.server import pack_submission, views  # noqa: E402
from custom_components.quizify.server.connection import ConnectionManager  # noqa: E402
from custom_components.quizify.server.context import APP_CTX_KEY  # noqa: E402
from custom_components.quizify.server.websocket import (  # noqa: E402
    QuizifyWebSocketHandler,
)

pytestmark = pytest.mark.room_code_gate

ADMIN_TOKEN = "admin-token-1016"


class _Runtime:
    def __init__(self, data_dir: Path, *, hass: Any = None) -> None:
        self.data_dir = data_dir
        if hass is not None:
            self.hass = hass

    def create_task(self, coro):  # noqa: ANN001, ANN202
        return asyncio.ensure_future(coro)

    async def run_in_executor(self, func, *args):  # noqa: ANN001, ANN002, ANN202
        return func(*args)


def _ws() -> MagicMock:
    ws = MagicMock()
    ws.closed = False
    ws.send_json = AsyncMock()
    ws.close = AsyncMock()
    return ws


def _sent(ws: MagicMock) -> list[dict]:
    return [call.args[0] for call in ws.send_json.await_args_list]


def _types(ws: MagicMock) -> list[str]:
    return [m.get("type") for m in _sent(ws)]


@pytest.fixture
def game(tmp_path: Path) -> QuizifyGameState:
    return QuizifyGameState(runtime=_Runtime(tmp_path), entry_id="test")


def _handler(game: QuizifyGameState, runtime: _Runtime) -> QuizifyWebSocketHandler:
    h = QuizifyWebSocketHandler(runtime=runtime, game_state_provider=lambda: game)
    h._conn = ConnectionManager(runtime, lambda: game)
    h._conn._admin_session_token = ADMIN_TOKEN
    h._conn._admin_token_loaded = True
    h._conn.send_error = AsyncMock()
    return h


@pytest.fixture
def handler(game: QuizifyGameState, tmp_path: Path) -> QuizifyWebSocketHandler:
    return _handler(game, _Runtime(tmp_path))


def _refused_with(handler: QuizifyWebSocketHandler, code: str) -> bool:
    return any(
        call.args[1] == code for call in handler._conn.send_error.await_args_list
    )


def _names(game: QuizifyGameState) -> list[str]:
    return [p.name for p in game.get_players()]


# ---------------------------------------------------------------------------
# The code itself
# ---------------------------------------------------------------------------


def test_a_game_has_a_room_code_of_the_documented_shape(
    game: QuizifyGameState,
) -> None:
    assert len(game.room_code) == ROOM_CODE_LENGTH
    assert set(game.room_code) <= set(ROOM_CODE_ALPHABET)


def test_rotation_mints_a_different_code(game: QuizifyGameState) -> None:
    seen = {game.room_code}
    for _ in range(5):
        seen.add(game.rotate_room_code())
    assert len(seen) > 1
    assert game.is_room_code_valid(game.room_code)
    assert game.is_room_code_valid(game.room_code.lower())
    assert not game.is_room_code_valid("")
    assert not game.is_room_code_valid(None)


# ---------------------------------------------------------------------------
# WebSocket join
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_join_without_room_code_is_refused(
    handler: QuizifyWebSocketHandler, game: QuizifyGameState
) -> None:
    ws = _ws()
    await handler._handle_join(ws, {"name": "Gast"}, game)
    assert _names(game) == []
    assert _refused_with(handler, ERR_ROOM_CODE_INVALID)
    assert "joined" not in _types(ws)


@pytest.mark.asyncio
async def test_join_with_wrong_room_code_is_refused(
    handler: QuizifyWebSocketHandler, game: QuizifyGameState
) -> None:
    wrong = "ZZZZZZ" if game.room_code != "ZZZZZZ" else "YYYYYY"
    await handler._handle_join(_ws(), {"name": "Gast", "room": wrong}, game)
    assert _names(game) == []
    assert _refused_with(handler, ERR_ROOM_CODE_INVALID)


@pytest.mark.asyncio
async def test_join_with_right_room_code_is_admitted(
    handler: QuizifyWebSocketHandler, game: QuizifyGameState
) -> None:
    ws = _ws()
    await handler._handle_join(ws, {"name": "Gast", "room": game.room_code}, game)
    assert _names(game) == ["Gast"]
    joined = next(m for m in _sent(ws) if m.get("type") == "joined")
    # The phone keeps the code for the flag POST.
    assert joined["room_code"] == game.room_code
    assert not _refused_with(handler, ERR_ROOM_CODE_INVALID)


@pytest.mark.asyncio
async def test_room_code_is_case_insensitive(
    handler: QuizifyWebSocketHandler, game: QuizifyGameState
) -> None:
    await handler._handle_join(
        _ws(), {"name": "Gast", "room": f" {game.room_code.lower()} "}, game
    )
    assert _names(game) == ["Gast"]


@pytest.mark.asyncio
async def test_reconnect_with_session_token_needs_no_room_code(
    handler: QuizifyWebSocketHandler, game: QuizifyGameState
) -> None:
    first = _ws()
    await handler._handle_join(first, {"name": "Gast", "room": game.room_code}, game)
    token = next(m for m in _sent(first) if m.get("type") == "joined")["session_token"]

    # The phone reloads: new socket, a reconnect that carries no room code.
    first.closed = True
    second = _ws()
    await handler._handle_reconnect(second, {"session_token": token}, game)

    frames = _sent(second)
    assert [m["type"] for m in frames][:1] == ["reconnected"]
    # A token-only tab (no ?room= in its URL) learns the code here.
    assert frames[0]["room_code"] == game.room_code
    assert not _refused_with(handler, ERR_ROOM_CODE_INVALID)
    assert game.get_player("Gast").connected


@pytest.mark.asyncio
async def test_admin_socket_joins_as_player_without_room_code(
    handler: QuizifyWebSocketHandler, game: QuizifyGameState
) -> None:
    admin_ws = _ws()
    handler._conn.add_connection(admin_ws, is_admin=True, is_dashboard=False)
    await handler._handle_join(admin_ws, {"name": "Host", "is_admin": True}, game)
    assert _names(game) == ["Host"]
    assert game.get_player("Host").is_admin


@pytest.mark.asyncio
async def test_join_with_valid_admin_token_needs_no_room_code(
    handler: QuizifyWebSocketHandler, game: QuizifyGameState
) -> None:
    await handler._handle_join(
        _ws(),
        {"name": "Host", "is_admin": True, "admin_token": ADMIN_TOKEN},
        game,
    )
    assert _names(game) == ["Host"]


@pytest.mark.asyncio
async def test_a_wrong_admin_token_does_not_replace_the_room_code(
    handler: QuizifyWebSocketHandler, game: QuizifyGameState
) -> None:
    await handler._handle_join(
        _ws(), {"name": "Host", "is_admin": True, "admin_token": "nope"}, game
    )
    assert _names(game) == []
    assert _refused_with(handler, ERR_ROOM_CODE_INVALID)


@pytest.mark.asyncio
async def test_join_through_the_message_router_is_gated_too(
    handler: QuizifyWebSocketHandler, game: QuizifyGameState
) -> None:
    await handler._handle_message(_ws(), {"type": "join", "name": "Gast"}, False)
    assert _names(game) == []
    assert _refused_with(handler, ERR_ROOM_CODE_INVALID)


# ---------------------------------------------------------------------------
# Admin frame and reset rotation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_admin_connect_frame_carries_the_join_link_with_code(
    handler: QuizifyWebSocketHandler, game: QuizifyGameState
) -> None:
    admin_ws = _ws()
    await handler._handle_admin_connect(admin_ws, game)
    frame = next(m for m in _sent(admin_ws) if m.get("type") == "game_state")
    assert frame["room_code"] == game.room_code
    assert frame["join_url"] == f"/quizify/player?room={game.room_code}"
    assert frame["join_url"] == join_path(game.room_code)


@pytest.mark.asyncio
async def test_reset_rotates_the_code_and_tells_the_host(
    handler: QuizifyWebSocketHandler, game: QuizifyGameState
) -> None:
    handler._conn.broadcast = AsyncMock()
    admin_ws = _ws()
    handler._conn.add_connection(admin_ws, is_admin=True, is_dashboard=False)
    old = game.room_code

    await handler._handle_reset_game(admin_ws, game)

    assert game.room_code != old
    pushed = [m for m in _sent(admin_ws) if m.get("type") == "room_code"]
    assert pushed == [
        {
            "type": "room_code",
            "room_code": game.room_code,
            "join_url": join_path(game.room_code),
        }
    ]
    # The room code frame is never broadcast to every socket.
    for call in handler._conn.broadcast.await_args_list:
        assert call.args[0].get("type") != "room_code"

    # The link from before the reset no longer admits anyone.
    await handler._handle_join(_ws(), {"name": "Gast", "room": old}, game)
    assert _names(game) == []
    assert _refused_with(handler, ERR_ROOM_CODE_INVALID)


# ---------------------------------------------------------------------------
# The television
# ---------------------------------------------------------------------------


def _dash_request(remote: str, room: str | None = None) -> SimpleNamespace:
    query = {"role": "dashboard"}
    if room is not None:
        query["room"] = room
    return SimpleNamespace(remote=remote, query=query)


@pytest.mark.asyncio
@pytest.mark.parametrize("remote", ["192.168.0.42", "10.0.0.7", "fe80::1"])
async def test_tv_on_the_lan_is_told_the_code(
    game: QuizifyGameState, tmp_path: Path, remote: str
) -> None:
    h = _handler(game, _Runtime(tmp_path, hass=object()))
    tv = _ws()
    await h._offer_room_code_to_dashboard(tv, _dash_request(remote))
    assert [m for m in _sent(tv) if m["type"] == "room_code"][0][
        "room_code"
    ] == game.room_code


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "remote", ["127.0.0.1", "::1", "8.8.8.8", "2001:4860:4860::8888"]
)
async def test_tv_through_the_tunnel_or_internet_is_not_told_the_code(
    game: QuizifyGameState, tmp_path: Path, remote: str
) -> None:
    # Nabu Casa hands every remote client to HA over loopback (#701).
    h = _handler(game, _Runtime(tmp_path, hass=object()))
    tv = _ws()
    await h._offer_room_code_to_dashboard(tv, _dash_request(remote))
    assert _sent(tv) == []
    assert tv not in h._room_code_sockets


@pytest.mark.asyncio
async def test_tv_opened_with_the_current_code_is_told_the_code(
    game: QuizifyGameState, tmp_path: Path
) -> None:
    h = _handler(game, _Runtime(tmp_path, hass=object()))
    tv = _ws()
    await h._offer_room_code_to_dashboard(
        tv, _dash_request("127.0.0.1", room=game.room_code)
    )
    assert _types(tv) == ["room_code"]


@pytest.mark.asyncio
async def test_trusted_tv_hears_the_new_code_after_reset(
    game: QuizifyGameState, tmp_path: Path
) -> None:
    h = _handler(game, _Runtime(tmp_path, hass=object()))
    h._conn.broadcast = AsyncMock()
    trusted, stranger = _ws(), _ws()
    for sock, remote in ((trusted, "192.168.0.42"), (stranger, "127.0.0.1")):
        h._conn.add_connection(sock, is_admin=False, is_dashboard=True)
        await h._offer_room_code_to_dashboard(sock, _dash_request(remote))

    await h._handle_reset_game(_ws(), game)

    assert [m["room_code"] for m in _sent(trusted) if m["type"] == "room_code"][
        -1
    ] == game.room_code
    assert [m for m in _sent(stranger) if m["type"] == "room_code"] == []


# ---------------------------------------------------------------------------
# HTTP: flag-question
# ---------------------------------------------------------------------------


class _Req:
    def __init__(
        self, ctx: Any, body: Any, *, token: str | None = None, remote: str = ""
    ) -> None:
        self.app = {APP_CTX_KEY: ctx}
        self.remote = remote
        self.host = "homeassistant.local:8123"
        self.headers = {"Content-Type": "application/json"}
        if token:
            self.headers["X-Quizify-Token"] = token
        self.query: dict[str, str] = {}
        self._body = body

    async def json(self) -> Any:
        return self._body


def _http_ctx(tmp_path: Path, game: QuizifyGameState, **extra: Any) -> Any:
    conn = SimpleNamespace(validate_admin_token=lambda t: t == ADMIN_TOKEN)
    return SimpleNamespace(
        runtime=_Runtime(tmp_path),
        game=game,
        ws_handler=SimpleNamespace(conn=conn),
        **extra,
    )


def _flag(ctx: Any, body: Any, *, token: str | None = None, ip: str) -> Any:
    views._flag_rate_limiter.forget(ip)
    return asyncio.run(
        views.flag_question_view(_Req(ctx, body, token=token, remote=ip))
    )


def test_flag_without_room_code_is_forbidden(
    tmp_path: Path, game: QuizifyGameState
) -> None:
    resp = _flag(_http_ctx(tmp_path, game), {"question_id": "q1"}, ip="198.51.100.1")
    assert resp.status == 403


def test_flag_with_wrong_room_code_is_forbidden(
    tmp_path: Path, game: QuizifyGameState
) -> None:
    resp = _flag(
        _http_ctx(tmp_path, game),
        {"question_id": "q1", "room_code": "WRONG1"},
        ip="198.51.100.2",
    )
    assert resp.status == 403


def test_flag_with_room_code_is_recorded(
    tmp_path: Path, game: QuizifyGameState
) -> None:
    resp = _flag(
        _http_ctx(tmp_path, game),
        {"question_id": "q1", "room_code": game.room_code},
        ip="198.51.100.3",
    )
    assert resp.status == 200


def test_flag_with_admin_token_is_recorded(
    tmp_path: Path, game: QuizifyGameState
) -> None:
    resp = _flag(
        _http_ctx(tmp_path, game),
        {"question_id": "q1"},
        token=ADMIN_TOKEN,
        ip="198.51.100.4",
    )
    assert resp.status == 200


@pytest.mark.parametrize("body", [[1, 2], "x", 5, None])
def test_flag_with_non_object_body_is_400_not_500(
    tmp_path: Path, game: QuizifyGameState, body: Any
) -> None:
    resp = _flag(_http_ctx(tmp_path, game), body, ip="198.51.100.5")
    assert resp.status == 400


# ---------------------------------------------------------------------------
# HTTP: pack-submit and pack-submit/request
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "view", [pack_submission.submit_pack_view, pack_submission.request_pack_view]
)
def test_pack_submit_without_admin_token_is_unauthorized(
    tmp_path: Path, game: QuizifyGameState, view: Any
) -> None:
    ctx = _http_ctx(
        tmp_path,
        game,
        community_submit_url="https://worker.example/submit",
        community_submit_secret=None,
    )
    resp = asyncio.run(view(_Req(ctx, {}, remote="198.51.100.6")))
    assert resp.status == 401
    assert json.loads(resp.body)["code"] == ERR_SUBMIT_UNAUTHORIZED


@pytest.mark.parametrize(
    "view", [pack_submission.submit_pack_view, pack_submission.request_pack_view]
)
def test_pack_submit_with_admin_token_passes_the_gate(
    tmp_path: Path, game: QuizifyGameState, view: Any
) -> None:
    # Feature off: the token gate passes and the next gate answers instead.
    ctx = _http_ctx(
        tmp_path, game, community_submit_url=None, community_submit_secret=None
    )
    resp = asyncio.run(view(_Req(ctx, {}, token=ADMIN_TOKEN, remote="198.51.100.7")))
    assert resp.status == 403
    assert json.loads(resp.body)["code"] != ERR_SUBMIT_UNAUTHORIZED


# --- TV lobby without the code ----------------------------------------------
# A TV that was not given the room code shows how to get it instead of a QR.
# The scan caption and typed-URL block around the QR must go with it, or the
# screen asks guests to scan something that is not there; and the message is
# a sentence, not the uppercase monospace caption style.

_WWW = _REPO_ROOT / "custom_components" / "quizify" / "www"


def test_tv_without_code_hides_the_join_block_and_reads_as_a_sentence() -> None:
    js = (_WWW / "js" / "dashboard.js").read_text(encoding="utf-8")
    body = js.split("function renderLobbyQr()", 1)[1].split("\n    function ", 1)[0]
    assert "classList.toggle('is-code-missing', !_roomCode)" in body
    missing = body.split("if (!_roomCode) {", 1)[1].split("return;\n        }", 1)[0]
    assert "dashboard-room-code-missing" in missing
    assert "dashboard-waiting-text" not in missing

    css = (_WWW / "css" / "tv.css").read_text(encoding="utf-8")
    assert "#lobby-qr.is-code-missing > .dashboard-waiting-text" in css
    assert "#lobby-qr.is-code-missing .dashboard-join-fallback" in css
    rule = css.split(".dashboard-room-code-missing {", 1)[1].split("}", 1)[0]
    assert "text-align: center" in rule
    assert "max-width" in rule
    assert "text-transform" not in rule
