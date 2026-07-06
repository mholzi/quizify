"""Tests for the party-light accent choreography — Variant B "Game Show" (#494).

QuizifyPartyLights layers momentary "accent" effects on top of its phase-driven
ambient glow by subscribing to the ``quizify_*`` HA bus events (#366 backbone).
Here a fake hass records bus listeners + service calls and runs the pulse task
deterministically (asyncio.sleep monkeypatched to a no-op), so we assert the
*intent* — which colour/brightness a given event drives, that it restores the
phase baseline, that a new accent cancels an in-flight one, and that detach()
tears everything down — without a real Home Assistant.
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
    EVENT_ANSWER_REVEALED,
    EVENT_QUESTION_SHOWN,
    EVENT_STREAK_MILESTONE,
    EVENT_TIME_RUNNING_OUT,
    EVENT_WINNER_DECIDED,
)
from custom_components.quizify.lights import QuizifyPartyLights  # noqa: E402

# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class _FakeEvent:
    def __init__(self, data: dict) -> None:
        self.data = data


class _FakeTask:
    """Cancel-tracking stand-in for asyncio.Task.

    When ``run`` is True the coroutine is driven straight to completion (sleeps
    are no-ops), so service calls land synchronously. When False the coroutine
    is left suspended so a test can observe cancel() semantics on an in-flight
    accent.
    """

    def __init__(self, coro, run: bool) -> None:
        self._coro = coro
        self.cancelled = False
        self._done = False
        if run:
            with contextlib.suppress(StopIteration):
                coro.send(None)
            self._done = True

    def done(self) -> bool:
        return self._done

    def cancel(self) -> None:
        self.cancelled = True
        if not self._done:
            # Unwind (or discard an unstarted) coroutine cleanly — no
            # "never awaited" warning.
            self._coro.close()
        self._done = True


class _FakeBus:
    def __init__(self) -> None:
        self.listeners: dict[str, list] = {}

    def async_listen(self, event_type, cb):  # noqa: ANN001
        self.listeners.setdefault(event_type, []).append(cb)

        def _unsub() -> None:
            lst = self.listeners.get(event_type, [])
            if cb in lst:
                lst.remove(cb)

        return _unsub

    def dispatch(self, event_type, data):  # noqa: ANN001
        """Test helper: deliver a fake Event to every registered listener."""
        for cb in list(self.listeners.get(event_type, [])):
            cb(_FakeEvent(data))


class _FakeHass:
    def __init__(self, run_tasks: bool = True) -> None:
        self.bus = _FakeBus()
        self.run_tasks = run_tasks
        self.tasks: list[_FakeTask] = []

    def async_create_task(self, coro):  # noqa: ANN001
        task = _FakeTask(coro, run=self.run_tasks)
        self.tasks.append(task)
        return task


class _FakeGame:
    """Minimal game stand-in — the lights only register/unregister a callback
    and (for the ambient path, untouched here) read phase/round/game_id."""

    def __init__(self) -> None:
        self.phase = GamePhase.LOBBY
        self.round = 0
        self.game_id = None
        self.callbacks: list = []

    def register_state_callback(self, cb):  # noqa: ANN001
        self.callbacks.append(cb)

    def unregister_state_callback(self, cb):  # noqa: ANN001
        if cb in self.callbacks:
            self.callbacks.remove(cb)


@pytest.fixture(autouse=True)
def _fast_sleep(monkeypatch):
    """Make asyncio.sleep a no-op so pulse tasks run to completion instantly."""

    async def _noop(*_args, **_kwargs):
        return

    monkeypatch.setattr(asyncio, "sleep", _noop)


@pytest.fixture
def calls(monkeypatch):
    """Capture (domain, service, data) tuples from every light service call."""
    recorded: list[tuple[str, str, dict]] = []

    def _record(_hass, domain, service, data, _ctx):  # noqa: ANN001
        recorded.append((domain, service, dict(data)))

    monkeypatch.setattr(lights_mod, "fire_and_forget_service", _record)
    return recorded


def _lights(hass, *, phase=GamePhase.QUESTION_ACTIVE, entities=("light.party",)):
    game = _FakeGame()
    pl = QuizifyPartyLights(hass=hass, entity_ids=list(entities), game_state=game)
    # Prime the ambient phase so baseline restores have something to apply.
    pl._last_phase = phase
    return pl


def _turn_ons(calls):
    return [data for (_d, service, data) in calls if service == "turn_on"]


# ---------------------------------------------------------------------------
# attach_events / gating
# ---------------------------------------------------------------------------


def test_attach_events_registers_all_listeners():
    hass = _FakeHass()
    pl = _lights(hass)
    pl.attach_events()
    assert set(hass.bus.listeners) == {
        EVENT_QUESTION_SHOWN,
        EVENT_TIME_RUNNING_OUT,
        EVENT_ANSWER_REVEALED,
        EVENT_STREAK_MILESTONE,
        EVENT_WINNER_DECIDED,
    }


def test_attach_events_idempotent():
    hass = _FakeHass()
    pl = _lights(hass)
    pl.attach_events()
    pl.attach_events()  # second call must not double-register
    assert all(len(v) == 1 for v in hass.bus.listeners.values())
    assert len(pl._event_unsubs) == 5


def test_not_configured_no_hass_registers_nothing_and_noops(calls):
    pl = _lights(None)  # hass is None → not configured
    pl.attach_events()
    assert pl._event_unsubs == []
    pl._on_question_shown(_FakeEvent({}))  # accent must be a no-op
    assert calls == []


def test_not_configured_no_entities_registers_nothing(calls):
    hass = _FakeHass()
    pl = _lights(hass, entities=())  # no entities → not configured
    pl.attach_events()
    assert hass.bus.listeners == {}
    pl._on_streak_milestone(_FakeEvent({}))
    assert calls == []


# ---------------------------------------------------------------------------
# Accents fire the expected colour / brightness
# ---------------------------------------------------------------------------


def test_question_shown_bumps_brightness_then_restores(calls):
    hass = _FakeHass()
    pl = _lights(hass, phase=GamePhase.QUESTION_ACTIVE)
    pl.attach_events()
    hass.bus.dispatch(EVENT_QUESTION_SHOWN, {"round": 1})

    outs = _turn_ons(calls)
    # First: brightness bump (no colour set → keeps live coral).
    assert outs[0] == {
        "entity_id": ["light.party"],
        "brightness_pct": 88,
        "transition": 0.2,
    }
    # Last: restore QUESTION_ACTIVE baseline (coral 70%).
    assert outs[-1] == {
        "entity_id": ["light.party"],
        **lights_mod._PHASE_LIGHT_RECIPES[GamePhase.QUESTION_ACTIVE],
    }


def test_answer_revealed_majority_is_green(calls):
    hass = _FakeHass()
    pl = _lights(hass)
    pl.attach_events()
    hass.bus.dispatch(
        EVENT_ANSWER_REVEALED, {"correct_count": 3, "total_players": 4}
    )
    outs = _turn_ons(calls)
    assert outs[0]["rgb_color"] == [120, 200, 140]
    assert outs[0]["brightness_pct"] == 85


def test_answer_revealed_minority_is_amber(calls):
    hass = _FakeHass()
    pl = _lights(hass)
    pl.attach_events()
    hass.bus.dispatch(
        EVENT_ANSWER_REVEALED, {"correct_count": 1, "total_players": 4}
    )
    outs = _turn_ons(calls)
    assert outs[0]["rgb_color"] == [235, 175, 95]


def test_answer_revealed_exact_half_is_amber(calls):
    """Half-right is not a *strict* majority → amber."""
    hass = _FakeHass()
    pl = _lights(hass)
    pl.attach_events()
    hass.bus.dispatch(
        EVENT_ANSWER_REVEALED, {"correct_count": 2, "total_players": 4}
    )
    assert _turn_ons(calls)[0]["rgb_color"] == [235, 175, 95]


def test_answer_revealed_zero_players_no_crash_amber(calls):
    hass = _FakeHass()
    pl = _lights(hass)
    pl.attach_events()
    # total_players == 0 must not divide-by-zero; treated as minority (amber).
    hass.bus.dispatch(
        EVENT_ANSWER_REVEALED, {"correct_count": 0, "total_players": 0}
    )
    assert _turn_ons(calls)[0]["rgb_color"] == [235, 175, 95]


def test_streak_milestone_double_pulses_coral(calls):
    hass = _FakeHass()
    pl = _lights(hass, phase=GamePhase.QUESTION_ACTIVE)
    pl.attach_events()
    hass.bus.dispatch(EVENT_STREAK_MILESTONE, {"player_name": "Alice", "streak": 5})

    outs = _turn_ons(calls)
    coral = [o for o in outs if o.get("rgb_color") == [255, 150, 120]]
    assert len(coral) == 2  # two "up" beats
    assert all(o["brightness_pct"] == 92 for o in coral)
    # Settles back on the phase baseline.
    assert outs[-1] == {
        "entity_id": ["light.party"],
        **lights_mod._PHASE_LIGHT_RECIPES[GamePhase.QUESTION_ACTIVE],
    }


def test_winner_decided_sweep_settles_to_sun_90(calls):
    hass = _FakeHass()
    pl = _lights(hass, phase=GamePhase.FINALE)
    pl.attach_events()
    hass.bus.dispatch(EVENT_WINNER_DECIDED, {"winner_name": "Bob", "score": 420})

    outs = _turn_ons(calls)
    ups = [o for o in outs if o.get("brightness_pct") == 100]
    assert len(ups) == 3  # three up-beats
    assert all(o["rgb_color"] == [232, 196, 127] for o in ups)
    # Final settle: steady sun 90% (explicit, not a baseline restore).
    assert outs[-1] == {
        "entity_id": ["light.party"],
        "rgb_color": [232, 196, 127],
        "brightness_pct": 90,
        "transition": 0.3,
    }


# ---------------------------------------------------------------------------
# Baseline restore respects the current phase
# ---------------------------------------------------------------------------


def test_baseline_restore_uses_current_phase(calls):
    hass = _FakeHass()
    pl = _lights(hass, phase=GamePhase.ANSWER_REVEAL)
    pl.attach_events()
    hass.bus.dispatch(EVENT_QUESTION_SHOWN, {})
    assert _turn_ons(calls)[-1] == {
        "entity_id": ["light.party"],
        **lights_mod._PHASE_LIGHT_RECIPES[GamePhase.ANSWER_REVEAL],
    }


def test_baseline_restore_noop_when_phase_recipe_is_none(calls):
    hass = _FakeHass()
    pl = _lights(hass, phase=GamePhase.PAUSED)  # PAUSED recipe is None
    pl.attach_events()
    hass.bus.dispatch(EVENT_QUESTION_SHOWN, {})
    outs = _turn_ons(calls)
    # Only the brightness bump lands; the baseline step is a no-op (PAUSED=None).
    assert outs == [
        {"entity_id": ["light.party"], "brightness_pct": 88, "transition": 0.2}
    ]


# ---------------------------------------------------------------------------
# Pulse cancellation / no stacking
# ---------------------------------------------------------------------------


def test_new_accent_cancels_in_flight_pulse():
    hass = _FakeHass(run_tasks=False)  # leave pulses suspended
    pl = _lights(hass)
    pl.attach_events()

    hass.bus.dispatch(EVENT_QUESTION_SHOWN, {})
    first = pl._pulse_task
    assert first is not None and not first.cancelled

    hass.bus.dispatch(EVENT_STREAK_MILESTONE, {})
    # Starting the second accent must cancel the first (no stacking).
    assert first.cancelled is True
    assert pl._pulse_task is not first
    assert pl._pulse_task.cancelled is False

    # Tidy the still-suspended second pulse (run_tasks=False leaves it live).
    pl.detach()


def test_detach_unregisters_listeners_and_cancels_pulse():
    hass = _FakeHass(run_tasks=False)
    pl = _lights(hass)
    pl.attach_events()
    hass.bus.dispatch(EVENT_QUESTION_SHOWN, {})
    task = pl._pulse_task
    assert task is not None

    pl.detach()

    # All listeners gone.
    assert all(len(v) == 0 for v in hass.bus.listeners.values())
    assert pl._event_unsubs == []
    # In-flight pulse cancelled and cleared.
    assert task.cancelled is True
    assert pl._pulse_task is None


def test_detach_without_events_is_safe():
    """detach() must be safe even if attach_events() was never called."""
    hass = _FakeHass()
    pl = _lights(hass)
    pl.attach()
    pl.detach()  # no listeners, no pulse → must not raise
    assert pl._event_unsubs == []
    assert pl._pulse_task is None
