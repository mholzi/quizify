"""Home Assistant event backbone for Quizify — "The House Plays Along" (#366).

Fires ``hass.bus`` events at every game milestone so the host can drive their
own automations off the quiz (dim the lights on a reveal, flash a lamp on a
streak, play a fanfare when the winner is decided, …). This is the clean event
layer the later light/SFX phases of #494 hook into instead of poking the game
loop directly.

The emitter mirrors :class:`~custom_components.quizify.lights.QuizifyPartyLights`:
it subscribes to ``QuizifyGameState`` state callbacks for the phase-driven
milestones (game start / finale / winner) and is called from the WebSocket
dispatch points for the richer per-event milestones (question shown, reveal,
streak, game ended) where the full round data is already assembled — exactly
like the ``_notify_tts_*`` hooks next to which these fire.

Everything no-ops cleanly when there is no Home Assistant instance (the
standalone dev server has no event bus), matching
:func:`~custom_components.quizify.ha_service.fire_and_forget_service`.

Event catalog
-------------
All events live in the ``quizify_*`` namespace. Payloads are JSON-serializable
and PII-minimal — player *display* names are included (they are already
broadcast to every client), but nothing else identifying. These names +
payload shapes are the stable contract the #366 automation blueprints build on,
so treat them as an API: additive changes only.

``quizify_game_started``      — {game_id, player_count, round_count}
``quizify_question_shown``    — {round, total_rounds, question_type}
``quizify_time_running_out``  — {seconds_remaining}
``quizify_answer_revealed``   — {round, correct_option_index, correct_option_text,
                                 correct_count, total_players}
``quizify_streak_milestone``  — {player_name, streak, bonus}
``quizify_finale_started``    — {}
``quizify_winner_decided``    — {winner_name, score}
``quizify_game_ended``        — {leaderboard: [{name, score}, ...]}
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from .game.state import GamePhase, QuizifyGameState

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

_LOGGER = logging.getLogger(__name__)

# --- Event catalog (stable names — see module docstring for payload schemas) --
EVENT_GAME_STARTED = "quizify_game_started"
EVENT_QUESTION_SHOWN = "quizify_question_shown"
EVENT_TIME_RUNNING_OUT = "quizify_time_running_out"
EVENT_ANSWER_REVEALED = "quizify_answer_revealed"
EVENT_STREAK_MILESTONE = "quizify_streak_milestone"
EVENT_FINALE_STARTED = "quizify_finale_started"
EVENT_WINNER_DECIDED = "quizify_winner_decided"
EVENT_GAME_ENDED = "quizify_game_ended"

# Seconds-remaining threshold for the ``quizify_time_running_out`` event. Fires
# once per round when the round timer first drops to/below this — the final
# stretch that drives the faster "breathing" light pulse. Deliberately tighter
# than the TTS spoken warning (10s, see tts.COUNTDOWN_THRESHOLD_SECONDS) so the
# accent reads as the truly-final urgency, not the same moment as the narration.
TIME_RUNNING_OUT_THRESHOLD_SECONDS = 5.0


class QuizifyEventEmitter:
    """Fires ``hass.bus`` events at game milestones (#366).

    Constructed per config entry; attaches a state callback for the phase-driven
    milestones and exposes ``notify_*`` forwarders the WS handler calls at the
    same points as the ``_notify_tts_*`` hooks. A no-op everywhere when there is
    no HA instance (standalone dev server).
    """

    def __init__(
        self,
        hass: HomeAssistant | None,
        game_state: QuizifyGameState,
        enabled: bool = True,
    ) -> None:
        self._hass = hass
        self._game = game_state
        # Master toggle for the whole event backbone (#366). Off => every fire
        # is a no-op, so the integration stays silent on the bus until the host
        # opts in via the options flow (CONF_HOUSE_EVENTS_ENABLED, default off).
        # The constructor default is True because the sole production caller
        # (__init__) always passes the resolved option explicitly; the product
        # "off by default" lives at the config layer, not here.
        self._enabled = enabled
        # The admin "House Plays Along" panel's runtime master (#494 P4).
        # ``None`` means the panel never touched it, so the config-entry value
        # above still wins — which is what keeps a host toggling
        # CONF_HOUSE_EVENTS_ENABLED in the options UI working after a reload,
        # while a panel-set master survives an unrelated options change (#411).
        self._enabled_override: bool | None = None
        # Track the last phase we observed so phase-driven events fire only on
        # transitions, not on every state_callback flap. Mirrors the lights /
        # TTS observers. Survives an options reload via export/restore below.
        self._last_phase: GamePhase | None = None
        # One-shot guard so the "time running out" event fires at most once per
        # round. Reset at every question start (see ``notify_question_shown``),
        # mirroring the TTS announcer's ``_countdown_announced`` pattern.
        self._time_running_out_fired: bool = False

    @property
    def _master_enabled(self) -> bool:
        """The effective master: panel override if set, else the config entry."""
        if self._enabled_override is None:
            return self._enabled
        return self._enabled_override

    @property
    def is_configured(self) -> bool:
        # Configured == the master toggle is on AND we have a bus to fire on.
        # Unlike lights/TTS there is no per-entry entity to gate on, but the
        # host must still opt in via CONF_HOUSE_EVENTS_ENABLED (default off) —
        # or via the admin panel's runtime master (#494 P4).
        return self._master_enabled and self._hass is not None

    def attach(self) -> None:
        """Subscribe to game-state phase transitions. Idempotent."""
        self._game.register_state_callback(self._on_state_changed)

    def detach(self) -> None:
        self._game.unregister_state_callback(self._on_state_changed)

    # ------------------------------------------------------------------
    # Per-game configuration (#494 Phase 4 — the "House Plays Along" panel)
    # ------------------------------------------------------------------

    def configure(self, enabled: bool) -> None:
        """Override the config-entry master toggle at runtime (#494 P4).

        The admin panel's master switch decides whether the house reacts at all;
        this is how it reaches the event backbone without an options-flow round
        trip.

        There are deliberately NO per-event toggles here. The bus events are the
        integration's public automation API (#366) — a host's own blueprints ride
        them, and those must keep firing even when Quizify's OWN light/SFX
        consumers are switched off. That asymmetry IS the panel's "events only"
        preset: master on, every light/SFX toggle off → the bus stays live, the
        house stays quiet.
        """
        self._enabled_override = bool(enabled)

    # ------------------------------------------------------------------
    # Options-reload lifecycle (#411 pattern)
    # ------------------------------------------------------------------

    def export_runtime_state(self) -> dict[str, Any]:
        """Snapshot the transient phase-tracking state + master toggle (#411).

        An options reload rebuilds this emitter from scratch, which would reset
        ``_last_phase`` to ``None``. On its own that is mostly harmless (the
        game_started/finale detectors are further gated on ``round``), but a
        reload landing exactly on the first active round could otherwise re-fire
        ``quizify_game_started``. Snapshotting the last phase across the reload
        closes that gap, matching how the TTS announcer preserves its runtime
        config (#411).

        ``enabled_override`` rides along for the same reason (#494 P4): the
        rebuild reads the master from the config entry, which would silently
        discard the admin panel's runtime master mid-game. Note it is the
        OVERRIDE that is snapshotted, not the effective master — a ``None``
        override means the panel never set one, so the rebuilt emitter correctly
        picks up the (possibly just changed) CONF_HOUSE_EVENTS_ENABLED value
        rather than being pinned to the old one.
        """
        return {
            "last_phase": self._last_phase.value
            if self._last_phase is not None
            else None,
            "enabled_override": self._enabled_override,
        }

    def restore_runtime_state(self, snapshot: dict[str, Any] | None) -> None:
        """Restore a snapshot from :meth:`export_runtime_state` (#411 pattern).

        Defensive: a falsy/empty snapshot is a no-op, an absent key falls back to
        the current value, and an unknown phase value is ignored so a stale
        snapshot can never crash the rebuild. ``enabled_override`` is tri-state
        (True/False/None) so it is NOT coerced through ``bool()`` — that would
        turn "the panel never set a master" into a hard False.
        """
        if not snapshot:
            return
        value = snapshot.get("last_phase")
        if value:
            try:
                self._last_phase = GamePhase(value)
            except ValueError:
                _LOGGER.debug("Ignoring unknown phase in snapshot: %s", value)
        override = snapshot.get("enabled_override", self._enabled_override)
        self._enabled_override = None if override is None else bool(override)

    # ------------------------------------------------------------------
    # Phase-driven milestones (state-callback path)
    # ------------------------------------------------------------------

    def _on_state_changed(self) -> None:
        """React to phase transitions: game start, finale, winner.

        The richer per-event milestones (question / reveal / streak / game end)
        are pushed from the WS handler instead — this path only sees the phase.
        """
        if not self.is_configured:
            return
        phase = self._game.phase
        if phase == self._last_phase:
            return
        prev = self._last_phase
        self._last_phase = phase

        # LOBBY → first QUESTION_ACTIVE at round 1 means "game starting".
        # Mirrors the TTS "Quizify starting" detection so the two agree on what
        # a game start is.
        if (
            prev in (None, GamePhase.LOBBY)
            and phase == GamePhase.QUESTION_ACTIVE
            and self._game.round == 1
        ):
            self._fire(
                EVENT_GAME_STARTED,
                {
                    "game_id": self._game.game_id,
                    "player_count": len(self._game.get_players()),
                    "round_count": self._game.total_rounds,
                },
            )
            return

        # Any phase → FINALE means the game is over: announce the finale and,
        # when there is a clear leader, who won. Two thin events so a blueprint
        # can react to "the game ended" and "we have a winner" independently.
        if phase == GamePhase.FINALE:
            self._fire(EVENT_FINALE_STARTED, {})
            leader = self._game.leader
            if leader is not None:
                self._fire(
                    EVENT_WINNER_DECIDED,
                    {"winner_name": leader.name, "score": leader.score},
                )

    # ------------------------------------------------------------------
    # Per-event milestones (WS-dispatch path) — thin forwarders the WS
    # handler calls next to its _notify_tts_* hooks.
    # ------------------------------------------------------------------

    def notify_question_shown(
        self, question: Any, round_no: int, total_rounds: int
    ) -> None:
        """Fire ``quizify_question_shown`` at round start.

        Reuses the ``question`` already assembled at the fan-out point; only the
        type (multiple_choice / estimate) is exposed — not the text/answers,
        which would leak the question to automations before players see it.

        Resets the per-round "time running out" one-shot guard unconditionally
        (round start is the lifecycle point the TTS announcer resets its own
        ``_countdown_announced`` guard) so the countdown event can fire once
        more this round.
        """
        self._time_running_out_fired = False
        self._fire(
            EVENT_QUESTION_SHOWN,
            {
                "round": round_no,
                "total_rounds": total_rounds,
                "question_type": getattr(question, "type", None),
            },
        )

    def notify_time_running_out(self, seconds_remaining: float) -> None:
        """Fire ``quizify_time_running_out`` in the final seconds of a round.

        Driven from the timer-tick loop, which calls this every tick with the
        room's minimum remaining time — mirroring the spoken countdown warning
        (:meth:`~custom_components.quizify.tts.QuizifyTTSAnnouncer.announce_countdown`).
        Fires the first time that crosses to/below
        ``TIME_RUNNING_OUT_THRESHOLD_SECONDS`` and then no-ops for the rest of
        the round via the ``_time_running_out_fired`` one-shot guard (reset at
        every question start), so a blueprint sees a single "hurry up" pulse in
        the final stretch rather than a stream of ticks.
        """
        if self._time_running_out_fired:
            return
        if not 0 < seconds_remaining <= TIME_RUNNING_OUT_THRESHOLD_SECONDS:
            return
        self._time_running_out_fired = True
        self._fire(
            EVENT_TIME_RUNNING_OUT,
            {"seconds_remaining": seconds_remaining},
        )

    def notify_answer_revealed(self, game_state: QuizifyGameState) -> None:
        """Fire ``quizify_answer_revealed`` from the reveal dispatch point.

        Pulls everything from the round summary the reveal broadcast already
        built. ``correct_option_index`` is the index of the correct answer in
        the question's canonical ``answers`` list (``None`` for estimate rounds,
        which have no option list).
        """
        summary = game_state.get_round_summary()
        if summary is None:
            return
        correct_text = ""
        if summary.correct_answer is not None:
            correct_text = summary.correct_answer.text or ""
        # Index of the correct option in the canonical answers list. Estimate
        # rounds carry no options → leave it None.
        correct_index: int | None = None
        answers = getattr(summary.question, "answers", None) or []
        for i, ans in enumerate(answers):
            if getattr(ans, "correct", False):
                correct_index = i
                break
        correct_count = sum(1 for r in summary.results if r.correct)
        self._fire(
            EVENT_ANSWER_REVEALED,
            {
                "round": game_state.round,
                "correct_option_index": correct_index,
                "correct_option_text": correct_text,
                "correct_count": correct_count,
                "total_players": len(summary.results),
            },
        )

    def notify_streak_milestone(
        self, player_name: str, streak: int, bonus: int
    ) -> None:
        """Fire ``quizify_streak_milestone`` when a player hits a streak level.

        Mirrors the ``streak_milestone`` WS broadcast payload (#366) so an
        automation sees the same name/streak/bonus the TV celebration does.
        """
        self._fire(
            EVENT_STREAK_MILESTONE,
            {
                "player_name": player_name,
                "streak": streak,
                "bonus": bonus,
            },
        )

    def notify_game_ended(self, game_state: QuizifyGameState) -> None:
        """Fire ``quizify_game_ended`` with the final leaderboard.

        Emitted from the ``game_ended`` dispatch point (richer than the FINALE
        phase transition, which only knows the single leader). The leaderboard
        is trimmed to ``{name, score}`` per entry — the stable, PII-minimal
        subset a blueprint needs — not the full wire contract.
        """
        leaderboard = [
            {"name": entry.get("name"), "score": entry.get("score")}
            for entry in game_state.get_leaderboard()
            if isinstance(entry, dict)
        ]
        self._fire(EVENT_GAME_ENDED, {"leaderboard": leaderboard})

    # ------------------------------------------------------------------
    # Internal — dev-safe bus wrapper
    # ------------------------------------------------------------------

    def _fire(self, event_type: str, data: dict[str, Any]) -> None:
        """Fire ``event_type`` on the HA event bus.

        No-ops when there is no HA instance (standalone dev server has no bus).
        Any exception is caught and logged at WARNING so a bad fire can never
        escape onto the game loop — the same posture as
        :func:`~custom_components.quizify.ha_service.fire_and_forget_service`.
        """
        # Single choke point for every path (phase-callback + notify_* WS
        # forwarders): the master toggle is enforced here, so an off emitter
        # fires nothing regardless of which milestone triggered it. Reads the
        # EFFECTIVE master so the admin panel's runtime override lands (#494 P4).
        if not self._master_enabled:
            return
        hass = self._hass
        if hass is None:
            return
        try:
            hass.bus.async_fire(event_type, data)
        except Exception as err:  # noqa: BLE001
            _LOGGER.warning("Quizify event %s failed: %s", event_type, err)
