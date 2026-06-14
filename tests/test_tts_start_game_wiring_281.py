"""Wiring tests for TTS narration through the WS handler (issue #281).

Asserts the three hook points actually fire:
  - start_game carries a ``tts`` block → announcer.configure() applied;
  - _emit_question → the question narration utterance is produced;
  - _dispatch_round_evaluated → the reveal utterance is produced.

Mirrors the harness in test_start_game_fresh.py (real game state + a real
ConnectionManager with broadcasts stubbed), with a real announcer wired to a
fake hass that records tts.speak calls.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT))

from custom_components.quizify.game.state import (  # noqa: E402
    GamePhase,
    QuizifyGameState,
)
from custom_components.quizify.server.connection import ConnectionManager  # noqa: E402
from custom_components.quizify.server.websocket import (  # noqa: E402
    QuizifyWebSocketHandler,
)
from custom_components.quizify.tts import QuizifyTTSAnnouncer  # noqa: E402


class _FakeRuntime:
    def __init__(self, tmp_path: Path) -> None:
        self.data_dir = tmp_path


class _FakeHass:
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


def _ws() -> MagicMock:
    ws = MagicMock()
    ws.closed = False
    ws.send_json = AsyncMock()
    return ws


@pytest.fixture
def game(tmp_path: Path) -> QuizifyGameState:
    return QuizifyGameState(runtime=_FakeRuntime(tmp_path), entry_id="test")


@pytest.fixture
def hass() -> _FakeHass:
    return _FakeHass()


@pytest.fixture
def handler(game, hass, monkeypatch):
    runtime = _FakeRuntime(game._runtime.data_dir)  # type: ignore[attr-defined]
    h = QuizifyWebSocketHandler(runtime=runtime, game_state_provider=lambda: game)
    h._conn = ConnectionManager(runtime, lambda: game)
    h._conn.broadcast = AsyncMock()
    h._conn.send_error = AsyncMock()
    h._conn.broadcast_to_admins_and_dashboards = AsyncMock()
    # Zero the admin-redirect grace instead of patching asyncio.sleep globally:
    # the fire-and-forget TTS task is flushed with a real ``await sleep(0)`` in
    # the tests, so a stubbed sleep would silently swallow the announcement.
    h.START_REDIRECT_GRACE = 0
    monkeypatch.setattr(h, "_start_timer_tick", lambda *a, **k: None)
    # Wire a real announcer (the unit under test) to the fake hass.
    h._tts_announcer = QuizifyTTSAnnouncer(
        hass=hass,
        tts_entity_id="tts.cloud",
        media_player_entity_id="media_player.kitchen",
        game_state=game,
    )
    return h


@pytest.mark.asyncio
async def test_start_game_applies_tts_config_and_narrates_question(
    handler, game, hass
) -> None:
    admin = _ws()
    game.add_player("Markus", admin)
    game.get_player("Markus").is_admin = True

    await handler._handle_start_game(
        admin,
        {
            "category": "geographie",
            "difficulty": None,
            "num_rounds": 5,
            "language": "de",
            "timer_duration": 30,
            "tts": {
                "enabled": True,
                "announce_question": True,
                "announce_reveal": True,
                "announce_standings": True,
            },
        },
        game,
    )

    # configure() applied from the payload.
    ann = handler._tts_announcer
    assert ann._enabled is True
    assert ann._announce_question is True

    # _emit_question ran (start → first question) → question narrated.
    # Flush the fire-and-forget tts.speak task onto the loop.
    await asyncio.sleep(0)
    assert game.phase == GamePhase.QUESTION_ACTIVE
    speak = [c for c in hass.calls if c[1] == "speak"]
    assert len(speak) == 1
    msg = speak[0][2]["message"]
    assert msg.startswith("Frage 1 von 5:")
    assert speak[0][2]["entity_id"] == "tts.cloud"
    assert speak[0][2]["media_player_entity_id"] == "media_player.kitchen"


@pytest.mark.asyncio
async def test_start_game_tts_disabled_stays_silent(handler, game, hass) -> None:
    admin = _ws()
    game.add_player("Markus", admin)
    game.get_player("Markus").is_admin = True

    await handler._handle_start_game(
        admin,
        {
            "category": "geographie",
            "num_rounds": 5,
            "language": "de",
            "timer_duration": 30,
            "tts": {"enabled": False},
        },
        game,
    )
    await asyncio.sleep(0)
    assert handler._tts_announcer._enabled is False
    assert [c for c in hass.calls if c[1] == "speak"] == []


@pytest.mark.asyncio
async def test_missing_tts_block_defaults_disabled(handler, game, hass) -> None:
    admin = _ws()
    game.add_player("Markus", admin)
    game.get_player("Markus").is_admin = True

    await handler._handle_start_game(
        admin,
        {"category": "geographie", "num_rounds": 5, "language": "de"},
        game,
    )
    await asyncio.sleep(0)
    # No tts block → master switch off → silent.
    assert handler._tts_announcer._enabled is False
    assert [c for c in hass.calls if c[1] == "speak"] == []


@pytest.mark.asyncio
async def test_round_evaluated_narrates_reveal(handler, game, hass) -> None:
    admin = _ws()
    game.add_player("Markus", admin)
    game.get_player("Markus").is_admin = True

    await handler._handle_start_game(
        admin,
        {
            "category": "geographie",
            "num_rounds": 5,
            "language": "de",
            "timer_duration": 30,
            "tts": {
                "enabled": True,
                "announce_question": False,  # isolate the reveal utterance
                "announce_reveal": True,
                "announce_standings": True,
            },
        },
        game,
    )
    # Drive the round to a reveal: evaluate the active question, which fires the
    # round_evaluated state event → _dispatch_round_evaluated.
    assert game.phase == GamePhase.QUESTION_ACTIVE
    game.evaluate_round()
    await handler._dispatch_round_evaluated()
    await asyncio.sleep(0)

    speak = [c for c in hass.calls if c[1] == "speak"]
    assert len(speak) == 1
    msg = speak[0][2]["message"]
    # Reveal utterance leads with the correct answer phrase (German).
    assert msg.startswith("Die richtige Antwort ist")
