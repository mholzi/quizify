"""One settings object, one construction site, one milestone (#789).

The five house consumers used to be built twice from the same options — once in
``async_setup_entry`` and once, with identical keyword arguments, in the nested
``_update_listener`` closure. Because each object fused its config-entry values
with the admin panel's runtime overrides, the reload had to snapshot every
consumer, detach it, rebuild it, restore the snapshot, re-stash it under a
string key and re-point the WS handler at it. Four classes carried an
``export/restore_runtime_config`` pair that existed for no other reason.

What replaces it:

* :class:`HouseSettings` — the config-entry defaults in one mutable object every
  consumer holds a reference to and reads lazily;
* :func:`build_house_consumers` — the single construction site;
* :class:`~custom_components.quizify.server.context.AppContext` — where the
  consumers now live;
* ``services.py`` — the seven HA service handlers, out of ``async_setup_entry``.

The reload assertions that matter are at the bottom, driven through the REAL
config-entries state machine: change an option and the running consumers must
see the new value *and* keep their runtime overrides.
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from custom_components.quizify.const import (  # noqa: E402
    CONF_FINALE_SCENE,
    CONF_HOUSE_EVENTS_ENABLED,
    CONF_LOBBY_MUSIC_URL,
    CONF_MEDIA_PLAYER_ENTITY,
    CONF_PARTY_LIGHT_ENTITIES,
    CONF_SFX_CORRECT_URL,
    CONF_TTS_ENTITY,
    DOMAIN,
)
from custom_components.quizify.game.state import QuizifyGameState  # noqa: E402
from custom_components.quizify.house import (  # noqa: E402
    build_house_consumers,
)
from custom_components.quizify.house_settings import HouseSettings  # noqa: E402


class _FakeRuntime:
    def __init__(self, tmp_path: Path) -> None:
        self.data_dir = tmp_path


@pytest.fixture
def game(tmp_path: Path) -> QuizifyGameState:
    return QuizifyGameState(runtime=_FakeRuntime(tmp_path), entry_id="t")


# ---------------------------------------------------------------------------
# HouseSettings
# ---------------------------------------------------------------------------


def test_options_are_normalised_once_for_every_consumer() -> None:
    """Whitespace, empties and duplicates are cleaned in ONE place — the five
    consumers used to each carry their own copy of this."""
    settings = HouseSettings.from_options(
        {
            CONF_PARTY_LIGHT_ENTITIES: [" light.a ", "", "light.a", "light.b"],
            CONF_FINALE_SCENE: "  ",
            CONF_MEDIA_PLAYER_ENTITY: " media_player.tv ",
            CONF_TTS_ENTITY: "",
            CONF_SFX_CORRECT_URL: " http://a.test/c.mp3 ",
        }
    )

    assert settings.light_entities == ["light.a", "light.b"]
    assert settings.finale_scene is None
    assert settings.media_player == "media_player.tv"
    assert settings.tts_entity is None
    assert settings.cue_urls == {
        "correct": "http://a.test/c.mp3",
        "wrong": None,
        "streak": None,
        "winner": None,
    }


def test_house_events_default_off_at_the_config_layer() -> None:
    assert HouseSettings.from_options({}).house_enabled is False
    assert HouseSettings.from_options({CONF_HOUSE_EVENTS_ENABLED: True}).master_enabled


def test_update_never_touches_the_panel_master() -> None:
    """The whole point of splitting the two sources apart (#411)."""
    settings = HouseSettings.from_options({CONF_HOUSE_EVENTS_ENABLED: True})
    settings.enabled_override = False  # the panel silenced the house mid-game

    settings.update_from_options({CONF_HOUSE_EVENTS_ENABLED: True})

    assert settings.enabled_override is False
    assert settings.master_enabled is False


# ---------------------------------------------------------------------------
# The single construction site
# ---------------------------------------------------------------------------


def test_the_five_consumers_share_one_settings_object(game) -> None:
    """A second construction site is what made a new option need two edits."""
    house = build_house_consumers(
        None, {CONF_PARTY_LIGHT_ENTITIES: ["light.a"]}, game
    )

    for _name, consumer in house.as_pairs():
        assert consumer._settings is house.settings

    # And updating that one object is visible on every one of them.
    house.settings.update_from_options(
        {
            CONF_PARTY_LIGHT_ENTITIES: ["light.b"],
            CONF_MEDIA_PLAYER_ENTITY: "media_player.new",
            CONF_TTS_ENTITY: "tts.new",
        }
    )
    assert house.party_lights._active_entity_ids == ["light.b"]
    assert house.tts_announcer._active_tts_entity == "tts.new"
    assert house.sound_effects._active_media_player == "media_player.new"
    assert house.lobby_music._media_player_entity_id == "media_player.new"


def test_as_pairs_covers_every_consumer(game) -> None:
    """The unload sweep (#605) iterates this — a consumer missing from it is a
    listener leak that survives removing the integration."""
    house = build_house_consumers(None, {}, game)
    assert [name for name, _ in house.as_pairs()] == [
        "party_lights",
        "tts_announcer",
        "lobby_music",
        "event_emitter",
        "sound_effects",
    ]


# ---------------------------------------------------------------------------
# The milestone fan-out
# ---------------------------------------------------------------------------


def test_narration_is_not_gated_behind_the_house_master(game) -> None:
    """The deliberate asymmetry in the fan-out (#789).

    Lights and SFX subscribe to the event emitter, whose fires are gated on the
    house master (``CONF_HOUSE_EVENTS_ENABLED``, off out of the box). Narration
    has its own master in the TTS panel, so it is fanned out directly instead —
    routing it through the bus would silence the quizmaster on every install
    that has narration on and house events off, which is the default shape.
    """
    house = build_house_consumers(None, {}, game)
    assert house.event_emitter._master_enabled is False

    house.tts_announcer.configure(
        enabled=True,
        announce_question=True,
        announce_reveal=True,
        announce_standings=True,
    )

    assert house.tts_announcer._enabled is True


# ---------------------------------------------------------------------------
# Through the real config-entries state machine
# ---------------------------------------------------------------------------

pytest.importorskip("homeassistant")
pytest.importorskip("pytest_homeassistant_custom_component")

from homeassistant.core import HomeAssistant  # noqa: E402
from homeassistant.setup import async_setup_component  # noqa: E402
from pytest_homeassistant_custom_component.common import (  # noqa: E402
    MockConfigEntry,
)

pytestmark = pytest.mark.usefixtures("enable_custom_integrations")

#: Every service ``async_register_services`` is responsible for. They used to be
#: seven closures inside ``async_setup_entry``.
_SERVICES = (
    "reset_admin_session",
    "start_game",
    "next_round",
    "pause",
    "resume",
    "end_game",
    "reload_packs",
)


@pytest.fixture(autouse=True)
def _stub_frontend_panel():
    """Stub the frontend panel helpers (no hass_frontend wheel under test)."""
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
    assert await async_setup_component(hass, "http", {"http": {}})
    await hass.async_block_till_done()
    return hass


async def _setup(hass: HomeAssistant, **options) -> MockConfigEntry:
    entry = MockConfigEntry(domain=DOMAIN, unique_id=DOMAIN, options=options or {})
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id) is True
    await hass.async_block_till_done()
    return entry


