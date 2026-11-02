"""One speaker, one voice (#1185, P2).

Voice and room sounds share a speaker. At the reveal the sting used to start
while the TTS engine was still generating the sentence, and the voice cut it
off a second later. Now: with the voice on it owns the speaker — no sting, no
streak chime — and the winner fanfare waits for the last sentence. With the
voice off a reveal is ONE sound: the streak chime if somebody hit a milestone,
else the sting. The lights may run alongside; the streak pulse queues behind
the reveal flash instead of cancelling it.
"""

from __future__ import annotations

import asyncio
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
    EVENT_STREAK_MILESTONE,
    EVENT_WINNER_DECIDED,
)
from custom_components.quizify.house_settings import HouseSettings  # noqa: E402
from custom_components.quizify.lights import QuizifyPartyLights  # noqa: E402
from custom_components.quizify.sound_effects import QuizifySoundEffects  # noqa: E402
from custom_components.quizify.tts import QuizifyTTSAnnouncer  # noqa: E402
from tests.signal_fakes import FakeHass  # noqa: E402

_SPEAKER = "media_player.wohnzimmer"


class _Game:
    round = 3
    language = "de"

    def register_state_callback(self, _cb):
        pass

    def unregister_state_callback(self, _cb):
        pass


def _settings() -> HouseSettings:
    return HouseSettings(
        house_enabled=True,
        light_entities=["light.a"],
        media_player=_SPEAKER,
        tts_entity="tts.cloud",
        cue_urls={c: f"http://x/{c}.mp3" for c in ("correct", "wrong", "streak", "winner")},
    )


def _sounds(hass: FakeHass, settings: HouseSettings) -> QuizifySoundEffects:
    sounds = QuizifySoundEffects(hass, _Game(), settings=settings)
    sounds.attach_events()
    return sounds


def _played(hass: FakeHass) -> list[str]:
    return [
        s.data["media_content_id"].rsplit("/", 1)[-1]
        for s in hass.of("media_player.play_media")
    ]


def _reveal(hass: FakeHass, *, milestones: int = 0, correct: int = 3) -> None:
    hass.bus.async_fire(
        EVENT_ANSWER_REVEALED, {"correct_count": correct, "total_players": 4}
    )
    for i in range(milestones):
        hass.bus.async_fire(
            EVENT_STREAK_MILESTONE, {"player_name": f"P{i}", "streak": 3, "bonus": 25}
        )


def _voice_on(hass: FakeHass, settings: HouseSettings) -> QuizifyTTSAnnouncer:
    announcer = QuizifyTTSAnnouncer(hass, _Game(), settings=settings)
    announcer.configure(
        enabled=True, announce_question=True, announce_reveal=True, announce_standings=True
    )
    return announcer


@pytest.mark.asyncio
async def test_no_sting_while_the_voice_is_on() -> None:
    hass, settings = FakeHass(), _settings()
    _sounds(hass, settings)
    _voice_on(hass, settings)
    assert settings.voice_active is True
    _reveal(hass, milestones=1)
    await hass.settle()
    assert _played(hass) == []
    await hass.drain()


@pytest.mark.asyncio
async def test_voice_off_reveal_plays_its_sting() -> None:
    hass, settings = FakeHass(), _settings()
    _sounds(hass, settings)
    _reveal(hass)
    await hass.settle()
    assert _played(hass) == ["correct.mp3"]
    await hass.drain()


@pytest.mark.asyncio
async def test_voice_off_a_streak_does_not_cut_off_the_sting() -> None:
    """One sound per moment: a reveal with two milestones is one sting."""
    hass, settings = FakeHass(), _settings()
    _sounds(hass, settings)
    _reveal(hass, milestones=2)
    await hass.settle()
    assert _played(hass) == ["correct.mp3"]


@pytest.mark.asyncio
async def test_voice_off_reveal_cue_off_rings_the_chime_once() -> None:
    hass, settings = FakeHass(), _settings()
    sounds = _sounds(hass, settings)
    sounds.configure(enabled=True, sfx_correct=False)
    _reveal(hass, milestones=2)
    await hass.settle()
    assert _played(hass) == ["streak.mp3"]
    await hass.drain()


