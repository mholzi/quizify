"""Regression tests for issue #221 — blank player screen during lightning.

Root cause: the live ``game_state`` leaderboard-refresh builder
``RoundMessageBuilder.build_game_state_with_leaderboard()`` emitted only the
base keys (``type/phase/round/total_rounds/player_count/players/leaderboard``)
with **no** ``lightning`` sub-object. When such a message arrived while the
phase was LIGHTNING, the player client's LIGHTNING case (player-core.js ~437)
found no ``msg.lightning`` and fell through to an empty ``lightning-view`` —
a blank screen. The fix makes the builder carry the same ``lightning``
sub-state that ``serialize_state_snapshot(QuizifyGameState)`` already produced, so
reconnect snapshots and live refreshes are wire-identical.

These tests assert the builder's ``game_state`` payload includes a non-empty
``lightning`` payload during LIGHTNING, covering both the splash-pending and
the live-question states.
"""

from __future__ import annotations

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
from custom_components.quizify.server.round_message_builder import (  # noqa: E402
    RoundMessageBuilder,
)
from custom_components.quizify.server.serializers import (  # noqa: E402
    serialize_state_snapshot,
)


class _FakeRuntime:
    def __init__(self, tmp_path: Path) -> None:
        self.data_dir = tmp_path


def _fake_ws() -> MagicMock:
    ws = MagicMock()
    ws.closed = False
    return ws


@pytest.fixture
def state(tmp_path: Path) -> QuizifyGameState:
    return QuizifyGameState(runtime=_FakeRuntime(tmp_path), entry_id="test")


@pytest.fixture
def builder() -> RoundMessageBuilder:
    return RoundMessageBuilder()


def test_game_state_includes_lightning_when_splash_pending(
    state: QuizifyGameState, builder: RoundMessageBuilder
) -> None:
    """Splash-pending: payload carries lightning meta, splash_pending=True."""
    state.add_player("A", _fake_ws())
    assert state.start_lightning_round() is True
    assert state.phase == GamePhase.LIGHTNING
    assert state.lightning_splash_pending is True

    payload = builder.build_game_state_with_leaderboard(
        state, players=state.get_players()
    )

    assert payload["type"] == "game_state"
    assert payload["phase"] == "LIGHTNING"
    # The fix: a non-empty lightning sub-object must be present.
    assert "lightning" in payload
    ln = payload["lightning"]
    assert ln["splash_pending"] is True
    assert ln["num_questions"] >= 1
    assert isinstance(ln["seconds_per_question"], (int, float))
    assert isinstance(ln["time_remaining"], (int, float))
    assert ln["index"] == 0


def test_game_state_includes_lightning_with_live_question(
    state: QuizifyGameState, builder: RoundMessageBuilder
) -> None:
    """Live round: splash_pending=False and a fully-shaped question is sent."""
    state.add_player("A", _fake_ws())
    assert state.start_lightning_round() is True
    # Admin dismisses the intro splash → questions are live.
    assert state.begin_lightning_questions() is True
    assert state.lightning_splash_pending is False

    payload = builder.build_game_state_with_leaderboard(
        state, players=state.get_players()
    )

    assert "lightning" in payload
    ln = payload["lightning"]
    assert ln["splash_pending"] is False
    assert "question" in ln
    q = ln["question"]
    assert isinstance(q["text"], str) and q["text"]
    assert isinstance(q["answers"], list) and q["answers"]
    assert "category" in q
    assert "image_url" in q  # may be None, key must exist


def test_lightning_substate_matches_snapshot_shape(
    state: QuizifyGameState, builder: RoundMessageBuilder
) -> None:
    """The builder's lightning sub-object is wire-identical to the snapshot's.

    Guards the contract that drove #221: both message producers must hand the
    client the same key set, so the LIGHTNING client path behaves the same on
    a reconnect snapshot and on a live leaderboard refresh.
    """
    state.add_player("A", _fake_ws())
    assert state.start_lightning_round() is True
    assert state.begin_lightning_questions() is True

    snap = serialize_state_snapshot(state)
    payload = builder.build_game_state_with_leaderboard(
        state, players=state.get_players()
    )

    assert set(payload["lightning"].keys()) == set(snap["lightning"].keys())
    assert set(payload["lightning"]["question"].keys()) == set(
        snap["lightning"]["question"].keys()
    )


def test_non_lightning_game_state_has_no_lightning_key(
    state: QuizifyGameState, builder: RoundMessageBuilder
) -> None:
    """Outside LIGHTNING the builder must not inject a lightning sub-object."""
    state.add_player("A", _fake_ws())
    payload = builder.build_game_state_with_leaderboard(
        state, players=state.get_players()
    )
    assert payload["phase"] != "LIGHTNING"
    assert "lightning" not in payload
