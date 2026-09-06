"""Quizify's Home Assistant services (#367, #743, #744, #789).

A safe subset of the admin game controls, exposed as HA services so hosts can
drive the game from Assist voice ("Hey Nabu, start the quiz"), a Zigbee remote,
a dashboard button or an automation — instead of only the admin WebSocket UI.
Each delegates to the SAME socket-independent core the admin WS handler uses
(the ``admin_action_*`` methods), so there is one implementation and the
broadcast that refreshes entities + connected clients always fires.

These handlers used to be seven nested closures inside ``async_setup_entry``
(#789). They close over nothing but ``hass``, which is what let them move out
whole; the entry-scoped state they need is looked up per call, which is also
what makes the "Quizify is not set up" guard below possible at all.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

import voluptuous as vol
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import config_validation as cv

from .const import DOMAIN

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant, ServiceCall

    from .game.state import QuizifyGameState
    from .server.websocket import QuizifyWebSocketHandler

_LOGGER = logging.getLogger(__name__)

#: Everything ``quizify.start_game`` accepts (#744). Every field is optional —
#: the service with no data at all still starts a game, it just starts the one
#: the host last configured instead of a hard-coded default one.
START_GAME_SCHEMA = vol.Schema(
    {
        vol.Optional("preset"): cv.string,
        # A single pack slug, a list of them, or the literal "mixed" to clear
        # the pack selection. The admin UI sends exactly these three shapes.
        vol.Optional("category"): vol.Any(cv.string, [cv.string]),
        vol.Optional("difficulty"): cv.string,
        vol.Optional("num_rounds"): vol.All(vol.Coerce(int), vol.Range(min=1, max=50)),
        vol.Optional("language"): cv.string,
        vol.Optional("timer_duration"): vol.All(
            vol.Coerce(int), vol.Range(min=5, max=300)
        ),
    }
)


def _require_runtime(
    hass: HomeAssistant,
) -> tuple[QuizifyWebSocketHandler, QuizifyGameState]:
    """Return (ws_handler, game_state) or raise if setup isn't complete.

    Guards against a raw ``KeyError`` when a service fires before/without a
    loaded config entry (e.g. during teardown): the host sees a clear message
    in the HA UI / voice response instead of an opaque crash.
    """
    domain_data = hass.data.get(DOMAIN)
    if not domain_data:
        raise ServiceValidationError(
            "Quizify is not set up. Add the Quizify integration first."
        )
    handler = domain_data.get("ws_handler")
    game = domain_data.get("game")
    if handler is None or game is None:
        raise ServiceValidationError("Quizify is not ready yet. Try again in a moment.")
    return handler, game


async def _resolve_preset(hass: HomeAssistant, name: str) -> dict[str, Any]:
    """Look a saved preset up by name (case-insensitive) — #744.

    Saved presets (:mod:`server.preset_store`) were reachable only from the
    admin UI, so "start my Friday setup" by voice was impossible. Matching is on
    the host-visible NAME rather than the generated id, because the name is the
    only part of a preset a voice sentence or a dashboard button can carry.

    Raises :class:`ServiceValidationError` naming the presets that do exist, so
    a typo is self-correcting instead of silently starting the wrong game.
    """
    from .server.views import get_preset_store  # noqa: PLC0415

    presets = await get_preset_store(hass.http.app).list()
    wanted = name.strip().casefold()
    for preset in presets:
        if str(preset.get("name", "")).strip().casefold() == wanted:
            return preset
    known = ", ".join(str(p.get("name", "")) for p in presets) or "none saved yet"
    raise ServiceValidationError(
        f"No saved Quizify preset called {name!r}. Available presets: {known}."
    )


def async_register_services(hass: HomeAssistant) -> None:
    """Register every ``quizify.*`` service on ``hass``.

    Called from ``async_setup_entry``. ``hass.services.async_register`` is
    idempotent per (domain, service) name, so a second config entry — or a
    reload — simply re-points the same names at equivalent handlers.
    """
    from .game.state import GamePhase  # noqa: PLC0415

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

    async def start_game_service(call: ServiceCall) -> None:
        """Start a game honouring the host's settings (#744).

        Before this, the service and the Lovelace card both landed on a bare
        ``game_state.start_game()`` — mixed packs, medium, 10 rounds, German —
        and muted the narrator on the way. Now an optional saved ``preset`` and
        the five explicit fields ride the call, and anything still unspecified
        falls back to the settings of the host's last game rather than to the
        factory defaults.
        """
        handler, game = _require_runtime(hass)
        overrides = dict(call.data)
        preset_name = str(overrides.pop("preset", "") or "").strip()
        preset = await _resolve_preset(hass, preset_name) if preset_name else None
        try:
            await handler.admin_action_start_game(
                game, preset=preset, overrides=overrides
            )
        except ValueError as err:
            raise ServiceValidationError(
                "Cannot start a new quiz right now — a game is already in "
                "progress. End it first, then start a new one."
            ) from err

    async def next_round_service(call: ServiceCall) -> None:  # noqa: ARG001
        handler, game = _require_runtime(hass)
        try:
            await handler.admin_action_next_round(game)
        except ValueError as err:
            raise ServiceValidationError(
                "Cannot advance to the next question right now. Start a game "
                "first, or wait until the current question has been revealed."
            ) from err

    async def pause_service(call: ServiceCall) -> None:  # noqa: ARG001
        handler, game = _require_runtime(hass)
        if not await handler.admin_action_pause(game):
            raise ServiceValidationError(
                "There is no active question to pause right now."
            )

    async def resume_service(call: ServiceCall) -> None:  # noqa: ARG001
        handler, game = _require_runtime(hass)
        if not await handler.admin_action_resume(game):
            raise ServiceValidationError(
                "The game is not paused, so there is nothing to resume."
            )

    async def end_game_service(call: ServiceCall) -> None:  # noqa: ARG001
        handler, game = _require_runtime(hass)
        if game.phase == GamePhase.LOBBY:
            raise ServiceValidationError(
                "No game is currently running, so there is nothing to end."
            )
        await handler.admin_action_end_game(game)

    async def reload_packs_service(call: ServiceCall) -> None:  # noqa: ARG001
        """Re-read the question packs from disk (#743).

        The host-owned drop-in folder is ``<config>/quizify/packs``; a pack
        added there used to need a full Home Assistant restart, because
        ``reload_categories`` had no caller and no service. Reloading is
        refused outside the lobby: the running game's queue was built from the
        packs as they were, and swapping the bank underneath it would leave
        the round the players are answering pointing at questions that are no
        longer loaded.
        """
        _handler, game = _require_runtime(hass)
        if game.phase != GamePhase.LOBBY:
            raise ServiceValidationError(
                "Packs can only be reloaded from the lobby. End the running "
                "game first, then reload."
            )
        # Blocking disk I/O (glob + JSON parse of every pack) — off the loop.
        # ``HARuntime.run_in_executor`` is exactly this call, so going straight
        # to hass keeps the handler free of an entry-scoped dependency it would
        # otherwise have to look up and guard.
        categories = await hass.async_add_executor_job(
            game.question_bank.reload_categories
        )
        _LOGGER.info(
            "Quizify packs reloaded via service: %d packs available",
            len(categories),
        )

    hass.services.async_register(DOMAIN, "reset_admin_session", reset_admin_session)
    hass.services.async_register(
        DOMAIN, "start_game", start_game_service, schema=START_GAME_SCHEMA
    )
    hass.services.async_register(DOMAIN, "next_round", next_round_service)
    hass.services.async_register(DOMAIN, "pause", pause_service)
    hass.services.async_register(DOMAIN, "resume", resume_service)
    hass.services.async_register(DOMAIN, "end_game", end_game_service)
    hass.services.async_register(DOMAIN, "reload_packs", reload_packs_service)
    _LOGGER.debug("Quizify services registered (#367 / #743 / #744)")
