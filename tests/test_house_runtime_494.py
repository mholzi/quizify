"""Backend runtime layer of the "House Plays Along" admin panel (#494 Phase 4).

The panel sends ONE flat, already-resolved config dict (presets are a
frontend-only concept — the backend only ever sees booleans). It lands on three
consumers via ``configure()``:

* :class:`QuizifyPartyLights` — the five event-driven accents + the winner scene,
* :class:`QuizifySoundEffects` — the four one-shot cues,
* :class:`QuizifyEventEmitter` — the master only; the bus events are the public
  automation API (#366) and must keep firing for the host's own blueprints even
  when Quizify's own light/SFX consumers are switched off. That asymmetry IS the
  panel's "events only" preset.

Covered here (fakes only — no real Home Assistant):

* master off ⇒ nothing fires, even with a per-effect toggle on;
* each per-effect toggle independently gates exactly its own accent/cue;
* entity overrides beat the config-entry values, and an EMPTY override falls
  back instead of masking them;
* the "events only" shape still fires bus events;
* the export/restore snapshot round-trip (the #411 pattern) preserves the
  panel's toggles — including the tri-state master override, whose ``None``
  ("panel never touched it") must survive as ``None`` so a config-entry master
  change still lands.

The end-to-end guard that the REAL ``__init__._update_listener`` performs that
round-trip lives in ``test_house_reload_494`` (needs the HA harness).
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
from custom_components.quizify import sound_effects as sfx_mod  # noqa: E402
from custom_components.quizify.game.state import GamePhase  # noqa: E402
from custom_components.quizify.game_events import (  # noqa: E402
    EVENT_ANSWER_REVEALED,
    EVENT_GAME_STARTED,
    EVENT_QUESTION_SHOWN,
    EVENT_STREAK_MILESTONE,
    EVENT_TIME_RUNNING_OUT,
    EVENT_WINNER_DECIDED,
    QuizifyEventEmitter,
)
from custom_components.quizify.house_settings import HouseSettings  # noqa: E402
from custom_components.quizify.lights import QuizifyPartyLights  # noqa: E402
from custom_components.quizify.sound_effects import (  # noqa: E402
    QuizifySoundEffects,
)

_BASE = "http://ha.local:8123"

# The exact contract the admin panel puts on the wire. Master OFF, every child
# toggle ON — the TTS posture (silent until enabled, then fully expressive).
_PANEL_DEFAULT: dict = {
    "enabled": False,
    "light_question": True,
    "light_countdown": True,
    "light_reveal": True,
    "light_streak": True,
    "light_winner": True,
    "winner_scene": True,
    "sfx_correct": True,
    "sfx_wrong": True,
    "sfx_streak": True,
    "sfx_winner": True,
    "light_entities": [],
    "media_player": "",
    "winner_scene_entity": "",
}


# ---------------------------------------------------------------------------
# Fakes (mirror test_light_choreography / test_sound_effects / test_game_events)
# ---------------------------------------------------------------------------


class _FakeEvent:
    def __init__(self, data: dict) -> None:
        self.data = data


class _FakeState:
    def __init__(self, state: str) -> None:
        self.state = state


class _FakeStates:
    def __init__(self) -> None:
        self._states: dict[str, _FakeState] = {}

    def set(self, entity_id: str, state: str) -> None:
        self._states[entity_id] = _FakeState(state)

    def get(self, entity_id: str):  # noqa: ANN201
        return self._states.get(entity_id)


class _FakeTask:
    """Drives the accent coroutine straight to completion (sleeps are no-ops)."""

    def __init__(self, coro) -> None:  # noqa: ANN001
        with contextlib.suppress(StopIteration):
            coro.send(None)

    def done(self) -> bool:
        return True

    def cancel(self) -> None:
        return


class _FakeBus:
    def __init__(self) -> None:
        self.listeners: dict[str, list] = {}
        self.fired: list[tuple[str, dict]] = []

    def async_listen(self, event_type, cb):  # noqa: ANN001
        self.listeners.setdefault(event_type, []).append(cb)

        def _unsub() -> None:
            lst = self.listeners.get(event_type, [])
            if cb in lst:
                lst.remove(cb)

        return _unsub

    def async_fire(self, event_type, data):  # noqa: ANN001
        self.fired.append((event_type, dict(data)))

    def dispatch(self, event_type, data):  # noqa: ANN001
        for cb in list(self.listeners.get(event_type, [])):
            cb(_FakeEvent(data))


class _FakeHass:
    def __init__(self) -> None:
        self.bus = _FakeBus()
        self.states = _FakeStates()

    def async_create_task(self, coro):  # noqa: ANN001
        return _FakeTask(coro)


class _FakeGame:
    """Minimal stand-in — these consumers only touch the callback registry."""

    def __init__(self) -> None:
        self.phase = GamePhase.LOBBY
        self.round = 0
        self.total_rounds = 10
        self.game_id = None
        self.leader = None
        self.callbacks: list = []

    def register_state_callback(self, cb):  # noqa: ANN001
        self.callbacks.append(cb)

    def unregister_state_callback(self, cb):  # noqa: ANN001
        if cb in self.callbacks:
            self.callbacks.remove(cb)

    def get_players(self):
        return []


@pytest.fixture(autouse=True)
def _fast_sleep(monkeypatch):
    """Accent pulses run instantly — no wall-clock waiting in tests."""

    async def _noop(*_args, **_kwargs):
        return

    monkeypatch.setattr(asyncio, "sleep", _noop)


@pytest.fixture
def light_calls(monkeypatch):
    recorded: list[tuple[str, str, dict]] = []

    def _record(_hass, domain, service, data, _ctx):  # noqa: ANN001
        recorded.append((domain, service, dict(data)))

    monkeypatch.setattr(lights_mod, "fire_and_forget_service", _record)
    return recorded


@pytest.fixture
def sfx_calls(monkeypatch, tmp_path):
    """Capture SFX service calls, with all four bundled defaults available."""
    recorded: list[tuple[str, str, dict]] = []

    def _record(_hass, domain, service, data, _ctx):  # noqa: ANN001
        recorded.append((domain, service, dict(data)))

    monkeypatch.setattr(sfx_mod, "fire_and_forget_service", _record)
    monkeypatch.setattr(sfx_mod, "_ha_get_url", lambda _hass: _BASE)
    sfx_dir = tmp_path / "sfx"
    sfx_dir.mkdir(parents=True, exist_ok=True)
    for cue in ("correct", "wrong", "streak", "winner"):
        (sfx_dir / f"{cue}.mp3").write_bytes(b"")
    monkeypatch.setattr(sfx_mod, "WWW_DIR", tmp_path)
    return recorded


# ---------------------------------------------------------------------------
# Builders + helpers
# ---------------------------------------------------------------------------


def _lights(hass, *, entities=("light.party",), finale_scene=None):
    pl = QuizifyPartyLights(
        hass=hass,
        entity_ids=list(entities),
        game_state=_FakeGame(),
        finale_scene=finale_scene,
    )
    # Prime the ambient phase so baseline restores have a recipe to apply.
    pl._last_phase = GamePhase.QUESTION_ACTIVE
    pl.attach_events()
    return pl


def _sfx(hass, *, media_player="media_player.tv"):
    sfx = QuizifySoundEffects(
        hass=hass,
        media_player_entity_id=media_player,
        game_state=_FakeGame(),
        cue_urls={},
    )
    sfx.attach_events()
    return sfx


def _configure_lights(pl, **overrides):
    """Apply the panel contract, keeping only the light-relevant keys."""
    cfg = {**_PANEL_DEFAULT, **overrides}
    pl.configure(
        enabled=cfg["enabled"],
        light_question=cfg["light_question"],
        light_countdown=cfg["light_countdown"],
        light_reveal=cfg["light_reveal"],
        light_streak=cfg["light_streak"],
        light_winner=cfg["light_winner"],
        winner_scene=cfg["winner_scene"],
        light_entities=cfg["light_entities"],
        winner_scene_entity=cfg["winner_scene_entity"],
    )


def _configure_sfx(sfx, **overrides):
    """Apply the panel contract, keeping only the SFX-relevant keys."""
    cfg = {**_PANEL_DEFAULT, **overrides}
    sfx.configure(
        enabled=cfg["enabled"],
        sfx_correct=cfg["sfx_correct"],
        sfx_wrong=cfg["sfx_wrong"],
        sfx_streak=cfg["sfx_streak"],
        sfx_winner=cfg["sfx_winner"],
        media_player=cfg["media_player"],
    )


def _fire_all_light_events(hass) -> None:
    """Dispatch every event the five light accents listen for."""
    hass.bus.dispatch(EVENT_QUESTION_SHOWN, {"round": 1, "total_rounds": 10})
    hass.bus.dispatch(EVENT_TIME_RUNNING_OUT, {"seconds_remaining": 3})
    hass.bus.dispatch(EVENT_ANSWER_REVEALED, {"correct_count": 3, "total_players": 4})
    hass.bus.dispatch(EVENT_STREAK_MILESTONE, {"player_name": "Ann", "streak": 5})
    hass.bus.dispatch(EVENT_WINNER_DECIDED, {"winner_name": "Ann", "score": 10})


def _fire_all_sfx_events(hass) -> None:
    """Dispatch every event the four SFX cues listen for (correct + wrong)."""
    hass.bus.dispatch(EVENT_ANSWER_REVEALED, {"correct_count": 3, "total_players": 4})
    hass.bus.dispatch(EVENT_ANSWER_REVEALED, {"correct_count": 1, "total_players": 4})
    hass.bus.dispatch(EVENT_STREAK_MILESTONE, {"player_name": "Ann", "streak": 5})
    hass.bus.dispatch(EVENT_WINNER_DECIDED, {"winner_name": "Ann", "score": 10})


def _light_turn_ons(calls):
    return [d for (dom, svc, d) in calls if dom == "light" and svc == "turn_on"]


def _scene_turn_ons(calls):
    return [d for (dom, svc, d) in calls if dom == "scene" and svc == "turn_on"]


def _played_cues(calls):
    """Map the played media URLs back to their cue names."""
    return [
        data["media_content_id"].rsplit("/", 1)[-1].removesuffix(".mp3")
        for (dom, svc, data) in calls
        if dom == "media_player" and svc == "play_media"
    ]


# ---------------------------------------------------------------------------
# Master switch — off means the house stays still
# ---------------------------------------------------------------------------


def test_lights_master_off_fires_no_accent_even_with_toggles_on(light_calls):
    """Master off beats every per-accent toggle (all ON here)."""
    hass = _FakeHass()
    pl = _lights(hass, finale_scene="scene.victory")
    _configure_lights(pl, enabled=False)  # children all default True

    _fire_all_light_events(hass)

    assert light_calls == []


def test_sfx_master_off_plays_no_cue_even_with_toggles_on(sfx_calls):
    """Master off beats every per-cue toggle (all ON here)."""
    hass = _FakeHass()
    sfx = _sfx(hass)
    _configure_sfx(sfx, enabled=False)

    _fire_all_sfx_events(hass)

    assert _played_cues(sfx_calls) == []


def test_lights_master_on_fires_every_accent(light_calls):
    """The full-house shape: master on, all accents on → all five fire."""
    hass = _FakeHass()
    pl = _lights(hass, finale_scene="scene.victory")
    _configure_lights(pl, enabled=True)

    _fire_all_light_events(hass)

    assert _light_turn_ons(light_calls)
    assert _scene_turn_ons(light_calls) == [{"entity_id": "scene.victory"}]


def test_sfx_master_on_plays_every_cue(sfx_calls):
    hass = _FakeHass()
    sfx = _sfx(hass)
    _configure_sfx(sfx, enabled=True)

    _fire_all_sfx_events(hass)

    assert _played_cues(sfx_calls) == ["correct", "wrong", "streak", "winner"]


def test_master_off_leaves_ambient_phase_glow_alone(light_calls):
    """The master gates ACCENTS only — the phase-driven ambient recipe still runs.

    Turning the accents off should calm the room, not leave it dark mid-question,
    so ``_on_state_changed`` is deliberately NOT gated on the master.
    """
    hass = _FakeHass()
    pl = _lights(hass)
    _configure_lights(pl, enabled=False)

    pl._last_phase = None  # force a transition
    pl._game.phase = GamePhase.QUESTION_ACTIVE
    pl._game.round = 1
    pl._game.game_id = "g1"
    pl._on_state_changed()

    # The QUESTION_ACTIVE coral recipe was still applied.
    assert _light_turn_ons(light_calls) == [
        {
            "entity_id": ["light.party"],
            "rgb_color": [232, 138, 127],
            "brightness_pct": 70,
            "transition": 0.5,
        }
    ]


# ---------------------------------------------------------------------------
# Per-effect toggles — each gates exactly its own accent / cue
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("toggle", "event", "payload"),
    [
        ("light_question", EVENT_QUESTION_SHOWN, {"round": 1}),
        ("light_countdown", EVENT_TIME_RUNNING_OUT, {"seconds_remaining": 3}),
        (
            "light_reveal",
            EVENT_ANSWER_REVEALED,
            {"correct_count": 3, "total_players": 4},
        ),
        ("light_streak", EVENT_STREAK_MILESTONE, {"streak": 5}),
        ("light_winner", EVENT_WINNER_DECIDED, {"winner_name": "Ann"}),
    ],
)
def test_each_light_toggle_gates_only_its_own_accent(
    light_calls, toggle, event, payload
):
    """Master on, exactly ONE accent off → that accent is silent, the rest fire."""
    hass = _FakeHass()
    pl = _lights(hass)
    # No finale scene configured, so light_winner is the only winner-driven call.
    _configure_lights(pl, enabled=True, **{toggle: False})

    # The gated event alone produces nothing…
    hass.bus.dispatch(event, payload)
    assert _light_turn_ons(light_calls) == [], f"{toggle}=False still fired"

    # …while every other accent still works.
    _fire_all_light_events(hass)
    assert _light_turn_ons(light_calls), f"{toggle}=False silenced other accents"


@pytest.mark.parametrize(
    ("toggle", "cue", "payload"),
    [
        ("sfx_correct", "correct", {"correct_count": 3, "total_players": 4}),
        ("sfx_wrong", "wrong", {"correct_count": 1, "total_players": 4}),
        ("sfx_streak", "streak", {"streak": 5}),
        ("sfx_winner", "winner", {"winner_name": "Ann"}),
    ],
)
def test_each_sfx_toggle_gates_only_its_own_cue(sfx_calls, toggle, cue, payload):
    """Master on, exactly ONE cue off → that cue is silent, the others still play."""
    hass = _FakeHass()
    sfx = _sfx(hass)
    _configure_sfx(sfx, enabled=True, **{toggle: False})

    _fire_all_sfx_events(hass)

    played = _played_cues(sfx_calls)
    assert cue not in played, f"{toggle}=False still played {cue!r}"
    # The other three cues are untouched.
    assert set(played) == {"correct", "wrong", "streak", "winner"} - {cue}


def test_winner_scene_toggle_is_independent_of_the_winner_sweep(light_calls):
    """A host can keep their own victory scene while dropping Quizify's sweep."""
    hass = _FakeHass()
    pl = _lights(hass, finale_scene="scene.victory")
    _configure_lights(pl, enabled=True, light_winner=False, winner_scene=True)

    hass.bus.dispatch(EVENT_WINNER_DECIDED, {"winner_name": "Ann"})

    assert _light_turn_ons(light_calls) == []  # no bulb sweep…
    assert _scene_turn_ons(light_calls) == [{"entity_id": "scene.victory"}]  # …scene on