async def test_the_services_are_registered_from_their_own_module(
    http_hass: HomeAssistant,
) -> None:
    hass = http_hass
    await _setup(hass)
    for service in _SERVICES:
        assert hass.services.has_service(DOMAIN, service), service


async def test_the_consumers_live_on_the_app_context(
    http_hass: HomeAssistant,
) -> None:
    """``context.py`` says AppContext replaced the ``hass.data[DOMAIN]`` service
    locator; the house consumers were the part that had not moved (#789)."""
    hass = http_hass
    await _setup(hass)
    ctx = hass.data[DOMAIN]["ctx"]

    assert ctx.house is not None
    # The hass.data keys are a mirror of the same objects, kept for the existing
    # lookups — since nothing is rebuilt they can no longer drift apart.
    for name, consumer in ctx.house.as_pairs():
        assert hass.data[DOMAIN][name] is consumer


async def test_unload_detaches_the_consumers_off_the_context(
    http_hass: HomeAssistant,
) -> None:
    """The #605 listener-leak guard, now reading from the AppContext."""
    hass = http_hass
    entry = await _setup(
        hass,
        **{
            CONF_PARTY_LIGHT_ENTITIES: ["light.a"],
            CONF_MEDIA_PLAYER_ENTITY: "media_player.tv",
            CONF_HOUSE_EVENTS_ENABLED: True,
        },
    )
    house = hass.data[DOMAIN]["ctx"].house
    assert house.party_lights._event_unsubs
    assert house.sound_effects._event_unsubs

    assert await hass.config_entries.async_unload(entry.entry_id) is True
    await hass.async_block_till_done()

    assert house.party_lights._event_unsubs == []
    assert house.sound_effects._event_unsubs == []


