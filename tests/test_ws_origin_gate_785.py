"""Regression tests for issue #785 (security).

The WebSocket handshake never looked at ``Origin``. Browsers do not apply CORS
to a WebSocket upgrade, so any page open in the victim's browser could open
``ws://homeassistant.local:8123/api/quizify/ws?role=admin`` *from the victim's
own address* — past every per-IP cap — and, inside the admin-bootstrap window,
be handed the admin role plus the persisted session token.

The same gap covered the three unauthenticated POST views (flag-question,
pack-submit, pack-submit/request): ``request.json()`` does not enforce a
Content-Type, so a cross-site page could reach them as a CORS *simple* request
with no preflight and no consent.

Both are now gated by ``server/origin.py``: a foreign ``Origin`` is refused
(403 before the upgrade / before the body is read), the POST views additionally
require ``Content-Type: application/json``, and a request with **no** Origin
keeps passing — non-browser clients, the dev server and the tests never send
one, and a browser cannot be made to omit it.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from aiohttp import WSServerHandshakeError, web
from aiohttp.test_utils import TestClient, TestServer

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT))

from custom_components.quizify.game.state import QuizifyGameState  # noqa: E402
from custom_components.quizify.server import WS_PATH, views  # noqa: E402
from custom_components.quizify.server.connection import ConnectionManager  # noqa: E402
from custom_components.quizify.server.context import APP_CTX_KEY  # noqa: E402
from custom_components.quizify.server.flag_store import (  # noqa: E402
    FILENAME as FLAG_FILE,
)
from custom_components.quizify.server.origin import (  # noqa: E402
    allowed_origin_hosts,
    is_origin_allowed,
)
from custom_components.quizify.server.websocket import (  # noqa: E402
    QuizifyWebSocketHandler,
)

EVIL = "http://evil.example"


class _FakeRuntime:
    """Runtime with no ``hass`` — the standalone/dev shape."""

    def __init__(self, tmp_path: Path) -> None:
        self.data_dir = tmp_path

    def create_task(self, coro):  # noqa: ANN001, ANN202
        return asyncio.ensure_future(coro)

    async def run_in_executor(self, func, *args):  # noqa: ANN001, ANN002
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, func, *args)


class _HARuntime(_FakeRuntime):
    """Runtime that carries a hass with configured internal/external URLs."""

    def __init__(
        self,
        tmp_path: Path,
        internal_url: str | None = None,
        external_url: str | None = None,
    ) -> None:
        super().__init__(tmp_path)
        self.hass = SimpleNamespace(
            config=SimpleNamespace(
                internal_url=internal_url, external_url=external_url
            )
        )


@pytest.fixture
def game(tmp_path: Path) -> QuizifyGameState:
    return QuizifyGameState(runtime=_FakeRuntime(tmp_path), entry_id="test")


def _handler(runtime, game: QuizifyGameState) -> QuizifyWebSocketHandler:  # noqa: ANN001
    h = QuizifyWebSocketHandler(runtime=runtime, game_state_provider=lambda: game)
    h._conn = ConnectionManager(runtime, lambda: game)
    return h


async def _make_client(handler: QuizifyWebSocketHandler) -> TestClient:
    app = web.Application()
    app.router.add_get(WS_PATH, handler.handle)
    client = TestClient(TestServer(app))
    await client.start_server()
    return client


def _own_origin(client: TestClient) -> str:
    url = client.make_url(WS_PATH)
    return f"http://{url.host}:{url.port}"


# ---------------------------------------------------------------------------
# The WebSocket handshake
# ---------------------------------------------------------------------------


class TestWebSocketOriginGate:
    @pytest.mark.asyncio
    async def test_a_foreign_origin_is_refused_before_the_upgrade(
        self, game: QuizifyGameState, tmp_path: Path
    ) -> None:
        """This is the CSWSH itself: a page on another site, the victim's IP."""
        handler = _handler(_FakeRuntime(tmp_path), game)
        client = await _make_client(handler)
        try:
            with pytest.raises(WSServerHandshakeError) as exc:
                await client.ws_connect(WS_PATH, headers={"Origin": EVIL})
            assert exc.value.status == 403
            # Nothing was upgraded, so nothing was registered.
            assert len(handler._conn.connections) == 0
        finally:
            await client.close()

    @pytest.mark.asyncio
    async def test_the_refused_handshake_never_reaches_the_admin_bootstrap(
        self, game: QuizifyGameState, tmp_path: Path
    ) -> None:
        """``?role=admin`` is exactly the request the issue is about."""
        handler = _handler(_FakeRuntime(tmp_path), game)
        client = await _make_client(handler)
        try:
            with pytest.raises(WSServerHandshakeError) as exc:
                await client.ws_connect(
                    f"{WS_PATH}?role=admin", headers={"Origin": EVIL}
                )
            assert exc.value.status == 403
            assert not handler._conn.has_admin_connections()
        finally:
            await client.close()

    @pytest.mark.asyncio
    async def test_a_request_without_an_origin_still_connects(
        self, game: QuizifyGameState, tmp_path: Path
    ) -> None:
        """Non-browser clients (and every existing test) send no Origin."""
        handler = _handler(_FakeRuntime(tmp_path), game)
        client = await _make_client(handler)
        try:
            ws = await client.ws_connect(WS_PATH)
            await ws.close()
        finally:
            await client.close()

    @pytest.mark.asyncio
    async def test_the_pages_own_origin_connects(
        self, game: QuizifyGameState, tmp_path: Path
    ) -> None:
        """A real phone sends the host it loaded the page from."""
        handler = _handler(_FakeRuntime(tmp_path), game)
        client = await _make_client(handler)
        try:
            ws = await client.ws_connect(
                WS_PATH, headers={"Origin": _own_origin(client)}
            )
            await ws.close()
        finally:
            await client.close()

    @pytest.mark.asyncio
    async def test_a_configured_external_url_is_accepted(
        self, game: QuizifyGameState, tmp_path: Path
    ) -> None:
        """HA behind a proxy: the browser's Origin is not ``request.host``."""
        runtime = _HARuntime(tmp_path, external_url="https://quiz.example.com")
        handler = _handler(runtime, game)
        client = await _make_client(handler)
        try:
            ws = await client.ws_connect(
                WS_PATH, headers={"Origin": "https://quiz.example.com"}
            )
            await ws.close()
        finally:
            await client.close()

    @pytest.mark.asyncio
    async def test_a_sandboxed_origin_is_refused(
        self, game: QuizifyGameState, tmp_path: Path
    ) -> None:
        """``Origin: null`` is a sandboxed iframe or a file:// page."""
        handler = _handler(_FakeRuntime(tmp_path), game)
        client = await _make_client(handler)
        try:
            with pytest.raises(WSServerHandshakeError) as exc:
                await client.ws_connect(WS_PATH, headers={"Origin": "null"})
            assert exc.value.status == 403
        finally:
            await client.close()


