"""Unloading must unsubscribe the house consumers, not just empty hass.data (#605).

``async_unload_entry`` flushed stats, cancelled game tasks, removed the panel
and popped ``hass.data[DOMAIN]`` — but never called ``detach()`` on the five
house consumers, while the options-change path right next to it detached all
five. The asymmetry was the bug.

Popping the dict unsubscribes nothing. ``QuizifyPartyLights.attach_events``
holds five ``hass.bus.async_listen`` handles and ``QuizifySoundEffects`` three;
those live on the bus. The lights' pulse task is cancelled inside ``detach()``
too. So after N reloads one ``quizify_*`` event drove ``light.turn_on`` /
``media_player.play_media`` N+1 times, and after *removing* the integration the
lights and SFX kept reacting until Home Assistant restarted.

Trigger: any reload — the Integrations UI button, and every HACS update.

**Why the existing test did not catch it.** ``test_unload_entry_tears_down``
asserts ``hass.data`` is empty and the entities are gone. Both are true while
the subscriptions leak, so the assertion passes and says nothing. These tests
count listeners on the bus instead — the thing that actually leaked.
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
    CONF_HOUSE_EVENTS_ENABLED,
    CONF_PARTY_LIGHT_ENTITIES,
    DOMAIN,
)
from custom_components.quizify.game_events import (  # noqa: E402
    EVENT_ANSWER_REVEALED,
    EVENT_QUESTION_SHOWN,
)

pytestmark = pytest.mark.usefixtures("enable_custom_integrations")

# The consumers only subscribe when they are configured; an unconfigured
# instance holds no handles, and the leak would be invisible.
CONFIGURED = {
    CONF_PARTY_LIGHT_ENTITIES: ["light.living_room"],
    CONF_HOUSE_EVENTS_ENABLED: True,
}

WATCHED_EVENTS = (EVENT_QUESTION_SHOWN, EVENT_ANSWER_REVEALED)


@pytest.fixture(autouse=True)
def _stub_frontend_panel():
    """Patch the frontend panel helpers (harness from #313)."""
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


def _listener_counts(hass: HomeAssistant) -> dict[str, int]:
    """Listeners per watched event — the number that actually leaked."""
    listeners = hass.bus.async_listeners()
    return {event: listeners.get(event, 0) for event in WATCHED_EVENTS}


async def _setup(hass: HomeAssistant) -> MockConfigEntry:
    entry = MockConfigEntry(domain=DOMAIN, unique_id=DOMAIN, options=CONFIGURED)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id) is True
    await hass.async_block_till_done()
    return entry


async def test_setup_actually_subscribes(http_hass: HomeAssistant) -> None:
    """Guards the premise: without subscriptions there is nothing to leak."""
    hass = http_hass
    before = _listener_counts(hass)
    await _setup(hass)
    after = _listener_counts(hass)

    assert any(after[e] > before[e] for e in WATCHED_EVENTS), (
        "no house consumer subscribed — the leak tests below would be vacuous"
    )


async def test_unload_returns_the_bus_to_its_pre_setup_state(
    http_hass: HomeAssistant,
) -> None:
    """The assertion the old unload test was missing."""
    hass = http_hass
    before = _listener_counts(hass)

    entry = await _setup(hass)
    assert await hass.config_entries.async_unload(entry.entry_id) is True
    await hass.async_block_till_done()

    assert _listener_counts(hass) == before


async def test_repeated_reloads_do_not_stack_subscriptions(
    http_hass: HomeAssistant,
) -> None:
    """The symptom users see: double flashes and overlapping stings.

    One reload is enough to double every callback, so this asserts the steady
    state after several — a fix that detaches one consumer but not the rest
    would still grow here.
    """
    hass = http_hass
    entry = await _setup(hass)
    after_first_setup = _listener_counts(hass)

    for _ in range(3):
        assert await hass.config_entries.async_reload(entry.entry_id) is True
        await hass.async_block_till_done()

    assert _listener_counts(hass) == after_first_setup
