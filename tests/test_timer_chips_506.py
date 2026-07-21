"""Regression: the longer per-question timer options from #506.

An external host (#506) plays with small kids and reported they cannot read the
question plus four answers in time *even at 45 s* — which was the highest value
the admin timer picker offered. The picker now also offers 60/90/120 s.

The ceiling was purely a frontend one: ``_handle_start_game`` has always
accepted 5..300 s and silently dropped anything outside that range back to the
difficulty-derived default. So these tests guard both ends of that contract:

1. the new values actually survive validation and become the round duration
   (a value that got clamped would silently fall back to 20/15/10 s, which is
   exactly the *opposite* of what the host asked for), and
2. every chip rendered in ``admin.html`` stays inside the backend's accepted
   range — so a future chip added to the markup can't silently no-op.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT))

from custom_components.quizify.game.state import QuizifyGameState  # noqa: E402
from custom_components.quizify.server.connection import ConnectionManager  # noqa: E402
from custom_components.quizify.server.websocket import (  # noqa: E402
    QuizifyWebSocketHandler,
)

_ADMIN_HTML = (
    _REPO_ROOT / "custom_components" / "quizify" / "www" / "admin.html"
)

# Mirrors the backend clamp in websocket.py::_handle_start_game.
_MIN_TIMER = 5
_MAX_TIMER = 300


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
    # The tick loop counts real wall-clock time; neutralise it. These tests
    # assert the configured duration, not the countdown itself.
    monkeypatch.setattr(h, "_start_timer_tick", lambda *a, **k: None)
    return h


def _timer_chip_values() -> list[int]:
    """The data-value of every chip inside #timer-chips in admin.html."""
    html = _ADMIN_HTML.read_text(encoding="utf-8")
    group = re.search(
        r'id="timer-chips".*?</div>', html, re.DOTALL
    )
    assert group is not None, "#timer-chips group not found in admin.html"
    return [int(v) for v in re.findall(r'data-value="(\d+)"', group.group(0))]


@pytest.mark.asyncio
@pytest.mark.parametrize("seconds", [60, 90, 120])
async def test_long_timer_reaches_the_round(
    handler: QuizifyWebSocketHandler, game: QuizifyGameState, seconds: int
) -> None:
    """60/90/120 s must survive validation, not fall back to the default."""
    admin = _ws()
    game.add_player("Markus", admin)
    game.get_player("Markus").is_admin = True

    await handler._handle_start_game(
        admin,
        {
            "category": None,
            "difficulty": None,
            "num_rounds": 5,
            "language": "de",
            "timer_duration": seconds,
        },
        game,
    )

    assert game._timer_override == seconds
    assert game._current_question is not None
    assert game._round_duration == float(seconds)


@pytest.mark.asyncio
async def test_out_of_range_timer_still_falls_back(
    handler: QuizifyWebSocketHandler, game: QuizifyGameState
) -> None:
    """Sanity: the clamp is untouched — 301 s is still rejected.

    Guards the other direction of #506: widening the picker must not have
    widened the backend's accepted range.
    """
    admin = _ws()
    game.add_player("Markus", admin)
    game.get_player("Markus").is_admin = True

    await handler._handle_start_game(
        admin,
        {
            "category": None,
            "difficulty": None,
            "num_rounds": 5,
            "language": "de",
            "timer_duration": 301,
        },
        game,
    )

    assert game._timer_override is None


def test_admin_picker_offers_the_longer_options() -> None:
    """#506: the picker must actually expose the longer values."""
    values = _timer_chip_values()
    assert values == [20, 30, 45, 60, 90, 120]


def test_every_timer_chip_is_accepted_by_the_backend() -> None:
    """No chip may sit outside the 5..300 s window the backend accepts.

    A chip outside that range would be silently swallowed by the clamp in
    ``_handle_start_game`` and the host would get the difficulty default
    instead of the value they picked — with no error anywhere.
    """
    for value in _timer_chip_values():
        assert _MIN_TIMER <= value <= _MAX_TIMER, (
            f"timer chip {value}s is outside the backend's accepted "
            f"{_MIN_TIMER}..{_MAX_TIMER}s range and would silently no-op"
        )
