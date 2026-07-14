"""Options-reload continuity for the "House Plays Along" panel (#494 P4 / #411).

``__init__._update_listener`` rebuilds the optional-integration helpers from the
config entry on EVERY options change. Before #494 P4, ``QuizifyPartyLights`` and
``QuizifySoundEffects`` had no ``export_runtime_config`` /
``restore_runtime_config`` at all — so an unrelated options change (the host
tweaks the lobby-music URL mid-game) rebuilt them from scratch and silently wiped
every toggle and entity override the admin panel had just set. That is exactly
the #411 bug the TTS announcer already fixed; these are the regression guards for
the three house consumers.

The subtle half is the tri-state master. ``export`` snapshots the panel's
*override*, not the effective master, so:

* panel set a master  → it survives an unrelated reload (the #411 fix), while
* panel never opened  → the override stays ``None`` and a host toggling
  CONF_HOUSE_EVENTS_ENABLED in the options UI still sees it take effect.

Snapshotting the effective master instead would have quietly broken the second
case (the options-flow switch would do nothing until an HA restart).

These drive the REAL config-entries state machine — setup the entry, then
``async_update_entry`` with new options, which fires the registered listener for
real. Reuses the harness from ``test_init_reattach_328``. Needs the real
``homeassistant`` package + the custom-component harness; skipped (not errored)
where those aren't installed; CI installs them.
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
    CONF_FINALE_SCENE,
    CONF_HOUSE_EVENTS_ENABLED,
    CONF_LOBBY_MUSIC_URL,
    CONF_MEDIA_PLAYER_ENTITY,
    CONF_PARTY_LIGHT_ENTITIES,
    DOMAIN,
)

pytestmark = pytest.mark.usefixtures("enable_custom_integrations")


@pytest.fixture(autouse=True)
def _stub_frontend_panel():
    """Patch the frontend panel helpers (the component can't set up under test)."""
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


async def _setup(hass: HomeAssistant, **options) -> MockConfigEntry:
    """Set the entry up through the real config-entries state machine."""
    entry = MockConfigEntry(domain=DOMAIN, unique_id=DOMAIN, options=options or {})
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id) is True
    await hass.async_block_till_done()
    return entry


def _configure_house(hass, **overrides) -> None:
    """Push the panel's resolved config onto the three live house consumers."""
    data = hass.data[DOMAIN]
    cfg = {
        "enabled": True,
        "light_question": True,
        "light_countdown": True,
        "light_reveal": True,
        "light_streak": True,
        "light_winner": True,
        "winner_scene": True,
        "sfx_correct": True,
        "sfx_wrong": True,
        "sfx_streak": True,
        "sfx_winner": True,
        "light_entities": [],
        "media_player": "",
        "winner_scene_entity": "",
        **overrides,
    }
    data["event_emitter"].configure(enabled=cfg["enabled"])
    data["party_lights"].configure(
        enabled=cfg["enabled"],
        light_question=cfg["light_question"],
        light_countdown=cfg["light_countdown"],
        light_reveal=cfg["light_reveal"],
        light_streak=cfg["light_streak"],
        light_winner=cfg["light_winner"],
        winner_scene=cfg["winner_scene"],
        light_entities=cfg["light_entities"],
        winner_scene_entity=cfg["winner_scene_entity"],
    )
    data["sound_effects"].configure(
        enabled=cfg["enabled"],
        sfx_correct=cfg["sfx_correct"],
        sfx_wrong=cfg["sfx_wrong"],
        sfx_streak=cfg["sfx_streak"],
        sfx_winner=cfg["sfx_winner"],
        media_player=cfg["media_player"],
    )