def test_winner_scene_off_still_sweeps_the_bulbs(light_calls):
    """…and the mirror image: sweep on, scene off."""
    hass = _FakeHass()
    pl = _lights(hass, finale_scene="scene.victory")
    _configure_lights(pl, enabled=True, light_winner=True, winner_scene=False)

    hass.bus.dispatch(EVENT_WINNER_DECIDED, {"winner_name": "Ann"})

    assert _light_turn_ons(light_calls)  # sweep ran…
    assert _scene_turn_ons(light_calls) == []  # …scene untouched


# ---------------------------------------------------------------------------
# Entity resolution — override wins; an EMPTY override falls back
# ---------------------------------------------------------------------------


def test_light_entities_override_wins_over_config_entry(light_calls):
    hass = _FakeHass()
    pl = _lights(hass, entities=("light.config_entry",))
    _configure_lights(pl, enabled=True, light_entities=["light.panel_pick"])

    hass.bus.dispatch(EVENT_QUESTION_SHOWN, {"round": 1})

    assert _light_turn_ons(light_calls)[0]["entity_id"] == ["light.panel_pick"]


def test_empty_light_entities_override_falls_back_to_config_entry(light_calls):
    """An empty list must NOT mask the config-entry lights (it means 'unset')."""
    hass = _FakeHass()
    pl = _lights(hass, entities=("light.config_entry",))
    _configure_lights(pl, enabled=True, light_entities=[])

    hass.bus.dispatch(EVENT_QUESTION_SHOWN, {"round": 1})

    assert pl._entity_ids_override is None
    assert _light_turn_ons(light_calls)[0]["entity_id"] == ["light.config_entry"]


