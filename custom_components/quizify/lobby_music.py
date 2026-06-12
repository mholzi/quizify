"""Lobby music player for Quizify.

Subscribes to ``QuizifyGameState`` state callbacks and loops an ambient
audio file on a configured Home Assistant ``media_player`` entity while
the game sits in the LOBBY phase, then stops it as soon as the game
starts (leaves the lobby).

The audio is routed through the SAME ``media_player`` entity used for TTS
announcements (no separate config field). The trade-off: lobby music and
in-game TTS share one speaker, so the music is deliberately stopped at
game start to avoid overlapping the "Quizify starting!" announcement.

No audio asset ships with the integration. The feature is completely
inert unless the user supplies their own URL via the options flow
(e.g. ``/local/quizify-lobby.mp3`` served from ``config/www``).

The service no-ops cleanly when:
- no media_player entity is configured
- no lobby_music_url is configured
- the runtime has no HA instance (standalone dev server)
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from .game.state import GamePhase, QuizifyGameState
from .ha_service import fire_and_forget_service

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant


class QuizifyLobbyMusic:
    """Plays/stops lobby music on an HA media_player based on game phase."""

    def __init__(
        self,
        hass: "HomeAssistant | None",
        media_player_entity_id: str | None,
        game_state: QuizifyGameState,
    ) -> None:
        self._hass = hass
        self._media_player_entity_id = (media_player_entity_id or "").strip() or None
        self._game = game_state
        self._last_phase: GamePhase | None = None
        # Track whether we currently believe music is playing so we only
        # issue one stop on leaving the lobby (not on every state push).
        self._playing = False

    @property
    def is_configured(self) -> bool:
        # The lobby_music_url lives on the shared game state so options
        # reloads update it without rebuilding this object. We still read it
        # lazily in the handler — here we only gate on the static pieces.
        return self._hass is not None and bool(self._media_player_entity_id)

    def attach(self) -> None:
        self._game.register_state_callback(self._on_state_changed)

    def detach(self) -> None:
        self._game.unregister_state_callback(self._on_state_changed)
        # Best-effort: stop any lingering playback when we're torn down
        # (e.g. options reload swaps this instance for a fresh one).
        if self._playing:
            self._stop()

    def _on_state_changed(self) -> None:
        phase = self._game.phase
        if phase == self._last_phase:
            return
        self._last_phase = phase

        if not self.is_configured:
            return

        if phase == GamePhase.LOBBY:
            self._start()
        else:
            # Any non-lobby phase (game started, paused, finale, reveal) →
            # stop so the music never overlaps in-game TTS announcements.
            if self._playing:
                self._stop()

    def _start(self) -> None:
        url = self._game.lobby_music_url
        if not url:
            return
        self._playing = True
        # play_media starts the file; repeat_set with "all" loops it. The
        # repeat call is best-effort — players that don't support repeat
        # just ignore it (single play-through).
        self._call(
            "media_player",
            "play_media",
            {
                "entity_id": self._media_player_entity_id,
                "media_content_id": url,
                "media_content_type": "music",
            },
        )
        self._call(
            "media_player",
            "repeat_set",
            {
                "entity_id": self._media_player_entity_id,
                "repeat": "all",
            },
        )

    def _stop(self) -> None:
        self._playing = False
        self._call(
            "media_player",
            "media_stop",
            {"entity_id": self._media_player_entity_id},
        )

    def _call(self, domain: str, service: str, data: dict[str, object]) -> None:
        fire_and_forget_service(
            self._hass,
            domain,
            service,
            data,
            f"Lobby music {domain}.{service} "
            f"(media_player={self._media_player_entity_id})",
        )