async def test_reload_preserves_house_panel_config(http_hass: HomeAssistant) -> None:
    """THE #411 regression guard: an unrelated options change must not wipe the
    panel's toggles or entity overrides on lights, SFX and the emitter."""
    hass = http_hass
    entry = await _setup(
        hass,
        **{
            CONF_PARTY_LIGHT_ENTITIES: ["light.from_config"],
            CONF_MEDIA_PLAYER_ENTITY: "media_player.from_config",
            CONF_FINALE_SCENE: "scene.from_config",
        },
    )
    data = hass.data[DOMAIN]
    old_pl = data["party_lights"]
    old_sfx = data["sound_effects"]
    old_ev = data["event_emitter"]
    # House events default OFF at the config layer…
    assert old_ev.is_configured is False

    # …and the admin panel now turns the house on mid-game, with a partial
    # choreography and its own entity picks.
    _configure_house(
        hass,
        enabled=True,
        light_question=False,
        light_streak=False,
        winner_scene=False,
        sfx_correct=False,
        sfx_winner=False,
        light_entities=["light.from_panel"],
        media_player="media_player.from_panel",
        winner_scene_entity="scene.from_panel",
    )

    # An UNRELATED options change (lobby music) fires the reload listener.
    hass.config_entries.async_update_entry(
        entry,
        options={
            **dict(entry.options),
            CONF_LOBBY_MUSIC_URL: "http://example.test/lobby.mp3",
        },
    )
    await hass.async_block_till_done()

    new_pl = hass.data[DOMAIN]["party_lights"]
    new_sfx = hass.data[DOMAIN]["sound_effects"]
    new_ev = hass.data[DOMAIN]["event_emitter"]

    # Fresh instances (a real rebuild, not a lucky reuse) …
    assert new_pl is not old_pl
    assert new_sfx is not old_sfx
    assert new_ev is not old_ev

    # … whose panel config survived intact.
    assert new_pl._master_enabled is True
    assert new_pl._light_question is False
    assert new_pl._light_streak is False
    assert new_pl._light_countdown is True  # untouched toggle keeps its default
    assert new_pl._winner_scene is False
    assert new_pl._active_entity_ids == ["light.from_panel"]
    assert new_pl._active_finale_scene == "scene.from_panel"

    assert new_sfx._master_enabled is True
    assert new_sfx._cue_enabled == {
        "correct": False,
        "wrong": True,
        "streak": True,
        "winner": False,
    }
    assert new_sfx._active_media_player == "media_player.from_panel"

    # The emitter's master survived too — the bus keeps firing for automations.
    assert new_ev._master_enabled is True
    assert new_ev.is_configured is True

    # The WS handler is re-pointed at the FRESH consumers, mirroring how it is
    # re-pointed at the new TTS announcer. Without this, the next
    # ``configure_house`` frame from the panel would reconfigure the orphaned
    # pre-reload instances and the host's toggles would appear to do nothing.
    handler = hass.data[DOMAIN]["ws_handler"]
    assert handler._party_lights is new_pl
    assert handler._sound_effects is new_sfx
    assert handler._event_emitter is new_ev


async def test_reload_preserves_the_events_only_preset(
    http_hass: HomeAssistant,
) -> None:
    """"Events only": master on, every light/SFX toggle off. The reload must keep
    the bus alive AND keep the house quiet."""
    hass = http_hass
    entry = await _setup(
        hass,
        **{
            CONF_PARTY_LIGHT_ENTITIES: ["light.from_config"],
            CONF_MEDIA_PLAYER_ENTITY: "media_player.from_config",
        },
    )
    _configure_house(
        hass,
        enabled=True,
        light_question=False,
        light_countdown=False,
        light_reveal=False,
        light_streak=False,
        light_winner=False,
        winner_scene=False,
        sfx_correct=False,
        sfx_wrong=False,
        sfx_streak=False,
        sfx_winner=False,
    )

    hass.config_entries.async_update_entry(
        entry,
        options={**dict(entry.options), CONF_LOBBY_MUSIC_URL: "http://a.test/x.mp3"},
    )
    await hass.async_block_till_done()

    data = hass.data[DOMAIN]
    # The event backbone is live (host automations keep working) …
    assert data["event_emitter"].is_configured is True
    # … while every one of Quizify's own house effects stays off.
    pl = data["party_lights"]
    assert pl._master_enabled is True
    assert not any(
        (
            pl._light_question,
            pl._light_countdown,
            pl._light_reveal,
            pl._light_streak,
            pl._light_winner,
            pl._winner_scene,
        )
    )
    assert not any(data["sound_effects"]._cue_enabled.values())


