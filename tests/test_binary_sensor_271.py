"""Tests for the Quizify binary sensor platform (issue #271).

Covers ``custom_components.quizify.binary_sensor`` —
``binary_sensor.quizify_game_active``: its ``is_on`` phase mapping, the extra
state attributes, ``async_setup_entry`` wiring, and that registering as a
state callback triggers ``async_write_ha_state``.

Needs the real ``homeassistant`` package (``BinarySensorEntity`` base) plus the
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

from custom_components.quizify.binary_sensor import (  # noqa: E402
    QuizifyGameActiveSensor,
    async_setup_entry,
)
from custom_components.quizify.const import DOMAIN  # noqa: E402
from custom_components.quizify.game.state import (  # noqa: E402
    GamePhase,
    QuizifyGameState,
)

pytestmark = pytest.mark.usefixtures("enable_custom_integrations")


def _make_state(
    *, game_id: str | None, phase: GamePhase
) -> QuizifyGameState:
    """A real GameState pinned to a given game_id + phase."""
    state = QuizifyGameState()
    state.game_id = game_id
    # ``phase`` is a property delegating to the phase controller; set through it.
    state.phase = phase
    return state


@pytest.mark.parametrize(
    ("game_id", "phase", "expected"),
    [
        # Active phases -> on (only when a game is actually running).
        ("g1", GamePhase.QUESTION_ACTIVE, True),
        ("g1", GamePhase.ANSWER_REVEAL, True),
        ("g1", GamePhase.PAUSED, True),
        # Idle / boundary phases -> off.
        ("g1", GamePhase.LOBBY, False),
        ("g1", GamePhase.FINALE, False),
        # No game_id -> off even if the phase looks active.
        (None, GamePhase.QUESTION_ACTIVE, False),
        (None, GamePhase.LOBBY, False),
    ],
)
def test_is_on_phase_mapping(
    game_id: str | None, phase: GamePhase, expected: bool
) -> None:
    """``is_on`` is true only for active phases *and* a live game_id."""
    state = _make_state(game_id=game_id, phase=phase)
    sensor = QuizifyGameActiveSensor(state, "entry-123")
    assert sensor.is_on is expected


def test_unique_id_and_static_attrs() -> None:
    """unique_id derives from the entry id; name/icon are stable."""
    state = _make_state(game_id=None, phase=GamePhase.LOBBY)
    sensor = QuizifyGameActiveSensor(state, "entry-xyz")
    assert sensor.unique_id == "entry-xyz_game_active"
    assert sensor._attr_has_entity_name is True
    assert sensor._attr_should_poll is False


def test_extra_state_attributes() -> None:
    """Attributes surface phase/round/total_rounds and a null leader."""
    state = _make_state(game_id="g7", phase=GamePhase.QUESTION_ACTIVE)
    state.round = 3
    state.total_rounds = 8
    sensor = QuizifyGameActiveSensor(state, "entry-1")

    attrs = sensor.extra_state_attributes
    assert attrs["phase"] == GamePhase.QUESTION_ACTIVE.value
    assert attrs["round"] == 3
    assert attrs["total_rounds"] == 8
    assert attrs["leader"] is None  # no players registered


async def test_state_callback_writes_ha_state() -> None:
    """The sensor registers a state callback that calls async_write_ha_state."""
    state = _make_state(game_id="g1", phase=GamePhase.LOBBY)
    sensor = QuizifyGameActiveSensor(state, "entry-1")
    sensor.async_write_ha_state = MagicMock()

    await sensor.async_added_to_hass()
    # Driving the game state's callbacks should propagate to the sensor.
    state._notify_state_callbacks()  # type: ignore[attr-defined]
    assert sensor.async_write_ha_state.called

    sensor.async_write_ha_state.reset_mock()
    await sensor.async_will_remove_from_hass()
    state._notify_state_callbacks()  # type: ignore[attr-defined]
    assert not sensor.async_write_ha_state.called


async def test_async_setup_entry_adds_one_entity(hass: HomeAssistant) -> None:
    """``async_setup_entry`` reads the game state from hass.data and adds one
    QuizifyGameActiveSensor."""
    game_state = _make_state(game_id=None, phase=GamePhase.LOBBY)
    hass.data[DOMAIN] = {"game": game_state}

    entry = MockConfigEntry(domain=DOMAIN, unique_id=DOMAIN)
    entry.add_to_hass(hass)

    added: list = []

    def _add(entities, update_before_add: bool = False) -> None:
        added.extend(entities)

    await async_setup_entry(hass, entry, _add)

    assert len(added) == 1
    sensor = added[0]
    assert isinstance(sensor, QuizifyGameActiveSensor)
    assert sensor.unique_id == f"{entry.entry_id}_game_active"