def test_blank_light_entities_are_cleaned_and_fall_back(light_calls):
    """A list of only whitespace normalizes to None → fall back, don't break."""
    hass = _FakeHass()
    pl = _lights(hass, entities=("light.config_entry",))
    _configure_lights(pl, enabled=True, light_entities=["", "   "])

    hass.bus.dispatch(EVENT_QUESTION_SHOWN, {"round": 1})

    assert pl._entity_ids_override is None
    assert _light_turn_ons(light_calls)[0]["entity_id"] == ["light.config_entry"]


def test_winner_scene_entity_override_wins_and_empty_falls_back(light_calls):
    hass = _FakeHass()
    pl = _lights(hass, finale_scene="scene.from_config")

    _configure_lights(pl, enabled=True, winner_scene_entity="scene.from_panel")
    hass.bus.dispatch(EVENT_WINNER_DECIDED, {"winner_name": "Ann"})
    assert _scene_turn_ons(light_calls) == [{"entity_id": "scene.from_panel"}]

    # An empty override falls back to the config-entry scene.
    light_calls.clear()
    _configure_lights(pl, enabled=True, winner_scene_entity="")
    hass.bus.dispatch(EVENT_WINNER_DECIDED, {"winner_name": "Ann"})
    assert _scene_turn_ons(light_calls) == [{"entity_id": "scene.from_config"}]