async def test_reload_preserves_a_panel_master_switched_off(
    http_hass: HomeAssistant,
) -> None:
    """An explicit panel OFF must not be undone by a reload — even when the
    config entry says house events are ON."""
    hass = http_hass
    entry = await _setup(hass, **{CONF_HOUSE_EVENTS_ENABLED: True})
    assert hass.data[DOMAIN]["event_emitter"].is_configured is True

    _configure_house(hass, enabled=False)  # host silences the house mid-game

    hass.config_entries.async_update_entry(
        entry,
        options={**dict(entry.options), CONF_LOBBY_MUSIC_URL: "http://a.test/x.mp3"},
    )
    await hass.async_block_till_done()

    data = hass.data[DOMAIN]
    assert data["event_emitter"]._master_enabled is False
    assert data["event_emitter"].is_configured is False
    assert data["party_lights"]._master_enabled is False
    assert data["sound_effects"]._master_enabled is False


async def test_options_master_toggle_still_lands_when_panel_never_used(
    http_hass: HomeAssistant,
) -> None:
    """The counter-regression to the guard above (the tri-state master).

    A host who never opened the admin panel flips CONF_HOUSE_EVENTS_ENABLED in
    the options UI. Because nothing ever set a runtime override, the snapshot
    carries ``None`` and the config-entry value wins — the switch takes effect
    immediately, no HA restart. A naive ``{"enabled": <effective>}`` snapshot
    would have restored the stale ``False`` over it and made the switch inert.
    """
    hass = http_hass
    entry = await _setup(hass, **{CONF_PARTY_LIGHT_ENTITIES: ["light.x"]})
    data = hass.data[DOMAIN]
    assert data["event_emitter"].is_configured is False
    assert data["party_lights"]._master_enabled is False
    assert data["sound_effects"]._master_enabled is False

    # Options UI: turn house events ON. No panel/configure() ever happened.
    hass.config_entries.async_update_entry(
        entry, options={**dict(entry.options), CONF_HOUSE_EVENTS_ENABLED: True}
    )
    await hass.async_block_till_done()

    data = hass.data[DOMAIN]
    assert data["event_emitter"].is_configured is True
    assert data["party_lights"]._master_enabled is True
    assert data["sound_effects"]._master_enabled is True
    # …and the override genuinely stayed unset (not coerced to a hard False).
    assert data["party_lights"]._enabled_override is None
    assert data["event_emitter"]._enabled_override is None
    assert data["sound_effects"]._enabled_override is None


async def test_reload_rewires_entity_overrides_over_new_config_entities(
    http_hass: HomeAssistant,
) -> None:
    """A panel entity override keeps beating the config entry even when the
    options flow changes the underlying entities in the same reload."""
    hass = http_hass
    entry = await _setup(hass, **{CONF_PARTY_LIGHT_ENTITIES: ["light.old"]})
    _configure_house(hass, enabled=True, light_entities=["light.from_panel"])

    hass.config_entries.async_update_entry(
        entry, options={**dict(entry.options), CONF_PARTY_LIGHT_ENTITIES: ["light.new"]}
    )
    await hass.async_block_till_done()

    pl = hass.data[DOMAIN]["party_lights"]
    # The config-entry list was refreshed…
    assert pl._entity_ids == ["light.new"]
    # …but the panel's explicit pick still wins for the running game.
    assert pl._active_entity_ids == ["light.from_panel"]


async def test_panel_lights_override_survives_reload_on_a_bare_install(
    http_hass: HomeAssistant,
) -> None:
    """No lights configured in the options flow at all: the panel's pick is the
    ONLY thing making the lights configured, so it must survive the rebuild —
    including re-subscribing the accent listeners (restore runs before attach)."""
    hass = http_hass
    entry = await _setup(hass)  # no party lights, no media player
    assert hass.data[DOMAIN]["party_lights"].is_configured is False

    _configure_house(
        hass,
        enabled=True,
        light_entities=["light.from_panel"],
        media_player="media_player.from_panel",
    )
    assert hass.data[DOMAIN]["party_lights"].is_configured is True

    hass.config_entries.async_update_entry(
        entry,
        options={**dict(entry.options), CONF_LOBBY_MUSIC_URL: "http://a.test/x.mp3"},
    )
    await hass.async_block_till_done()

    new_pl = hass.data[DOMAIN]["party_lights"]
    new_sfx = hass.data[DOMAIN]["sound_effects"]
    # Still configured purely off the panel's override…
    assert new_pl.is_configured is True
    assert new_pl._active_entity_ids == ["light.from_panel"]
    assert new_sfx.is_configured is True
    assert new_sfx._active_media_player == "media_player.from_panel"
    # …and the accent listeners were actually re-subscribed (not left inert).
    assert new_pl._event_unsubs
    assert new_sfx._event_unsubs
