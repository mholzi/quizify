"""Config flow for Quizify."""

from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.core import callback
from homeassistant.helpers import selector

from .const import (
    CONF_LOBBY_MUSIC_URL,
    CONF_MEDIA_PLAYER_ENTITY,
    CONF_PARTY_LIGHT_ENTITIES,
    CONF_TTS_ENTITY,
    DOMAIN,
)

_LOGGER = logging.getLogger(__name__)


class QuizifyConfigFlow(ConfigFlow, domain=DOMAIN):
    """Config flow for Quizify."""

    VERSION = 1

    async def async_step_user(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> ConfigFlowResult:
        """Handle a flow initialized by the user."""
        # Prevent multiple instances
        await self.async_set_unique_id(DOMAIN)
        self._abort_if_unique_id_configured()

        if user_input is not None:
            return self.async_create_entry(
                title="Quizify",
                data={},
            )

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema({}),
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        """Return the options flow so users can wire up party lights + TTS
        without re-creating the integration."""
        return QuizifyOptionsFlow(config_entry)


class QuizifyOptionsFlow(OptionsFlow):
    """Per-entry options: party light entities, TTS entity, target speaker."""

    def __init__(self, config_entry: ConfigEntry) -> None:
        # Stash by attribute (not by deprecated self.config_entry assignment
        # which HA's options flow base provides automatically).
        self._entry_id = config_entry.entry_id

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)

        current = self.config_entry.options
        schema = vol.Schema({
            vol.Optional(
                CONF_PARTY_LIGHT_ENTITIES,
                default=current.get(CONF_PARTY_LIGHT_ENTITIES, []),
            ): selector.EntitySelector(
                selector.EntitySelectorConfig(domain="light", multiple=True),
            ),
            vol.Optional(
                CONF_TTS_ENTITY,
                default=current.get(CONF_TTS_ENTITY, ""),
            ): selector.EntitySelector(
                selector.EntitySelectorConfig(domain="tts"),
            ),
            vol.Optional(
                CONF_MEDIA_PLAYER_ENTITY,
                default=current.get(CONF_MEDIA_PLAYER_ENTITY, ""),
            ): selector.EntitySelector(
                selector.EntitySelectorConfig(domain="media_player"),
            ),
            # Optional lobby background music. Free-text URL to an audio file
            # the user supplies themselves (e.g. "/local/quizify-lobby.mp3").
            # Empty = lobby music stays off; the integration ships no audio.
            vol.Optional(
                CONF_LOBBY_MUSIC_URL,
                default=current.get(CONF_LOBBY_MUSIC_URL, ""),
            ): selector.TextSelector(
                selector.TextSelectorConfig(type=selector.TextSelectorType.URL),
            ),
        })
        return self.async_show_form(step_id="init", data_schema=schema)
