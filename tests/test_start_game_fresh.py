"""Regression: an explicit admin ``start_game`` must apply the NEW settings
even when a previous game is still running (phase != LOBBY).

Before the fix the phase guard in ``_handle_start_game`` silently rejected the
start, so the OLD game's category/language kept running. Markus 2026-05-31:
picked Geographie/DE but kept seeing the previous English *mixed* game because
that game was never formally reset to LOBBY first.
"""

from __future__ import annotations

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


class _FakeRuntime:
    def __init__(self, tmp_path: Path) -> None:
        self.data_dir = tmp_path


def _ws() -> MagicMock:
    ws = MagicMock()
    ws.closed = False
    ws.send_json = AsyncMock()
    return ws


@pytest.fixture
def game(tmp_path: Path) -> QuizifyGameState:
    return QuizifyGameState(runtime=_FakeRuntime(tmp_path), entry_id="test")


@pytest.fixture
def handler(game: QuizifyGameState, monkeypatch) -> QuizifyWebSocketHandler:
    runtime = _FakeRuntime(game._runtime.data_dir)  # type: ignore[attr-defined]
    h = QuizifyWebSocketHandler(runtime=runtime, game_state_provider=lambda: game)
    h._conn = ConnectionManager(runtime, lambda: game)
    h._conn.broadcast = AsyncMock()
    h._conn.send_error = AsyncMock()
    # _handle_start_game sleeps 2.5s for the admin-redirect grace — skip it.
    monkeypatch.setattr(
        "custom_components.quizify.server.websocket.asyncio.sleep", AsyncMock()
    )
    # The per-question timer tick loop counts real wall-clock time; with sleep
    # stubbed it would busy-spin for the full timer. Neutralise it — these
    # tests only assert the active question's category/language, not the timer.
    monkeypatch.setattr(h, "_start_timer_tick", lambda *a, **k: None)
    return h


@pytest.mark.asyncio
async def test_start_game_while_running_applies_new_settings(
    handler: QuizifyWebSocketHandler, game: QuizifyGameState
) -> None:
    admin = _ws()
    game.add_player("Markus", admin)
    game.get_player("Markus").is_admin = True

    # Earlier English *mixed* game, mid-question.
    game.start_game(category=None, language="en", num_rounds=10)
    game.start_next_question()
    assert game.phase == GamePhase.QUESTION_ACTIVE
    assert game.category is None and game.language == "en"

    # Admin explicitly starts a NEW Geographie/DE game WITHOUT resetting first.
    await handler._handle_start_game(
        admin,
        {
            "category": "geographie",
            "difficulty": None,
            "num_rounds": 10,
            "language": "de",
            "timer_duration": 30,
        },
        game,
    )

    # The new settings must have taken effect (silently dropped before the fix).
    assert game.category == "geographie"
    assert game.language == "de"
    q = game._current_question
    assert q is not None
    assert q.category == "Geographie"
    assert q.language == "de"


@pytest.mark.asyncio
async def test_start_game_from_lobby_still_works(
    handler: QuizifyWebSocketHandler, game: QuizifyGameState
) -> None:
    """Sanity: the normal LOBBY -> start path is unaffected by the fix."""
    admin = _ws()
    game.add_player("Markus", admin)
    game.get_player("Markus").is_admin = True
    assert game.phase == GamePhase.LOBBY

    await handler._handle_start_game(
        admin,
        {
            "category": "geographie",
            "difficulty": None,
            "num_rounds": 5,
            "language": "de",
            "timer_duration": 30,
        },
        game,
    )

    assert game.category == "geographie"
    assert game.language == "de"
    assert game._current_question is not None
    assert game._current_question.category == "Geographie"
