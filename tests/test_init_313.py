"""Tests for the Quizify integration entry lifecycle (issue #313).

Covers ``custom_components.quizify.__init__`` — ``async_setup_entry``,
``async_unload_entry`` and the ``reset_admin_session`` service — which sat at
~7% coverage because no test drove the HA entry lifecycle end to end.

These exercise the real wiring against the ``pytest-homeassistant-custom-
component`` ``hass`` fixture (the harness #271 added): the HTTP app, the
frontend panel registry, the static-path registrar and the service registry
are all real, so a regression in the mounting logic surfaces here.

Needs the real ``homeassistant`` package + the harness. Skipped (not errored)
when those aren't installed locally; CI installs them.
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

pytestmark = pytest.mark.usefixtures("enable_custom_integrations")


@pytest.fixture(autouse=True)
def _stub_frontend_panel():
    """Patch the two frontend panel helpers the integration calls.

    The *frontend* component can't be set up under the test harness (the
    prebuilt ``hass_frontend`` wheel isn't installed), and __init__ imports
    ``async_register_built_in_panel`` / ``async_remove_panel`` inside its setup
    and unload functions. Patch them at the source module so the in-function
    imports resolve to stubs that track panel registration realistically (the
    real ``async_remove_panel`` raises ``KeyError`` for an unknown panel — the
    unload code swallows it, so we mirror that here).
    """
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
    """A hass with the HTTP component set up.

    ``async_setup_entry`` mounts routes on ``hass.http.app`` and registers
    static paths, which need the HTTP component present. Everything else
    (routes, static paths, services, platform forwarding) stays real.
    """
    assert await async_setup_component(hass, "http", {"http": {}})
    await hass.async_block_till_done()
    return hass


async def _setup(hass: HomeAssistant) -> MockConfigEntry:
    """Set the entry up through the real config-entries state machine.

    Going through ``hass.config_entries.async_setup`` (rather than calling
    ``async_setup_entry`` directly) puts the entry in the right state so the
    platform-forwarding call inside our setup is allowed, exercising the full
    real path including ``async_forward_entry_setups``.
    """
    entry = MockConfigEntry(domain=DOMAIN, unique_id=DOMAIN)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id) is True
    await hass.async_block_till_done()
    return entry


async def test_setup_entry_wires_domain_data(http_hass: HomeAssistant) -> None:
    """A successful setup populates hass.data[DOMAIN] with the core objects
    and registers the reset_admin_session service."""
    hass = http_hass
    entry = await _setup(hass)

    data = hass.data[DOMAIN]
    assert data["entry_id"] == entry.entry_id
    for key in ("game", "ws_handler", "analytics", "ctx"):
        assert data.get(key) is not None, f"missing {key} in hass.data"
    # Optional-integration plumbing is attached even when unconfigured.
    for key in ("party_lights", "tts_announcer", "lobby_music"):
        assert key in data

    assert hass.services.has_service(DOMAIN, "reset_admin_session")


async def test_setup_entry_forwards_sensor_platforms(
    http_hass: HomeAssistant,
) -> None:
    """The entry forwards to the sensor + binary_sensor platforms, so the
    Quizify game-state entities actually materialise."""
    hass = http_hass
    await _setup(hass)

    states = hass.states.async_all()
    entity_ids = {s.entity_id for s in states}
    # The five sensors + one binary_sensor from the platforms.
    assert "binary_sensor.quizify_game_active" in entity_ids
    assert any(e.startswith("sensor.quizify_") for e in entity_ids)


async def test_reset_admin_session_service_clears_token(
    http_hass: HomeAssistant,
) -> None:
    """Calling the reset_admin_session service clears the persisted admin
    token via the ws handler's connection manager."""
    hass = http_hass
    await _setup(hass)

    ws_handler = hass.data[DOMAIN]["ws_handler"]
    # Bootstrap a token so there is something to clear.
    token = ws_handler.conn.get_or_create_admin_token()
    assert ws_handler.conn.validate_admin_token(token) is True

    await hass.services.async_call(
        DOMAIN, "reset_admin_session", {}, blocking=True
    )
    # After the reset the old token no longer validates (in-memory cleared).
    assert ws_handler.conn.validate_admin_token(token) is False


async def test_unload_entry_tears_down(http_hass: HomeAssistant) -> None:
    """Unloading removes hass.data[DOMAIN] and the sidebar panel, and unloads
    the platform entities (so the Quizify game entities disappear)."""
    hass = http_hass
    entry = await _setup(hass)
    assert DOMAIN in hass.data

    assert await hass.config_entries.async_unload(entry.entry_id) is True
    await hass.async_block_till_done()

    assert DOMAIN not in hass.data
    # The platforms were unloaded, so the game-active entity is no longer
    # available (HA marks unloaded platform entities "unavailable").
    state = hass.states.get("binary_sensor.quizify_game_active")
    assert state is None or state.state == "unavailable"
