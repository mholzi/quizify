"""Regression tests for issue #872 (security).

The origin gate from #785 seeded its allow-list from the request's own ``Host``
header, so ``Origin == Host`` was enough to pass. A browser cannot forge
``Host`` cross-site — but with DNS rebinding it does not have to. A page served
from ``evil.example:8123`` with a TTL-0 record, re-pointed at the Home
Assistant LAN address, opens
``ws://evil.example:8123/api/quizify/ws?role=admin`` from the victim's phone.
The handshake then carries ``Host: evil.example:8123`` **and**
``Origin: http://evil.example:8123``; they compared equal, and the gate opened.
The payload is the one #785 closed: the admin token inside the bootstrap
window, and ``join`` with ``is_admin: true`` outside it.

The fix judges both headers against the same allow-list, and that list only
ever contains

* names the public DNS cannot delegate — an IP literal, a single-label host,
  or a reserved private suffix such as ``.local``; and
* the URLs Home Assistant is configured to answer to — ``internal_url``,
  ``external_url``, the Nabu Casa remote UI host.

The second half of this file is the reason the first half is not simply
"refuse everything unfamiliar": these are the shapes a phone on Markus' LAN
actually sends, and every one of them must keep working.
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
from custom_components.quizify.server import WS_PATH  # noqa: E402
from custom_components.quizify.server.connection import ConnectionManager  # noqa: E402
from custom_components.quizify.server.origin import (  # noqa: E402
    allowed_origin_hosts,
    configured_origin_hosts,
    is_origin_allowed,
    is_private_network_host,
)
from custom_components.quizify.server.websocket import (  # noqa: E402
    QuizifyWebSocketHandler,
)

#: The rebinding shape: the attacker's own domain in *both* headers, because
#: after the rebind the browser believes that is where it is talking to.
REBOUND = "evil.example:8123"


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
    """Runtime carrying a hass with configured internal/external URLs."""

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


class _Req:
    """The two headers the gate judges, and nothing else."""

    def __init__(self, host: str, origin: str | None = None) -> None:
        self.host = host
        self.headers = {"Origin": origin} if origin is not None else {}
        self.remote = "192.168.0.31"


@pytest.fixture
def game(tmp_path: Path) -> QuizifyGameState:
    return QuizifyGameState(runtime=_FakeRuntime(tmp_path), entry_id="test")


# ---------------------------------------------------------------------------
# The attack
# ---------------------------------------------------------------------------


class TestDnsRebindingIsRefused:
    def test_origin_matching_a_rebound_host_no_longer_passes(
        self, tmp_path: Path
    ) -> None:
        """The whole of #872 in one assertion.

        Before the fix ``allowed_origin_hosts`` contained ``request.host``, so
        this pair compared equal and returned True.
        """
        req = _Req(REBOUND, f"http://{REBOUND}")
        assert not is_origin_allowed(req, _FakeRuntime(tmp_path))

    def test_a_rebound_host_is_refused_even_without_an_origin(
        self, tmp_path: Path
    ) -> None:
        """The allow-list applies to ``Host`` itself, not only to ``Origin``."""
        assert not is_origin_allowed(_Req(REBOUND), _FakeRuntime(tmp_path))

    def test_a_rebound_host_is_refused_on_a_configured_install(
        self, tmp_path: Path
    ) -> None:
        """Configuring a URL widens the list by that URL, not by the request."""
        runtime = _HARuntime(tmp_path, internal_url="http://192.168.0.69:8123")
        req = _Req(REBOUND, f"http://{REBOUND}")
        assert not is_origin_allowed(req, runtime)

    def test_a_rebound_subdomain_of_a_configured_host_is_refused(
        self, tmp_path: Path
    ) -> None:
        """``quiz.example.com`` allowed must not allow ``evil.quiz.example.com``."""
        runtime = _HARuntime(tmp_path, external_url="https://quiz.example.com")
        req = _Req(
            "evil.quiz.example.com", "https://evil.quiz.example.com"
        )
        assert not is_origin_allowed(req, runtime)

    def test_the_rebound_host_never_enters_the_allow_list(
        self, tmp_path: Path
    ) -> None:
        """Belt and braces: the set itself must not have grown a public name."""
        hosts = allowed_origin_hosts(_Req(REBOUND), _FakeRuntime(tmp_path))
        assert hosts == set()

    def test_configured_hosts_are_request_independent(self, tmp_path: Path) -> None:
        """``configured_origin_hosts`` takes no request, by construction."""
        runtime = _HARuntime(tmp_path, external_url="https://quiz.example.com")
        assert configured_origin_hosts(runtime) == {"quiz.example.com"}

    @pytest.mark.asyncio
    async def test_the_rebound_handshake_is_refused_before_the_upgrade(
        self, game: QuizifyGameState, tmp_path: Path
    ) -> None:
        """End to end: ``?role=admin`` over a rebound name gets 403, not admin.

        ``Host`` is set explicitly because that is precisely what the rebind
        achieves — the browser sends the attacker's name to the LAN address.
        """
        handler = QuizifyWebSocketHandler(
            runtime=_FakeRuntime(tmp_path), game_state_provider=lambda: game
        )
        handler._conn = ConnectionManager(_FakeRuntime(tmp_path), lambda: game)
        app = web.Application()
        app.router.add_get(WS_PATH, handler.handle)
        client = TestClient(TestServer(app))
        await client.start_server()
        try:
            with pytest.raises(WSServerHandshakeError) as exc:
                await client.ws_connect(
                    f"{WS_PATH}?role=admin",
                    headers={"Host": REBOUND, "Origin": f"http://{REBOUND}"},
                )
            assert exc.value.status == 403
            assert not handler._conn.has_admin_connections()
            assert len(handler._conn.connections) == 0
        finally:
            await client.close()


# ---------------------------------------------------------------------------
# The shapes a real host uses — every one of these must keep working
# ---------------------------------------------------------------------------


class TestLegitimateHostShapesStillPass:
    """Quizify is played on a LAN with phones that reach HA in five ways.

    A future tightening that breaks one of these breaks the game in the living
    room, so each shape gets its own named test rather than a parametrised
    blur.
    """

    def test_an_ipv4_literal(self, tmp_path: Path) -> None:
        """The QR code usually carries the raw LAN address."""
        req = _Req("192.168.0.69:8123", "http://192.168.0.69:8123")
        assert is_origin_allowed(req, _FakeRuntime(tmp_path))

    def test_an_ipv6_literal(self, tmp_path: Path) -> None:
        """A v6-only LAN sends a bracketed literal."""
        req = _Req("[fd00::1]:8123", "http://[fd00::1]:8123")
        assert is_origin_allowed(req, _FakeRuntime(tmp_path))

    def test_the_mdns_name(self, tmp_path: Path) -> None:
        """``homeassistant.local`` is what HA advertises over mDNS."""
        req = _Req("homeassistant.local:8123", "http://homeassistant.local:8123")
        assert is_origin_allowed(req, _FakeRuntime(tmp_path))

    def test_the_mdns_name_as_a_fully_qualified_name(self, tmp_path: Path) -> None:
        """Some resolvers hand the browser the trailing dot."""
        req = _Req("homeassistant.local.:8123", "http://homeassistant.local.:8123")
        assert is_origin_allowed(req, _FakeRuntime(tmp_path))

    def test_a_single_label_hostname(self, tmp_path: Path) -> None:
        """``http://homeassistant:8123`` — a router's DHCP name, no dot.

        A dotless name cannot exist in the public DNS, so it cannot be the
        rebinding page's origin.
        """
        req = _Req("homeassistant:8123", "http://homeassistant:8123")
        assert is_origin_allowed(req, _FakeRuntime(tmp_path))

    def test_localhost(self, tmp_path: Path) -> None:
        """The dev server, and the loopback forwarder used for browser tests."""
        req = _Req("localhost:8123", "http://localhost:8123")
        assert is_origin_allowed(req, _FakeRuntime(tmp_path))

    def test_loopback_ip(self, tmp_path: Path) -> None:
        req = _Req("127.0.0.1:8123", "http://127.0.0.1:8123")
        assert is_origin_allowed(req, _FakeRuntime(tmp_path))

    def test_a_nabu_casa_remote_ui_host(self, tmp_path: Path) -> None:
        """Remote UI: the browser's Origin is the cloud host, not the LAN.

        ``_nabu_casa_host`` imports ``homeassistant.components.cloud`` lazily,
        so the host is injected here as ``external_url`` — the same code path
        the cloud host feeds into.
        """
        runtime = _HARuntime(
            tmp_path, external_url="https://abc123.ui.nabu.casa"
        )
        req = _Req("192.168.0.69:8123", "https://abc123.ui.nabu.casa")
        assert is_origin_allowed(req, runtime)

    def test_a_reverse_proxy_named_in_external_url(self, tmp_path: Path) -> None:
        """A public name is allowed exactly when it was configured."""
        runtime = _HARuntime(tmp_path, external_url="https://quiz.example.com")
        req = _Req("quiz.example.com", "https://quiz.example.com")
        assert is_origin_allowed(req, runtime)

    def test_a_custom_lan_domain_named_in_internal_url(self, tmp_path: Path) -> None:
        """``ha.fritz.box`` is a public-TLD name — configuring it is the way in.

        This is the shape that cannot be told apart from the attack shape
        without configuration, and this test is what documents the remedy.
        """
        runtime = _HARuntime(tmp_path, internal_url="http://ha.fritz.box:8123")
        req = _Req("ha.fritz.box:8123", "http://ha.fritz.box:8123")
        assert is_origin_allowed(req, runtime)

    def test_a_home_arpa_name(self, tmp_path: Path) -> None:
        """RFC 8375 reserved this zone for home networks."""
        req = _Req("ha.home.arpa:8123", "http://ha.home.arpa:8123")
        assert is_origin_allowed(req, _FakeRuntime(tmp_path))

    def test_a_dot_lan_name(self, tmp_path: Path) -> None:
        """The suffix OpenWrt and most consumer routers hand out."""
        req = _Req("ha.lan:8123", "http://ha.lan:8123")
        assert is_origin_allowed(req, _FakeRuntime(tmp_path))

    def test_no_origin_still_passes_from_a_lan_host(self, tmp_path: Path) -> None:
        """Non-browser clients — the dev server, scripts, the tests."""
        assert is_origin_allowed(_Req("192.168.0.69:8123"), _FakeRuntime(tmp_path))

    def test_a_request_with_no_host_at_all_is_not_judged(
        self, tmp_path: Path
    ) -> None:
        """Test doubles and internal calls have no Host; that is not an attack."""
        req = SimpleNamespace(headers={}, remote=None)
        assert is_origin_allowed(req, _FakeRuntime(tmp_path))


# ---------------------------------------------------------------------------
# The port distinction #785 bought, kept
# ---------------------------------------------------------------------------


class TestNeighbouringServicesAreStillForeign:
    def test_another_port_on_the_same_lan_box_is_refused(
        self, tmp_path: Path
    ) -> None:
        """An add-on on :9000 is not Quizify, even though the host is local."""
        req = _Req("192.168.0.69:8123", "http://192.168.0.69:9000")
        assert not is_origin_allowed(req, _FakeRuntime(tmp_path))

    def test_another_lan_machine_is_refused(self, tmp_path: Path) -> None:
        """A device down the hall is a different origin, private or not."""
        req = _Req("192.168.0.69:8123", "http://192.168.0.99:8123")
        assert not is_origin_allowed(req, _FakeRuntime(tmp_path))

    def test_another_mdns_name_is_refused(self, tmp_path: Path) -> None:
        req = _Req("homeassistant.local:8123", "http://printer.local:8123")
        assert not is_origin_allowed(req, _FakeRuntime(tmp_path))


class TestPrivateHostPredicate:
    """The predicate on its own, because the whole gate rests on it."""

    @pytest.mark.parametrize(
        "netloc",
        [
            "192.168.0.69:8123",
            "10.0.0.5",
            "[fd00::1]:8123",
            "::1",
            "localhost:8123",
            "homeassistant:8123",
            "homeassistant.local:8123",
            "homeassistant.local.:8123",
            "ha.home.arpa:8123",
            "ha.internal",
            "ha.lan",
            "ha.home",
            "ha.intranet",
        ],
    )
    def test_private(self, netloc: str) -> None:
        assert is_private_network_host(netloc)

    @pytest.mark.parametrize(
        "netloc",
        [
            "evil.example:8123",
            "quiz.example.com",
            "ha.fritz.box:8123",
            "notlocal.evil.com",
            "evil.com",
        ],
    )
    def test_public(self, netloc: str) -> None:
        assert not is_private_network_host(netloc)
