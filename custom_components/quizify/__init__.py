"""Home Assistant adapter for the Quizify multiplayer quiz game.

Quizify itself is a plain aiohttp application built in
:mod:`custom_components.quizify.server`. This module is the thin layer that
mounts that application onto Home Assistant's HTTP server, builds an
:class:`~custom_components.quizify.runtime.HARuntime`, and wires up the HA
config-flow entry, sidebar panel, and the admin-reset service.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from .const import DOMAIN

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[str] = ["sensor", "binary_sensor"]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Quizify from a config entry.

    HA-specific imports live inside this function so that the rest of the
    package can be imported (e.g. by ``scripts/dev_server.py`` or by tests)
    on machines that don't have Home Assistant installed.
    """
    from homeassistant.components.frontend import (  # noqa: PLC0415
        async_register_built_in_panel,
    )
    from homeassistant.components.http import StaticPathConfig  # noqa: PLC0415
    from homeassistant.core import ServiceCall  # noqa: PLC0415

    from .analytics import QuizifyAnalytics  # noqa: PLC0415
    from .const import (  # noqa: PLC0415
        CONF_LOBBY_MUSIC_URL,
        CONF_MEDIA_PLAYER_ENTITY,
        CONF_PARTY_LIGHT_ENTITIES,
        CONF_TTS_ENTITY,
    )
    from .game.state import QuizifyGameState  # noqa: PLC0415
    from .lights import QuizifyPartyLights  # noqa: PLC0415
    from .question_stats import QuestionStatsService  # noqa: PLC0415
    from .runtime import HARuntime  # noqa: PLC0415
    from .server import STATIC_URL_PREFIX, WS_PATH, WWW_DIR  # noqa: PLC0415
    from .server.context import APP_CTX_KEY, AppContext  # noqa: PLC0415
    from .server.views import register_routes  # noqa: PLC0415
    from .server.websocket import QuizifyWebSocketHandler  # noqa: PLC0415
    from .tts import QuizifyTTSAnnouncer  # noqa: PLC0415

    _LOGGER.debug("Setting up Quizify integration")

    hass.data.setdefault(DOMAIN, {})

    runtime = HARuntime(hass)

    analytics = QuizifyAnalytics(runtime)
    await analytics.load()

    question_stats = QuestionStatsService(runtime)
    await question_stats.load()

    game_state = QuizifyGameState(runtime=runtime, entry_id=entry.entry_id)
    game_state._stats_service = analytics
    game_state._question_stats = question_stats

    ws_handler = QuizifyWebSocketHandler(
        runtime=runtime,
        game_state_provider=lambda: hass.data.get(DOMAIN, {}).get("game"),
    )

    # Load persisted admin session token (survives HA restarts).
    # Without this, any LAN client could seize admin after every restart.
    await ws_handler._conn.async_load_admin_token()

    # Wire broadcast callback so game state can push events to clients.
    game_state.set_broadcast_callback(ws_handler.broadcast_state)

    ctx = AppContext(
        runtime=runtime,
        game=game_state,
        analytics=analytics,
        ws_handler=ws_handler,
        question_stats=question_stats,
        # HA's configured language drives the admin UI's initial language
        # (Settings → General). hass.config.language is always set on HA.
        ha_language=hass.config.language,
    )

    # Stash on hass.data so existing tooling (services.yaml, lookups in
    # tests) keeps working. The handler/AppContext relationship lives
    # alongside it.
    hass.data[DOMAIN] = {
        "entry_id": entry.entry_id,
        "game": game_state,
        "ws_handler": ws_handler,
        "analytics": analytics,
        "ctx": ctx,
    }

    # Stash the AppContext on HA's underlying aiohttp app so the plain
    # request handlers in server.views can read it via request.app.
    hass.http.app[APP_CTX_KEY] = ctx

    # Register routes directly on HA's aiohttp router (no HomeAssistantView
    # needed — the handlers don't require HA auth).
    register_routes(hass.http.app.router)

    # Register WebSocket endpoint.
    hass.http.app.router.add_get(WS_PATH, ws_handler.handle)

    # Register static file paths via HA's helper so it does the right thing
    # for the frontend resource version stamp.
    await hass.http.async_register_static_paths(
        [StaticPathConfig(STATIC_URL_PREFIX, str(WWW_DIR), cache_headers=True)]
    )

    # Register sidebar panel.
    async_register_built_in_panel(
        hass,
        component_name="iframe",
        sidebar_title="Quizify",
        sidebar_icon="mdi:head-question",
        frontend_url_path="quizify",
        config={"url": "/quizify/launcher"},
        require_admin=False,
    )
    _LOGGER.debug("Quizify sidebar panel registered")

    # Register HA service to reset the persisted admin session token.
    async def reset_admin_session(call: ServiceCall) -> None:  # noqa: ARG001
        domain_data = hass.data.get(DOMAIN)
        if domain_data:
            handler = domain_data.get("ws_handler")
            if handler:
                await handler._conn.async_clear_admin_token()
                _LOGGER.warning(
                    "Quizify admin session token RESET via HA service. "
                    "Next admin connection will bootstrap a fresh token."
                )

    hass.services.async_register(DOMAIN, "reset_admin_session", reset_admin_session)
    _LOGGER.debug("Quizify reset_admin_session service registered")

    # Forward to sensor/binary_sensor platforms so HA exposes Quizify game
    # state as entities (sensor.quizify_current_round, etc.).
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    # Wire optional HA integrations from the options flow. Both attach via
    # game state callbacks (Phase 1 plumbing) and stay silent if their
    # respective entities aren't configured.
    options = entry.options or {}
    # Surface the optional lobby-music URL to the frontend via the game-state
    # snapshot. Empty/unset → the host's lobby never tries to play anything.
    game_state.lobby_music_url = (options.get(CONF_LOBBY_MUSIC_URL) or "").strip() or None
    party_lights = QuizifyPartyLights(
        hass=hass,
        entity_ids=list(options.get(CONF_PARTY_LIGHT_ENTITIES) or []),
        game_state=game_state,
    )
    party_lights.attach()

    tts_announcer = QuizifyTTSAnnouncer(
        hass=hass,
        tts_entity_id=options.get(CONF_TTS_ENTITY) or None,
        media_player_entity_id=options.get(CONF_MEDIA_PLAYER_ENTITY) or None,
        game_state=game_state,
    )
    tts_announcer.attach()
    # Let the WS handler push milestone announcements directly — the
    # state-callback path only sees phase transitions.
    ws_handler._tts_announcer = tts_announcer

    hass.data[DOMAIN]["party_lights"] = party_lights
    hass.data[DOMAIN]["tts_announcer"] = tts_announcer

    # Re-attach on options change so toggling lights/TTS in the UI takes
    # effect without an HA restart.
    async def _update_listener(_hass: HomeAssistant, updated_entry: ConfigEntry) -> None:
        opts = updated_entry.options or {}
        game_state.lobby_music_url = (opts.get(CONF_LOBBY_MUSIC_URL) or "").strip() or None
        domain_data = _hass.data.get(DOMAIN, {})
        pl: QuizifyPartyLights | None = domain_data.get("party_lights")
        tts: QuizifyTTSAnnouncer | None = domain_data.get("tts_announcer")
        if pl is not None:
            pl.detach()
        if tts is not None:
            tts.detach()
        new_pl = QuizifyPartyLights(
            hass=_hass,
            entity_ids=list(opts.get(CONF_PARTY_LIGHT_ENTITIES) or []),
            game_state=game_state,
        )
        new_pl.attach()
        new_tts = QuizifyTTSAnnouncer(
            hass=_hass,
            tts_entity_id=opts.get(CONF_TTS_ENTITY) or None,
            media_player_entity_id=opts.get(CONF_MEDIA_PLAYER_ENTITY) or None,
            game_state=game_state,
        )
        new_tts.attach()
        domain_data["party_lights"] = new_pl
        domain_data["tts_announcer"] = new_tts
        handler = domain_data.get("ws_handler")
        if handler is not None:
            handler._tts_announcer = new_tts
        _LOGGER.info("Quizify options reloaded")

    entry.async_on_unload(entry.add_update_listener(_update_listener))

    _LOGGER.info("Quizify integration setup complete")
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    from homeassistant.components.frontend import async_remove_panel  # noqa: PLC0415

    _LOGGER.debug("Unloading Quizify integration")

    # Unload sensor/binary_sensor platforms first so their callbacks
    # detach cleanly before we tear down the game state they observe.
    await hass.config_entries.async_unload_platforms(entry, PLATFORMS)

    domain_data = hass.data.get(DOMAIN)
    if domain_data:
        ws_handler = domain_data.get("ws_handler")
        if ws_handler:
            await ws_handler.cleanup_game_tasks()

    try:
        async_remove_panel(hass, "quizify")
        _LOGGER.debug("Quizify sidebar panel removed")
    except KeyError:
        _LOGGER.debug("Quizify sidebar panel was not registered, skipping removal")

    if DOMAIN in hass.data:
        hass.data.pop(DOMAIN)

    _LOGGER.info("Quizify integration unloaded")
    return True