def test_media_player_override_wins_and_empty_falls_back(sfx_calls):
    hass = _FakeHass()
    sfx = _sfx(hass, media_player="media_player.config_entry")

    _configure_sfx(sfx, enabled=True, media_player="media_player.panel_pick")
    hass.bus.dispatch(EVENT_STREAK_MILESTONE, {"streak": 5})
    assert sfx_calls[-1][2]["entity_id"] == "media_player.panel_pick"

    # Empty → back to the config-entry speaker.
    _configure_sfx(sfx, enabled=True, media_player="")
    hass.bus.dispatch(EVENT_STREAK_MILESTONE, {"streak": 5})
    assert sfx._media_player_override is None
    assert sfx_calls[-1][2]["entity_id"] == "media_player.config_entry"


def test_light_override_configures_a_bare_install(light_calls):
    """No lights in the options flow + a panel pick ⇒ the accents come alive.

    ``attach_events`` had nothing to attach to at setup (is_configured was
    False), so ``configure()`` must re-run it or the panel's picks would be inert.
    """
    hass = _FakeHass()
    pl = QuizifyPartyLights(
        hass=hass, entity_ids=[], game_state=_FakeGame()
    )  # nothing configured
    pl._last_phase = GamePhase.QUESTION_ACTIVE
    pl.attach_events()
    assert pl.is_configured is False
    assert hass.bus.listeners == {}  # nothing subscribed yet

    _configure_lights(pl, enabled=True, light_entities=["light.panel_pick"])

    assert pl.is_configured is True
    hass.bus.dispatch(EVENT_QUESTION_SHOWN, {"round": 1})
    assert _light_turn_ons(light_calls)[0]["entity_id"] == ["light.panel_pick"]


