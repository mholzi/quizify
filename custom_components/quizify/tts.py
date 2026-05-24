"""Text-to-speech announcer for Quizify.

Subscribes to ``QuizifyGameState`` state callbacks and speaks short
announcements at game events via Home Assistant's ``tts.speak`` service.
The service is intentionally thin — phrase composition lives here, but
the actual audio routing is HA's job.

HA's modern ``tts.speak`` API requires BOTH a TTS provider entity
(e.g. ``tts.google_translate_say``) AND a media player to route the
audio to. Beatify learned the hard way (#793) that calling tts.speak
with only the provider produces audio with nowhere to play.

Standalone runtime (no HA) skips silently — the dev server doesn't have
a TTS service to call.
"""

from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING

from .game.state import GamePhase, QuizifyGameState

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

_LOGGER = logging.getLogger(__name__)

# Minimum seconds between two TTS calls. Without this, milestone storms
# (a player on a 25-streak crossing 3/5/10/15/20/25 in quick succession
# during a wager round) would stack 6 announcements on top of each
# other. 5s gives the speaker time to finish a short sentence.
TTS_MIN_INTERVAL = 5.0


class QuizifyTTSAnnouncer:
    """Speaks game events via HA TTS. Configured per-entry; no-op when
    either the tts entity or media player isn't set."""

    def __init__(
        self,
        hass: "HomeAssistant | None",
        tts_entity_id: str | None,
        media_player_entity_id: str | None,
        game_state: QuizifyGameState,
    ) -> None:
        self._hass = hass
        self._tts_entity_id = tts_entity_id
        self._media_player_entity_id = media_player_entity_id
        self._game = game_state
        # None means "never spoken yet" so the first call always passes
        # the throttle. (Initializing to 0.0 would block when
        # ``time.monotonic()`` happens to start near zero on the host —
        # python's monotonic clock origin is implementation-defined.)
        self._last_spoken_at: float | None = None
        # Track the last phase we observed so we only announce on
        # transitions, not on every state_callback fire.
        self._last_phase: GamePhase | None = None
        # Track which milestone broadcasts we've already announced per
        # game so a flap (player score change replaying the same
        # snapshot) doesn't re-trigger the same announcement.
        self._announced_milestones: set[tuple[str, int]] = set()

    @property
    def is_configured(self) -> bool:
        return (
            self._hass is not None
            and bool(self._tts_entity_id)
            and bool(self._media_player_entity_id)
        )

    def attach(self) -> None:
        """Register with the game state. Idempotent."""
        self._game.register_state_callback(self._on_state_changed)

    def detach(self) -> None:
        self._game.unregister_state_callback(self._on_state_changed)

    def announce_milestone(self, player_name: str, streak: int) -> None:
        """Trigger a milestone announcement. Called from the WS handler
        when it broadcasts ``streak_milestone`` so the announcement is
        tied to the actual award, not to a state-snapshot reading."""
        key = (player_name, streak)
        if key in self._announced_milestones:
            return
        self._announced_milestones.add(key)
        if streak >= 10:
            phrase = f"{player_name} is on fire — {streak} in a row!"
        else:
            phrase = f"{player_name} hit a {streak}-streak!"
        self._speak(phrase)

    # ------------------------------------------------------------------
    # State change handler
    # ------------------------------------------------------------------

    def _on_state_changed(self) -> None:
        phase = self._game.phase
        if phase == self._last_phase:
            return
        prev = self._last_phase
        self._last_phase = phase

        # LOBBY → first QUESTION_ACTIVE means "game starting".
        if (
            prev in (None, GamePhase.LOBBY)
            and phase == GamePhase.QUESTION_ACTIVE
            and self._game.round == 1
        ):
            self._announced_milestones.clear()
            self._speak("Quizify starting. Good luck!")
            return

        # ANSWER_REVEAL on the last round transitions through FINALE.
        if phase == GamePhase.FINALE:
            leader = self._game.leader
            if leader is not None:
                self._speak(f"Game over. The winner is {leader.name} with {leader.score} points!")
            else:
                self._speak("Game over.")
            return

        # "Final round!" announcement when entering the last round.
        if (
            phase == GamePhase.QUESTION_ACTIVE
            and self._game.round == self._game.total_rounds
            and self._game.total_rounds > 1
        ):
            self._speak("Final round!")

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _speak(self, message: str) -> None:
        """Fire-and-forget TTS call. Swallows errors so a bad config can't
        break the game loop."""
        if not self.is_configured:
            return
        now = time.monotonic()
        if self._last_spoken_at is not None and now - self._last_spoken_at < TTS_MIN_INTERVAL:
            _LOGGER.debug("TTS throttled (%.1fs since last): %s", now - self._last_spoken_at, message)
            return
        self._last_spoken_at = now

        hass = self._hass
        if hass is None:
            return

        async def _do_speak() -> None:
            try:
                await hass.services.async_call(
                    "tts",
                    "speak",
                    {
                        "entity_id": self._tts_entity_id,
                        "media_player_entity_id": self._media_player_entity_id,
                        "message": message,
                    },
                    blocking=False,
                )
            except Exception as err:  # noqa: BLE001
                _LOGGER.warning(
                    "TTS announcement failed (tts=%s, media_player=%s): %s",
                    self._tts_entity_id,
                    self._media_player_entity_id,
                    err,
                )

        hass.async_create_task(_do_speak())
