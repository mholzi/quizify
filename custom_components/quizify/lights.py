"""Party Lights integration for Quizify.

Subscribes to ``QuizifyGameState`` state callbacks and pushes a colour
preset to a configured set of HA light entities each time the game
phase changes — the room glows coral while a question is live, sage
on reveal, sun on the finale. Off when the game returns to lobby.

On top of that phase-driven *ambient* glow, the lights also react to the
``quizify_*`` HA bus events (the #366 event backbone) with momentary
"accent" effects — Variant B "Game Show" choreography (#494 Phase 2). A
question appearing bumps the coral; a reveal flashes green (crowd got it)
or amber (they missed); a streak double-pulses; the winner triggers a
victory sweep. Every accent is transient and RESTORES to the current phase
baseline afterwards, so the ambient recipe stays the single source of the
resting colour — accents only layer on top.

Colours come straight from DESIGN.md (Soft Parlor palette) so the
lights match the on-screen accent. Brightness/transition are
intentionally subtle — single gentle pulses, no strobing, every accent
well under ~1.5s. This is a cozy family-board-game party, not a club.

The service no-ops cleanly when:
- no light entities are configured
- the runtime has no HA instance (standalone dev server)
- the configured light entity is unavailable
"""

from __future__ import annotations

import asyncio
import contextlib
from typing import TYPE_CHECKING

from .game.state import GamePhase, QuizifyGameState
from .game_events import (
    EVENT_ANSWER_REVEALED,
    EVENT_QUESTION_SHOWN,
    EVENT_STREAK_MILESTONE,
    EVENT_WINNER_DECIDED,
)
from .ha_service import fire_and_forget_service

if TYPE_CHECKING:
    from collections.abc import Callable

    from homeassistant.core import Event, HomeAssistant


# Per-phase light recipe. Colours are RGB triples matching DESIGN.md.
# brightness_pct is 0-100; transition is seconds. None for ``state`` =
# leave the light alone (used in PAUSED so the room doesn't flicker
# when the host steps away).
_PHASE_LIGHT_RECIPES: dict[GamePhase, dict[str, object] | None] = {
    GamePhase.LOBBY: {
        "rgb_color": [232, 196, 127],  # sun #E8C47F — warm anticipation
        "brightness_pct": 40,
        "transition": 1.5,
    },
    GamePhase.QUESTION_ACTIVE: {
        "rgb_color": [232, 138, 127],  # coral #E88A7F — primary accent, "go!"
        "brightness_pct": 70,
        "transition": 0.5,
    },
    GamePhase.ANSWER_REVEAL: {
        "rgb_color": [127, 168, 151],  # sage #7FA897 — calm, "result"
        "brightness_pct": 60,
        "transition": 1.0,
    },
    GamePhase.FINALE: {
        "rgb_color": [232, 196, 127],  # sun — celebration
        "brightness_pct": 90,
        "transition": 2.0,
    },
    # PAUSED: leave the room as-is. Restoring on resume would surprise.
    GamePhase.PAUSED: None,
}


# --- Accent choreography (Variant B "Game Show", #494 Phase 2) ----------------
# Momentary effects layered on top of the ambient phase glow. Each accent is a
# short list of pulse steps: (command, hold_seconds). ``command`` is either a
# ``light.turn_on`` payload (rgb/brightness/transition, entity_id is added at
# call time) or the ``_BASELINE`` sentinel, which re-applies the current phase
# recipe so the room settles back to its resting colour. ``hold_seconds`` is how
# long to wait before the next step (asyncio.sleep). Total accent stays < ~1.5s;
# single gentle pulses only — no strobing (physical bulbs, no reduced-motion).
_BASELINE = "baseline"

# question_shown: brief brightness bump on the *current* coral (no colour set →
# keeps whatever the QUESTION_ACTIVE recipe is showing), then settle back.
_ACCENT_QUESTION_SHOWN: list[tuple[object, float]] = [
    ({"brightness_pct": 88, "transition": 0.2}, 0.5),
    (_BASELINE, 0.0),
]

# answer_revealed reactions — one pulse, then restore. Green when the crowd
# mostly got it, amber when they mostly missed.
_ACCENT_REVEAL_GREEN: list[tuple[object, float]] = [
    ({"rgb_color": [120, 200, 140], "brightness_pct": 85, "transition": 0.3}, 0.5),
    (_BASELINE, 0.0),
]
_ACCENT_REVEAL_AMBER: list[tuple[object, float]] = [
    ({"rgb_color": [235, 175, 95], "brightness_pct": 85, "transition": 0.3}, 0.5),
    (_BASELINE, 0.0),
]