@pytest.mark.asyncio
async def test_voice_off_streak_cue_off_still_stings() -> None:
    hass, settings = FakeHass(), _settings()
    sounds = _sounds(hass, settings)
    sounds.configure(enabled=True, sfx_streak=False)
    _reveal(hass, milestones=1, correct=0)
    await hass.settle()
    assert _played(hass) == ["wrong.mp3"]
    await hass.drain()


@pytest.mark.asyncio
async def test_voice_switched_off_gives_the_speaker_back() -> None:
    hass, settings = FakeHass(), _settings()
    _sounds(hass, settings)
    announcer = _voice_on(hass, settings)
    announcer.configure(
        enabled=False, announce_question=True, announce_reveal=True, announce_standings=True
    )
    assert settings.voice_active is False
    _reveal(hass)
    await hass.settle()
    assert _played(hass) == ["correct.mp3"]
    await hass.drain()


@pytest.mark.asyncio
async def test_fanfare_waits_for_the_last_sentence(monkeypatch) -> None:
    monkeypatch.setattr(sfx_mod, "_SPEECH_POLL_S", 0.01)
    hass, settings = FakeHass(), _settings()
    _sounds(hass, settings)
    _voice_on(hass, settings)

    hass.bus.async_fire(EVENT_WINNER_DECIDED, {"winner_name": "Lena", "score": 90})
    await asyncio.sleep(0.03)
    assert _played(hass) == []  # the speech has not even started

    hass.states.set(_SPEAKER, "playing")  # "Das Spiel ist vorbei …"
    await asyncio.sleep(0.05)
    assert _played(hass) == []  # still talking

    hass.states.set(_SPEAKER, "idle")
    await asyncio.sleep(0.05)
    assert _played(hass) == ["winner.mp3"]
    await hass.drain()


@pytest.mark.asyncio
async def test_fanfare_does_not_wait_forever(monkeypatch) -> None:
    """A speaker that never reports playing delays the fanfare, it does not
    swallow it."""
    monkeypatch.setattr(sfx_mod, "_SPEECH_POLL_S", 0.01)
    monkeypatch.setattr(sfx_mod, "_SPEECH_START_S", 0.03)
    hass, settings = FakeHass(), _settings()
    _sounds(hass, settings)
    _voice_on(hass, settings)
    hass.bus.async_fire(EVENT_WINNER_DECIDED, {"winner_name": "Lena", "score": 90})
    await asyncio.sleep(0.1)
    assert _played(hass) == ["winner.mp3"]
    await hass.drain()


@pytest.mark.asyncio
async def test_fanfare_plays_at_once_without_voice() -> None:
    hass, settings = FakeHass(), _settings()
    _sounds(hass, settings)
    hass.bus.async_fire(EVENT_WINNER_DECIDED, {"winner_name": "Lena", "score": 90})
    await hass.settle()
    assert _played(hass) == ["winner.mp3"]
    await hass.drain()


@pytest.mark.asyncio
async def test_streak_light_queues_behind_the_reveal_flash(monkeypatch) -> None:
    """Green first, then the double pulse — one streak pulse per reveal."""
    monkeypatch.setattr(lights_mod, "_STREAK_AFTER_REVEAL_S", 0.3)
    hass, settings = FakeHass(), _settings()
    lights = QuizifyPartyLights(hass, _Game(), settings=settings)
    lights.attach_events()
    lights._last_phase = GamePhase.PAUSED  # no baseline recipe: accents only
    _reveal(hass, milestones=2)
    await asyncio.sleep(1.3)
    colours = [
        tuple(s.data.get("rgb_color") or ())
        for s in hass.of("light.turn_on")
        if s.data.get("rgb_color")
    ]
    assert colours[0] == (120, 200, 140)  # the green reveal flash showed
    assert colours.count((255, 150, 120)) == 2  # one double pulse, not two
    await hass.drain()
