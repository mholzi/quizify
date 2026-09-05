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

# Seconds-remaining threshold for the spoken countdown warning (#281). Fires
# once per round when the round timer first drops to/below this. Kept >
# TTS_MIN_INTERVAL so the warning never throttles the more-important reveal
# narration that follows a few seconds later when the round ends.
COUNTDOWN_THRESHOLD_SECONDS = 10

# Key under which HA stores the ``tts`` entity component in ``hass.data``
# (``homeassistant.components.tts.const.DATA_COMPONENT`` is ``HassKey("tts")``,
# a plain ``str`` subclass). Read by entity id rather than imported so this
# module keeps its "no runtime HA import" shape (#745).
_TTS_ENTITY_COMPONENT_KEY = "tts"

# Regional tag preferred when an engine advertises only regional variants of a
# language we speak ("de" → "de-DE"). Engines differ: google_translate lists
# bare ``de``, HA Cloud lists ``de-DE``, and ``tts.speak`` matches by strict
# membership — so a blind ``language: "de"`` would raise on Cloud and the room
# would hear nothing. Anything not listed here falls back to the
# alphabetically first matching tag, so the pick is always deterministic.
_PREFERRED_REGIONAL_TAG = {
    "de": "de-DE",
    "en": "en-US",
    "es": "es-ES",
}


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
        self._announce_options: bool = True
        self._announce_reveal: bool = True
        self._announce_standings: bool = True
        self._announce_join: bool = True
        self._announce_countdown: bool = True
        self._announce_milestone: bool = True
        # One-shot guard so the spoken countdown warning fires at most once per
        # round. Reset at every question start (see ``announce_question``).
        self._countdown_announced: bool = False
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
        announce_options: bool = True,
        announce_join: bool = True,
        announce_countdown: bool = True,
        announce_milestone: bool = True,
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
        self._announce_options = bool(announce_options)
        self._announce_reveal = bool(announce_reveal)
        self._announce_standings = bool(announce_standings)
        self._announce_join = bool(announce_join)
        self._announce_countdown = bool(announce_countdown)
        self._announce_milestone = bool(announce_milestone)
        self._tts_entity_override = (tts_entity or "").strip() or None
        self._media_player_override = (media_player or "").strip() or None
        self._previous_leader = None

    def export_runtime_config(self) -> dict[str, Any]:
        """Snapshot the mutable per-game narration config (#411).

        An options reload rebuilds this announcer from the config entry, which
        would otherwise reset ``_enabled`` back to ``False`` and drop the admin's
        per-game entity overrides — silently killing narration mid-game until the
        next ``start_game``. The listener snapshots the live config here and
        restores it onto the fresh instance via :meth:`restore_runtime_config`.
        """
        return {
            "enabled": self._enabled,
            "announce_question": self._announce_question,
            "announce_options": self._announce_options,
            "announce_reveal": self._announce_reveal,
            "announce_standings": self._announce_standings,
            "announce_join": self._announce_join,
            "announce_countdown": self._announce_countdown,
            "announce_milestone": self._announce_milestone,
            "tts_entity_override": self._tts_entity_override,
            "media_player_override": self._media_player_override,
        }

    def restore_runtime_config(self, snapshot: dict[str, Any] | None) -> None:
        """Restore a config snapshot from :meth:`export_runtime_config` (#411).

        Defensive: a falsy/empty snapshot is a no-op, and each field falls back
        to the current value when absent, so a partial snapshot never clobbers
        an unrelated default.
        """
        if not snapshot:
            return
        self._enabled = bool(snapshot.get("enabled", self._enabled))
        self._announce_question = bool(
            snapshot.get("announce_question", self._announce_question)
        )
        self._announce_options = bool(
            snapshot.get("announce_options", self._announce_options)
        )
        self._announce_reveal = bool(
            snapshot.get("announce_reveal", self._announce_reveal)
        )
        self._announce_standings = bool(
            snapshot.get("announce_standings", self._announce_standings)
        )
        self._announce_join = bool(
            snapshot.get("announce_join", self._announce_join)
        )
        self._announce_countdown = bool(
            snapshot.get("announce_countdown", self._announce_countdown)
        )
        self._announce_milestone = bool(
            snapshot.get("announce_milestone", self._announce_milestone)
        )
        self._tts_entity_override = (
            snapshot.get("tts_entity_override", self._tts_entity_override)
            or None
        )
        self._media_player_override = (
            snapshot.get("media_player_override", self._media_player_override)
            or None
        )

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

    def _engine_supported_languages(self) -> list[str] | None:
        """Language tags the active TTS entity advertises, or ``None``.

        ``supported_languages`` lives on the entity object (HA keeps TTS
        entities in an ``EntityComponent`` under ``hass.data["tts"]``); it is
        deliberately not published in the entity's state attributes, so there
        is no cheaper way to read it. ``None`` means "could not find out" —
        the standalone dev server, a legacy non-entity provider, or a future
        HA that moves the component — and the caller then omits the language
        rather than guessing.
        """
        hass = self._hass
        entity_id = self._active_tts_entity
        if hass is None or not entity_id:
            return None
        try:
            component = hass.data[_TTS_ENTITY_COMPONENT_KEY]
            entity = component.get_entity(entity_id)
            languages = list(entity.supported_languages)
        except Exception:  # noqa: BLE001
            return None
        return [str(tag) for tag in languages] or None

    def _speech_language(self) -> str | None:
        """The tag to hand ``tts.speak``, or ``None`` to let the engine pick.

        Without this the engine always spoke in its own default language, so a
        Spanish game read Spanish sentences with a German voice (#745). The
        tag is resolved against what the engine actually supports: an exact
        match wins, then the preferred regional variant, then the first
        regional variant in alphabetical order. When the engine speaks none of
        it — or we could not read its list at all — the language is left out
        and the engine keeps its default, exactly as before.
        """
        lang = self._lang()
        supported = self._engine_supported_languages()
        if not supported:
            return None
        for tag in supported:
            if tag.lower() == lang:
                return tag
        regional = sorted(
            tag
            for tag in supported
            if tag.lower().replace("_", "-").split("-")[0] == lang
        )
        if not regional:
            _LOGGER.debug(
                "TTS entity %s does not speak %s — using its default language",
                self._active_tts_entity,
                lang,
            )
            return None
        preferred = _PREFERRED_REGIONAL_TAG.get(lang)
        for tag in regional:
            if preferred and tag.lower() == preferred.lower():
                return tag
        return regional[0]

    # ------------------------------------------------------------------
    # Narration hooks (#281) — driven from the WS handler
    # ------------------------------------------------------------------

    def announce_question(
        self,
        question: Any,
        round_no: int,
        total_rounds: int,
        options: list[str] | None = None,
    ) -> None:
        """Narrate the question text (and optionally the answer options) at
        round start.

        Gated on the master switch AND the per-event question toggle. The
        options readout has its own toggle and is appended to the same
        utterance so the host hears "Question 3 of 10: … Your options are …"
        as one flowing line. ``options`` are the canonical shuffled answer
        texts — the same order the TV grid shows — so spoken letters match the
        screen. No-op when narration is off so a configured TTS entity stays
        quiet.

        Resets the per-round countdown guard unconditionally (even when
        question narration is off) so the spoken time warning can fire later
        this round regardless of the question toggle.
        """
        self._countdown_announced = False
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
        options_fragment = self._build_options_fragment(options)
        if options_fragment:
            message = f"{message} {options_fragment}"
        self._speak(message)

    def _build_options_fragment(self, options: list[str] | None) -> str:
        """Render the spoken answer-options list, or "" when not applicable.

        Gated by the options toggle. Skipped for non-multiple-choice rounds
        (estimate questions carry < 2 options). Each option is prefixed with
        its on-screen letter (A, B, C, …) so the audio matches the TV grid.
        """
        if not self._announce_options:
            return ""
        texts = [t for t in (options or []) if t]
        if len(texts) < 2:
            return ""
        lang = self._lang()
        lettered = [
            f"{chr(ord('A') + i)}, {text}" for i, text in enumerate(texts)
        ]
        return tts_phrases.phrase(lang, "options", options=". ".join(lettered))

    def announce_join(self, player_name: str, is_admin: bool = False) -> None:
        """Narrate a player joining the lobby (#281).

        Gated on the master switch AND the per-event join toggle. The host's
        own admin-as-player tab is skipped (``is_admin``) so the room doesn't
        hear the host announce themselves on every game.
        """
        if not (self._enabled and self._announce_join):
            return
        if is_admin or not player_name:
            return
        message = tts_phrases.phrase(
            self._lang(), "player_joined", name=player_name
        )
        self._speak(message)

    def announce_countdown(self, seconds_remaining: float) -> None:
        """Narrate a "time running out" warning, once per round (#281).

        Driven from the timer-tick loop, which calls this every tick with the
        room's minimum remaining time. Fires the first time that crosses to/
        below ``COUNTDOWN_THRESHOLD_SECONDS`` and then no-ops for the rest of
        the round (``_countdown_announced``). Gated on the master switch AND
        the per-event countdown toggle.
        """
        if not (self._enabled and self._announce_countdown):
            return
        if self._countdown_announced:
            return
        if not 0 < seconds_remaining <= COUNTDOWN_THRESHOLD_SECONDS:
            return
        self._countdown_announced = True
        seconds = max(1, round(seconds_remaining))
        message = tts_phrases.phrase(self._lang(), "countdown", seconds=seconds)
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
        tied to the actual award, not to a state-snapshot reading.

        Localized and toggleable since #745: the line used to be a hardcoded
        English f-string spoken into German games, and it was the one
        announcement without a per-event switch of its own.
        """
        if not (self._enabled and self._announce_milestone):
            return
        key = (player_name, streak)
        if key in self._announced_milestones:
            return
        self._announced_milestones.add(key)
        phrase_key = "milestone_fire" if streak >= 10 else "milestone_streak"
        self._speak(
            tts_phrases.phrase(
                self._lang(), phrase_key, name=player_name, streak=streak
            )
        )

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
            self._speak(tts_phrases.phrase(self._lang(), "game_start"))
            return

        # ANSWER_REVEAL on the last round transitions through FINALE.
        if phase == GamePhase.FINALE:
            leader = self._game.leader
            if leader is not None:
                self._speak(
                    tts_phrases.phrase(
                        self._lang(),
                        "game_over_winner",
                        name=leader.name,
                        score=leader.score,
                    )
                )
            else:
                self._speak(tts_phrases.phrase(self._lang(), "game_over"))
            return

        # "Final round!" announcement when entering the last round.
        if (
            phase == GamePhase.QUESTION_ACTIVE
            and self._game.round == self._game.total_rounds
            and self._game.total_rounds > 1
        ):
            self._speak(tts_phrases.phrase(self._lang(), "final_round"))

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
        data: dict[str, object] = {
            "entity_id": tts_entity,
            "media_player_entity_id": media_player,
            "message": message,
        }
        # Speak the game's language, not the engine's default (#745). Omitted
        # when the engine does not advertise it — ``tts.speak`` raises on an
        # unsupported tag, and silence is worse than the wrong accent.
        language = self._speech_language()
        if language:
            data["language"] = language
        fire_and_forget_service(
            self._hass,
            "tts",
            "speak",
            data,
            f"TTS announcement (tts={tts_entity}, "
            f"media_player={media_player}, language={language})",
        )