def test_sfx_override_configures_a_bare_install(sfx_calls):
    """Same for a speaker picked in the panel on an install with none configured."""
    hass = _FakeHass()
    sfx = QuizifySoundEffects(
        hass=hass, media_player_entity_id=None, game_state=_FakeGame(), cue_urls={}
    )
    sfx.attach_events()
    assert sfx.is_configured is False
    assert hass.bus.listeners == {}

    _configure_sfx(sfx, enabled=True, media_player="media_player.panel_pick")

    assert sfx.is_configured is True
    hass.bus.dispatch(EVENT_STREAK_MILESTONE, {"streak": 5})
    assert _played_cues(sfx_calls) == ["streak"]


def test_dev_safe_no_hass_stays_a_noop(light_calls, sfx_calls):
    """No hass (standalone dev server) → configure() changes nothing."""
    pl = QuizifyPartyLights(hass=None, entity_ids=["light.x"], game_state=_FakeGame())
    _configure_lights(pl, enabled=True, light_entities=["light.y"])
    assert pl.is_configured is False
    pl._on_question_shown(_FakeEvent({}))
    assert light_calls == []

    sfx = QuizifySoundEffects(
        hass=None,
        media_player_entity_id="media_player.x",
        game_state=_FakeGame(),
        cue_urls={},
    )
    _configure_sfx(sfx, enabled=True)
    assert sfx.is_configured is False
    sfx._on_streak_milestone(_FakeEvent({}))
    assert sfx_calls == []


