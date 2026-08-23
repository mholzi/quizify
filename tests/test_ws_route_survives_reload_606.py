"""A reload must leave the *current* WebSocket handler in charge (issue #606).

``async_setup_entry`` used to call ``router.add_get(WS_PATH, ws_handler.handle)``
on every setup with no guard. aiohttp normally freezes its router at startup so
a second registration would raise, but HA un-freezes it on purpose
(``homeassistant/components/http/__init__.py``: ``self.app._router.freeze =
lambda: None``), so the duplicate quietly succeeded. ``UrlDispatcher.resolve()``
matches in registration order, so the handler bound during the *first* setup
kept serving every socket forever, while ``game_state.set_broadcast_callback``
pointed at the new one — whose connection manager holds zero sockets. Every
broadcast went nowhere and the game looked hung, which is the same shape users
report in #586.

The trigger is any unload followed by a setup: the Integrations "Reload" button,
and every HACS update.

These tests drive the real config-entries state machine and inspect the real
aiohttp router, because the bug lived in the interaction between the two. They
were run against the unfixed code first, where both fail.

Not the same thing as the documented "an rsync of changed Python needs a full HA
restart" rule: that one is about ``sys.modules`` holding old modules. This one
bites with completely unchanged code.
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

import pytest

pytest.importorskip("homeassistant")
pytest.importorskip("pytest_homeassistant_custom_component")

from homeassistant.core import HomeAssistant  # noqa: E402
from homeassistant.setup import async_setup_component  # noqa: E402
from pytest_homeassistant_custom_component.common import (  # noqa: E402
    MockConfigEntry,
)

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from custom_components.quizify.const import DOMAIN  # noqa: E402
from custom_components.quizify.server import WS_PATH  # noqa: E402

pytestmark = pytest.mark.usefixtures("enable_custom_integrations")


@pytest.fixture(autouse=True)
def _stub_frontend_panel():
    """Patch the frontend panel helpers (same harness as #313 / #328)."""
    panels: dict = {}

    def _register_panel(_hass, *, frontend_url_path, **_kw):
        panels[frontend_url_path] = True

    def _remove_panel(_hass, path):
        if path not in panels:
            raise KeyError(path)
        del panels[path]

    with (
        patch(
            "homeassistant.components.frontend.async_register_built_in_panel",
            side_effect=_register_panel,
        ),
        patch(
            "homeassistant.components.frontend.async_remove_panel",
            side_effect=_remove_panel,
        ),
    ):
        yield panels


@pytest.fixture
async def http_hass(hass: HomeAssistant) -> HomeAssistant:
    """A hass with the HTTP component set up (setup mounts routes on it)."""
    assert await async_setup_component(hass, "http", {"http": {}})
    await hass.async_block_till_done()
    return hass


async def _setup(hass: HomeAssistant) -> MockConfigEntry:
    entry = MockConfigEntry(domain=DOMAIN, unique_id=DOMAIN)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id) is True
    await hass.async_block_till_done()
    return entry


async def _reload(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    """Unload and set up again — exactly what the Reload button does."""
    assert await hass.config_entries.async_unload(entry.entry_id) is True
    await hass.async_block_till_done()
    assert await hass.config_entries.async_setup(entry.entry_id) is True
    await hass.async_block_till_done()


def _ws_routes(hass: HomeAssistant) -> list:
    """Every GET route registered on the WebSocket path.

    Filtered to GET on purpose: ``add_get`` also creates an implicit HEAD route
    on the same resource pointing at the same handler, so counting raw routes
    reports 2 for a single registration and the count assertion below would pass
    for the wrong reason.
    """
    return [
        route
        for route in hass.http.app.router.routes()
        if getattr(route.resource, "canonical", None) == WS_PATH
        and route.method == "GET"
    ]


async def test_reload_does_not_stack_a_second_ws_route(
    http_hass: HomeAssistant,
) -> None:
    """The route is registered once per process, not once per setup.

    Before the fix this found two routes after one reload (and would grow by one
    on every further reload), with the *first* one winning resolution.
    """
    hass = http_hass
    entry = await _setup(hass)
    assert len(_ws_routes(hass)) == 1

    await _reload(hass, entry)
    assert len(_ws_routes(hass)) == 1

    await _reload(hass, entry)
    assert len(_ws_routes(hass)) == 1


async def test_the_route_reaches_the_handler_from_the_latest_setup(
    http_hass: HomeAssistant,
) -> None:
    """Resolution must land on the live handler, not the one it started with.

    This is the actual defect: with two stacked routes the socket went to the
    previous handler while broadcasts went to the current one. Asserting the
    route count alone would not have caught a fix that kept one route but still
    closed over a stale instance, so this test follows the route object to the
    handler it actually calls.
    """
    hass = http_hass
    entry = await _setup(hass)
    first_handler = hass.data[DOMAIN]["ws_handler"]

    await _reload(hass, entry)
    live_handler = hass.data[DOMAIN]["ws_handler"]
    assert live_handler is not first_handler, "reload should build a fresh handler"

    called_on: list = []

    async def _spy(request):  # noqa: ARG001
        called_on.append(live_handler)
        return "ok"

    with patch.object(live_handler, "handle", side_effect=_spy):
        route_handler = _ws_routes(hass)[0].handler
        assert await route_handler(object()) == "ok"

    assert called_on == [live_handler]


async def test_the_route_answers_503_while_unloaded(
    http_hass: HomeAssistant,
) -> None:
    """The path outlives the integration, so "not set up" needs an honest answer.

    ``async_unload_entry`` pops ``hass.data[DOMAIN]`` while the route stays on
    the router forever. Without this branch the dispatcher would raise on a
    socket that arrives between unload and setup.
    """
    hass = http_hass
    entry = await _setup(hass)
    assert await hass.config_entries.async_unload(entry.entry_id) is True
    await hass.async_block_till_done()

    route_handler = _ws_routes(hass)[0].handler
    response = await route_handler(object())
    assert response.status == 503
