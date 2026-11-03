"""Volume and the quiet evening (#1185, P6).

Quizify may set the speaker volume before an announcement and restore it after,
but only when the host moved the volume slider. The "quiet evening" preset
caps the lights at 60 % and the volume at 30 %; after 21:00 the setup suggests
it with a chip and never switches on its own.
"""

from __future__ import annotations

import asyncio
import re
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO))

from custom_components.quizify import speaker_volume as sv_mod  # noqa: E402
from custom_components.quizify.house_settings import HouseSettings  # noqa: E402
from custom_components.quizify.lights import QuizifyPartyLights  # noqa: E402
from custom_components.quizify.server.settings_handlers import (  # noqa: E402
    _pct_or_none,
    apply_house_config,
)
from custom_components.quizify.speaker_volume import SpeakerVolume  # noqa: E402
from custom_components.quizify.tts import QuizifyTTSAnnouncer  # noqa: E402
from tests.signal_fakes import FakeHass  # noqa: E402

_SPEAKER = "media_player.wohnzimmer"
_JS = _REPO / "custom_components" / "quizify" / "www" / "js"


class _Game:
    language = "de"
    round = 1

    def register_state_callback(self, _cb):
        pass

    def unregister_state_callback(self, _cb):
        pass


def _announcer(hass, settings):
    t = QuizifyTTSAnnouncer(hass, _Game(), settings=settings)
    t.configure(enabled=True, announce_question=True, announce_reveal=True,
                announce_standings=True, announce_join=True)
    t.speaker_volume = SpeakerVolume(hass, settings)
    return t


@pytest.mark.asyncio
async def test_an_untouched_slider_never_touches_the_speaker() -> None:
    hass = FakeHass()
    settings = HouseSettings(tts_entity="tts.x", media_player=_SPEAKER)
    _announcer(hass, settings).announce_join("Lena")
    await hass.settle()
    assert hass.names() == ["tts.speak"]
    await hass.drain()


@pytest.mark.asyncio
async def test_a_moved_slider_sets_before_and_restores_after(monkeypatch) -> None:
    monkeypatch.setattr(sv_mod, "_POLL_S", 0.01)
    monkeypatch.setattr(sv_mod, "_SETTLE_S", 0.03)
    hass = FakeHass()
    hass.states.set(_SPEAKER, "idle", volume_level=0.8)
    settings = HouseSettings(tts_entity="tts.x", media_player=_SPEAKER, volume_pct=35)
    _announcer(hass, settings).announce_join("Lena")
    await hass.settle()
    assert hass.names() == ["media_player.volume_set", "tts.speak"]
    assert hass.of("media_player.volume_set")[0].data == {
        "entity_id": _SPEAKER, "volume_level": 0.35,
    }

    hass.states.set(_SPEAKER, "playing", volume_level=0.35)
    await asyncio.sleep(0.05)
    assert len(hass.of("media_player.volume_set")) == 1  # still talking
    hass.states.set(_SPEAKER, "idle", volume_level=0.35)
    await asyncio.sleep(0.1)
    restores = hass.of("media_player.volume_set")
    assert len(restores) == 2
    assert restores[1].data["volume_level"] == 0.8
    await hass.drain()


@pytest.mark.asyncio
async def test_two_lines_in_a_row_restore_once_to_the_first_level(monkeypatch) -> None:
    monkeypatch.setattr(sv_mod, "_POLL_S", 0.01)
    monkeypatch.setattr(sv_mod, "_START_S", 0.02)
    monkeypatch.setattr(sv_mod, "_SETTLE_S", 0.02)
    hass = FakeHass()
    hass.states.set(_SPEAKER, "idle", volume_level=0.6)
    settings = HouseSettings(volume_pct=30)
    vol = SpeakerVolume(hass, settings)
    vol.before_play(_SPEAKER)
    hass.states.set(_SPEAKER, "idle", volume_level=0.3)  # our own set landed
    vol.before_play(_SPEAKER)
    await asyncio.sleep(0.2)
    levels = [s.data["volume_level"] for s in hass.of("media_player.volume_set")]
    assert levels == [0.3, 0.3, 0.6]
    await hass.drain()


@pytest.mark.asyncio
async def test_the_brightness_ceiling_caps_every_light_call() -> None:
    hass = FakeHass()
    settings = HouseSettings(light_entities=["light.a"], max_brightness_pct=60)
    lights = QuizifyPartyLights(hass, _Game(), settings=settings)
    lights._call("light", "turn_on", {"entity_id": ["light.a"], "brightness_pct": 90})
    lights._call("light", "turn_on", {"entity_id": ["light.a"], "brightness_pct": 40})
    await hass.settle()
    assert [s.data["brightness_pct"] for s in hass.of("light.turn_on")] == [60, 40]


@pytest.mark.parametrize(
    ("raw", "pct"),
    [(None, None), (True, None), ("x", None), (35, 35), ("30", 30), (130, 100), (-5, 0)],
)
def test_percent_from_client_json(raw, pct) -> None:
    assert _pct_or_none(raw) == pct


def test_the_house_config_writes_volume_and_ceiling() -> None:
    settings = HouseSettings()
    lights = SimpleNamespace(_settings=settings, configure=lambda **kw: None)
    handler = SimpleNamespace(
        _party_lights=lights, _sound_effects=None, _event_emitter=None
    )
    apply_house_config(handler, {"enabled": True, "volume": 30, "light_max_brightness": 60})
    assert (settings.volume_pct, settings.max_brightness_pct) == (30, 60)
    apply_house_config(handler, {"enabled": True, "volume": None})
    assert (settings.volume_pct, settings.max_brightness_pct) == (None, None)


def test_the_quiet_evening_preset_and_the_chip() -> None:
    src = (_JS / "admin.js").read_text("utf-8")
    assert "QUIET_EVENING_VOLUME = 30" in src
    assert "QUIET_EVENING_BRIGHTNESS = 60" in src
    assert "QUIET_EVENING_FROM_HOUR = 21" in src
    chip = src[src.index("function _syncEveningChip()") :]
    chip = chip[: chip.index("\n    }\n")]
    # It only shows or hides itself — it never applies anything.
    assert "_applyHousePreset" not in chip
    assert re.search(r"volume:\s*null", src), "untouched slider is null by default"
