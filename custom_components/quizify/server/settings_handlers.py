"""Lobby settings WebSocket handlers: TTS, "House Plays Along", language, difficulty.

Moved out of ``QuizifyWebSocketHandler`` (issue #1124, the follow-up of #1116)
the same way the wager, Hot Seat and lightning messages moved: module
functions that take the handler as their first argument. The handler keeps
``_apply_tts_config`` / ``_handle_configure_tts`` / ``_apply_house_config`` /
``_handle_configure_house`` / ``_handle_set_language`` /
``_handle_set_difficulty`` as one-line delegates, because the ``_DISPATCH``
table, ``_after_start_game`` and the tests reach them by those names.

What stays on the handler: the public wiring (``set_tts_announcer``,
``set_party_lights``, ``set_sound_effects``, ``set_event_emitter``) and the
consumer references those set. The ``configure_tts`` / ``configure_house``
admin gate (``_WS_ADMIN_ONLY``) stays in ``_handle_message``.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from custom_components.quizify.const import DIFFICULTY_AUTO
from custom_components.quizify.game.state import GamePhase
from custom_components.quizify.game.types import Difficulty

if TYPE_CHECKING:  # pragma: no cover - typing only
    from aiohttp import web

    from custom_components.quizify.game.state import QuizifyGameState
    from custom_components.quizify.server.websocket import QuizifyWebSocketHandler

# Same logger name as before the move (#1124), so a host's ``logger:`` config
# for ``custom_components.quizify.server.websocket`` still covers these
# warnings.
_LOGGER = logging.getLogger("custom_components.quizify.server.websocket")

# What ``set_difficulty`` accepts (#851): the four chips the setup screen
# offers. Anything else is a client that has drifted from the markup, and the
# lobby is better off keeping the value it has than painting a label nothing
# can translate.
_LOBBY_DIFFICULTIES = frozenset({d.value for d in Difficulty} | {DIFFICULTY_AUTO})


def _in_domain(entity_id: str, domain: str) -> bool:
    """True when ``entity_id`` names an entity of ``domain``.

    The house config arrives as untyped client JSON and its ids are handed
    straight to ``light.turn_on`` / ``scene.turn_on`` / ``media_player.play_media``.
    A domain check is the cheap half of #724: even an authorized host should not
    be able to aim the party lights at ``lock.front_door`` by editing the frame.
    """
    return entity_id.startswith(f"{domain}.")


def _entities_in_domain(entity_ids: list[str], domain: str, field: str) -> list[str]:
    """Keep only the ids of ``domain``, logging what was dropped (#724)."""
    kept = [e for e in entity_ids if _in_domain(e, domain)]
    dropped = [e for e in entity_ids if not _in_domain(e, domain)]
    if dropped:
        _LOGGER.warning(
            "House config: ignored %d %s entry/entries outside the %s domain: %s",
            len(dropped),
            field,
            domain,
            ", ".join(dropped),
        )
    return kept


def _entity_in_domain(entity_id: str, domain: str, field: str) -> str:
    """Keep ``entity_id`` only if it is of ``domain``, else "" (#724)."""
    if not entity_id or _in_domain(entity_id, domain):
        return entity_id
    _LOGGER.warning(
        "House config: ignored %s %r - not a %s entity",
        field,
        entity_id,
        domain,
    )
    return ""


def apply_tts_config(handler: QuizifyWebSocketHandler, tts: dict[str, Any]) -> None:
    """Push a flat TTS-settings dict onto the announcer (#281).

    Shared by ``start_game`` (config nested under ``data["tts"]``) and the
    lobby-time ``configure_tts`` message (config sent flat). No-op when no
    announcer is wired (standalone dev server, HA without a TTS entity);
    guarded so a malformed payload can't break the caller.
    """
    announcer = handler._tts_announcer
    if announcer is None:
        return
    try:
        announcer.configure(
            enabled=bool(tts.get("enabled")),
            announce_question=bool(tts.get("announce_question", True)),
            announce_options=bool(tts.get("announce_options", True)),
            announce_reveal=bool(tts.get("announce_reveal", True)),
            announce_standings=bool(tts.get("announce_standings", True)),
            announce_join=bool(tts.get("announce_join", True)),
            announce_countdown=bool(tts.get("announce_countdown", True)),
            announce_milestone=bool(tts.get("announce_milestone", True)),
            # Per-game entity overrides from the admin dropdowns (#281).
            # Empty/missing → the announcer falls back to the config-entry
            # default entities.
            tts_entity=_entity_in_domain(
                str(tts.get("tts_entity") or "").strip(), "tts", "tts_entity"
            )
            or None,
            media_player=_entity_in_domain(
                str(tts.get("media_player") or "").strip(),
                "media_player",
                "media_player",
            )
            or None,
        )
    except Exception:  # noqa: BLE001
        _LOGGER.exception("TTS configure raised")


async def handle_configure_tts(
    handler: QuizifyWebSocketHandler,
    ws: web.WebSocketResponse,
    data: dict,
    game_state: QuizifyGameState,
) -> None:
    """Handle the admin ``configure_tts`` message (#281).

    Sent by admin.js on connect and whenever a narration toggle/entity
    changes, so the announcer is configured during the pre-game lobby —
    otherwise player-join narration (which fires before ``start_game``)
    would never speak. The payload is the flat TTS-settings object.
    """
    handler._apply_tts_config(data or {})


def apply_house_config(handler: QuizifyWebSocketHandler, house: dict[str, Any]) -> None:
    """Push the flat "House Plays Along" settings dict onto its consumers.

    Shared by ``start_game`` (config nested under ``data["house"]``) and the
    lobby-time ``configure_house`` message (config sent flat), exactly like
    :meth:`_apply_tts_config`. The panel persists one flat dict in
    localStorage and pushes it verbatim; "presets" are a frontend-only
    concept, so a ``preset`` key (if any) is simply ignored here — the
    backend only ever sees the resolved booleans.

    Fans out to all THREE consumers, because the panel presents one master
    switch over what are internally three independent subsystems:
      * party lights  — the 5 accent cues + the winner scene;
      * sound effects — the 4 one-shot cues;
      * event emitter — the ``quizify_*`` bus events the other two ride on.
    The master ``enabled`` is a runtime override of the
    ``CONF_HOUSE_EVENTS_ENABLED`` option and is handed to each of them, so
    flipping it off silences the whole feature in one frame.

    Defensive by design — the payload is untyped client JSON:
      * each child toggle defaults to ON (``bool(house.get(key, True))``),
        so a partial dict degrades to "master decides" rather than to a
        silently half-dead panel; only the master defaults OFF;
      * each ``configure()`` is individually guarded, so one bad entity id
        (e.g. a stale ``light.*`` the host has since removed) can at worst
        kill its own subsystem, never the other two — and never the frame,
        which would drop the admin's whole message.
    No-op per consumer when it isn't wired (standalone dev server).
    """
    enabled = bool(house.get("enabled"))

    def _toggle(key: str) -> bool:
        return bool(house.get(key, True))

    # Entity overrides. An empty selection ("" / []) means "no override" —
    # the consumer then falls back to its config-entry default
    # (CONF_PARTY_LIGHT_ENTITIES / CONF_MEDIA_PLAYER_ENTITY /
    # CONF_FINALE_SCENE). Coerced defensively: a non-list ``light_entities``
    # from a malformed payload must not reach the light service call.
    raw_light_entities = house.get("light_entities")
    light_entities = (
        [str(e) for e in raw_light_entities]
        if isinstance(raw_light_entities, list)
        else []
    )
    media_player = str(house.get("media_player") or "").strip()
    winner_scene_entity = str(house.get("winner_scene_entity") or "").strip()

    # Domain allowlist (#724). Each override is forwarded to exactly one
    # service - light.turn_on, scene.turn_on, media_player.play_media - so
    # an id from any other domain is never a legitimate value here. Dropped
    # rather than rejected: one bad picker entry must not disarm the whole
    # house block, which is the same defensive stance as the guards below.
    light_entities = _entities_in_domain(light_entities, "light", "light_entities")
    media_player = _entity_in_domain(media_player, "media_player", "media_player")
    winner_scene_entity = _entity_in_domain(
        winner_scene_entity, "scene", "winner_scene_entity"
    )

    lights = handler._party_lights
    if lights is not None:
        try:
            lights.configure(
                enabled=enabled,
                light_question=_toggle("light_question"),
                light_countdown=_toggle("light_countdown"),
                light_reveal=_toggle("light_reveal"),
                light_streak=_toggle("light_streak"),
                light_winner=_toggle("light_winner"),
                winner_scene=_toggle("winner_scene"),
                light_entities=light_entities,
                winner_scene_entity=winner_scene_entity,
            )
        except Exception:  # noqa: BLE001
            _LOGGER.exception("House party-lights configure raised")

    effects = handler._sound_effects
    if effects is not None:
        try:
            effects.configure(
                enabled=enabled,
                sfx_correct=_toggle("sfx_correct"),
                sfx_wrong=_toggle("sfx_wrong"),
                sfx_streak=_toggle("sfx_streak"),
                sfx_winner=_toggle("sfx_winner"),
                media_player=media_player,
            )
        except Exception:  # noqa: BLE001
            _LOGGER.exception("House sound-effects configure raised")

    emitter = handler._event_emitter
    if emitter is not None:
        try:
            # The emitter has no per-cue toggles of its own — the bus events
            # are the substrate the lights and SFX subscribe to, so it only
            # honours the master switch. Leaving it armed while the master is
            # off would keep spamming quizify_* events at the host's own
            # automations, which is exactly what the master promises to stop.
            emitter.configure(enabled=enabled)
        except Exception:  # noqa: BLE001
            _LOGGER.exception("House event-emitter configure raised")


async def handle_configure_house(
    handler: QuizifyWebSocketHandler,
    ws: web.WebSocketResponse,
    data: dict,
    game_state: QuizifyGameState,
) -> None:
    """Handle the admin ``configure_house`` message (#494 Phase 4).

    Sent by admin.js on connect (right after ``admin_connect``) and whenever
    a house toggle / entity picker changes, mirroring ``configure_tts``. The
    on-connect push is why this must work with no game in progress: the
    lobby-phase cues (player-join glow, lobby SFX) fire before ``start_game``
    ever lands, so waiting for the start payload would leave them
    misconfigured for the entire lobby. Nothing here touches ``game_state``
    — the applier only reconfigures long-lived consumers — so the lobby path
    is safe by construction. The payload is the flat house-settings object.
    """
    handler._apply_house_config(data or {})


async def handle_set_language(
    handler: QuizifyWebSocketHandler,
    ws: web.WebSocketResponse,
    data: dict,
    game_state: QuizifyGameState,
) -> None:
    """Handle the admin ``set_language`` message (#776).

    The host's language pick used to reach the game only in the
    ``start_game`` payload, so for the whole life of the lobby
    ``GameState.language`` sat at its constructor default ``"de"``. Every
    phone that joined an English game was handed a German frame by
    ``_syncServerLanguage`` in player-core.js — the German lobby, then the
    German chrome around the first English question, because the correction
    only arrived with ``start_game``.

    Sent by admin.js on connect (right after ``admin_connect``) and whenever
    the language chip changes, exactly like ``configure_house`` (#494). Two
    consequences follow, and both are the point: a phone joining an English
    lobby sees English on its first painted frame, and flipping the chip
    while people are already sitting in the lobby re-renders their screens
    because we re-broadcast the state.

    Only accepted in LOBBY. Mid-game this would swap the chrome under the
    players without swapping the questions, which is worse than the bug.
    The value is not checked against a bundle list here for the same reason
    ``start_game`` doesn't check it: the client's ``QuizifyI18n.setLanguage``
    already clamps an unknown code to English, and a second, stricter gate
    on this path only invites the two to drift apart.
    """
    language = data.get("language")
    if not isinstance(language, str) or not language:
        return
    if game_state.phase != GamePhase.LOBBY:
        return
    if language == game_state.language:
        return
    game_state.language = language
    await handler._broadcast_state_projected(game_state)


async def handle_set_difficulty(
    handler: QuizifyWebSocketHandler,
    ws: web.WebSocketResponse,
    data: dict,
    game_state: QuizifyGameState,
) -> None:
    """Handle the admin ``set_difficulty`` message (#851).

    The same hole ``set_language`` closed in #776, one field over.
    ``GameState.difficulty`` is written by ``start_game`` and nowhere else,
    so for the whole life of the lobby it described the game BEFORE this
    one — the constructor default on the first game of the evening, the
    previous game's pick after that. The state snapshot carries that field
    to every phone and to the television, so a lobby the host had set to
    *Einfach* told the guests *Medium*, and the value was not wrong by a
    rendering: it was the last game's, faithfully rendered.

    Sent by admin.js on connect (right after ``admin_connect``), whenever
    the difficulty chip changes, and whenever a preset sets it — the same
    three moments ``set_language`` is sent at.

    Only accepted in LOBBY, and only for the four values the setup screen
    offers. Mid-game this would relabel a running game underneath the
    players; and ``start_game`` writes the field again from its own
    payload, so nothing here can survive into the game itself.
    """
    difficulty = data.get("difficulty")
    if not isinstance(difficulty, str) or difficulty not in _LOBBY_DIFFICULTIES:
        return
    if game_state.phase != GamePhase.LOBBY:
        return
    if difficulty == game_state.difficulty:
        return
    game_state.difficulty = difficulty
    await handler._broadcast_state_projected(game_state)
