"""Party Lights integration for Quizify.

Subscribes to ``QuizifyGameState`` state callbacks and pushes a colour
preset to a configured set of HA light entities each time the game
phase changes — the room glows coral while a question is live, sage
on reveal, sun on the finale. Off when the game returns to lobby.

Colours come straight from DESIGN.md (Soft Parlor palette) so the
lights match the on-screen accent. Brightness/transition are
intentionally subtle — no strobing. This is a cozy family-board-game
party, not a club.

The service no-ops cleanly when:
- no light entities are configured
- the runtime has no HA instance (standalone dev server)
- the configured light entity is unavailable
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from .game.state import GamePhase, QuizifyGameState
from .ha_service import fire_and_forget_service

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant


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

    @property
    def is_configured(self) -> bool:
        return self._hass is not None and bool(self._entity_ids)

    def attach(self) -> None:
        self._game.register_state_callback(self._on_state_changed)

    def detach(self) -> None:
        self._game.unregister_state_callback(self._on_state_changed)

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