async def test_a_reload_lands_new_options_without_dropping_panel_overrides(
    http_hass: HomeAssistant,
) -> None:
    """THE #789 test: change an option, reload, and the RUNNING consumers must
    see the new value while keeping every runtime override.

    Both halves used to be one mechanism fighting itself. Rebuilding from the
    fresh options is what made the new values land — and is exactly what wiped
    the panel's settings, which is why four classes grew a snapshot protocol to
    put them back. Nothing is rebuilt now, so the two halves are independent.
    """
    hass = http_hass
    entry = await _setup(
        hass,
        **{
            CONF_PARTY_LIGHT_ENTITIES: ["light.old"],
            CONF_MEDIA_PLAYER_ENTITY: "media_player.old",
            CONF_TTS_ENTITY: "tts.old",
            CONF_FINALE_SCENE: "scene.old",
        },
    )
    house = hass.data[DOMAIN]["ctx"].house

    # The admin panels set their per-game overrides mid-game.
    house.tts_announcer.configure(
        enabled=True,
        announce_question=True,
        announce_reveal=False,
        announce_standings=True,
        tts_entity="tts.from_panel",
    )
    house.party_lights.configure(
        enabled=True, light_streak=False, light_entities=["light.from_panel"]
    )
    house.sound_effects.configure(enabled=True, sfx_winner=False)

    # The host now edits the integration options.
    hass.config_entries.async_update_entry(
        entry,
        options={
            CONF_PARTY_LIGHT_ENTITIES: ["light.new"],
            CONF_MEDIA_PLAYER_ENTITY: "media_player.new",
            CONF_TTS_ENTITY: "tts.new",
            CONF_FINALE_SCENE: "scene.new",
            CONF_SFX_CORRECT_URL: "http://a.test/correct.mp3",
            CONF_LOBBY_MUSIC_URL: "http://a.test/lobby.mp3",
        },
    )
    await hass.async_block_till_done()

    assert hass.data[DOMAIN]["ctx"].house is house  # same live consumers

    # Half one — the NEW config-entry values are live on the running objects.
    assert house.party_lights._entity_ids == ["light.new"]
    assert house.party_lights._finale_scene == "scene.new"
    assert house.tts_announcer._tts_entity_id == "tts.new"
    assert house.sound_effects._media_player_entity_id == "media_player.new"
    assert house.sound_effects._cue_urls["correct"] == "http://a.test/correct.mp3"
    assert house.lobby_music._media_player_entity_id == "media_player.new"

    # Half two — every runtime override survived, so the game in progress keeps
    # narrating and keeps the choreography the host picked.
    assert house.tts_announcer._enabled is True
    assert house.tts_announcer._announce_reveal is False
    assert house.tts_announcer._active_tts_entity == "tts.from_panel"
    assert house.party_lights._master_enabled is True
    assert house.party_lights._light_streak is False
    assert house.party_lights._active_entity_ids == ["light.from_panel"]
    assert house.sound_effects._cue_enabled["winner"] is False
    assert house.event_emitter._master_enabled is True

    # …and the announcer's fallback, where the panel set no override, followed
    # the options change instead.
    assert house.tts_announcer._active_media_player == "media_player.new"


async def test_a_first_time_speaker_arms_the_sfx_listeners_on_reload(
    http_hass: HomeAssistant,
) -> None:
    """The side effect the rebuild used to have for free: a host who configures
    their first speaker/light has to get the bus listeners armed."""
    hass = http_hass
    entry = await _setup(hass, **{CONF_HOUSE_EVENTS_ENABLED: True})
    house = hass.data[DOMAIN]["ctx"].house
    assert house.sound_effects._event_unsubs == []
    assert house.party_lights._event_unsubs == []

    hass.config_entries.async_update_entry(
        entry,
        options={
            CONF_HOUSE_EVENTS_ENABLED: True,
            CONF_MEDIA_PLAYER_ENTITY: "media_player.new",
            CONF_PARTY_LIGHT_ENTITIES: ["light.new"],
        },
    )
    await hass.async_block_till_done()

    assert house.sound_effects._event_unsubs
    assert house.party_lights._event_unsubs
