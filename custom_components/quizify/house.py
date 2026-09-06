"""One place that builds the five "House Plays Along" consumers (#789).

``async_setup_entry`` used to construct the TTS announcer, the party lights, the
lobby music, the event emitter and the room SFX inline — and then the nested
``_update_listener`` closure repeated every one of those constructor calls with
identical keyword arguments, so any new option had to be added in two places
with nothing forcing the pairing.

There is now one construction site, here, and no second one: the reload path
updates the shared :class:`~custom_components.quizify.house_settings.HouseSettings`
in place (:meth:`HouseConsumers.apply_options`) and the consumers read the new
values through it. Nothing is torn down, rebuilt or snapshotted.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from .game_events import QuizifyEventEmitter
from .house_settings import HouseSettings
from .lights import QuizifyPartyLights
from .lobby_music import QuizifyLobbyMusic
from .sound_effects import QuizifySoundEffects
from .tts import QuizifyTTSAnnouncer

if TYPE_CHECKING:
    from collections.abc import Mapping
    from typing import Any

    from homeassistant.core import HomeAssistant

    from .game.state import QuizifyGameState


@dataclass
class HouseConsumers:
    """The five long-lived house consumers plus the settings they share.

    Held on the :class:`~custom_components.quizify.server.context.AppContext`
    for the lifetime of the config entry. Every member outlives an options
    reload — that is the point of the settings object — so a reference taken at
    setup stays valid, and the WS handler never has to be re-pointed.
    """

    settings: HouseSettings
    party_lights: QuizifyPartyLights
    tts_announcer: QuizifyTTSAnnouncer
    lobby_music: QuizifyLobbyMusic
    event_emitter: QuizifyEventEmitter
    sound_effects: QuizifySoundEffects

    # Order matters only for the detach sweep on unload (#605); the pairs are
    # (label, consumer) so a failure can be logged against a readable name.
    def as_pairs(self) -> tuple[tuple[str, object], ...]:
        """The five consumers with their ``hass.data`` labels."""
        return (
            ("party_lights", self.party_lights),
            ("tts_announcer", self.tts_announcer),
            ("lobby_music", self.lobby_music),
            ("event_emitter", self.event_emitter),
            ("sound_effects", self.sound_effects),
        )

    def attach(self) -> None:
        """Subscribe every consumer to the game state and the event bus.

        Exactly the sequence ``async_setup_entry`` ran inline: the lights take
        both the phase callback and the accent listeners, the SFX take only the
        bus listeners (they have no phase behaviour), and the rest take the
        phase callback.
        """
        self.party_lights.attach()
        # Accent choreography (#494 Phase 2): react to the quizify_* bus events
        # on top of the phase-driven ambient glow. No-op unless configured.
        self.party_lights.attach_events()
        self.tts_announcer.attach()
        self.lobby_music.attach()
        self.event_emitter.attach()
        # No phase callback for SFX, so only attach_events() — which also does
        # the one-time bundled-default disk stat.
        self.sound_effects.attach_events()

    def apply_options(self, options: Mapping[str, Any]) -> None:
        """Push a fresh set of config-entry options onto the live consumers.

        The whole of the options-reload path (#789). ``update_from_options``
        rewrites the shared defaults, which every consumer reads lazily, so the
        new values are live immediately; the three ``refresh_from_settings``
        calls only reproduce the *side effects* the old rebuild had — arming
        listeners for a host who just configured their first entity, re-syncing
        the bulbs, restarting lobby music on a changed speaker.
        """
        self.settings.update_from_options(options)
        self.party_lights.refresh_from_settings()
        self.sound_effects.refresh_from_settings()
        self.lobby_music.refresh_from_settings()


def build_house_consumers(
    hass: HomeAssistant | None,
    options: Mapping[str, Any],
    game_state: QuizifyGameState,
) -> HouseConsumers:
    """Build all five house consumers off one config entry's options.

    Nothing is attached here — the caller does that via
    :meth:`HouseConsumers.attach` once it has also wired the WS handler, so the
    ordering stays visible at the setup site.
    """
    settings = HouseSettings.from_options(options)
    return HouseConsumers(
        settings=settings,
        # The house-lights master defaults to CONF_HOUSE_EVENTS_ENABLED (off out
        # of the box) — the admin "House Plays Along" panel overrides it per game
        # via configure() (#494 P4). Only the event-driven ACCENTS are gated by
        # it; the phase-driven ambient glow always follows the game.
        party_lights=QuizifyPartyLights(
            hass=hass, game_state=game_state, settings=settings
        ),
        tts_announcer=QuizifyTTSAnnouncer(
            hass=hass, game_state=game_state, settings=settings
        ),
        # Lobby music shares the TTS media_player entity (no separate field).
        lobby_music=QuizifyLobbyMusic(
            hass=hass, game_state=game_state, settings=settings
        ),
        # HA event backbone (#366) — fires quizify_* bus events at game
        # milestones so the host can drive their own automations. Gated behind
        # the same master toggle, and always inert on the standalone dev server
        # (no bus).
        event_emitter=QuizifyEventEmitter(
            hass=hass, game_state=game_state, settings=settings
        ),
        # Room SFX (#494 Phase 3): one-shot stings on the shared media_player,
        # driven off the quizify_* bus events. Hybrid source — per-cue override
        # URLs from the options flow, else the bundled CC0 default at
        # www/sfx/<cue>.mp3 (if the host installed one).
        sound_effects=QuizifySoundEffects(
            hass=hass, game_state=game_state, settings=settings
        ),
    )
