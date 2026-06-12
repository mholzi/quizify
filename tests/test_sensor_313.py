"""Tests for the Quizify sensor platform (issue #313).

Mirrors ``test_binary_sensor_271.py`` for the five ``sensor.quizify_*``
entities that were left untested when binary_sensor got #271. Covers each
sensor's ``native_value`` + ``extra_state_attributes``, the
QUESTION_ACTIVE answer-leak guard on the question sensor, the push-based
state-callback wiring, and ``async_setup_entry`` adding all five entities.

Needs the real ``homeassistant`` package (``SensorEntity`` base) plus the
``pytest-homeassistant-custom-component`` harness for the ``hass`` fixture.
Skipped (not errored) when those aren't installed locally; CI installs them.
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

pytest.importorskip("homeassistant")
pytest.importorskip("pytest_homeassistant_custom_component")

from homeassistant.core import HomeAssistant  # noqa: E402
from pytest_homeassistant_custom_component.common import (  # noqa: E402
    MockConfigEntry,
)

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from custom_components.quizify.const import DOMAIN  # noqa: E402
from custom_components.quizify.game.player import PlayerSession  # noqa: E402
from custom_components.quizify.game.questions import (  # noqa: E402
    Answer,
    Question,
)
from custom_components.quizify.game.state import (  # noqa: E402
    GamePhase,
    QuizifyGameState,
)
from custom_components.quizify.sensor import (  # noqa: E402
    QuizifyCurrentQuestionSensor,
    QuizifyCurrentRoundSensor,
    QuizifyLeaderSensor,
    QuizifyPlayerCountSensor,
    QuizifyTopScoreSensor,
    async_setup_entry,
)

pytestmark = pytest.mark.usefixtures("enable_custom_integrations")


def _make_state(*, phase: GamePhase = GamePhase.LOBBY) -> QuizifyGameState:
    """A real GameState pinned to a given phase, no players."""
    state = QuizifyGameState()
    # ``phase`` is a property delegating to the phase controller; set through it.
    state.phase = phase
    return state


def _add_player(
    state: QuizifyGameState,
    name: str,
    *,
    score: int = 0,
    streak: int = 0,
    connected: bool = True,
) -> PlayerSession:
    """Insert a PlayerSession directly into the registry (no real WebSocket).

    ``is_active`` reads ``ws.closed``; a MagicMock with ``closed`` set lets us
    control the connected/active distinction without a live socket.
    """
    ws = MagicMock()
    ws.closed = not connected
    player = PlayerSession(name=name, ws=ws)
    player.score = score
    player.streak = streak
    player.connected = connected
    state._player_registry.players[name] = player
    return player


# ---------------------------------------------------------------------------
# Current round sensor
# ---------------------------------------------------------------------------


def test_current_round_value_and_attrs() -> None:
    state = _make_state(phase=GamePhase.QUESTION_ACTIVE)
    state.round = 4
    state.total_rounds = 9
    state.category = "geographie"
    state.difficulty = "medium"
    sensor = QuizifyCurrentRoundSensor(state, "entry-1")

    assert sensor.native_value == 4
    attrs = sensor.extra_state_attributes
    assert attrs["total_rounds"] == 9
    assert attrs["phase"] == GamePhase.QUESTION_ACTIVE.value
    assert attrs["category"] == "geographie"
    assert attrs["difficulty"] == "medium"


def test_current_round_default_zero() -> None:
    """No game running → round 0."""
    sensor = QuizifyCurrentRoundSensor(_make_state(), "e")
    assert sensor.native_value == 0


# ---------------------------------------------------------------------------
# Leader + top-score sensors
# ---------------------------------------------------------------------------


def test_leader_none_when_no_players() -> None:
    state = _make_state()
    leader = QuizifyLeaderSensor(state, "e")
    top = QuizifyTopScoreSensor(state, "e")
    assert leader.native_value is None
    assert leader.extra_state_attributes == {"score": 0, "streak": 0}
    assert top.native_value == 0


def test_leader_is_highest_scorer() -> None:
    state = _make_state()
    _add_player(state, "Alice", score=30, streak=2)
    _add_player(state, "Bob", score=70, streak=5)  # leader
    leader = QuizifyLeaderSensor(state, "e")
    top = QuizifyTopScoreSensor(state, "e")

    assert leader.native_value == "Bob"
    attrs = leader.extra_state_attributes
    assert attrs["score"] == 70
    assert attrs["streak"] == 5
    assert top.native_value == 70


# ---------------------------------------------------------------------------
# Player count sensor
# ---------------------------------------------------------------------------


def test_player_count_value_and_connected_split() -> None:
    state = _make_state()
    _add_player(state, "Alice", connected=True)
    _add_player(state, "Bob", connected=True)
    _add_player(state, "Ghost", connected=False)  # disconnected within grace
    sensor = QuizifyPlayerCountSensor(state, "e")

    assert sensor.native_value == 3  # all players, incl. disconnected
    attrs = sensor.extra_state_attributes
    assert set(attrs["players"]) == {"Alice", "Bob", "Ghost"}
    # ``connected`` lists only genuinely active sockets (is_active).
    assert set(attrs["connected"]) == {"Alice", "Bob"}


# ---------------------------------------------------------------------------
# Current question sensor (answer-leak guard)
# ---------------------------------------------------------------------------


def _question() -> Question:
    return Question(
        id="q1",
        question="Capital of France?",
        answers=[
            Answer(text="Paris", correct=True),
            Answer(text="Lyon", correct=False),
            Answer(text="Nice", correct=False),
        ],
    )


def test_question_sensor_hidden_during_active() -> None:
    """During QUESTION_ACTIVE the sensor returns None and nulls the correct
    answer so a live question's solution can't leak onto a dashboard."""
    state = _make_state(phase=GamePhase.QUESTION_ACTIVE)
    state._current_question = _question()
    sensor = QuizifyCurrentQuestionSensor(state, "e")

    assert sensor.native_value is None
    assert sensor.extra_state_attributes == {"correct_answer": None}


def test_question_sensor_reveals_and_caches() -> None:
    """On ANSWER_REVEAL the sensor surfaces the text + correct answer and
    caches them so they persist into the following idle phase."""
    state = _make_state(phase=GamePhase.ANSWER_REVEAL)
    state._current_question = _question()
    sensor = QuizifyCurrentQuestionSensor(state, "e")

    assert sensor.native_value == "Capital of France?"
    assert sensor.extra_state_attributes == {"correct_answer": "Paris"}

    # Move to a non-active idle phase: the cached text/answer persists.
    state.phase = GamePhase.LOBBY
    state._current_question = None
    assert sensor.native_value == "Capital of France?"
    assert sensor.extra_state_attributes == {"correct_answer": "Paris"}

    # A new live question hides everything again.
    state.phase = GamePhase.QUESTION_ACTIVE
    assert sensor.native_value is None
    assert sensor.extra_state_attributes == {"correct_answer": None}


def test_question_sensor_starts_empty() -> None:
    state = _make_state(phase=GamePhase.LOBBY)
    sensor = QuizifyCurrentQuestionSensor(state, "e")
    assert sensor.native_value is None
    assert sensor.extra_state_attributes == {"correct_answer": None}


# ---------------------------------------------------------------------------
# Shared base: unique_id, static attrs, push callback wiring
# ---------------------------------------------------------------------------


def test_unique_ids_derive_from_entry_and_key() -> None:
    state = _make_state()
    assert (
        QuizifyCurrentRoundSensor(state, "entry-x").unique_id
        == "entry-x_current_round"
    )
    assert QuizifyLeaderSensor(state, "entry-x").unique_id == "entry-x_leader"
    assert (
        QuizifyTopScoreSensor(state, "entry-x").unique_id == "entry-x_top_score"
    )
    assert (
        QuizifyPlayerCountSensor(state, "entry-x").unique_id
        == "entry-x_player_count"
    )
    assert (
        QuizifyCurrentQuestionSensor(state, "entry-x").unique_id
        == "entry-x_current_question"
    )


def test_static_attrs() -> None:
    sensor = QuizifyCurrentRoundSensor(_make_state(), "e")
    assert sensor._attr_has_entity_name is True
    assert sensor._attr_should_poll is False


async def test_state_callback_writes_ha_state() -> None:
    """Each sensor registers a callback that fires async_write_ha_state and
    unregisters cleanly on removal."""
    state = _make_state()
    sensor = QuizifyLeaderSensor(state, "e")
    sensor.async_write_ha_state = MagicMock()

    await sensor.async_added_to_hass()
    state._notify_state_callbacks()
    assert sensor.async_write_ha_state.called

    sensor.async_write_ha_state.reset_mock()
    await sensor.async_will_remove_from_hass()
    state._notify_state_callbacks()
    assert not sensor.async_write_ha_state.called


# ---------------------------------------------------------------------------
# async_setup_entry
# ---------------------------------------------------------------------------


async def test_async_setup_entry_adds_all_five(hass: HomeAssistant) -> None:
    """``async_setup_entry`` reads the game state from hass.data and adds the
    five Quizify sensor entities with entry-scoped unique ids."""
    game_state = _make_state()
    hass.data[DOMAIN] = {"game": game_state}

    entry = MockConfigEntry(domain=DOMAIN, unique_id=DOMAIN)
    entry.add_to_hass(hass)

    added: list = []

    def _add(entities, update_before_add: bool = False) -> None:
        added.extend(entities)

    await async_setup_entry(hass, entry, _add)

    assert len(added) == 5
    types = {type(e) for e in added}
    assert types == {
        QuizifyCurrentRoundSensor,
        QuizifyLeaderSensor,
        QuizifyTopScoreSensor,
        QuizifyPlayerCountSensor,
        QuizifyCurrentQuestionSensor,
    }
    for entity in added:
        assert entity.unique_id.startswith(entry.entry_id)
