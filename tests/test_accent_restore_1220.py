"""Accents restore the room when "lights follow the game" is off (#1220).

With the house master on and "lights follow" off there is no phase baseline, so
the settle step used to be a no-op and the bulbs stayed green/amber. Now the
lights are snapshotted before the first accent step and put back afterwards.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from custom_components.quizify.game.state import GamePhase  # noqa: E402
from custom_components.quizify.house_settings import HouseSettings  # noqa: E402
from custom_components.quizify.lights import QuizifyPartyLights  # noqa: E402
from tests.signal_fakes import FakeHass  # noqa: E402


class _Game:
    phase = GamePhase.QUESTION_ACTIVE
    round = 1
    game_id = "g"

    def register_state_callback(self, _cb):
        pass

    def unregister_state_callback(self, _cb):
        pass


def _lights(hass, **kw):
    settings = HouseSettings(
        light_entities=["light.a", "light.b"],
        max_brightness_pct=kw.pop("max_pct", None),
    )
    lights = QuizifyPartyLights(hass, _Game(), settings=settings)
    lights.configure(enabled=True, lights_follow=kw.pop("follow", False))
    return lights


def _reveal(correct=3, total=4):
    return SimpleNamespace(data={"correct_count": correct, "total_players": total})


async def _run(lights):
    await asyncio.wait_for(lights._pulse_task, 5)


@pytest.mark.asyncio
async def test_reveal_accent_restores_each_light_when_not_following() -> None:
    hass = FakeHass()
    hass.states.set("light.a", "on", brightness=128, color_mode="rgb",
                    rgb_color=[10, 20, 30])
    hass.states.set("light.b", "off")
    lights = _lights(hass)
    lights._on_answer_revealed(_reveal())
    await _run(lights)
    await hass.settle()
    calls = hass.signals
    last_a = [s for s in calls if s.data.get("entity_id") == ["light.a"]]
    last_b = [s for s in calls if s.data.get("entity_id") == ["light.b"]]
    assert last_a[-1].name == "light.turn_on"
    assert last_a[-1].data["rgb_color"] == [10, 20, 30]
    assert last_a[-1].data["brightness_pct"] == 50
    assert last_b[-1].name == "light.turn_off"


@pytest.mark.asyncio
async def test_restore_respects_the_brightness_ceiling() -> None:
    hass = FakeHass()
    hass.states.set("light.a", "on", brightness=255, color_mode="color_temp",
                    color_temp_kelvin=2700)
    lights = _lights(hass, max_pct=60)
    lights._on_answer_revealed(_reveal())
    await _run(lights)
    await hass.settle()
    restore = [s for s in hass.signals if s.data.get("entity_id") == ["light.a"]][-1]
    assert restore.data["brightness_pct"] == 60
    assert restore.data["color_temp_kelvin"] == 2700


@pytest.mark.asyncio
async def test_cancelled_accent_keeps_the_original_snapshot() -> None:
    hass = FakeHass()
    hass.states.set("light.a", "on", brightness=128, rgb_color=[1, 2, 3],
                    color_mode="rgb")
    lights = _lights(hass)
    lights._on_answer_revealed(_reveal())
    await asyncio.sleep(0.05)
    # the bulb now shows the flash; a second accent cancels the first mid-way
    hass.states.set("light.a", "on", brightness=255, rgb_color=[0, 255, 0],
                    color_mode="rgb")
    lights._on_answer_revealed(_reveal(0, 4))
    await _run(lights)
    await hass.settle()
    restore = [s for s in hass.signals if s.data.get("entity_id") == ["light.a"]][-1]
    assert restore.data["rgb_color"] == [1, 2, 3]


@pytest.mark.asyncio
async def test_phase_baseline_unchanged_when_following() -> None:
    hass = FakeHass()
    hass.states.set("light.a", "on", brightness=128, rgb_color=[1, 2, 3],
                    color_mode="rgb")
    lights = _lights(hass, follow=True)
    lights._last_phase = GamePhase.QUESTION_ACTIVE
    lights._on_answer_revealed(_reveal())
    await _run(lights)
    await hass.settle()
    last = hass.signals[-1]
    assert last.name == "light.turn_on"
    assert last.data["entity_id"] == ["light.a", "light.b"]
    assert last.data["rgb_color"] == [232, 138, 127]
    assert lights._restore_snapshot is None
