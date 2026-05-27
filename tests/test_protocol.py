"""Tests for the WebSocket protocol contract.

These tests guard against the C3 class of bug: server code adds/removes
a field in a payload, but the client still reads the old key and gets
``undefined``. The dataclasses in server/protocol.py are the single
source of truth; tests here check they serialize to the shape the
client expects, and that the server's actual dispatch covers every
client-message type listed in CLIENT_MESSAGE_TYPES.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT))

from custom_components.quizify.server.protocol import (  # noqa: E402
    CLIENT_MESSAGE_TYPES,
    AnswerResultMessage,
    ErrorMessage,
    FinaleMessage,
    GameStateMessage,
    JoinedMessage,
    PlayerListMessage,
    QuestionStartedMessage,
    RoundSummaryMessage,
    TimerTickMessage,
)


class TestServerMessageShapes:
    def test_joined_has_required_fields(self) -> None:
        m = JoinedMessage(
            type="joined",
            player_id="Alice",
            session_token="abc",
            color="#FF6B6B",
            is_admin=False,
        ).to_dict()
        for k in ("type", "player_id", "session_token", "color", "is_admin", "powerup"):
            assert k in m

    def test_question_started_carries_shuffled_answers(self) -> None:
        m = QuestionStartedMessage(
            type="question_started",
            question_text="?",
            answers=["A", "B", "C"],
            timer_duration=30.0,
            round_num=1,
            total_rounds=10,
            category="cat",
            difficulty="medium",
        ).to_dict()
        assert m["answers"] == ["A", "B", "C"]
        assert m["timer_duration"] == 30.0

    def test_round_summary_includes_question_id_for_flag_button(self) -> None:
        m = RoundSummaryMessage(
            type="round_summary",
            correct_answer_index=0,
            correct_answer="Foo",
            question_id="geo_037",  # required by 🚩 button
            fun_fact="",
            leaderboard=[],
            players=[],
            round=1,
            total_rounds=10,
            last_round=False,
            all_answers=[],
            answer_distribution=[],
            question_text="?",
        ).to_dict()
        assert m["question_id"] == "geo_037"

    def test_finale_default_collections(self) -> None:
        m = FinaleMessage(
            type="finale",
            podium=[],
            leaderboard=[],
            all_players=[],
        ).to_dict()
        assert m["superlatives"] == []

    def test_game_state_phase_field(self) -> None:
        m = GameStateMessage(
            type="game_state",
            phase="LOBBY",
            round=0,
            total_rounds=10,
            players=[],
        ).to_dict()
        assert m["phase"] == "LOBBY"

    def test_error_round_trip(self) -> None:
        m = ErrorMessage(type="error", code="INVALID_ACTION", message="nope").to_dict()
        assert m == {"type": "error", "code": "INVALID_ACTION", "message": "nope"}


class TestClientDispatchCoverage:
    """The server's _handle_message dispatch must implement every key
    listed in CLIENT_MESSAGE_TYPES. If a new key is added to the set
    here without a handler, this test fails — prevents silent typos."""

    def test_every_listed_client_message_is_handled(self) -> None:
        # Read websocket.py and grep the dispatch — cheaper than
        # importing the full ws machinery (which needs aiohttp + a
        # runtime). Direct text search is brittle but acceptable for
        # what's essentially a compile-time check.
        ws_src = (
            _REPO_ROOT
            / "custom_components"
            / "quizify"
            / "server"
            / "websocket.py"
        ).read_text("utf-8")

        missing: list[str] = []
        for msg_type in CLIENT_MESSAGE_TYPES:
            needle = f'msg_type == "{msg_type}"'
            if needle not in ws_src and f"msg_type in (\"{msg_type}\"" not in ws_src:
                # Some types are matched via `in (...)` — check generously
                if f'"{msg_type}"' not in ws_src:
                    missing.append(msg_type)
        assert not missing, f"Client message types with no server handler: {missing}"

    def test_no_orphan_handlers(self) -> None:
        """If the server handles a type that's NOT in CLIENT_MESSAGE_TYPES,
        flag it — usually that's a leftover/dead branch."""
        import re

        ws_src = (
            _REPO_ROOT
            / "custom_components"
            / "quizify"
            / "server"
            / "websocket.py"
        ).read_text("utf-8")

        # Find every msg_type == "X" comparison
        found = set(re.findall(r'msg_type == "([a-z_]+)"', ws_src))
        # Also find msg_type in ("X", "Y") tuples
        for match in re.findall(r'msg_type in \(([^)]+)\)', ws_src):
            for piece in match.split(","):
                piece = piece.strip().strip('"').strip("'")
                if piece:
                    found.add(piece)

        orphans = found - CLIENT_MESSAGE_TYPES
        assert not orphans, (
            f"Server handles client-message types not in protocol contract: {orphans}. "
            "Add them to CLIENT_MESSAGE_TYPES or remove the handler."
        )
