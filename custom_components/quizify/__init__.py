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

# Marks that this HA process has already put Quizify's routes on the aiohttp
# router. Deliberately NOT stored under hass.data[DOMAIN]: that key is popped on
# unload, while the routes stay on the router forever (see #606 and the comment
# in async_setup_entry).
ROUTES_REGISTERED_KEY = f"{DOMAIN}_routes_registered"


def _ws_dispatch(hass: HomeAssistant):
    """Return a WebSocket route handler that resolves the live handler per call.

    The route object is created once and outlives every reload, so it must not
    close over a handler instance — that is precisely the bug in #606. It looks
    the current handler up in ``hass.data`` at call time instead, the same shape
    the ``game_state_provider`` lambda already uses for the game.

    While the integration is unloaded, ``hass.data[DOMAIN]`` is gone and the
    route answers 503 rather than raising: the path stays registered whether or
    not Quizify is set up, so "not set up right now" is a normal state and needs
    an honest status code.
    """

    async def dispatch(request):
        from aiohttp import web  # noqa: PLC0415

        handler = (hass.data.get(DOMAIN) or {}).get("ws_handler")
        if handler is None:
            _LOGGER.debug("WebSocket request while Quizify is not set up")
            return web.Response(status=503, text="Quizify is not set up")
        return await handler.handle(request)

    return dispatch


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

    from .analytics import QuizifyAnalytics  # noqa: PLC0415
    from .const import (  # noqa: PLC0415
        CONF_AVOID_RECENT_REPEATS,
        CONF_COMMUNITY_SUBMIT_SECRET,
        CONF_COMMUNITY_SUBMIT_URL,
        CONF_LOBBY_MUSIC_URL,
        DEFAULT_AVOID_RECENT_REPEATS,
    )
    from .game.state import QuizifyGameState  # noqa: PLC0415
    from .house import build_house_consumers  # noqa: PLC0415
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
    from .services import async_register_services  # noqa: PLC0415

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

    # Routes are registered ONCE per HA process, not once per setup (#606).
    #
    # aiohttp normally freezes its router at startup and a second add_get()
    # would raise — but HA deliberately un-freezes it
    # (homeassistant/components/http/__init__.py: `self.app._router.freeze =
    # lambda: None`), so a duplicate registration silently succeeds instead.
    # UrlDispatcher.resolve() then matches in *registration order*, which means
    # the handler bound during the FIRST setup keeps serving forever. After a
    # reload — the Integrations "Reload" button, and every HACS update — new
    # sockets reached the previous QuizifyWebSocketHandler while the game
    # broadcast callback pointed at the new one, whose connection manager holds
    # zero sockets. Every state broadcast went nowhere and the game looked hung.
    #
    # The plain HTTP views survived that because they read APP_CTX_KEY per
    # request (refreshed just above), which is exactly what made the failure
    # look like a game bug rather than a routing bug.
    if not hass.data.get(ROUTES_REGISTERED_KEY):
        register_routes(hass.http.app.router)
        hass.http.app.router.add_get(WS_PATH, _ws_dispatch(hass))
        await hass.http.async_register_static_paths(
            [StaticPathConfig(STATIC_URL_PREFIX, str(WWW_DIR), cache_headers=True)]
        )
        hass.data[ROUTES_REGISTERED_KEY] = True
        _LOGGER.debug("Quizify routes registered (once per HA process)")
    else:
        _LOGGER.debug(
            "Quizify routes already registered; reusing them with the new handler"
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

    # HA services (#367 / #743 / #744): the admin-session reset plus a safe
    # subset of the game controls, so hosts can drive the game from Assist
    # voice, a Zigbee remote, a dashboard button or an automation. They live in
    # ``services.py`` rather than as closures here (#789) — they close over
    # nothing but ``hass`` and look their entry-scoped state up per call.
    async_register_services(hass)

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
    # The five "House Plays Along" consumers (#494): TTS announcer, party
    # lights, lobby music, event emitter, room SFX. Built ONCE, here, off one
    # shared HouseSettings holding the config-entry defaults (#789) — the
    # options-reload listener below refreshes that object in place, so nothing
    # is torn down, rebuilt or snapshotted and every reference taken here stays
    # valid for the life of the entry.
    house = build_house_consumers(hass, options, game_state)
    house.attach()

    # Let the WS handler push milestone announcements directly — the
    # state-callback path only sees phase transitions — and give it the live
    # house consumers so the admin "House Plays Along" panel's
    # ``configure_house`` message (and start_game) can drive them per game
    # (#494 P4). Wired once: the consumers outlive an options reload now, so
    # there is no second wiring pass to keep in step with this one.
    ws_handler.set_tts_announcer(house.tts_announcer)
    ws_handler.set_event_emitter(house.event_emitter)
    _wire_house_consumers(ws_handler, house.party_lights, house.sound_effects)

    # The consumers live on the AppContext, which is what "the views and
    # websocket handlers need to do their job" already means (#789). The
    # hass.data mirror below is kept because services.yaml lookups and a dozen
    # tests read it; it aliases the same objects, and since nothing is ever
    # rebuilt the two can no longer drift.
    ctx.house = house
    for key, consumer in house.as_pairs():
        hass.data[DOMAIN][key] = consumer

    # Re-apply the options live so toggling lights/TTS/SFX in the UI takes
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
        # The whole house-consumer reload path (#789). This used to be 100
        # lines: export each consumer's runtime config, detach it, rebuild it
        # from the fresh options with a duplicate of every constructor call
        # above, restore the snapshot, re-attach it, re-stash it under a string
        # key and re-point the WS handler at it — a sequence #411 had to be
        # re-fixed in once per consumer as they were added. The consumers read
        # their config-entry defaults through the shared settings object, so
        # updating it in place is the entire operation, and every panel
        # override survives because nothing touches it.
        house.apply_options(opts)
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

        # Unsubscribe the five house consumers (#605). This is the ONLY
        # teardown path left: an options change no longer detaches anything,
        # because it no longer rebuilds anything (#789).
        #
        # Popping ``hass.data`` below does NOT unsubscribe anything: the party
        # lights hold five ``hass.bus.async_listen`` handles and the sound
        # effects three, and those live on the bus, not in the dict. The lights'
        # pulse task is cancelled inside ``detach()`` too. Without this, every
        # reload left the previous instances subscribed, so one ``quizify_*``
        # event fired ``light.turn_on`` / ``media_player.play_media`` once per
        # past reload — and after *removing* the integration they kept reacting
        # until Home Assistant restarted.
        #
        # Each detach is guarded on its own: a consumer that raises must not
        # stop the other four from unsubscribing, or a single bad teardown
        # leaves exactly the leak this fixes.
        #
        # Read off the AppContext (#789), which is where the consumers live; the
        # ``hass.data`` keys are only a mirror, and an entry that never finished
        # setup has neither.
        house = getattr(ctx, "house", None)
        if house is not None:
            for key, consumer in house.as_pairs():
                try:
                    consumer.detach()
                except Exception:  # noqa: BLE001
                    _LOGGER.exception("Failed to detach %s on unload", key)

    try:
        async_remove_panel(hass, "quizify")
        _LOGGER.debug("Quizify sidebar panel removed")
    except KeyError:
        _LOGGER.debug("Quizify sidebar panel was not registered, skipping removal")

    if DOMAIN in hass.data:
        hass.data.pop(DOMAIN)

    _LOGGER.info("Quizify integration unloaded")
    return True
