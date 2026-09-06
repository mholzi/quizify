"""Room sound effects (SFX) for Quizify — "The House Plays Along" Phase 3 (#494).

Subscribes to the ``quizify_*`` HA bus events (the #366 event backbone) and
plays a short one-shot sound on a configured ``media_player`` at every game
milestone: a "correct" sting when the crowd mostly nailed the answer, a "wrong"
buzzer when they mostly missed, a chime on a hot streak, and a fanfare when the
winner is decided. Variant C "Hybrid" — bundled CC0 defaults with per-cue
user-URL overrides.

Two sources per cue (hybrid):

* **Override** — a free-text URL the host supplied in the options flow
  (``CONF_SFX_*_URL``). Wins when set.
* **Bundled default** — ``www/sfx/<cue>.mp3`` served at
  ``/quizify/static/sfx/<cue>.mp3``, used only when no override is set AND the
  file physically exists on disk. The four defaults ship with the integration
  (synthesized originals, see ``www/sfx/README.md``); deleting one mutes that
  cue, which is why the existence check stays.

If neither an override nor an available default resolves for a cue, that cue
no-ops silently.

The SFX share the SAME ``media_player`` entity used for TTS announcements and
lobby music (no separate config field). To avoid an effect talking over a live
TTS announcement, a cue is **dropped** (not queued) whenever the shared speaker
is already ``playing`` — TTS wins the speaker, the sting is skipped.

Everything no-ops cleanly when:
- no media_player entity is configured
- the runtime has no HA instance (standalone dev server)
- neither an override URL nor an available bundled default resolves for the cue
"""

from __future__ import annotations

import contextlib
import logging
from pathlib import Path
from typing import TYPE_CHECKING

from .game_events import (
    EVENT_ANSWER_REVEALED,
    EVENT_STREAK_MILESTONE,
    EVENT_WINNER_DECIDED,
)
from .ha_service import fire_and_forget_service
from .house_settings import CUE_KEYS, HouseSettings

if TYPE_CHECKING:
    from collections.abc import Callable

    from homeassistant.core import Event, HomeAssistant

    from .game.state import QuizifyGameState

_LOGGER = logging.getLogger(__name__)

# Static-asset location — mirrors ``server.__init__`` (the source of truth) but
# is redeclared here to avoid importing the whole server stack (views /
# pack_submission …) just for two constants, which would drag heavy deps into
# this light event-handler module. ``www/sfx/<cue>.mp3`` is served at
# ``/quizify/static/sfx/<cue>.mp3``. Keep these two in sync with server.__init__.
WWW_DIR = Path(__file__).parent / "www"
STATIC_URL_PREFIX = "/quizify/static"

# The four cue keys. Each maps to an optional override URL (from the options
# flow) and a bundled default at ``www/sfx/<cue>.mp3``. The list now lives with
# the config-entry defaults that carry the override URLs (:mod:`house_settings`);
# this alias keeps the established module-local name.
_CUE_KEYS = CUE_KEYS

# Best-effort HA base-URL helper. Imported lazily/guarded so the module still
# imports on the standalone dev server (no ``homeassistant`` package) and so
# tests can monkeypatch it. ``None`` => we cannot build a bundled-default URL
# (only host-supplied override URLs work); that is a safe degradation.
try:  # pragma: no cover - import guard exercised only off-HA
    from homeassistant.helpers.network import get_url as _ha_get_url
except Exception:  # noqa: BLE001 - any import failure => run without defaults
    _ha_get_url = None  # type: ignore[assignment]


