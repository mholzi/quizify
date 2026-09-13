"""Binary sensor platform for Quizify.

Exposes a single ``binary_sensor.quizify_game_active`` that is on whenever
a quiz is in progress. Useful for "turn down the lights when a game starts"
automations.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from homeassistant.components.binary_sensor import BinarySensorEntity
from homeassistant.core import callback

from .const import DOMAIN
from .game.state import GamePhase, QuizifyGameState

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.entity_platform import AddEntitiesCallback

_LOGGER = logging.getLogger(__name__)

# Phases in which no game is in progress (#938). The sensor is derived from
# this set rather than from a list of "active" phases: the allow-list had to be
# extended by hand for every new phase and silently missed WAGER_ACTIVE and the
# whole Hot Seat detour, turning the sensor off mid-game. A new phase is now
# "active" by default, and tests/test_binary_sensor_271.py fails until it is
# classified explicitly.
_IDLE_PHASES: frozenset[GamePhase] = frozenset({GamePhase.LOBBY, GamePhase.FINALE})


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Quizify binary sensor entities from a config entry."""
    game_state: QuizifyGameState = hass.data[DOMAIN]["game"]
    async_add_entities([QuizifyGameActiveSensor(game_state, entry.entry_id)])


class QuizifyGameActiveSensor(BinarySensorEntity):
    """On while a Quizify game is mid-flight: every phase except the idle
    LOBBY and FINALE, so the wager window and the Lightning Round / Hot Seat
    detours count as part of the game. Off whenever no game is running."""

    _attr_should_poll = False
    _attr_has_entity_name = True
    _attr_name = "Quizify game active"
    _attr_icon = "mdi:gamepad-variant"

    def __init__(self, game_state: QuizifyGameState, entry_id: str) -> None:
        self._game_state = game_state
        self._attr_unique_id = f"{entry_id}_game_active"

    async def async_added_to_hass(self) -> None:
        self._game_state.register_state_callback(self._on_state_changed)

    async def async_will_remove_from_hass(self) -> None:
        self._game_state.unregister_state_callback(self._on_state_changed)

    @callback
    def _on_state_changed(self) -> None:
        self.async_write_ha_state()

    @property
    def is_on(self) -> bool:
        return (
            self._game_state.game_id is not None
            and self._game_state.phase not in _IDLE_PHASES
        )

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        leader = self._game_state.leader
        return {
            "phase": self._game_state.phase.value,
            "round": self._game_state.round,
            "total_rounds": self._game_state.total_rounds,
            "leader": leader.name if leader else None,
        }
