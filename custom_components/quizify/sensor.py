"""Sensor platform for Quizify.

Exposes game state as Home Assistant sensor entities so users can build
automations and dashboards on top of an in-progress quiz:

- ``sensor.quizify_current_round`` — current round (with phase + total in attrs)
- ``sensor.quizify_leader`` — leader player name (with score in attrs)
- ``sensor.quizify_top_score`` — leader's score
- ``sensor.quizify_player_count`` — players in lobby/game
- ``sensor.quizify_current_question`` — question text during REVEAL/lobby

Entities subscribe to ``QuizifyGameState.register_state_callback`` so updates
are push-based (no polling).
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from homeassistant.components.sensor import SensorEntity
from homeassistant.core import callback

from .const import DOMAIN
from .game.state import GamePhase, QuizifyGameState

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.entity_platform import AddEntitiesCallback

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Quizify sensor entities from a config entry."""
    game_state: QuizifyGameState = hass.data[DOMAIN]["game"]

    async_add_entities([
        QuizifyCurrentRoundSensor(game_state, entry.entry_id),
        QuizifyLeaderSensor(game_state, entry.entry_id),
        QuizifyTopScoreSensor(game_state, entry.entry_id),
        QuizifyPlayerCountSensor(game_state, entry.entry_id),
        QuizifyCurrentQuestionSensor(game_state, entry.entry_id),
    ])


class QuizifySensorBase(SensorEntity):
    """Base class for Quizify sensors with push-based updates."""

    _attr_should_poll = False
    _attr_has_entity_name = True
    _sensor_key: str = ""

    def __init__(self, game_state: QuizifyGameState, entry_id: str) -> None:
        self._game_state = game_state
        self._attr_unique_id = f"{entry_id}_{self._sensor_key}"

    async def async_added_to_hass(self) -> None:
        self._game_state.register_state_callback(self._on_state_changed)

    async def async_will_remove_from_hass(self) -> None:
        self._game_state.unregister_state_callback(self._on_state_changed)

    @callback
    def _on_state_changed(self) -> None:
        self.async_write_ha_state()


class QuizifyCurrentRoundSensor(QuizifySensorBase):
    """Current round number, 0 when no game is active."""

    _attr_name = "Quizify current round"
    _attr_icon = "mdi:numeric"
    _sensor_key = "current_round"

    @property
    def native_value(self) -> int:
        return self._game_state.round

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {
            "total_rounds": self._game_state.total_rounds,
            "phase": self._game_state.phase.value,
            "category": self._game_state.category,
            "difficulty": self._game_state.difficulty,
        }


class QuizifyLeaderSensor(QuizifySensorBase):
    """Name of the current leader, or None if no players."""

    _attr_name = "Quizify leader"
    _attr_icon = "mdi:trophy"
    _sensor_key = "leader"

    @property
    def native_value(self) -> str | None:
        leader = self._game_state.leader
        return leader.name if leader else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        leader = self._game_state.leader
        if leader is None:
            return {"score": 0, "streak": 0}
        return {
            "score": leader.score,
            "streak": leader.streak,
        }


class QuizifyTopScoreSensor(QuizifySensorBase):
    """Top score across all players."""

    _attr_name = "Quizify top score"
    _attr_icon = "mdi:star"
    _sensor_key = "top_score"

    @property
    def native_value(self) -> int:
        leader = self._game_state.leader
        return leader.score if leader else 0


class QuizifyPlayerCountSensor(QuizifySensorBase):
    """Number of players currently in the game (incl. disconnected within grace)."""

    _attr_name = "Quizify player count"
    _attr_icon = "mdi:account-group"
    _sensor_key = "player_count"

    @property
    def native_value(self) -> int:
        return len(self._game_state.players)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {
            "players": [p.name for p in self._game_state.players.values()],
            "connected": [
                p.name for p in self._game_state.players.values() if p.is_active
            ],
        }


class QuizifyCurrentQuestionSensor(QuizifySensorBase):
    """Current question text. Hidden during QUESTION_ACTIVE so the answer
    isn't accidentally surfaced on a dashboard. Holds the last asked
    question text between rounds for visibility."""

    _attr_name = "Quizify current question"
    _attr_icon = "mdi:help-circle-outline"
    _sensor_key = "current_question"

    def __init__(self, game_state: QuizifyGameState, entry_id: str) -> None:
        super().__init__(game_state, entry_id)
        self._last_text: str | None = None
        self._last_correct: str | None = None

    @property
    def native_value(self) -> str | None:
        phase = self._game_state.phase
        q = self._game_state.get_current_question()

        if q and phase in (GamePhase.ANSWER_REVEAL,):
            self._last_text = q.question
            correct = next((a.text for a in q.answers if a.correct), None)
            self._last_correct = correct

        if phase == GamePhase.QUESTION_ACTIVE:
            # Hide during live question — answer would leak via attrs cache.
            return None

        return self._last_text

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        if self._game_state.phase == GamePhase.QUESTION_ACTIVE:
            return {"correct_answer": None}
        return {"correct_answer": self._last_correct}
