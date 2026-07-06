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
    CONF_COMMUNITY_SUBMIT_SECRET,
    CONF_COMMUNITY_SUBMIT_URL,
    CONF_FINALE_SCENE,
    CONF_HOUSE_EVENTS_ENABLED,
    CONF_LOBBY_MUSIC_URL,
    CONF_MEDIA_PLAYER_ENTITY,
    CONF_PARTY_LIGHT_ENTITIES,
    CONF_SFX_CORRECT_URL,
    CONF_SFX_STREAK_URL,
    CONF_SFX_WINNER_URL,
    CONF_SFX_WRONG_URL,
    CONF_TTS_ENTITY,
    DEFAULT_HOUSE_EVENTS_ENABLED,
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
            # Endpoint a composed community pack is POSTed to so it can land as
            # a GitHub issue for review (#180). Empty = the whole in-app pack
            # submission feature stays hidden and inert. The GitHub token lives
            # in that worker, never in the browser or this integration.
            vol.Optional(
                CONF_COMMUNITY_SUBMIT_URL,
                default=current.get(CONF_COMMUNITY_SUBMIT_URL, ""),
            ): selector.TextSelector(
                selector.TextSelectorConfig(type=selector.TextSelectorType.URL),
            ),
            # Shared secret paired with the worker's SHARED_SECRET (#256). When
            # set, it's sent as the X-Quizify-Secret header so the worker can
            # reject unauthenticated requests, closing the open-proxy hole.
            # Empty = header omitted (fully back-compatible).
            vol.Optional(
                CONF_COMMUNITY_SUBMIT_SECRET,
                default=current.get(CONF_COMMUNITY_SUBMIT_SECRET, ""),
            ): selector.TextSelector(
                selector.TextSelectorConfig(type=selector.TextSelectorType.PASSWORD),
            ),
            # Per-cue room-SFX override URLs (#494 Phase 3). Each is an optional
            # free-text URL to a short one-shot sound played on the media_player
            # above at a game milestone. Empty = fall back to the bundled CC0
            # default at www/sfx/<cue>.mp3 (if the host installed one), else the
            # cue stays silent. The SFX share the TTS speaker and are dropped
            # while a TTS announcement is live.
            vol.Optional(
                CONF_SFX_CORRECT_URL,
                default=current.get(CONF_SFX_CORRECT_URL, ""),
            ): selector.TextSelector(
                selector.TextSelectorConfig(type=selector.TextSelectorType.URL),
            ),
            vol.Optional(
                CONF_SFX_WRONG_URL,
                default=current.get(CONF_SFX_WRONG_URL, ""),
            ): selector.TextSelector(
                selector.TextSelectorConfig(type=selector.TextSelectorType.URL),
            ),
            vol.Optional(
                CONF_SFX_STREAK_URL,
                default=current.get(CONF_SFX_STREAK_URL, ""),
            ): selector.TextSelector(
                selector.TextSelectorConfig(type=selector.TextSelectorType.URL),
            ),
            vol.Optional(
                CONF_SFX_WINNER_URL,
                default=current.get(CONF_SFX_WINNER_URL, ""),
            ): selector.TextSelector(
                selector.TextSelectorConfig(type=selector.TextSelectorType.URL),
            ),
            # Master toggle for "The House Plays Along" (#366). Off by default:
            # the integration fires no quizify_* bus events until the host opts
            # in, so existing broad event automations are never surprised. This
            # is the single gate the later light/SFX phases also ride on.
            vol.Optional(
                CONF_HOUSE_EVENTS_ENABLED,
                default=current.get(
                    CONF_HOUSE_EVENTS_ENABLED, DEFAULT_HOUSE_EVENTS_ENABLED
                ),
            ): selector.BooleanSelector(),
            # Optional scene fired on the finale/winner alongside the party-light
            # victory sweep (#280). Empty = no scene is touched. Lets the host
            # drive a whole-room "victory" look from one of their own HA scenes.
            vol.Optional(
                CONF_FINALE_SCENE,
                default=current.get(CONF_FINALE_SCENE, ""),
            ): selector.EntitySelector(
                selector.EntitySelectorConfig(domain="scene"),
            ),
        })
        return self.async_show_form(step_id="init", data_schema=schema)
