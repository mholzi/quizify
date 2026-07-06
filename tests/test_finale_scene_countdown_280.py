"""Tests for the #280 remaining delta — countdown pulse + finale scene.

Two additive features on top of "The House Plays Along" (#494):

* Item A — a ``quizify_time_running_out`` bus event fired once per round in the
  final seconds (``QuizifyEventEmitter.notify_time_running_out``), which the
  party lights turn into a faster "breathing" brightness pulse.
* Item B — an optional finale scene the party lights activate alongside the
  winner victory sweep (``QuizifyPartyLights`` ``finale_scene`` arg).

Fakes mirror the existing suite (``test_game_events`` / ``test_light_choreography``):
a fake hass records ``bus.async_fire`` / service calls so we assert *intent*
without a real Home Assistant. The options-flow round-trip needs the real HA
harness and is skipped where it (Python 3.13+) is unavailable.
"""

from __future__ import annotations

import asyncio
import contextlib
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT))

from custom_components.quizify import lights as lights_mod  # noqa: E402
from custom_components.quizify.game.state import GamePhase  # noqa: E402
from custom_components.quizify.game_events import (  # noqa: E402
    EVENT_TIME_RUNNING_OUT,
    QuizifyEventEmitter,
)
from custom_components.quizify.lights import QuizifyPartyLights  # noqa: E402

# ---------------------------------------------------------------------------
# Fakes (shared style with test_game_events / test_light_choreography)
# ---------------------------------------------------------------------------


class _FakeBus:
    def __init__(self) -> None:
        self.fired: list[tuple[str, dict]] = []
        self.listeners: dict[str, list] = {}

    def async_fire(self, event_type, data):  # noqa: ANN001
        self.fired.append((event_type, dict(data)))

    def async_listen(self, event_type, cb):  # noqa: ANN001
        self.listeners.setdefault(event_type, []).append(cb)

        def _unsub() -> None:
            lst = self.listeners.get(event_type, [])
            if cb in lst:
                lst.remove(cb)

        return _unsub

    def dispatch(self, event_type, data):  # noqa: ANN001
        for cb in list(self.listeners.get(event_type, [])):
            cb(_FakeEvent(data))


class _FakeEvent:
    def __init__(self, data: dict) -> None:
        self.data = data


class _FakeTask:
    """Runs the pulse coroutine straight to completion (sleeps no-op'd)."""

    def __init__(self, coro) -> None:
        self._coro = coro
        self._done = False
        with contextlib.suppress(StopIteration):
            coro.send(None)
        self._done = True

    def done(self) -> bool:
        return self._done

    def cancel(self) -> None:
        if not self._done:
            self._coro.close()
        self._done = True


class _FakeHass:
    def __init__(self) -> None:
        self.bus = _FakeBus()
        self.tasks: list[_FakeTask] = []

    def async_create_task(self, coro):  # noqa: ANN001
        task = _FakeTask(coro)
        self.tasks.append(task)
        return task


class _FakeGame:
    def __init__(self) -> None:
        self.phase = GamePhase.LOBBY
        self.round = 0
        self.total_rounds = 10
        self.game_id = None
        self.callbacks: list = []

    def register_state_callback(self, cb):  # noqa: ANN001
        self.callbacks.append(cb)

    def unregister_state_callback(self, cb):  # noqa: ANN001
        if cb in self.callbacks:
            self.callbacks.remove(cb)


class _Question:
    type = "multiple_choice"


# ---------------------------------------------------------------------------
# Item A — emitter: notify_time_running_out one-shot guard (#280)
# ---------------------------------------------------------------------------


def _emitter(hass, game, *, enabled=True):
    return QuizifyEventEmitter(hass=hass, game_state=game, enabled=enabled)


def test_time_running_out_fires_once_and_is_suppressed_same_round():
    """First in-window tick fires; later ticks in the same round no-op."""
    hass = _FakeHass()
    t = _emitter(hass, _FakeGame())

    t.notify_time_running_out(4.0)
    t.notify_time_running_out(3.0)
    t.notify_time_running_out(1.0)

    fired = [(e, d) for e, d in hass.bus.fired if e == EVENT_TIME_RUNNING_OUT]
    assert len(fired) == 1
    assert fired[0][1] == {"seconds_remaining": 4.0}