# ---------------------------------------------------------------------------
# The helper itself
# ---------------------------------------------------------------------------


class _Req:
    def __init__(self, host: str, headers: dict | None = None) -> None:
        self.host = host
        self.headers = headers or {}
        self.remote = "203.0.113.9"


class TestOriginHelper:
    def test_no_origin_passes(self, tmp_path: Path) -> None:
        assert is_origin_allowed(_Req("ha.local:8123"), _FakeRuntime(tmp_path))

    def test_matching_host_passes(self, tmp_path: Path) -> None:
        req = _Req("ha.local:8123", {"Origin": "http://ha.local:8123"})
        assert is_origin_allowed(req, _FakeRuntime(tmp_path))

    def test_a_different_port_on_the_same_host_is_foreign(
        self, tmp_path: Path
    ) -> None:
        """Another service on the LAN box is not Quizify."""
        req = _Req("ha.local:8123", {"Origin": "http://ha.local:9000"})
        assert not is_origin_allowed(req, _FakeRuntime(tmp_path))

    def test_default_ports_compare_equal(self, tmp_path: Path) -> None:
        """Browsers omit :443; a configured URL may spell it out."""
        runtime = _HARuntime(tmp_path, external_url="https://quiz.example.com:443")
        req = _Req("127.0.0.1:8123", {"Origin": "https://quiz.example.com"})
        assert is_origin_allowed(req, runtime)

    def test_internal_url_is_honoured(self, tmp_path: Path) -> None:
        runtime = _HARuntime(tmp_path, internal_url="http://192.168.1.5:8123")
        req = _Req("127.0.0.1:8123", {"Origin": "http://192.168.1.5:8123"})
        assert is_origin_allowed(req, runtime)

    def test_the_allowed_set_never_leaks_an_unset_url(self, tmp_path: Path) -> None:
        runtime = _HARuntime(tmp_path)  # both URLs None
        hosts = allowed_origin_hosts(_Req("ha.local:8123"), runtime)
        assert hosts == {"ha.local:8123"}


# ---------------------------------------------------------------------------
# The unauthenticated POST views
# ---------------------------------------------------------------------------


class _PostRuntime:
    def __init__(self, data_dir: Path) -> None:
        self.data_dir = data_dir

    async def run_in_executor(self, func, *args):  # noqa: ANN001, ANN002
        return func(*args)


class _PostReq:
    def __init__(self, ctx, headers: dict, body: dict) -> None:  # noqa: ANN001
        self.app = {APP_CTX_KEY: ctx}
        self.remote = "203.0.113.55"
        self.host = "ha.local:8123"
        self.headers = headers
        self._body = body

    async def json(self) -> dict:
        return self._body


def _flag(tmp_path: Path, headers: dict):  # noqa: ANN001, ANN202
    ctx = SimpleNamespace(runtime=_PostRuntime(tmp_path))
    views._flag_rate_limiter.forget("203.0.113.55")
    req = _PostReq(ctx, headers, {"question_id": "geo_037"})
    return asyncio.run(views.flag_question_view(req))  # type: ignore[arg-type]


class TestUnauthenticatedPostGate:
    def test_a_cross_site_origin_is_refused(self, tmp_path: Path) -> None:
        resp = _flag(
            tmp_path, {"Origin": EVIL, "Content-Type": "application/json"}
        )
        assert resp.status == 403
        assert not (tmp_path / FLAG_FILE).exists(), (
            "the cross-site POST still appended to flagged.jsonl"
        )

    def test_a_form_content_type_is_refused(self, tmp_path: Path) -> None:
        """The CSRF half: no simple request may reach this view."""
        resp = _flag(
            tmp_path, {"Content-Type": "application/x-www-form-urlencoded"}
        )
        assert resp.status == 415
        assert not (tmp_path / FLAG_FILE).exists()

    def test_a_missing_content_type_is_refused(self, tmp_path: Path) -> None:
        resp = _flag(tmp_path, {})
        assert resp.status == 415

    def test_the_apps_own_post_still_works(self, tmp_path: Path) -> None:
        resp = _flag(
            tmp_path,
            {
                "Origin": "http://ha.local:8123",
                "Content-Type": "application/json; charset=utf-8",
            },
        )
        assert resp.status == 200
        assert (tmp_path / FLAG_FILE).exists()
