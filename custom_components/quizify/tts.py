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
from typing import TYPE_CHECKING, Any

from .game import tts_phrases
from .game.state import GamePhase, QuizifyGameState
from .ha_service import fire_and_forget_service

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
        hass: HomeAssistant | None,
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

        # Runtime narration config (#281). The master switch defaults OFF so
        # a configured TTS entity stays silent until the host enables it in the
        # admin setup; the per-event toggles default ON so flipping the master
        # on narrates the full round out of the box. Configured per-game from
        # the ``start_game`` WS payload (see ``configure``).
        self._enabled: bool = False
        self._announce_question: bool = True
        self._announce_reveal: bool = True
        self._announce_standings: bool = True
        # Per-game entity overrides (#281). The admin TTS panel can pick a TTS
        # engine + media player directly (dropdowns backed by
        # /api/quizify/tts-entities); those ride the start_game payload like the
        # toggles. ``None``/empty → fall back to the construction-time
        # config-entry values (self._tts_entity_id / self._media_player_entity_id).
        self._tts_entity_override: str | None = None
        self._media_player_override: str | None = None
        # Last single leader name we observed, for leader-change detection.
        # None means "no leader yet" — used to suppress the round-1 change.
        self._previous_leader: str | None = None

    # ------------------------------------------------------------------
    # Per-game configuration (#281)
    # ------------------------------------------------------------------

    def configure(
        self,
        *,
        enabled: bool,
        announce_question: bool,
        announce_reveal: bool,
        announce_standings: bool,
        tts_entity: str | None = None,
        media_player: str | None = None,
    ) -> None:
        """Apply per-game narration settings from the start_game payload.

        ``tts_entity`` / ``media_player`` are the optional per-game entity
        overrides from the admin TTS dropdowns (#281). When provided and
        non-empty they win for this game; when empty/``None`` the announcer
        falls back to the construction-time config-entry values. Stored
        normalized so an empty string never masks the fallback.

        Resets leader-change tracking so a fresh game's first scored round
        never fires a spurious "takes the lead".
        """
        self._enabled = bool(enabled)
        self._announce_question = bool(announce_question)
        self._announce_reveal = bool(announce_reveal)
        self._announce_standings = bool(announce_standings)
        self._tts_entity_override = (tts_entity or "").strip() or None
        self._media_player_override = (media_player or "").strip() or None
        self._previous_leader = None

    @property
    def _active_tts_entity(self) -> str | None:
        """Per-game override if set, else the config-entry default."""
        return self._tts_entity_override or self._tts_entity_id

    @property
    def _active_media_player(self) -> str | None:
        """Per-game override if set, else the config-entry default."""
        return self._media_player_override or self._media_player_entity_id

    @property
    def is_configured(self) -> bool:
        return (
            self._hass is not None
            and bool(self._active_tts_entity)
            and bool(self._active_media_player)
        )

    def attach(self) -> None:
        """Register with the game state. Idempotent."""
        self._game.register_state_callback(self._on_state_changed)

    def detach(self) -> None:
        self._game.unregister_state_callback(self._on_state_changed)

    def _lang(self) -> str:
        """Normalized language for spoken phrases, from the live game."""
        return tts_phrases.normalize_language(self._game.language)

    # ------------------------------------------------------------------
    # Narration hooks (#281) — driven from the WS handler
    # ------------------------------------------------------------------

    def announce_question(
        self, question: Any, round_no: int, total_rounds: int
    ) -> None:
        """Narrate the question text at round start.

        Gated on the master switch AND the per-event question toggle. No-op
        when narration is off so a configured TTS entity stays quiet.
        """
        if not (self._enabled and self._announce_question):
            return
        text = getattr(question, "question", "") or ""
        message = tts_phrases.phrase(
            self._lang(),
            "question",
            round=round_no,
            total=total_rounds,
            text=text,
        )
        self._speak(message)

    def announce_reveal(self, game_state: QuizifyGameState) -> None:
        """Narrate the reveal as ONE combined utterance (#281).

        Fragments — correct answer, who-got-it, leader-change — are each gated
        by their own per-event toggle and joined into a single sentence so the
        audio flows the way a host would describe the round, instead of a
        stutter of separate clips. No-op when the master switch is off.
        """
        if not self._enabled:
            return
        summary = game_state.get_round_summary()
        if summary is None:
            return
        lang = self._lang()
        frags: list[str] = []

        # Correct answer.
        if self._announce_reveal:
            answer_text = ""
            if summary.correct_answer is not None:
                answer_text = summary.correct_answer.text or ""
            if answer_text:
                frags.append(
                    tts_phrases.phrase(lang, "answer", answer=answer_text)
                )

        # Who got it — names of players who answered correctly, else "nobody".
        # Gated by the same reveal toggle as the answer line.
        if self._announce_reveal:
            correct_names = [r.player_id for r in summary.results if r.correct]
            if correct_names:
                names = tts_phrases.join_names(lang, correct_names)
                key = "got_it_single" if len(correct_names) == 1 else "got_it_multi"
                frags.append(tts_phrases.phrase(lang, key, names=names))
            elif summary.results:
                # Players answered but none correctly.
                frags.append(tts_phrases.phrase(lang, "nobody"))

        # Standings — leader change / tie at the top. _previous_leader is
        # updated regardless of the toggle so detection stays correct even if
        # the host toggles standings off mid-game.
        self._announce_standings_fragment(lang, summary, frags)

        if frags:
            self._speak(" ".join(frags))

    def _announce_standings_fragment(
        self, lang: str, summary: Any, frags: list[str]
    ) -> None:
        """Append the leader-change / tie fragment, tracking _previous_leader.

        Round 1 is suppressed: the leader always "changes" from nobody on the
        first scored round.
        """
        leaderboard = summary.leaderboard or []
        # Entries are dicts (serialize_leaderboard): {name, score, rank, ...},
        # already sorted high→low. Find the players sharing the top score.
        top = [e for e in leaderboard if isinstance(e, dict) and "score" in e]
        if not top:
            return
        top_score = top[0]["score"]
        if not top_score:
            # Nobody has scored yet — no standings to narrate.
            return
        leaders = [e["name"] for e in top if e["score"] == top_score]
        if len(leaders) > 1:
            if self._announce_standings:
                frags.append(tts_phrases.phrase(lang, "tie_at_top"))
            self._previous_leader = None
            return
        new_leader = leaders[0]
        # A real change (new_leader != previous) only narrates when there WAS a
        # previous leader (suppresses round 1) and the toggle is on. The
        # _previous_leader update below runs regardless so detection stays
        # correct even with the toggle off.
        if (
            new_leader != self._previous_leader
            and self._previous_leader is not None
            and self._announce_standings
        ):
            frags.append(
                tts_phrases.phrase(lang, "leader_change", name=new_leader)
            )
        self._previous_leader = new_leader

    def announce_milestone(self, player_name: str, streak: int) -> None:
        """Trigger a milestone announcement. Called from the WS handler
        when it broadcasts ``streak_milestone`` so the announcement is
        tied to the actual award, not to a state-snapshot reading."""
        if not self._enabled:
            return
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
        # Narration off → don't fire the lifecycle phrases. The phase
        # tracking below is skipped too; configure() resets it per game.
        if not self._enabled:
            return
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
                self._speak(
                    f"Game over. The winner is {leader.name} "
                    f"with {leader.score} points!"
                )
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
        if (
            self._last_spoken_at is not None
            and now - self._last_spoken_at < TTS_MIN_INTERVAL
        ):
            _LOGGER.debug(
                "TTS throttled (%.1fs since last): %s",
                now - self._last_spoken_at,
                message,
            )
            return
        self._last_spoken_at = now

        tts_entity = self._active_tts_entity
        media_player = self._active_media_player
        fire_and_forget_service(
            self._hass,
            "tts",
            "speak",
            {
                "entity_id": tts_entity,
                "media_player_entity_id": media_player,
                "message": message,
            },
            f"TTS announcement (tts={tts_entity}, "
            f"media_player={media_player})",
        )