def test_sfx_still_drops_a_cue_while_the_speaker_is_mid_tts(sfx_calls):
    """The panel must not weaken the existing TTS-contention behaviour."""
    hass = _FakeHass()
    sfx = _sfx(hass)
    _configure_sfx(sfx, enabled=True)
    hass.states.set("media_player.tv", "playing")  # TTS owns the speaker

    hass.bus.dispatch(EVENT_STREAK_MILESTONE, {"streak": 5})

    assert _played_cues(sfx_calls) == []


def test_sfx_tts_contention_follows_the_overridden_speaker(sfx_calls):
    """The 'is it playing?' probe must look at the ACTIVE speaker, not the stale
    config-entry one — otherwise an overridden speaker would talk over TTS."""
    hass = _FakeHass()
    sfx = _sfx(hass, media_player="media_player.config_entry")
    _configure_sfx(sfx, enabled=True, media_player="media_player.panel_pick")
    hass.states.set("media_player.panel_pick", "playing")
    hass.states.set("media_player.config_entry", "idle")

    hass.bus.dispatch(EVENT_STREAK_MILESTONE, {"streak": 5})

    assert _played_cues(sfx_calls) == []


# ---------------------------------------------------------------------------
# The event emitter — master only, NO per-event toggles ("events only" preset)
# ---------------------------------------------------------------------------


def test_events_only_shape_still_fires_bus_events(light_calls, sfx_calls):
    """Master ON + every light/SFX toggle OFF = the "events only" preset.

    The house stays dark and silent, but the bus events keep flowing so the
    host's own automations/blueprints (the #366 public API) still fire.
    """
    hass = _FakeHass()
    game = _FakeGame()
    emitter = QuizifyEventEmitter(hass=hass, game_state=game, enabled=False)
    pl = _lights(hass, finale_scene="scene.victory")
    sfx = _sfx(hass)

    emitter.configure(enabled=True)
    _configure_lights(
        pl,
        enabled=True,
        light_question=False,
        light_countdown=False,
        light_reveal=False,
        light_streak=False,
        light_winner=False,
        winner_scene=False,
    )
    _configure_sfx(
        sfx,
        enabled=True,
        sfx_correct=False,
        sfx_wrong=False,
        sfx_streak=False,
        sfx_winner=False,
    )

    # Drive a milestone through the emitter's real forwarder.
    emitter.notify_streak_milestone("Ann", 5, 50)
    # …and let the consumers see the very events it fired.
    _fire_all_light_events(hass)
    _fire_all_sfx_events(hass)

    # The bus saw the event…
    assert [t for t, _ in hass.bus.fired] == [EVENT_STREAK_MILESTONE]
    # …while Quizify's own house consumers stayed completely quiet.
    assert light_calls == []
    assert _played_cues(sfx_calls) == []


def test_emitter_configure_overrides_the_config_entry_master():
    """The panel master flips the backbone on/off at runtime, both directions."""
    hass = _FakeHass()
    emitter = QuizifyEventEmitter(hass=hass, game_state=_FakeGame(), enabled=False)
    assert emitter.is_configured is False

    emitter.configure(enabled=True)
    assert emitter.is_configured is True
    emitter.notify_streak_milestone("Ann", 5, 50)
    assert [t for t, _ in hass.bus.fired] == [EVENT_STREAK_MILESTONE]

    # And back off again — the _fire() choke point goes silent.
    emitter.configure(enabled=False)
    emitter.notify_streak_milestone("Ann", 10, 100)
    assert [t for t, _ in hass.bus.fired] == [EVENT_STREAK_MILESTONE]


