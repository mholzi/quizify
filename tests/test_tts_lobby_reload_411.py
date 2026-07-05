"""Unit regression for the options-reload continuity fix (issue #411).

The ``_update_listener`` in ``custom_components.quizify.__init__`` rebuilds the
TTS announcer and lobby-music helper from the config entry on every options
change. That reset silently broke two mid-game behaviours:

* the fresh announcer started ``_enabled=False`` with empty per-game overrides,
  killing narration until the next ``start_game``;
* the fresh lobby-music instance started ``_last_phase=None`` and only reacted
  to a phase *change*, so LOBBY music never resumed after a reload while still
  in the lobby.

These drive the announcer/lobby helpers directly with a fake hass (no Home
Assistant install needed), so they run everywhere — mirroring the harness in
``test_tts_announcer_281``.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT))

from custom_components.quizify.game.state import (  # noqa: E402
    GamePhase,
    QuizifyGameState,
)
from custom_components.quizify.lobby_music import QuizifyLobbyMusic  # noqa: E402
from custom_components.quizify.tts import QuizifyTTSAnnouncer  # noqa: E402


class _FakeHass:
    """Records service calls so tests can assert playback/narration intent."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str, dict]] = []
        self.states = MagicMock()
        self.states.get = lambda eid: MagicMock(state="on")
        self.services = MagicMock()

        async def _async_call(domain, service, data, blocking=False):  # noqa: ANN001
            self.calls.append((domain, service, dict(data)))

        self.services.async_call = _async_call

    def async_create_task(self, coro):  # noqa: ANN001
        return asyncio.ensure_future(coro)


class _FakeRuntime:
    def __init__(self, tmp_path: Path) -> None:
        self.data_dir = tmp_path


@pytest.fixture
def game(tmp_path):
    return QuizifyGameState(runtime=_FakeRuntime(tmp_path), entry_id="t")


def _reload_announcer(
    old: QuizifyTTSAnnouncer, hass, game
) -> QuizifyTTSAnnouncer:
    """Mimic the __init__ listener: snapshot old → rebuild → restore."""
    snapshot = old.export_runtime_config()
    old.detach()
    new = QuizifyTTSAnnouncer(
        hass=hass,
        tts_entity_id="tts.cloud",
        media_player_entity_id="media_player.kitchen",
        game_state=game,
    )
    new.restore_runtime_config(snapshot)
    new.attach()
    return new


def test_reload_preserves_enabled_and_overrides(game):
    hass = _FakeHass()
    old = QuizifyTTSAnnouncer(
        hass=hass,
        tts_entity_id="tts.cloud",
        media_player_entity_id="media_player.kitchen",
        game_state=game,
    )
    old.attach()
    old.configure(
        enabled=True,
        announce_question=True,
        announce_reveal=False,
        announce_standings=True,
        announce_options=False,
        announce_join=True,
        announce_countdown=False,
        tts_entity="tts.game_engine",
        media_player="media_player.party",
    )

    new = _reload_announcer(old, hass, game)

    assert new is not old
    assert new._enabled is True
    assert new._announce_reveal is False
    assert new._announce_options is False
    assert new._announce_countdown is False
    assert new._active_tts_entity == "tts.game_engine"
    assert new._active_media_player == "media_player.party"


def test_restore_runtime_config_is_defensive(game):
    hass = _FakeHass()
    tts = QuizifyTTSAnnouncer(
        hass=hass,
        tts_entity_id="tts.cloud",
        media_player_entity_id="media_player.kitchen",
        game_state=game,
    )
    tts._enabled = True
    # An empty / None snapshot must never clobber the current config.
    tts.restore_runtime_config(None)
    tts.restore_runtime_config({})
    assert tts._enabled is True
    # A partial snapshot only touches the keys it carries.
    tts.restore_runtime_config({"announce_join": False})
    assert tts._enabled is True
    assert tts._announce_join is False


async def test_lobby_music_resumes_on_attach_in_lobby(game):
    hass = _FakeHass()
    game.lobby_music_url = "http://example.test/lobby.mp3"
    assert game.phase == GamePhase.LOBBY

    lm = QuizifyLobbyMusic(
        hass=hass,
        media_player_entity_id="media_player.kitchen",
        game_state=game,
    )
    # attach() evaluates the current phase once (#411) — no phase change needed.
    lm.attach()
    await asyncio.sleep(0)  # let the fire-and-forget service call run

    assert lm._playing is True
    services = [(d, s) for d, s, _ in hass.calls]
    assert ("media_player", "play_media") in services


async def test_lobby_music_attach_noop_when_not_in_lobby(game):
    hass = _FakeHass()
    game.lobby_music_url = "http://example.test/lobby.mp3"
    game.phase = GamePhase.QUESTION_ACTIVE

    lm = QuizifyLobbyMusic(
        hass=hass,
        media_player_entity_id="media_player.kitchen",
        game_state=game,
    )
    lm.attach()
    await asyncio.sleep(0)

    assert lm._playing is False
    assert hass.calls == []
