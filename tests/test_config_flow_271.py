"""Tests for the Quizify config flow + options flow (issue #271).

These tests exercise ``custom_components.quizify.config_flow`` — the user
setup flow and the options flow (including the ``community_submit_url`` /
``community_submit_secret`` options added in #256). They need the real
``homeassistant`` package (``config_entries``, ``data_entry_flow`` …) plus the
``pytest-homeassistant-custom-component`` test harness (``hass`` fixture,
``MockConfigEntry``).

The repo's *local* dev environment may run an older Python where
``homeassistant`` cannot be installed (HA requires Python 3.13+). To keep the
existing suite green there, the whole module is skipped when either dependency
is missing — ``pytest.importorskip`` turns a missing import into a *skip*, not
a collection error. CI installs both (see ``.github/workflows/test.yml``), so
the tests actually run there.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

# HA + the custom-component test harness. Missing locally on old Python -> skip.
pytest.importorskip("homeassistant")
pytest.importorskip("pytest_homeassistant_custom_component")

from homeassistant import config_entries, data_entry_flow  # noqa: E402
from homeassistant.core import HomeAssistant  # noqa: E402
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

# pytest-homeassistant-custom-component requires this opt-in fixture to allow
# loading a custom integration from outside HA core.
pytestmark = pytest.mark.usefixtures("enable_custom_integrations")


async def test_user_flow_happy_path_creates_entry(hass: HomeAssistant) -> None:
    """The user step shows an empty form, then creates the single entry."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    assert result["type"] == data_entry_flow.FlowResultType.FORM
    assert result["step_id"] == "user"
    assert result["errors"] in (None, {})

    # Submitting (empty user_input != None) creates the entry.
    result2 = await hass.config_entries.flow.async_configure(
        result["flow_id"], user_input={}
    )
    assert result2["type"] == data_entry_flow.FlowResultType.CREATE_ENTRY
    assert result2["title"] == "Quizify"
    assert result2["data"] == {}


async def test_user_flow_single_instance_only(hass: HomeAssistant) -> None:
    """A second setup attempt aborts (single_instance / unique_id guard)."""
    MockConfigEntry(domain=DOMAIN, unique_id=DOMAIN).add_to_hass(hass)

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    assert result["type"] == data_entry_flow.FlowResultType.ABORT
    assert result["reason"] == "single_instance_allowed"


async def test_options_flow_form_lists_all_options(hass: HomeAssistant) -> None:
    """The options form exposes every configurable key, including the #256
    community-submit URL + secret."""
    entry = MockConfigEntry(domain=DOMAIN, unique_id=DOMAIN)
    entry.add_to_hass(hass)

    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] == data_entry_flow.FlowResultType.FORM
    assert result["step_id"] == "init"

    schema_keys = {str(marker) for marker in result["data_schema"].schema}
    for key in (
        CONF_PARTY_LIGHT_ENTITIES,
        CONF_TTS_ENTITY,
        CONF_MEDIA_PLAYER_ENTITY,
        CONF_LOBBY_MUSIC_URL,
        CONF_COMMUNITY_SUBMIT_URL,
        CONF_COMMUNITY_SUBMIT_SECRET,
    ):
        assert key in schema_keys, f"option {key!r} missing from options form"


async def test_options_flow_saves_community_submit_options(
    hass: HomeAssistant,
) -> None:
    """Submitting the options form persists the community-submit URL + secret
    (the #256 surface) onto the entry's options."""
    entry = MockConfigEntry(domain=DOMAIN, unique_id=DOMAIN)
    entry.add_to_hass(hass)

    result = await hass.config_entries.options.async_init(entry.entry_id)

    user_input = {
        CONF_PARTY_LIGHT_ENTITIES: [],
        CONF_TTS_ENTITY: "",
        CONF_MEDIA_PLAYER_ENTITY: "",
        CONF_LOBBY_MUSIC_URL: "",
        CONF_COMMUNITY_SUBMIT_URL: "https://worker.example.com/submit",
        CONF_COMMUNITY_SUBMIT_SECRET: "s3cret-token",
    }
    result2 = await hass.config_entries.options.async_configure(
        result["flow_id"], user_input=user_input
    )
    assert result2["type"] == data_entry_flow.FlowResultType.CREATE_ENTRY
    assert result2["data"][CONF_COMMUNITY_SUBMIT_URL] == (
        "https://worker.example.com/submit"
    )
    assert result2["data"][CONF_COMMUNITY_SUBMIT_SECRET] == "s3cret-token"
    # And it lands on the entry options.
    assert entry.options[CONF_COMMUNITY_SUBMIT_URL] == (
        "https://worker.example.com/submit"
    )


async def test_options_flow_defaults_from_existing_options(
    hass: HomeAssistant,
) -> None:
    """Re-opening the options form pre-fills defaults from current options."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=DOMAIN,
        options={CONF_COMMUNITY_SUBMIT_URL: "https://preset.example/submit"},
    )
    entry.add_to_hass(hass)

    result = await hass.config_entries.options.async_init(entry.entry_id)
    # Find the schema marker for the community-submit URL and read its default.
    for marker in result["data_schema"].schema:
        if str(marker) == CONF_COMMUNITY_SUBMIT_URL:
            assert marker.default() == "https://preset.example/submit"
            break
    else:  # pragma: no cover - the key must exist (checked above)
        pytest.fail("community_submit_url marker not found in options schema")