class QuizifySoundEffects:
    """Plays one-shot SFX on an HA media_player at game milestones (#494 P3)."""

    def __init__(
        self,
        hass: HomeAssistant | None,
        game_state: QuizifyGameState,
        media_player_entity_id: str | None = None,
        cue_urls: dict[str, str | None] | None = None,
        enabled: bool = True,
        settings: HouseSettings | None = None,
    ) -> None:
        """Build the room-SFX player.

        ``settings`` is the shared :class:`HouseSettings` (#789): the speaker,
        the per-cue override URLs and the house master are read through it, so
        an options reload updates them in place rather than rebuilding this
        object. ``media_player_entity_id`` / ``cue_urls`` / ``enabled`` are the
        standalone form used by the dev server and the unit tests; they seed a
        private settings object that behaves identically.
        """
        self._hass = hass
        # Config-entry values (the shared speaker, the four per-cue override
        # URLs, the CONF_HOUSE_EVENTS_ENABLED master) are read lazily off this.
        # Per-cue URLs are normalized there: blank/whitespace => None (no
        # override); unknown keys are ignored and missing keys default to None.
        self._settings = settings or HouseSettings(
            house_enabled=bool(enabled),
            media_player=(media_player_entity_id or "").strip() or None,
            cue_urls={
                cue: ((cue_urls or {}).get(cue) or "").strip() or None
                for cue in _CUE_KEYS
            },
        )
        self._game = game_state
        # Resolved bundled-default URLs, computed ONCE at attach_events() time so
        # we never stat the disk on the event hot path (#475). Cue key -> URL for
        # every cue whose default file exists AND for which we could build a base
        # URL. Overrides are checked ahead of this at play time.
        self._default_urls: dict[str, str] = {}
        self._event_unsubs: list[Callable[[], None]] = []

        # --- "House Plays Along" runtime config (#494 Phase 4) ---------------
        # The master switch lives on the shared settings object in two layers
        # (the config-entry value plus the admin panel's tri-state override), so
        # a host toggling CONF_HOUSE_EVENTS_ENABLED in the options UI on a
        # never-panelled install takes effect immediately, while a panel-set
        # master survives an unrelated options change (#411).
        #
        # Per-cue toggles, all default ON so flipping the master on gives the
        # full soundtrack out of the box (the TTS posture, #281).
        self._cue_enabled: dict[str, bool] = dict.fromkeys(_CUE_KEYS, True)
        # Per-game media_player override from the admin panel. ``None`` → fall
        # back to the config-entry value. Stored normalized (empty → None) so a
        # blank override never masks the fallback — mirrors
        # ``tts.QuizifyTTSAnnouncer._active_media_player``.
        self._media_player_override: str | None = None

    # ------------------------------------------------------------------
    # Per-game configuration (#494 Phase 4 — the "House Plays Along" panel)
    # ------------------------------------------------------------------

    def configure(
        self,
        *,
        enabled: bool,
        sfx_correct: bool = True,
        sfx_wrong: bool = True,
        sfx_streak: bool = True,
        sfx_winner: bool = True,
        media_player: str | None = None,
    ) -> None:
        """Apply the admin panel's house-SFX settings (#494 P4).

        ``media_player`` is the panel's speaker picker. Non-empty → it wins for
        this game; empty/``None`` → fall back to the config-entry value. Re-runs
        ``attach_events`` so an override that makes the service configured for
        the first time (no speaker in the options flow, one picked in the panel)
        still gets its cue listeners — and its one-time bundled-default stat.
        """
        # The master is the one panel value shared with the party lights and the
        # event emitter, so it is written to the shared settings (#789).
        self._settings.enabled_override = bool(enabled)
        self._cue_enabled = {
            "correct": bool(sfx_correct),
            "wrong": bool(sfx_wrong),
            "streak": bool(sfx_streak),
            "winner": bool(sfx_winner),
        }
        self._media_player_override = (media_player or "").strip() or None
        # Idempotent; a no-op when already attached or still unconfigured.
        self.attach_events()

    # There is deliberately no export/restore_runtime_config pair any more
    # (#789): it existed only so the panel's toggles could survive the rebuild
    # an options reload used to perform. The settings object is now updated in
    # place, this instance outlives the reload, and the overrides below survive
    # simply by not being touched.

    @property
    def _enabled(self) -> bool:
        """The config-entry master (CONF_HOUSE_EVENTS_ENABLED), read live."""
        return self._settings.house_enabled

    @property
    def _enabled_override(self) -> bool | None:
        """The panel's tri-state master; ``None`` = the panel never set one."""
        return self._settings.enabled_override

    @property
    def _media_player_entity_id(self) -> str | None:
        """The config-entry speaker, read live off the settings."""
        return self._settings.media_player

    @property
    def _cue_urls(self) -> dict[str, str | None]:
        """The four config-entry per-cue override URLs, read live."""
        return self._settings.cue_urls

    @property
    def _master_enabled(self) -> bool:
        """The effective master: panel override if set, else the config entry."""
        return self._settings.master_enabled

    @property
    def _active_media_player(self) -> str | None:
        """Panel override if set, else the config-entry speaker."""
        return self._media_player_override or self._media_player_entity_id

    @property
    def is_configured(self) -> bool:
        # Deliberately NOT gated on ``_enabled``: the master silences the cues at
        # play time (see ``_play_cue``) rather than tearing down the listeners,
        # so the panel can flip it back on mid-game without a re-attach.
        return self._hass is not None and bool(self._active_media_player)

    def attach_events(self) -> None:
        """Subscribe to the milestone bus events and resolve bundled defaults.

        Idempotent, and a no-op unless configured (needs a hass bus + a target
        media_player). The one-time disk stat for the bundled defaults happens
        here, not per event (#475). The emitter (#366) only fires the underlying
        events when the host has opted into house events, so an off toggle means
        the listeners simply never trigger.
        """
        if not self.is_configured or self._event_unsubs:
            return
        hass = self._hass
        if hass is None:  # narrow for type-checkers; is_configured already guards
            return

        self._resolve_default_urls()

        self._event_unsubs = [
            hass.bus.async_listen(EVENT_ANSWER_REVEALED, self._on_answer_revealed),
            hass.bus.async_listen(EVENT_STREAK_MILESTONE, self._on_streak_milestone),
            hass.bus.async_listen(EVENT_WINNER_DECIDED, self._on_winner_decided),
        ]

    def refresh_from_settings(self) -> None:
        """Re-sync after the config entry's options changed (#789).

        The old reload path rebuilt this object, which re-stat'd the bundled
        defaults and re-ran ``attach_events()``. Both are reproduced here so a
        host who just configured their first speaker gets the cue listeners
        armed, and both are idempotent.
        """
        if self._event_unsubs:
            self._resolve_default_urls()
        self.attach_events()

    def detach(self) -> None:
        """Drop the bus listeners. Suppresses unsub errors (reload/unload safe)."""
        for unsub in self._event_unsubs:
            with contextlib.suppress(Exception):
                unsub()
        self._event_unsubs = []

    # ------------------------------------------------------------------
    # One-time bundled-default resolution
    # ------------------------------------------------------------------

    def _resolve_default_urls(self) -> None:
        """Stat ``www/sfx/<cue>.mp3`` once and cache the servable URL per cue.

        A cue's bundled default is available only when the file exists on disk
        AND we can build the HA base URL (``get_url``). Cues with an override
        set never need a default, but we still resolve all four so a later
        options change that clears an override falls back cleanly on the next
        rebuild. Any error (missing package, no configured URL) leaves the
        default unavailable — the cue then simply no-ops unless overridden.
        """
        self._default_urls = {}
        base = self._base_url()
        if base is None:
            return
        sfx_dir = WWW_DIR / "sfx"
        for cue in _CUE_KEYS:
            path = sfx_dir / f"{cue}.mp3"
            try:
                exists = path.is_file()
            except OSError:
                exists = False
            if exists:
                self._default_urls[cue] = f"{base}{STATIC_URL_PREFIX}/sfx/{cue}.mp3"

    def _base_url(self) -> str | None:
        """Best-effort HA external/internal base URL, or ``None`` if unavailable.

        Wraps ``homeassistant.helpers.network.get_url``; any failure (no HA, no
        configured URL) degrades to ``None`` so only override URLs remain usable.
        """
        hass = self._hass
        if hass is None or _ha_get_url is None:
            return None
        try:
            return _ha_get_url(hass).rstrip("/")
        except Exception:  # noqa: BLE001 - get_url raises when no URL is configured
            return None

    # ------------------------------------------------------------------
    # Bus-event handlers
    # ------------------------------------------------------------------

    def _on_answer_revealed(self, event: Event) -> None:
        """Correct sting when the crowd mostly nailed it, else the wrong buzzer.

        Guards ``total_players == 0`` (no div-by-zero) → treated as the minority
        case (wrong). A strict majority (> half) picks the correct cue.
        """
        data = event.data or {}
        correct = data.get("correct_count") or 0
        total = data.get("total_players") or 0
        majority = total > 0 and correct > total / 2
        self._play_cue("correct" if majority else "wrong")

    def _on_streak_milestone(self, event: Event) -> None:  # noqa: ARG002
        """Chime on a hot streak."""
        self._play_cue("streak")

    def _on_winner_decided(self, event: Event) -> None:  # noqa: ARG002
        """Fanfare when the winner is decided."""
        self._play_cue("winner")

    # ------------------------------------------------------------------
    # Playback
    # ------------------------------------------------------------------

    def _play_cue(self, cue: str) -> None:
        """Resolve the source for ``cue`` and one-shot it on the media_player.

        Gated on the master switch AND this cue's own panel toggle (#494 P4) —
        the single choke point every event handler funnels through, so an off
        cue is silent no matter which milestone triggered it.

        Source precedence: host override URL, else the bundled default (only if
        the file existed at attach time). No source → silent no-op. Held during
        TTS: if the shared speaker is already ``playing`` the cue is dropped so
        it never talks over a live announcement.
        """
        if not self.is_configured:
            return
        if not (self._master_enabled and self._cue_enabled.get(cue, True)):
            return
        url = self._cue_urls.get(cue) or self._default_urls.get(cue)
        if not url:
            return
        if self._tts_is_playing():
            # A TTS announcement owns the shared speaker right now — drop the
            # sting rather than queue it (queuing would play it late, after the
            # moment has passed, or cut the announcement short).
            _LOGGER.debug("Quizify SFX %r skipped: media_player is playing", cue)
            return
        self._call(
            "media_player",
            "play_media",
            {
                "entity_id": self._active_media_player,
                "media_content_id": url,
                "media_content_type": "music",
            },
        )

    def _tts_is_playing(self) -> bool:
        """True when the shared media_player is mid-playback (a TTS proxy).

        We can't observe the TTS layer directly, so the speaker's own
        ``playing`` state stands in for "an announcement is live". An
        unavailable/unknown state (or no hass) means nothing is playing → the
        cue proceeds.
        """
        hass = self._hass
        entity = self._active_media_player
        if hass is None or entity is None:
            return False
        state = hass.states.get(entity)
        return state is not None and state.state == "playing"

    def _call(self, domain: str, service: str, data: dict[str, object]) -> None:
        fire_and_forget_service(
            self._hass,
            domain,
            service,
            data,
            f"Room SFX {domain}.{service} "
            f"(media_player={self._active_media_player})",
        )
