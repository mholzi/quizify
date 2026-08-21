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


def _wire_house_consumers(
    ws_handler: object, party_lights: object, sound_effects: object
) -> None:
    """Give the WS layer the live house-lights / house-SFX instances (#494 P4).

    The admin "House Plays Along" panel pushes a resolved config dict down the
    socket (``configure_house``, and again on ``start_game``); the WS handler
    forwards it to these two consumers plus the event emitter. The setters are
    optional so this module keeps working against a handler that doesn't expose
    them yet — anything unwired simply stays on its config-entry defaults rather
    than blowing up setup.
    """
    for name, consumer in (
        ("set_party_lights", party_lights),
        ("set_sound_effects", sound_effects),
    ):
        setter = getattr(ws_handler, name, None)
        if callable(setter):
            setter(consumer)


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
        CONF_AVOID_RECENT_REPEATS,
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
        DEFAULT_AVOID_RECENT_REPEATS,
        DEFAULT_HOUSE_EVENTS_ENABLED,
    )
    from .game.state import QuizifyGameState  # noqa: PLC0415
    from .game_events import QuizifyEventEmitter  # noqa: PLC0415
    from .lights import QuizifyPartyLights  # noqa: PLC0415
    from .lobby_music import QuizifyLobbyMusic  # noqa: PLC0415
    from .question_stats import QuestionStatsService  # noqa: PLC0415
    from .runtime import HARuntime  # noqa: PLC0415
    from .server import STATIC_URL_PREFIX, WS_PATH, WWW_DIR  # noqa: PLC0415
    from .server.context import (  # noqa: PLC0415
        APP_CTX_KEY,
        AppContext,
        read_manifest_version,
    )
    from .server.views import (  # noqa: PLC0415
        prime_ui_languages,
        refresh_live_version,
        register_routes,
    )
    from .server.websocket import QuizifyWebSocketHandler  # noqa: PLC0415
    from .sound_effects import QuizifySoundEffects  # noqa: PLC0415
    from .tts import QuizifyTTSAnnouncer  # noqa: PLC0415

    _LOGGER.debug("Setting up Quizify integration")

    hass.data.setdefault(DOMAIN, {})

    runtime = HARuntime(hass)

    analytics = QuizifyAnalytics(runtime)
    await analytics.load()

    question_stats = QuestionStatsService(runtime)
    await question_stats.load()

    # Persist accumulated rounds when HA shuts down (#588). Without this the
    # only write path was end_game(), so a restart during a game — or after a
    # game the host simply walked away from — silently dropped every round it
    # had collected. async_flush() cancels the pending debounce and writes.
    from homeassistant.const import (  # noqa: PLC0415
        EVENT_HOMEASSISTANT_STOP,
    )

    async def _flush_question_stats(_event: object) -> None:
        await question_stats.async_flush()

    entry.async_on_unload(
        hass.bus.async_listen_once(
            EVENT_HOMEASSISTANT_STOP, _flush_question_stats
        )
    )

    game_state = QuizifyGameState(runtime=runtime, entry_id=entry.entry_id)
    game_state.set_stats_services(analytics, question_stats)
    # Read persisted question history off the event loop (issue #222).
    await game_state.async_load_history()

    # Preload the ~2 MB question bank off the event loop once at setup
    # (issue #258). load_all_categories() is idempotent (guarded by
    # _loaded), so the later inline calls in start_game()/LightningRound
    # .start() become guaranteed cache hits instead of synchronous disk
    # reads on the loop. Mirrors the analytics/stats preload pattern above.
    await runtime.run_in_executor(game_state.question_bank.load_all_categories)

    ws_handler = QuizifyWebSocketHandler(
        runtime=runtime,
        game_state_provider=lambda: hass.data.get(DOMAIN, {}).get("game"),
    )

    # Load persisted admin session token (survives HA restarts).
    # Without this, any LAN client could seize admin after every restart.
    await ws_handler.conn.async_load_admin_token()

    # Wire broadcast callback so game state can push events to clients.
    game_state.set_broadcast_callback(ws_handler.broadcast_state)

    # Read the manifest version OFF the event loop (#343). HA's loop-watcher
    # flags read_text() inside the loop; doing it in an executor and passing the
    # result in explicitly (instead of relying on AppContext's synchronous
    # default_factory) keeps the loop clean.
    version = await hass.async_add_executor_job(read_manifest_version)

    # Same treatment for the UI-language chips (#542): the set is derived from
    # the shipped www/i18n/*.json bundles, which means a directory scan. It is
    # lru_cached, so leaving it lazy cost exactly one blocking scandir per HA
    # start — on the first player render, where the loop-watcher flagged it.
    # Priming here moves that one scan into an executor thread.
    await hass.async_add_executor_job(prime_ui_languages)

    ctx = AppContext(
        runtime=runtime,
        game=game_state,
        analytics=analytics,
        ws_handler=ws_handler,
        question_stats=question_stats,
        version=version,
        # HA's configured language drives the admin UI's initial language
        # (Settings → General). hass.config.language is always set on HA.
        ha_language=hass.config.language,
        # Community-pack submission stays inert until this worker URL is set
        # (#180). Empty/unset → the in-app submit UI hides itself.
        community_submit_url=(
            entry.options.get(CONF_COMMUNITY_SUBMIT_URL) or ""
        ).strip()
        or None,
        # Shared secret sent as X-Quizify-Secret to the worker (#256). Empty →
        # header omitted (back-compatible); set it alongside the worker's
        # SHARED_SECRET to close the open-proxy hole.
        community_submit_secret=(
            entry.options.get(CONF_COMMUNITY_SUBMIT_SECRET) or ""
        ).strip()
        or None,
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

    # Keep the live manifest version (the asset cache-buster source) fresh OFF
    # the event loop (#343). The launcher/HTML serve path reads a pure
    # in-memory value with zero loop I/O; a background interval re-reads
    # manifest.json in an executor thread so a direct-rsync deploy (manifest
    # bumped on disk without an integration reload) still busts the browser
    # cache without a full HA restart — just without blocking the loop. Run it
    # once now so the first serve already has the live value, then on an
    # interval. The mtime cache makes the steady-state tick a single stat().
    from datetime import timedelta  # noqa: PLC0415

    from homeassistant.helpers.event import (  # noqa: PLC0415
        async_track_time_interval,
    )

    await refresh_live_version(runtime)

    async def _refresh_live_version_tick(_now: object) -> None:
        await refresh_live_version(runtime)

    entry.async_on_unload(
        async_track_time_interval(
            hass, _refresh_live_version_tick, timedelta(seconds=30)
        )
    )

    # Register the host card as a Lovelace resource (#278). Convenience only —
    # the helper swallows every failure and logs the manual step instead, so a
    # YAML-mode dashboard or a changed core API can't take setup down with it.
    from .cards import async_register_card_resource  # noqa: PLC0415

    await async_register_card_resource(hass, getattr(ctx, "version", None))

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
                await handler.conn.async_clear_admin_token()
                _LOGGER.warning(
                    "Quizify admin session token RESET via HA service. "
                    "Next admin connection will bootstrap a fresh token."
                )

    hass.services.async_register(DOMAIN, "reset_admin_session", reset_admin_session)
    _LOGGER.debug("Quizify reset_admin_session service registered")

    # Game-control services (#367). Expose a safe subset of the admin game
    # controls as HA services so hosts can drive the game via Assist voice
    # ("Hey Nabu, next question"), a Zigbee remote, a dashboard button or an
    # automation — instead of only the admin WebSocket UI. Each delegates to
    # the SAME socket-independent core the admin WS handler now uses (the
    # ``admin_action_*`` methods), so there is one implementation and the
    # broadcast that refreshes entities + connected clients always fires.
    from homeassistant.exceptions import (  # noqa: PLC0415
        ServiceValidationError,
    )

    from .game.state import GamePhase  # noqa: PLC0415

    def _require_runtime() -> tuple[QuizifyWebSocketHandler, QuizifyGameState]:
        """Return (ws_handler, game_state) or raise if setup isn't complete.

        Guards against a raw ``KeyError`` when a service fires before/without a
        loaded config entry (e.g. during teardown): the host sees a clear
        message in the HA UI / voice response instead of an opaque crash.
        """
        domain_data = hass.data.get(DOMAIN)
        if not domain_data:
            raise ServiceValidationError(
                "Quizify is not set up. Add the Quizify integration first."
            )
        handler = domain_data.get("ws_handler")
        game = domain_data.get("game")
        if handler is None or game is None:
            raise ServiceValidationError(
                "Quizify is not ready yet. Try again in a moment."
            )
        return handler, game

    async def start_game_service(call: ServiceCall) -> None:  # noqa: ARG001
        handler, game = _require_runtime()
        try:
            await handler.admin_action_start_game(game)
        except ValueError as err:
            raise ServiceValidationError(
                "Cannot start a new quiz right now — a game is already in "
                "progress. End it first, then start a new one."
            ) from err

    async def next_round_service(call: ServiceCall) -> None:  # noqa: ARG001
        handler, game = _require_runtime()
        try:
            await handler.admin_action_next_round(game)
        except ValueError as err:
            raise ServiceValidationError(
                "Cannot advance to the next question right now. Start a game "
                "first, or wait until the current question has been revealed."
            ) from err

    async def pause_service(call: ServiceCall) -> None:  # noqa: ARG001
        handler, game = _require_runtime()
        if not await handler.admin_action_pause(game):
            raise ServiceValidationError(
                "There is no active question to pause right now."
            )

    async def resume_service(call: ServiceCall) -> None:  # noqa: ARG001
        handler, game = _require_runtime()
        if not await handler.admin_action_resume(game):
            raise ServiceValidationError(
                "The game is not paused, so there is nothing to resume."
            )

    async def end_game_service(call: ServiceCall) -> None:  # noqa: ARG001
        handler, game = _require_runtime()
        if game.phase == GamePhase.LOBBY:
            raise ServiceValidationError(
                "No game is currently running, so there is nothing to end."
            )
        await handler.admin_action_end_game(game)

    hass.services.async_register(DOMAIN, "start_game", start_game_service)
    hass.services.async_register(DOMAIN, "next_round", next_round_service)
    hass.services.async_register(DOMAIN, "pause", pause_service)
    hass.services.async_register(DOMAIN, "resume", resume_service)
    hass.services.async_register(DOMAIN, "end_game", end_game_service)
    _LOGGER.debug("Quizify game-control services registered (#367)")

    # Forward to sensor/binary_sensor platforms so HA exposes Quizify game
    # state as entities (sensor.quizify_current_round, etc.).
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    # Wire optional HA integrations from the options flow. Both attach via
    # game state callbacks (Phase 1 plumbing) and stay silent if their
    # respective entities aren't configured. (entry.options is always a
    # MappingProxyType — never None — so no `or {}` fallback is needed.)
    options = entry.options
    # Lobby music plays server-side on the configured media_player while the
    # game waits in the lobby. Empty/unset → the playback service stays inert.
    game_state.lobby_music_url = (
        (options.get(CONF_LOBBY_MUSIC_URL) or "").strip() or None
    )
    # Freshness engine (#436): thread the "avoid recent repeats" toggle onto the
    # question bank so build_pool can hard-exclude recently shown questions
    # (guarded) instead of merely oldest-first ordering. Default preserves the
    # user-visible default; OFF restores the pre-#436 ordering exactly.
    game_state.question_bank.set_avoid_recent_repeats(
        bool(options.get(CONF_AVOID_RECENT_REPEATS, DEFAULT_AVOID_RECENT_REPEATS))
    )
    # The house-lights master defaults to the CONF_HOUSE_EVENTS_ENABLED option
    # (off out of the box) — the admin "House Plays Along" panel overrides it per
    # game via configure() (#494 P4). Only the event-driven ACCENTS are gated by
    # it; the phase-driven ambient glow always follows the game.
    house_enabled = bool(
        options.get(CONF_HOUSE_EVENTS_ENABLED, DEFAULT_HOUSE_EVENTS_ENABLED)
    )
    party_lights = QuizifyPartyLights(
        hass=hass,
        entity_ids=list(options.get(CONF_PARTY_LIGHT_ENTITIES) or []),
        game_state=game_state,
        finale_scene=options.get(CONF_FINALE_SCENE),
        enabled=house_enabled,
    )
    party_lights.attach()
    # Accent choreography (#494 Phase 2): react to the quizify_* bus events on
    # top of the phase-driven ambient glow. No-op unless lights are configured.
    party_lights.attach_events()

    tts_announcer = QuizifyTTSAnnouncer(
        hass=hass,
        tts_entity_id=options.get(CONF_TTS_ENTITY) or None,
        media_player_entity_id=options.get(CONF_MEDIA_PLAYER_ENTITY) or None,
        game_state=game_state,
    )
    tts_announcer.attach()
    # Let the WS handler push milestone announcements directly — the
    # state-callback path only sees phase transitions.
    ws_handler.set_tts_announcer(tts_announcer)

    # Lobby music shares the TTS media_player entity (no separate field).
    lobby_music = QuizifyLobbyMusic(
        hass=hass,
        media_player_entity_id=options.get(CONF_MEDIA_PLAYER_ENTITY) or None,
        game_state=game_state,
    )
    lobby_music.attach()

    # HA event backbone (#366) — fires quizify_* bus events at game milestones
    # so the host can drive their own automations. Attaches a state callback for
    # phase-driven events; the WS handler pushes the richer per-event ones.
    # Gated behind the CONF_HOUSE_EVENTS_ENABLED master toggle (default off, like
    # TTS/lights): stays a no-op until the host opts in, and always on the
    # standalone dev server (no bus).
    event_emitter = QuizifyEventEmitter(
        hass=hass,
        game_state=game_state,
        enabled=house_enabled,
    )
    event_emitter.attach()
    ws_handler.set_event_emitter(event_emitter)

    # Room SFX (#494 Phase 3): one-shot stings on the shared media_player at game
    # milestones, driven off the quizify_* bus events. Hybrid source — per-cue
    # override URLs from the options flow, else the bundled CC0 default at
    # www/sfx/<cue>.mp3 (if the host installed one). No phase callback, so only
    # attach_events() (which also does the one-time bundled-default disk stat).
    sound_effects = QuizifySoundEffects(
        hass=hass,
        media_player_entity_id=options.get(CONF_MEDIA_PLAYER_ENTITY) or None,
        game_state=game_state,
        cue_urls={
            "correct": options.get(CONF_SFX_CORRECT_URL) or None,
            "wrong": options.get(CONF_SFX_WRONG_URL) or None,
            "streak": options.get(CONF_SFX_STREAK_URL) or None,
            "winner": options.get(CONF_SFX_WINNER_URL) or None,
        },
        enabled=house_enabled,
    )
    sound_effects.attach_events()

    # Hand the house consumers to the WS layer so the admin panel's
    # ``configure_house`` message (and start_game) can drive them per game
    # (#494 P4). Guarded: the setters live in the WS slice, so this stays inert
    # if the handler predates them.
    _wire_house_consumers(ws_handler, party_lights, sound_effects)

    hass.data[DOMAIN]["party_lights"] = party_lights
    hass.data[DOMAIN]["tts_announcer"] = tts_announcer
    hass.data[DOMAIN]["lobby_music"] = lobby_music
    hass.data[DOMAIN]["event_emitter"] = event_emitter
    hass.data[DOMAIN]["sound_effects"] = sound_effects

    # Re-attach on options change so toggling lights/TTS in the UI takes
    # effect without an HA restart.
    async def _update_listener(
        _hass: HomeAssistant, updated_entry: ConfigEntry
    ) -> None:
        opts = updated_entry.options  # always a MappingProxyType, never None
        game_state.lobby_music_url = (
            (opts.get(CONF_LOBBY_MUSIC_URL) or "").strip() or None
        )
        # Freshness engine (#436) — re-apply the toggle live so flipping it in
        # the options UI takes effect on the next game without an HA restart.
        game_state.question_bank.set_avoid_recent_repeats(
            bool(opts.get(CONF_AVOID_RECENT_REPEATS, DEFAULT_AVOID_RECENT_REPEATS))
        )
        # Toggle the community-pack submit feature live (#180) — no HA restart.
        ctx.community_submit_url = (
            opts.get(CONF_COMMUNITY_SUBMIT_URL) or ""
        ).strip() or None
        ctx.community_submit_secret = (
            opts.get(CONF_COMMUNITY_SUBMIT_SECRET) or ""
        ).strip() or None
        domain_data = _hass.data.get(DOMAIN, {})
        pl: QuizifyPartyLights | None = domain_data.get("party_lights")
        tts: QuizifyTTSAnnouncer | None = domain_data.get("tts_announcer")
        lm: QuizifyLobbyMusic | None = domain_data.get("lobby_music")
        ev: QuizifyEventEmitter | None = domain_data.get("event_emitter")
        sfx: QuizifySoundEffects | None = domain_data.get("sound_effects")
        # Snapshot the old announcer's live per-game narration config BEFORE we
        # tear it down (#411). Rebuilding from the config entry resets
        # ``_enabled`` to False and drops the admin's per-game entity overrides,
        # which would silently kill narration mid-game until the next start_game.
        tts_snapshot = tts.export_runtime_config() if tts is not None else None
        # Snapshot the emitter's phase tracking + master toggle so a reload
        # landing on round 1 doesn't re-fire quizify_game_started (#366, same
        # #411 rationale) and doesn't discard the panel's master override (#494).
        ev_snapshot = ev.export_runtime_state() if ev is not None else None
        # Same #411 hazard for the two "House Plays Along" consumers (#494 P4):
        # rebuilding them from the config entry resets every per-accent/per-cue
        # toggle to its default and drops the admin panel's entity overrides,
        # silently wiping the host's mid-game settings. Snapshot before teardown,
        # restore onto the fresh instances below.
        pl_snapshot = pl.export_runtime_config() if pl is not None else None
        sfx_snapshot = sfx.export_runtime_config() if sfx is not None else None
        if pl is not None:
            pl.detach()
        if tts is not None:
            tts.detach()
        if lm is not None:
            lm.detach()
        if ev is not None:
            ev.detach()
        if sfx is not None:
            sfx.detach()
        new_house_enabled = bool(
            opts.get(CONF_HOUSE_EVENTS_ENABLED, DEFAULT_HOUSE_EVENTS_ENABLED)
        )
        new_pl = QuizifyPartyLights(
            hass=_hass,
            entity_ids=list(opts.get(CONF_PARTY_LIGHT_ENTITIES) or []),
            game_state=game_state,
            finale_scene=opts.get(CONF_FINALE_SCENE),
            enabled=new_house_enabled,
        )
        # Carry the panel's house-lights config across the reload BEFORE
        # attaching, so the restored entity override is already in place when
        # attach_events() evaluates is_configured (#411 / #494 P4).
        new_pl.restore_runtime_config(pl_snapshot)
        new_pl.attach()
        # Re-subscribe the accent choreography (#494) symmetrically — the old
        # pl.detach() above already dropped its listeners + cancelled any pulse.
        new_pl.attach_events()
        new_tts = QuizifyTTSAnnouncer(
            hass=_hass,
            tts_entity_id=opts.get(CONF_TTS_ENTITY) or None,
            media_player_entity_id=opts.get(CONF_MEDIA_PLAYER_ENTITY) or None,
            game_state=game_state,
        )
        # Carry the runtime narration config across the reload so an in-flight
        # game keeps narrating (#411).
        new_tts.restore_runtime_config(tts_snapshot)
        new_tts.attach()
        new_lm = QuizifyLobbyMusic(
            hass=_hass,
            media_player_entity_id=opts.get(CONF_MEDIA_PLAYER_ENTITY) or None,
            game_state=game_state,
        )
        new_lm.attach()
        new_ev = QuizifyEventEmitter(
            hass=_hass,
            game_state=game_state,
            enabled=new_house_enabled,
        )
        # Carry the phase tracking + the panel's master override across the
        # reload so an in-flight game keeps its game_started dedupe (#366) and
        # its house master (#494 P4). Toggling the master switch in the options
        # UI still takes effect immediately when the panel never overrode it.
        new_ev.restore_runtime_state(ev_snapshot)
        new_ev.attach()
        # Rebuild room SFX from the fresh options (new override URLs, new
        # media_player). attach_events() re-stats the bundled defaults and
        # re-subscribes; the old sfx.detach() above already dropped its
        # listeners. No phase callback, so no attach() — but the panel's cue
        # toggles + speaker override DO need to survive the rebuild (#494 P4).
        new_sfx = QuizifySoundEffects(
            hass=_hass,
            media_player_entity_id=opts.get(CONF_MEDIA_PLAYER_ENTITY) or None,
            game_state=game_state,
            cue_urls={
                "correct": opts.get(CONF_SFX_CORRECT_URL) or None,
                "wrong": opts.get(CONF_SFX_WRONG_URL) or None,
                "streak": opts.get(CONF_SFX_STREAK_URL) or None,
                "winner": opts.get(CONF_SFX_WINNER_URL) or None,
            },
            enabled=new_house_enabled,
        )
        new_sfx.restore_runtime_config(sfx_snapshot)
        new_sfx.attach_events()
        domain_data["party_lights"] = new_pl
        domain_data["tts_announcer"] = new_tts
        domain_data["lobby_music"] = new_lm
        domain_data["event_emitter"] = new_ev
        domain_data["sound_effects"] = new_sfx
        handler = domain_data.get("ws_handler")
        if handler is not None:
            handler.set_tts_announcer(new_tts)
            handler.set_event_emitter(new_ev)
            # Re-point the WS layer at the fresh house consumers (#494 P4).
            _wire_house_consumers(handler, new_pl, new_sfx)
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
        # Write out the per-question stats before anything is torn down
        # (#588). A reload or an integration update goes through here, and
        # whatever rounds are still only in memory would otherwise be lost.
        ctx = domain_data.get("ctx")
        question_stats = getattr(ctx, "question_stats", None)
        if question_stats is not None:
            try:
                await question_stats.async_flush()
            except Exception:  # noqa: BLE001
                _LOGGER.exception("Failed to flush question stats on unload")

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
