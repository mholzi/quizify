"""House bus listeners must be ``@callback`` (#1152).

``hass.bus.async_listen`` classifies a plain function as
``HassJobType.Executor`` and runs it in a worker thread; the handlers call
``hass.async_create_task`` which is not thread-safe there, so House Plays Along
(party-light accents, SFX cues) never fired. The fake bus below behaves like HA:
it refuses (raises) a listener that is not marked ``_hass_callback`` instead of
silently calling it inline, which is what the older fakes did.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from custom_components.quizify.game.state import GamePhase  # noqa: E402
from custom_components.quizify.lights import QuizifyPartyLights  # noqa: E402
from custom_components.quizify.sound_effects import QuizifySoundEffects  # noqa: E402


class _LoopOnlyBus:
    """Refuses listeners that HA would run off the event loop."""

    def __init__(self) -> None:
        self.listeners: list = []

    def async_listen(self, event_type, cb):  # noqa: ANN001
        if not getattr(cb, "_hass_callback", False):
            raise RuntimeError(
                f"{event_type}: listener would run in an executor thread"
            )
        self.listeners.append(cb)
        return lambda: None


class _Hass:
    def __init__(self) -> None:
        self.bus = _LoopOnlyBus()
        self.states = type("S", (), {"get": staticmethod(lambda _e: None)})()


class _Game:
    phase = GamePhase.LOBBY
    round = 0
    game_id = None

    def register_state_callback(self, cb):  # noqa: ANN001
        pass

    def unregister_state_callback(self, cb):  # noqa: ANN001
        pass


def _lights(hass):
    return QuizifyPartyLights(hass=hass, entity_ids=["light.p"], game_state=_Game())


def _sfx(hass):
    return QuizifySoundEffects(
        hass=hass, media_player_entity_id="media_player.tv", game_state=_Game()
    )


@pytest.mark.parametrize(
    ("factory", "count"), [(_lights, 5), (_sfx, 3)], ids=["lights", "sfx"]
)
def test_registered_listeners_are_callbacks(factory, count):
    hass = _Hass()
    factory(hass).attach_events()
    assert len(hass.bus.listeners) == count
    assert all(cb._hass_callback is True for cb in hass.bus.listeners)


def test_loop_only_bus_refuses_plain_function():
    with pytest.raises(RuntimeError):
        _LoopOnlyBus().async_listen("x", lambda _e: None)


@pytest.mark.parametrize("factory", [_lights, _sfx], ids=["lights", "sfx"])
def test_listeners_classify_as_hass_callback_job(factory):
    core = pytest.importorskip("homeassistant.core")
    hass = _Hass()
    factory(hass).attach_events()
    for cb in hass.bus.listeners:
        assert core.HassJob(cb).job_type is core.HassJobType.Callback