# streak_milestone: double-pulse bright coral — up, baseline, up, settle.
_STREAK_UP: dict[str, object] = {
    "rgb_color": [255, 150, 120],
    "brightness_pct": 92,
    "transition": 0.15,
}
_ACCENT_STREAK: list[tuple[object, float]] = [
    (_STREAK_UP, 0.2),
    (_BASELINE, 0.25),
    (_STREAK_UP, 0.2),
    (_BASELINE, 0.0),
]

# winner_decided: victory sweep in sun — 3-beat up/down/up/down/up, then settle
# to a steady 90% sun. Settles to an explicit sun 90% rather than the phase
# baseline because it fires alongside the FINALE transition, whose recipe is
# also sun 90% — settling explicitly keeps the two in agreement.
_WINNER_UP: dict[str, object] = {
    "rgb_color": [232, 196, 127],
    "brightness_pct": 100,
    "transition": 0.15,
}
_WINNER_DOWN: dict[str, object] = {
    "rgb_color": [232, 196, 127],
    "brightness_pct": 55,
    "transition": 0.15,
}
_WINNER_SETTLE: dict[str, object] = {
    "rgb_color": [232, 196, 127],
    "brightness_pct": 90,
    "transition": 0.3,
}
_ACCENT_WINNER: list[tuple[object, float]] = [
    (_WINNER_UP, 0.2),
    (_WINNER_DOWN, 0.2),
    (_WINNER_UP, 0.2),
    (_WINNER_DOWN, 0.2),
    (_WINNER_UP, 0.2),
    (_WINNER_SETTLE, 0.0),
]

# NOTE: there is deliberately NO finale_started accent. The FINALE phase recipe
# already drives the room to sun 90% at the same instant quizify_finale_started
# fires; a competing accent build would race the ambient transition. The winner
# sweep (above) is the finale's celebration instead.


