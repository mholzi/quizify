"""Regression (#364): cross-module wiring must go through public setters
instead of poking private attributes.

The setup path in ``__init__.py`` previously assigned private attributes
across module boundaries:
  * ``game_state._stats_service`` / ``game_state._question_stats``
  * ``ws_handler._tts_announcer``
and the reaction handler read/updated ``recipient._reaction_bonuses_received``
directly. These tests pin the public entry points that replaced them and
assert ``add_reaction_bonus`` enforces the exact same per-round cap.
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT))

from custom_components.quizify.game.player import PlayerSession  # noqa: E402
from custom_components.quizify.game.state import QuizifyGameState  # noqa: E402
from custom_components.quizify.server.websocket import (  # noqa: E402
    QuizifyWebSocketHandler,
)


class _FakeRuntime:
    def __init__(self, tmp_path: Path) -> None:
        self.data_dir = tmp_path


def test_set_stats_services_wires_both_sinks(tmp_path: Path) -> None:
    state = QuizifyGameState(runtime=_FakeRuntime(tmp_path), entry_id="test")
    # Default (pre-wire) is None on both.
    assert state._stats_service is None
    assert state._question_stats is None

    analytics = MagicMock(name="analytics")
    question_stats = MagicMock(name="question_stats")
    state.set_stats_services(analytics, question_stats)

    assert state._stats_service is analytics
    assert state._question_stats is question_stats

    # None clears them again (standalone/dev path).
    state.set_stats_services(None, None)
    assert state._stats_service is None
    assert state._question_stats is None


def test_set_tts_announcer_wires_and_clears(tmp_path: Path) -> None:
    handler = QuizifyWebSocketHandler(
        runtime=_FakeRuntime(tmp_path),
        game_state_provider=lambda: None,
    )
    assert handler._tts_announcer is None

    announcer = MagicMock(name="announcer")
    handler.set_tts_announcer(announcer)
    assert handler._tts_announcer is announcer

    handler.set_tts_announcer(None)
    assert handler._tts_announcer is None


def test_add_reaction_bonus_enforces_cap() -> None:
    cap = QuizifyWebSocketHandler._REACTION_BONUS_CAP_PER_ROUND
    player = PlayerSession(name="Alice", ws=None)
    round_num = 1

    # Up to the cap: each grant awards +1 and returns True.
    for expected_score in range(1, cap + 1):
        assert player.add_reaction_bonus(round_num, cap) is True
        assert player.score == expected_score
        assert player.round_score == expected_score

    # At the cap: further grants are no-ops returning False.
    assert player.add_reaction_bonus(round_num, cap) is False
    assert player.score == cap
    assert player.round_score == cap
    assert player._reaction_bonuses_received[round_num] == cap


def test_add_reaction_bonus_is_per_round() -> None:
    cap = 3
    player = PlayerSession(name="Bob", ws=None)

    # Fill round 1 to the cap.
    for _ in range(cap):
        assert player.add_reaction_bonus(1, cap) is True
    assert player.add_reaction_bonus(1, cap) is False

    # A different round has its own fresh counter.
    assert player.add_reaction_bonus(2, cap) is True
    assert player._reaction_bonuses_received == {1: cap, 2: 1}
    assert player.score == cap + 1


def test_reset_for_new_game_clears_received_bonuses() -> None:
    cap = 3
    player = PlayerSession(name="Carol", ws=None)
    player.add_reaction_bonus(1, cap)
    assert player._reaction_bonuses_received == {1: 1}

    player.reset_for_new_game()
    assert player._reaction_bonuses_received == {}
    # After reset the same round is grantable again (the #167 bug this guards).
    assert player.add_reaction_bonus(1, cap) is True
