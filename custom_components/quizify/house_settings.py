"""The config-entry side of "The House Plays Along" (#789).

Every house consumer — TTS announcer, party lights, lobby music, event emitter,
room SFX — is fed from two sources that used to be fused inside each object:

* the **config entry** (the options flow: which lights, which speaker, which TTS
  engine, the per-cue override URLs, the ``CONF_HOUSE_EVENTS_ENABLED`` master),
  which changes when the host edits the integration options; and
* the admin **panel**, which overrides some of those per game over the socket.

Fusing them is what made an options reload expensive. The listener had to tear
every consumer down, rebuild it from the fresh options, and then restore the
panel's runtime state onto the new instance — which is why four classes carried
an ``export_runtime_config`` / ``restore_runtime_config`` pair that existed for
no other reason, and why #411 ("a reload silently kills narration mid-game") had
to be re-fixed once per consumer as they were added.

:class:`HouseSettings` splits the two apart. It holds the config-entry defaults
in one mutable object that every consumer keeps a **reference** to and reads
lazily, so :func:`HouseSettings.update_from_options` is the whole of the reload
path: no teardown, no rebuild, no snapshot. The panel's own per-consumer
overrides simply stay where they always were — on the consumer instances, which
now outlive the reload.

The one panel override that lives here rather than on a consumer is
``enabled_override``, the house master. It is a single switch the admin panel
pushes to three consumers at once (lights, SFX, event emitter); keeping three
private copies of one value in sync was duplication, not encapsulation.

Nothing in this module imports Home Assistant or the consumers themselves, so it
is safe to import from any of them (and from the standalone dev server).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from .const import (
    CONF_FINALE_SCENE,
    CONF_HOUSE_EVENTS_ENABLED,
    CONF_MEDIA_PLAYER_ENTITY,
    CONF_PARTY_LIGHT_ENTITIES,
    CONF_SFX_CORRECT_URL,
    CONF_SFX_STREAK_URL,
    CONF_SFX_WINNER_URL,
    CONF_SFX_WRONG_URL,
    CONF_TTS_ENTITY,
    DEFAULT_HOUSE_EVENTS_ENABLED,
)

if TYPE_CHECKING:
    from collections.abc import Mapping

#: The four SFX cue keys. Each maps to an optional override URL from the options
#: flow and a bundled default at ``www/sfx/<cue>.mp3``. Declared here rather than
#: in :mod:`sound_effects` so the options-reading code and the player agree on
#: the key set without the settings module importing the consumer.
CUE_KEYS = ("correct", "wrong", "streak", "winner")

#: Option key per cue, in ``CUE_KEYS`` order.
_CUE_OPTION_KEYS = {
    "correct": CONF_SFX_CORRECT_URL,
    "wrong": CONF_SFX_WRONG_URL,
    "streak": CONF_SFX_STREAK_URL,
    "winner": CONF_SFX_WINNER_URL,
}


def clean_entity_ids(entity_ids: list[str] | None) -> list[str]:
    """Strip whitespace, drop empties, dedupe while keeping order."""
    seen: set[str] = set()
    cleaned: list[str] = []
    for raw in entity_ids or []:
        entity = (raw or "").strip()
        if entity and entity not in seen:
            seen.add(entity)
            cleaned.append(entity)
    return cleaned


def _clean_str(value: Any) -> str | None:
    """Normalize a free-text/entity option to a non-empty string or ``None``."""
    return (str(value).strip() or None) if value else None


@dataclass
class HouseSettings:
    """Config-entry defaults for the house consumers, plus the shared master.

    Mutable and shared **by reference**: the consumers built in
    :mod:`custom_components.quizify.house` all hold the same instance, so
    :meth:`update_from_options` on an options reload is immediately visible to
    every one of them without anything being rebuilt.

    The construction defaults describe a bare, unconfigured house (no entities,
    no cue overrides). ``house_enabled`` defaults to ``True`` for the same reason
    the consumers' own ``enabled=`` arguments did: the product's "off out of the
    box" lives at the config layer — :meth:`from_options` resolves it from
    ``CONF_HOUSE_EVENTS_ENABLED``, whose default is off.
    """

    # --- config-entry defaults, rewritten in place by an options reload -----
    house_enabled: bool = True
    light_entities: list[str] = field(default_factory=list)
    finale_scene: str | None = None
    media_player: str | None = None
    tts_entity: str | None = None
    cue_urls: dict[str, str | None] = field(
        default_factory=lambda: dict.fromkeys(CUE_KEYS)
    )

    # --- the admin panel's shared master override ---------------------------
    # ``None`` means "the panel never touched it", so the config-entry value
    # above still wins. Tri-state on purpose: coercing it to a hard ``False``
    # would pin the master off and make the options-flow switch inert for a host
    # who never opened the panel.
    enabled_override: bool | None = None

    # ------------------------------------------------------------------
    # Construction / refresh from the config entry
    # ------------------------------------------------------------------

    @classmethod
    def from_options(cls, options: Mapping[str, Any]) -> HouseSettings:
        """Build the defaults from a config entry's ``options`` mapping."""
        settings = cls()
        settings.update_from_options(options)
        return settings

    def update_from_options(self, options: Mapping[str, Any]) -> None:
        """Re-read every config-entry default IN PLACE (the whole reload path).

        Deliberately does not touch :attr:`enabled_override` or any consumer's
        own per-game overrides: an options change must not discard what the
        admin panel set for the game currently in progress (#411). Because the
        consumers read through this object, the new values are live the moment
        this returns.
        """
        self.house_enabled = bool(
            options.get(CONF_HOUSE_EVENTS_ENABLED, DEFAULT_HOUSE_EVENTS_ENABLED)
        )
        self.light_entities = clean_entity_ids(
            list(options.get(CONF_PARTY_LIGHT_ENTITIES) or [])
        )
        self.finale_scene = _clean_str(options.get(CONF_FINALE_SCENE))
        self.media_player = _clean_str(options.get(CONF_MEDIA_PLAYER_ENTITY))
        self.tts_entity = _clean_str(options.get(CONF_TTS_ENTITY))
        self.cue_urls = {
            cue: _clean_str(options.get(_CUE_OPTION_KEYS[cue])) for cue in CUE_KEYS
        }

    # ------------------------------------------------------------------
    # Resolved values
    # ------------------------------------------------------------------

    @property
    def master_enabled(self) -> bool:
        """The effective house master: panel override if set, else the entry."""
        if self.enabled_override is None:
            return self.house_enabled
        return self.enabled_override