def test_time_running_out_ignores_ticks_outside_final_window():
    """Ticks above the threshold (early in the round) never fire."""
    hass = _FakeHass()
    t = _emitter(hass, _FakeGame())

    # 30s / 6s remaining are outside the <=5s "final seconds" window.
    t.notify_time_running_out(30.0)
    t.notify_time_running_out(6.0)
    assert hass.bus.fired == []

    # Crossing into the window fires exactly once.
    t.notify_time_running_out(5.0)
    assert [e for e, _ in hass.bus.fired] == [EVENT_TIME_RUNNING_OUT]


def test_time_running_out_rearms_after_round_reset_hook():
    """notify_question_shown (round start) re-arms the one-shot guard."""
    hass = _FakeHass()
    t = _emitter(hass, _FakeGame())

    t.notify_time_running_out(3.0)
    # Round rolls over: the question-shown forwarder resets the guard.
    t.notify_question_shown(_Question(), 2, 10)
    t.notify_time_running_out(2.0)

    fired = [e for e, _ in hass.bus.fired if e == EVENT_TIME_RUNNING_OUT]
    assert len(fired) == 2


def test_time_running_out_noop_when_house_events_disabled():
    """Master toggle off (CONF_HOUSE_EVENTS_ENABLED) => nothing fires."""
    hass = _FakeHass()
    t = _emitter(hass, _FakeGame(), enabled=False)
    assert t.is_configured is False

    t.notify_time_running_out(3.0)
    assert hass.bus.fired == []


def test_time_running_out_noop_without_hass():
    """Standalone dev server (no hass) never raises and fires nothing."""
    t = _emitter(None, _FakeGame())
    # No bus to assert on — just prove it doesn't raise.
    t.notify_time_running_out(3.0)


# ---------------------------------------------------------------------------
# Item A — lights: the countdown pulse accent (#280)
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _fast_sleep(monkeypatch):
    """asyncio.sleep -> no-op so pulse tasks run to completion synchronously."""

    async def _noop(*_args, **_kwargs):
        return

    monkeypatch.setattr(asyncio, "sleep", _noop)


@pytest.fixture
def calls(monkeypatch):
    """Capture (domain, service, data) tuples from every fired service call."""
    recorded: list[tuple[str, str, dict]] = []

    def _record(_hass, domain, service, data, _ctx):  # noqa: ANN001
        recorded.append((domain, service, dict(data)))

    monkeypatch.setattr(lights_mod, "fire_and_forget_service", _record)
    return recorded


def _lights(hass, *, phase=GamePhase.QUESTION_ACTIVE, entities=("light.party",),
            finale_scene=None):
    game = _FakeGame()
    pl = QuizifyPartyLights(
        hass=hass,
        entity_ids=list(entities),
        game_state=game,
        finale_scene=finale_scene,
    )
    pl._last_phase = phase  # prime ambient so baseline restores apply
    return pl


def test_time_running_out_event_drives_brightness_only_pulse(calls):
    """The countdown accent breathes brightness with no rgb, then settles."""
    hass = _FakeHass()
    pl = _lights(hass, phase=GamePhase.QUESTION_ACTIVE)
    pl.attach_events()
    assert EVENT_TIME_RUNNING_OUT in hass.bus.listeners

    hass.bus.dispatch(EVENT_TIME_RUNNING_OUT, {"seconds_remaining": 3.0})

    turn_ons = [d for (_d, s, d) in calls if s == "turn_on"]
    # At least the two down/up beats plus the baseline restore ran.
    assert len(turn_ons) >= 3
    # The pulse steps set brightness but never a colour (works on live colour).
    pulse_steps = [
        d for d in turn_ons if "brightness_pct" in d and "rgb_color" not in d
    ]
    assert pulse_steps, "countdown pulse must set brightness only"
    assert all("rgb_color" not in d for d in pulse_steps)


# ---------------------------------------------------------------------------
# Item B — lights: finale scene on winner (#280)
# ---------------------------------------------------------------------------


def _scene_calls(calls):
    return [d for (domain, s, d) in calls if domain == "scene" and s == "turn_on"]


def test_winner_activates_configured_finale_scene(calls):
    """A configured finale scene is turned on alongside the winner sweep."""
    hass = _FakeHass()
    pl = _lights(hass, phase=GamePhase.FINALE, finale_scene="scene.victory")
    pl.attach_events()

    hass.bus.dispatch("quizify_winner_decided", {"winner_name": "Alice", "score": 5})

    scenes = _scene_calls(calls)
    assert scenes == [{"entity_id": "scene.victory"}]


