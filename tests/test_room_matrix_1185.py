"""The room as a moment × channel matrix, and the probe (#1185, P10).

Seventeen switches in two steps became one table: one row per moment
(question, countdown, reveal, streak, lead, new players, winner), one column
per channel (voice, light, sound). The keys did not change — every cell is one
of the old checkboxes — so stored setups, ``configure_tts`` / ``configure_house``
and ``start_game`` read exactly as before.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO))

from custom_components.quizify.house_settings import HouseSettings  # noqa: E402
from custom_components.quizify.lights import QuizifyPartyLights  # noqa: E402
from custom_components.quizify.server.settings_handlers import (  # noqa: E402
    handle_house_probe,
)
from custom_components.quizify.sound_effects import QuizifySoundEffects  # noqa: E402
from custom_components.quizify.tts import QuizifyTTSAnnouncer  # noqa: E402
from tests.signal_fakes import FakeHass  # noqa: E402

_WWW = _REPO / "custom_components" / "quizify" / "www"
_HTML = (_WWW / "admin.html").read_text("utf-8")
_MATRIX = _HTML.split('id="room-matrix"', 1)[1].split("</table>", 1)[0]

# row -> {column: checkbox id or None}
_CELLS = {
    "rowQuestion": {"voice": "tts-announce-question", "light": "house-light-question", "sound": None},
    "rowCountdown": {"voice": "tts-announce-countdown", "light": "house-light-countdown", "sound": None},
    "rowReveal": {"voice": "tts-announce-reveal", "light": "house-light-reveal", "sound": "house-sfx-correct"},
    "rowStreak": {"voice": "tts-announce-milestone", "light": "house-light-streak", "sound": "house-sfx-streak"},
    "rowLead": {"voice": "tts-announce-standings", "light": None, "sound": None},
    "rowJoin": {"voice": "tts-announce-join", "light": None, "sound": None},
    "rowWinner": {"voice": None, "light": "house-light-winner", "sound": "house-sfx-winner"},
}


def _row(key: str) -> str:
    start = _MATRIX.index(f'data-i18n="setup.room.{key}"')
    return _MATRIX[start : _MATRIX.index("</tr>", start)]


@pytest.mark.parametrize("row", sorted(_CELLS))
def test_each_cell_is_the_old_key(row: str) -> None:
    html = _row(row)
    for col, cb in _CELLS[row].items():
        cell = re.search(rf'<td data-col="{col}"[^>]*>(.*?)</td>', html, re.S)
        assert cell, (row, col)
        if cb is None:
            assert 'type="checkbox"' not in cell.group(1) or "hidden" in cell.group(1)
        else:
            assert f'id="{cb}"' in cell.group(1), (row, col)


def test_every_old_toggle_still_exists_once() -> None:
    for key in (
        "tts-enable-toggle", "house-enable-toggle", "tts-announce-options",
        "house-sfx-wrong", "house-winner-scene", "house-lights-follow",
        "tts-engine-select", "game-speaker-select", "house-speaker-select",
        "house-light-list", "house-scene-select",
    ):
        assert _HTML.count(f'id="{key}"') == 1, key


def test_countdown_sound_is_the_phone_and_off_by_default() -> None:
    row = _row("rowCountdown")
    assert 'data-i18n="setup.room.phone"' in row
    assert '<input type="checkbox" id="tts-announce-countdown">' in row


def test_presets_are_off_quiet_show_events() -> None:
    presets = re.findall(r'data-preset="([a-z_]+)"', _HTML.split('id="house-presets"', 1)[1].split("</div>", 1)[0])
    assert presets == ["off", "quiet_evening", "game_show", "events_only"]


def test_both_stings_follow_the_one_reveal_cell() -> None:
    js = (_WWW / "js" / "admin.js").read_text("utf-8")
    assert "_houseEls.sfx_wrong.checked = _houseEls.sfx_correct.checked" in js


@pytest.mark.asyncio
async def test_the_probe_plays_a_reveal_and_fires_no_event() -> None:
    hass = FakeHass()
    settings = HouseSettings(
        house_enabled=True, light_entities=["light.a"], media_player="media_player.s",
        tts_entity="tts.x", cue_urls={c: f"http://x/{c}.mp3" for c in ("correct", "wrong", "streak", "winner")},
    )
    game = SimpleNamespace(language="de", round=0, register_state_callback=lambda cb: None)
    tts = QuizifyTTSAnnouncer(hass, game, settings=settings)
    lights = QuizifyPartyLights(hass, game, settings=settings)
    sounds = QuizifySoundEffects(hass, game, settings=settings)
    handler = SimpleNamespace(_tts_announcer=tts, _party_lights=lights, _sound_effects=sounds)

    # Voice off: light + sting.
    await handle_house_probe(handler, None, {}, None)
    await hass.settle()
    assert "media_player.play_media" in hass.names()
    assert "light.turn_on" in hass.names()
    assert "tts.speak" not in hass.names()
    hass.clear()

    # Voice on: the sentence, no sting (one speaker, one voice).
    tts.configure(enabled=True, announce_question=True, announce_reveal=True, announce_standings=True)
    await handle_house_probe(handler, None, {}, None)
    await hass.settle()
    speech = hass.of("tts.speak")
    assert len(speech) == 1 and "3 in Folge" in speech[0].data["message"]
    assert "media_player.play_media" not in hass.names()
    assert not [s for s in hass.signals if s.kind == "event"]
    await hass.drain()
