"""Tests for the options-flow re-attach path in ``__init__`` (issue #328).

``async_setup_entry`` registers an ``_update_listener`` via
``entry.add_update_listener`` so that toggling the optional HA integrations
(party lights, TTS announcer, lobby music) and the community-pack submit
settings in the options flow takes effect live, without an HA restart. That
listener body (lines ~218-263 of ``custom_components/quizify/__init__``) sat
uncovered because no test ever updated the entry's options after setup.

These drive the real config-entries state machine: setup the entry, then call
``hass.config_entries.async_update_entry`` with new ``options`` — HA fires the
registered update listener — and assert the observable effects:

* the three optional-integration helpers in ``hass.data[DOMAIN]`` are *replaced*
  with fresh instances reflecting the new options,
* the new party-lights helper is wired to the new entity ids,
* ``game_state.lobby_music_url`` and the ``ctx`` community-submit fields are
  updated in place,
* the ws handler is re-pointed at the new TTS announcer.

Test-only, no behaviour change. Reuses the harness #313 established. Needs the
real ``homeassistant`` package + the harness; skipped (not errored) when those
aren't installed locally; CI installs them.
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

from custom_components.quizify.const import (  # noqa: E402
    CONF_COMMUNITY_SUBMIT_SECRET,
    CONF_COMMUNITY_SUBMIT_URL,
    CONF_LOBBY_MUSIC_URL,
    CONF_MEDIA_PLAYER_ENTITY,
    CONF_PARTY_LIGHT_ENTITIES,
    CONF_TTS_ENTITY,
    DOMAIN,
)

pytestmark = pytest.mark.usefixtures("enable_custom_integrations")


@pytest.fixture(autouse=True)
def _stub_frontend_panel():
    """Patch the two frontend panel helpers the integration calls.

    Mirrors the harness in ``test_init_313`` — the ``frontend`` component can't
    be set up under the test harness, and ``__init__`` imports the panel
    helpers inside its setup/unload functions, so patch them at the source.
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
    """A hass with the HTTP component set up (setup mounts routes on it)."""
    assert await async_setup_component(hass, "http", {"http": {}})
    await hass.async_block_till_done()
    return hass


async def _setup(hass: HomeAssistant) -> MockConfigEntry:
    """Set the entry up through the real config-entries state machine.

    Going through ``async_setup`` registers the update listener exactly as it
    would in production, so a later ``async_update_entry`` fires it for real.
    """
    entry = MockConfigEntry(domain=DOMAIN, unique_id=DOMAIN)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id) is True
    await hass.async_block_till_done()
    return entry


async def test_options_update_reattaches_optional_integrations(
    http_hass: HomeAssistant,
) -> None:
    """Updating the entry options fires ``_update_listener``, which detaches the
    old optional-integration helpers and re-attaches fresh ones wired to the new
    options — without an HA restart."""
    hass = http_hass
    entry = await _setup(hass)
    data = hass.data[DOMAIN]

    # Capture the originally-attached helper instances (unconfigured at setup).
    old_pl = data["party_lights"]
    old_tts = data["tts_announcer"]
    old_lm = data["lobby_music"]
    assert old_pl.is_configured is False

    new_lights = ["light.living_room", "light.kitchen"]
    hass.config_entries.async_update_entry(
        entry,
        options={
            CONF_PARTY_LIGHT_ENTITIES: new_lights,
            CONF_TTS_ENTITY: "tts.google_en",
            CONF_MEDIA_PLAYER_ENTITY: "media_player.living_room",
        },
    )
    await hass.async_block_till_done()

    data = hass.data[DOMAIN]
    # The helpers were replaced with brand-new instances (re-attach, not reuse).
    assert data["party_lights"] is not old_pl
    assert data["tts_announcer"] is not old_tts
    assert data["lobby_music"] is not old_lm

    # The new party-lights helper reflects the freshly-configured entity ids…
    assert data["party_lights"]._entity_ids == new_lights
    assert data["party_lights"].is_configured is True
    # …and the new TTS announcer is now fully configured too.
    assert data["tts_announcer"].is_configured is True

    # The ws handler is re-pointed at the new TTS announcer (so milestone
    # announcements use the live instance, not the stale one).
    assert hass.data[DOMAIN]["ws_handler"]._tts_announcer is data["tts_announcer"]


async def test_options_update_propagates_url_and_community_fields(
    http_hass: HomeAssistant,
) -> None:
    """The listener also pushes the lobby-music URL onto the game state and the
    community-submit settings onto the AppContext in place (no new objects)."""
    hass = http_hass
    entry = await _setup(hass)
    data = hass.data[DOMAIN]
    game_state = data["game"]
    ctx = data["ctx"]

    hass.config_entries.async_update_entry(
        entry,
        options={
            CONF_LOBBY_MUSIC_URL: "http://example.test/lobby.mp3",
            CONF_COMMUNITY_SUBMIT_URL: "https://worker.example.test/submit",
            CONF_COMMUNITY_SUBMIT_SECRET: "s3cr3t",
        },
    )
    await hass.async_block_till_done()

    # ``ctx`` and ``game`` are mutated in place — same objects, new values.
    assert hass.data[DOMAIN]["ctx"] is ctx
    assert hass.data[DOMAIN]["game"] is game_state
    assert game_state.lobby_music_url == "http://example.test/lobby.mp3"
    assert ctx.community_submit_url == "https://worker.example.test/submit"
    assert ctx.community_submit_secret == "s3cr3t"


async def test_options_update_clears_fields_when_emptied(
    http_hass: HomeAssistant,
) -> None:
    """Emptying the options again resets the live state back to ``None`` — the
    ``(x or "").strip() or None`` normalisation in the listener is exercised."""
    hass = http_hass
    entry = await _setup(hass)

    # First populate, then clear with whitespace-only values.
    hass.config_entries.async_update_entry(
        entry,
        options={
            CONF_LOBBY_MUSIC_URL: "http://example.test/lobby.mp3",
            CONF_COMMUNITY_SUBMIT_URL: "https://worker.example.test/submit",
        },
    )
    await hass.async_block_till_done()
    hass.config_entries.async_update_entry(
        entry,
        options={
            CONF_LOBBY_MUSIC_URL: "   ",
            CONF_COMMUNITY_SUBMIT_URL: "",
        },
    )
    await hass.async_block_till_done()

    data = hass.data[DOMAIN]
    assert data["game"].lobby_music_url is None
    assert data["ctx"].community_submit_url is None
    # An unconfigured party-lights helper is re-attached (no entity ids given).
    assert data["party_lights"].is_configured is False