def test_winner_without_finale_scene_does_not_touch_scenes(calls):
    """No finale scene configured => no scene.turn_on is ever fired."""
    hass = _FakeHass()
    pl = _lights(hass, phase=GamePhase.FINALE, finale_scene=None)
    pl.attach_events()

    hass.bus.dispatch("quizify_winner_decided", {"winner_name": "Alice", "score": 5})

    assert _scene_calls(calls) == []


def test_finale_scene_blank_string_is_normalized_to_none(calls):
    """Whitespace-only finale scene is treated as unset (no scene call)."""
    hass = _FakeHass()
    pl = _lights(hass, phase=GamePhase.FINALE, finale_scene="   ")
    assert pl._finale_scene is None
    pl.attach_events()

    hass.bus.dispatch("quizify_winner_decided", {"winner_name": "Bob", "score": 3})
    assert _scene_calls(calls) == []


def test_finale_scene_noop_when_unconfigured(calls):
    """No hass or no lights => finale scene activation is a clean no-op."""
    # No hass at all.
    pl_no_hass = _lights(None, finale_scene="scene.victory")
    pl_no_hass._activate_finale_scene()
    # No light entities.
    hass = _FakeHass()
    pl_no_lights = _lights(hass, entities=(), finale_scene="scene.victory")
    pl_no_lights._activate_finale_scene()

    assert _scene_calls(calls) == []


# ---------------------------------------------------------------------------
# Item B — options flow round-trip (#280). Needs the real HA harness.
# ---------------------------------------------------------------------------

pytest.importorskip("homeassistant")
pytest.importorskip("pytest_homeassistant_custom_component")

from homeassistant import data_entry_flow  # noqa: E402
from homeassistant.core import HomeAssistant  # noqa: E402
from pytest_homeassistant_custom_component.common import (  # noqa: E402
    MockConfigEntry,
)

from custom_components.quizify.const import (  # noqa: E402
    CONF_FINALE_SCENE,
    CONF_MEDIA_PLAYER_ENTITY,
    CONF_TTS_ENTITY,
    DOMAIN,
)

pytestmark = pytest.mark.usefixtures("enable_custom_integrations")


async def test_options_flow_lists_finale_scene(hass: HomeAssistant) -> None:
    """The options form exposes the new finale_scene field (#280)."""
    entry = MockConfigEntry(domain=DOMAIN, unique_id=DOMAIN)
    entry.add_to_hass(hass)

    result = await hass.config_entries.options.async_init(entry.entry_id)
    schema_keys = {str(marker) for marker in result["data_schema"].schema}
    assert CONF_FINALE_SCENE in schema_keys


async def test_options_flow_round_trips_finale_scene(hass: HomeAssistant) -> None:
    """Submitting a finale_scene value persists it onto the entry options."""
    entry = MockConfigEntry(domain=DOMAIN, unique_id=DOMAIN)
    entry.add_to_hass(hass)

    result = await hass.config_entries.options.async_init(entry.entry_id)
    # EntitySelectors validate entity-id FORMAT and reject "" — so give the
    # other single-entity fields valid-format ids; finale_scene is under test.
    user_input = {
        CONF_TTS_ENTITY: "tts.test",
        CONF_MEDIA_PLAYER_ENTITY: "media_player.test",
        CONF_FINALE_SCENE: "scene.movie_night",
    }
    result2 = await hass.config_entries.options.async_configure(
        result["flow_id"], user_input=user_input
    )
    assert result2["type"] == data_entry_flow.FlowResultType.CREATE_ENTRY
    assert result2["data"][CONF_FINALE_SCENE] == "scene.movie_night"
    assert entry.options[CONF_FINALE_SCENE] == "scene.movie_night"


async def test_options_flow_defaults_finale_scene_from_existing(
    hass: HomeAssistant,
) -> None:
    """Re-opening the options form pre-fills the finale_scene default."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=DOMAIN,
        options={CONF_FINALE_SCENE: "scene.preset"},
    )
    entry.add_to_hass(hass)

    result = await hass.config_entries.options.async_init(entry.entry_id)
    for marker in result["data_schema"].schema:
        if str(marker) == CONF_FINALE_SCENE:
            assert marker.default() == "scene.preset"
            break
    else:  # pragma: no cover
        pytest.fail("finale_scene marker not found in options schema")
