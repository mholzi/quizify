"""Regression tests for issue #703.

``pause_reason`` was added by the two pause *broadcasts* and nowhere else:
``get_state_snapshot`` had no PAUSED branch, so ``_handle_reconnect``,
``_handle_join`` and ``_handle_get_state`` all sent a snapshot without it.

The client reads exactly that field: ``updatePausedView`` shows "Host
disconnected" and arms the 60-second reset button (#299) only when
``data.pause_reason === 'admin_disconnected'``. A guest who reloaded during a
host-gone pause was therefore told "Paused — the host will resume" and lost the
only escape hatch from a dead game — on precisely the phones that had just
reconnected, which in a host-gone pause tends to be all of them.
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


class _FakeRuntime:
    def __init__(self, tmp_path: Path) -> None:
        self.data_dir = tmp_path


def _ws() -> MagicMock:
    ws = MagicMock()
    ws.closed = False
    ws.send_json = AsyncMock()
    ws.close = AsyncMock()
    return ws


@pytest.fixture
def game(tmp_path: Path) -> QuizifyGameState:
    return QuizifyGameState(runtime=_FakeRuntime(tmp_path), entry_id="test")


def _paused(game: QuizifyGameState, reason: str) -> None:
    game.add_player("Alice", _ws())
    game.add_player("Bob", _ws())
    game.start_game(language="de", num_rounds=3, difficulty="easy")
    assert game.start_next_question() is not None
    assert game.pause(reason=reason) is True
    assert game.phase == GamePhase.PAUSED


class TestSnapshotCarriesThePauseReason:
    def test_host_gone_pause_is_named_in_the_snapshot(
        self, game: QuizifyGameState
    ) -> None:
        """The bug: a reconnecting phone could not tell why the game stopped."""
        _paused(game, "admin_disconnected")
        assert game.get_state_snapshot()["pause_reason"] == "admin_disconnected"

    def test_deliberate_pause_is_named_too(self, game: QuizifyGameState) -> None:
        _paused(game, "admin_paused")
        assert game.get_state_snapshot()["pause_reason"] == "admin_paused"

    def test_snapshot_matches_get_pause_reason(self, game: QuizifyGameState) -> None:
        """One source of truth: the field is read off the phase controller."""
        _paused(game, "admin_disconnected")
        snapshot = game.get_state_snapshot()
        assert snapshot["pause_reason"] == game.get_pause_reason()

    def test_no_pause_reason_outside_a_pause(self, game: QuizifyGameState) -> None:
        """A running game must not carry a stale reason.

        The client only branches on the value, so a leftover
        ``admin_disconnected`` would arm a reset button mid-question.
        """
        game.add_player("Alice", _ws())
        game.start_game(language="de", num_rounds=3, difficulty="easy")
        assert game.start_next_question() is not None
        assert "pause_reason" not in game.get_state_snapshot()

    def test_reason_is_gone_again_after_resume(self, game: QuizifyGameState) -> None:
        _paused(game, "admin_disconnected")
        assert game.resume() is True
        assert "pause_reason" not in game.get_state_snapshot()

    def test_every_snapshot_consumer_sees_it(self, game: QuizifyGameState) -> None:
        """join / reconnect / get_state all serialize this same dict.

        They differ only in the per-player answer projection (#253/#286), so
        one PAUSED branch covers all three paths at once.
        """
        _paused(game, "admin_disconnected")
        first = game.get_state_snapshot()
        second = game.get_state_snapshot()
        assert first["pause_reason"] == second["pause_reason"] == "admin_disconnected"
        assert first["phase"] == GamePhase.PAUSED.value