def test_emitter_master_gates_the_phase_driven_path_too():
    """configure(False) must silence the state-callback path, not just notify_*."""
    hass = _FakeHass()
    game = _FakeGame()
    emitter = QuizifyEventEmitter(hass=hass, game_state=game, enabled=True)
    emitter.configure(enabled=False)

    game.round = 1
    game.phase = GamePhase.QUESTION_ACTIVE
    emitter._on_state_changed()

    assert hass.bus.fired == []


def test_emitter_has_no_per_event_toggles():
    """Guard the contract: per-event gating must NOT leak onto the emitter.

    The bus events are the host's automation API — they stay on for user
    blueprints even when Quizify's own consumers are off. If someone later adds
    ``light_reveal``-style kwargs here, that promise silently breaks.
    """
    hass = _FakeHass()
    emitter = QuizifyEventEmitter(hass=hass, game_state=_FakeGame(), enabled=True)
    with pytest.raises(TypeError):
        emitter.configure(enabled=True, light_reveal=False)  # type: ignore[call-arg]


# ---------------------------------------------------------------------------
# Options-reload continuity — the unit half (#411 / #494 P4, rebuilt for #789)
# ---------------------------------------------------------------------------
#
# These used to drive an export/restore_runtime_config pair on each consumer:
# an options reload rebuilt every house consumer from the config entry, so the
# panel's toggles and entity overrides had to be snapshotted onto the fresh
# instance or they were silently wiped mid-game. There is no rebuild any more —
# the consumers read their config-entry defaults through a shared HouseSettings
# that the listener refreshes in place — so the same guarantees are asserted
# against the real mechanism: change the options, and the LIVE consumer must
# see the new defaults while keeping every panel override.


def _reload(settings, **options):
    """Do what ``__init__._update_listener`` does: refresh the shared defaults."""
    settings.update_from_options(options)


def test_reload_keeps_the_panel_config_on_the_live_lights():
    hass = _FakeHass()
    settings = HouseSettings.from_options(
        {
            "party_light_entities": ["light.entry"],
            "finale_scene": "scene.entry",
        }
    )
    pl = QuizifyPartyLights(hass=hass, game_state=_FakeGame(), settings=settings)
    _configure_lights(
        pl,
        enabled=True,
        light_question=False,
        light_streak=False,
        winner_scene=False,
        light_entities=["light.panel"],
        winner_scene_entity="scene.panel",
    )

    # An unrelated options change — house events are still off at the entry.
    _reload(
        settings,
        party_light_entities=["light.entry"],
        finale_scene="scene.entry",
        lobby_music_url="http://a.test/x.mp3",
    )

    assert pl._master_enabled is True
    assert pl._light_question is False
    assert pl._light_streak is False
    assert pl._light_countdown is True  # untouched toggles keep their value
    assert pl._winner_scene is False
    assert pl._active_entity_ids == ["light.panel"]
    assert pl._active_finale_scene == "scene.panel"


def test_reload_keeps_the_panel_config_on_the_live_sfx():
    hass = _FakeHass()
    settings = HouseSettings.from_options(
        {"media_player_entity": "media_player.entry"}
    )
    sfx = QuizifySoundEffects(hass=hass, game_state=_FakeGame(), settings=settings)
    sfx.attach_events()
    _configure_sfx(
        sfx,
        enabled=True,
        sfx_correct=False,
        sfx_winner=False,
        media_player="media_player.panel",
    )

    _reload(settings, media_player_entity="media_player.entry")

    assert sfx._master_enabled is True
    assert sfx._cue_enabled == {
        "correct": False,
        "wrong": True,
        "streak": True,
        "winner": False,
    }
    assert sfx._active_media_player == "media_player.panel"