class QuizifyPartyLights:
    """Pushes phase-based light presets to HA light entities."""

    def __init__(
        self,
        hass: HomeAssistant | None,
        entity_ids: list[str],
        game_state: QuizifyGameState,
    ) -> None:
        self._hass = hass
        # Normalize: strip whitespace, drop empties, dedupe while keeping order.
        seen: set[str] = set()
        cleaned: list[str] = []
        for e in entity_ids or []:
            e = (e or "").strip()
            if e and e not in seen:
                seen.add(e)
                cleaned.append(e)
        self._entity_ids = cleaned
        self._game = game_state
        self._last_phase: GamePhase | None = None
        # Accent choreography (#494). Unsub handles for the four bus listeners
        # and the single in-flight multi-step pulse task (cancel-safe: only one
        # accent runs at a time — a new accent cancels the old).
        self._event_unsubs: list[Callable[[], None]] = []
        self._pulse_task: asyncio.Task | None = None

    @property
    def is_configured(self) -> bool:
        return self._hass is not None and bool(self._entity_ids)

    def attach(self) -> None:
        self._game.register_state_callback(self._on_state_changed)

    def attach_events(self) -> None:
        """Subscribe to the ``quizify_*`` bus events for accent choreography.

        Registers the four accent listeners (#494 Phase 2). Idempotent, and a
        no-op unless configured (needs a hass bus + at least one entity). The
        emitter (#366) only fires these when the host has opted into house
        events, so an off toggle means the listeners simply never trigger.
        """
        if not self.is_configured or self._event_unsubs:
            return
        hass = self._hass
        if hass is None:  # narrow for type-checkers; is_configured already guards
            return
        self._event_unsubs = [
            hass.bus.async_listen(EVENT_QUESTION_SHOWN, self._on_question_shown),
            hass.bus.async_listen(EVENT_ANSWER_REVEALED, self._on_answer_revealed),
            hass.bus.async_listen(EVENT_STREAK_MILESTONE, self._on_streak_milestone),
            hass.bus.async_listen(EVENT_WINNER_DECIDED, self._on_winner_decided),
        ]

    def detach(self) -> None:
        self._game.unregister_state_callback(self._on_state_changed)
        # Tear down accent choreography: drop the bus listeners and cancel any
        # in-flight pulse so the reload path (which already calls detach()) or an
        # unload leaves nothing dangling.
        for unsub in self._event_unsubs:
            # Never let an unsub failure escape the reload/unload path.
            with contextlib.suppress(Exception):
                unsub()
        self._event_unsubs = []
        self._cancel_pulse()

    def _on_state_changed(self) -> None:
        if not self.is_configured:
            return
        phase = self._game.phase
        if phase == self._last_phase:
            return
        self._last_phase = phase

        recipe = _PHASE_LIGHT_RECIPES.get(phase)
        if recipe is None:
            return

        hass = self._hass
        if hass is None:
            return

        # Game went LOBBY → LOBBY-via-reset? Turn the lights off so the
        # party glow doesn't linger after a session ends. Done by special-
        # casing LOBBY when the round just rolled back to 0 (i.e. came
        # from FINALE/reset, not a fresh boot).
        if (
            phase == GamePhase.LOBBY
            and self._game.round == 0
            and self._game.game_id is None
        ):
            self._call("light", "turn_off", {
                "entity_id": self._entity_ids,
                "transition": 1.5,
            })
            return

        data: dict[str, object] = {"entity_id": self._entity_ids, **recipe}
        self._call("light", "turn_on", data)

    def _call(self, domain: str, service: str, data: dict[str, object]) -> None:
        fire_and_forget_service(
            self._hass,
            domain,
            service,
            data,
            f"Party lights {domain}.{service} (entities={self._entity_ids})",
        )

    # ------------------------------------------------------------------
    # Accent choreography (#494 Phase 2) — bus-event handlers
    # ------------------------------------------------------------------

    def _on_question_shown(self, event: Event) -> None:  # noqa: ARG002
        """Brightness bump on the live coral when a question appears."""
        self._start_pulse(_ACCENT_QUESTION_SHOWN)

    def _on_answer_revealed(self, event: Event) -> None:
        """Green when the crowd mostly nailed it, amber when they mostly missed.

        Guards ``total_players == 0`` (no div-by-zero) → treated as the minority
        case (amber). A strict majority (> half) flips it green.
        """
        data = event.data or {}
        correct = data.get("correct_count") or 0
        total = data.get("total_players") or 0
        majority = total > 0 and correct > total / 2
        self._start_pulse(_ACCENT_REVEAL_GREEN if majority else _ACCENT_REVEAL_AMBER)

    def _on_streak_milestone(self, event: Event) -> None:  # noqa: ARG002
        """Double-pulse bright coral to celebrate a hot streak."""
        self._start_pulse(_ACCENT_STREAK)

    def _on_winner_decided(self, event: Event) -> None:  # noqa: ARG002
        """Victory sweep in sun, settling to a steady celebratory glow."""
        self._start_pulse(_ACCENT_WINNER)

    # ------------------------------------------------------------------
    # Accent choreography — pulse scheduling
    # ------------------------------------------------------------------

    def _apply_phase_baseline(self) -> None:
        """Re-apply the current phase's ambient recipe (no-op if None).

        The restore target for every accent: after a momentary effect the room
        settles back onto the phase-driven glow, so the ambient recipe stays the
        single owner of the resting colour.
        """
        recipe = _PHASE_LIGHT_RECIPES.get(self._last_phase)
        if recipe is None:
            return
        self._call("light", "turn_on", {"entity_id": self._entity_ids, **recipe})

    def _start_pulse(self, steps: list[tuple[object, float]]) -> None:
        """Kick off a multi-step accent as a cancel-safe background task.

        Cancels any in-flight accent first (no stacking), then schedules the new
        one. No-op unless configured. Gated so an off/unconfigured integration
        never touches the lights.
        """
        if not self.is_configured:
            return
        hass = self._hass
        if hass is None:  # narrow for type-checkers; is_configured already guards
            return
        self._cancel_pulse()
        self._pulse_task = hass.async_create_task(self._run_pulse(steps))

    def _cancel_pulse(self) -> None:
        """Cancel any pending accent pulse (re-entrant, safe if none running)."""
        if self._pulse_task is not None and not self._pulse_task.done():
            self._pulse_task.cancel()
        self._pulse_task = None

    async def _run_pulse(self, steps: list[tuple[object, float]]) -> None:
        """Walk the accent steps, sleeping between each.

        Each step is ``(command, hold_seconds)``; ``command`` is either a
        ``light.turn_on`` payload or ``_BASELINE`` (restore the phase recipe).
        A cancel (a newer accent, or detach) unwinds cleanly — the
        ``CancelledError`` is swallowed so it never leaks onto the loop.
        """
        try:
            for command, hold in steps:
                if command == _BASELINE:
                    self._apply_phase_baseline()
                elif isinstance(command, dict):
                    self._call(
                        "light",
                        "turn_on",
                        {"entity_id": self._entity_ids, **command},
                    )
                if hold:
                    await asyncio.sleep(hold)
        except asyncio.CancelledError:
            # Normal path: a newer accent or detach cancelled us. Stay quiet.
            pass