def test_reload_keeps_the_emitter_master_and_its_phase_dedupe():
    hass = _FakeHass()
    game = _FakeGame()
    settings = HouseSettings(house_enabled=False)
    emitter = QuizifyEventEmitter(hass=hass, game_state=game, settings=settings)
    emitter.configure(enabled=True)
    game.round = 1
    game.phase = GamePhase.QUESTION_ACTIVE
    emitter._on_state_changed()  # sets _last_phase, fires game_started
    assert [t for t, _ in hass.bus.fired] == [EVENT_GAME_STARTED]

    _reload(settings)

    # The panel's master survived…
    assert emitter._master_enabled is True
    # …and so did the phase dedupe (no duplicate game_started).
    hass.bus.fired.clear()
    emitter._on_state_changed()
    assert hass.bus.fired == []


def test_reload_refreshes_the_config_entry_defaults_underneath():
    """The other half: the NEW options genuinely land on the live consumers."""
    hass = _FakeHass()
    settings = HouseSettings.from_options(
        {"party_light_entities": ["light.old"], "media_player_entity": "mp.old"}
    )
    pl = QuizifyPartyLights(hass=hass, game_state=_FakeGame(), settings=settings)
    sfx = QuizifySoundEffects(hass=hass, game_state=_FakeGame(), settings=settings)
    assert pl._active_entity_ids == ["light.old"]
    assert sfx._active_media_player == "mp.old"

    _reload(
        settings,
        party_light_entities=["light.new"],
        media_player_entity="mp.new",
        finale_scene="scene.new",
        sfx_correct_url="http://a.test/correct.mp3",
    )

    assert pl._active_entity_ids == ["light.new"]
    assert pl._active_finale_scene == "scene.new"
    assert sfx._active_media_player == "mp.new"
    assert sfx._cue_urls["correct"] == "http://a.test/correct.mp3"


def test_untouched_master_stays_none_so_the_config_entry_still_wins():
    """The tri-state master is the subtle bit (#494 P4 + #411).

    An unset panel override means the config-entry switch is still in charge, so
    a host who flips CONF_HOUSE_EVENTS_ENABLED in the options UI sees it take
    effect immediately. Coercing that ``None`` to ``False`` anywhere on the path
    would pin the master off forever.
    """
    hass = _FakeHass()
    settings = HouseSettings.from_options({"party_light_entities": ["light.x"]})
    pl = QuizifyPartyLights(hass=hass, game_state=_FakeGame(), settings=settings)
    ev = QuizifyEventEmitter(hass=hass, game_state=_FakeGame(), settings=settings)
    sfx = QuizifySoundEffects(hass=hass, game_state=_FakeGame(), settings=settings)
    # Never configure()d — the panel was never opened.
    assert pl._enabled_override is None
    assert pl._master_enabled is False

    _reload(settings, party_light_entities=["light.x"], house_events_enabled=True)

    assert pl._enabled_override is None
    assert pl._master_enabled is True
    assert ev._master_enabled is True
    assert sfx._master_enabled is True


def test_panel_master_off_survives_a_config_entry_master_on():
    """The mirror case: an explicit panel OFF must not be undone by a reload."""
    hass = _FakeHass()
    settings = HouseSettings.from_options(
        {"party_light_entities": ["light.x"], "house_events_enabled": True}
    )
    pl = QuizifyPartyLights(hass=hass, game_state=_FakeGame(), settings=settings)
    _configure_lights(pl, enabled=False)  # host switched the house off mid-game
    assert pl._enabled_override is False

    _reload(settings, party_light_entities=["light.x"], house_events_enabled=True)

    assert pl._master_enabled is False


def test_the_house_master_is_one_switch_across_the_three_consumers():
    """The panel presents one master over three subsystems (#789).

    It used to be three private copies kept in sync by three separate
    ``configure()`` calls in the same frame; it is now one field on the shared
    settings, so a partial fan-out can no longer leave them disagreeing.
    """
    hass = _FakeHass()
    settings = HouseSettings.from_options({"party_light_entities": ["light.x"]})
    pl = QuizifyPartyLights(hass=hass, game_state=_FakeGame(), settings=settings)
    ev = QuizifyEventEmitter(hass=hass, game_state=_FakeGame(), settings=settings)
    sfx = QuizifySoundEffects(hass=hass, game_state=_FakeGame(), settings=settings)

    ev.configure(enabled=True)

    assert (pl._master_enabled, ev._master_enabled, sfx._master_enabled) == (
        True,
        True,
        True,
    )
