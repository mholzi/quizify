"""#833 — the television's leaderboard has to follow the Hot Seat settlement.

Found on real hardware during the v1.16.0-RC1 live test, three teams, TV at
1280x720. Sofa staked 100% of its 42 points on the chair and ran out of time.
The headline said so — and the board beside it still read ``1 Sofa 42``, with
Sessel and Teppich below it, at the exact moment of the biggest single points
swing in the game. The host page showed the true standing (Sessel 22 · Teppich
20 · Sofa 0) after a reload, so the settlement had happened; only the TV's
panel was stale.

Two causes, one screen:

* ``hot_seat_result`` carried ``scores`` — a ``name → number`` map. No rank, no
  ``entrant_id``, nothing ``leaderboardRowsHtml`` builds a row from. The board
  could not have repainted from it even if it had tried.
* ``handleHotSeatResult`` never touched ``els.leaderboard`` at all.

And the secondary finding on the same screenshot: apart from the one headline
the left column was blank, so the room watched an almost empty television while
it waited for the host. The correct answer was gone with it — ``correct_index``
is a CANONICAL index and the TV is shown the SHUFFLED order (#521/#604), so an
index is the one thing that screen cannot use. The frame now spells the answer
out.
"""

from __future__ import annotations

import re
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from custom_components.quizify.game.phase_controller import GamePhase
from custom_components.quizify.game.state import QuizifyGameState
from custom_components.quizify.server.protocol import SERVER_FRAMES

REPO = Path(__file__).resolve().parent.parent
DASHBOARD = REPO / "custom_components" / "quizify" / "www" / "dashboard.html"
WEBSOCKET = REPO / "custom_components" / "quizify" / "server" / "websocket.py"


def _ws() -> MagicMock:
    ws = MagicMock()
    ws.closed = False
    return ws


class _Runtime:
    def __init__(self, tmp_path: Path) -> None:
        self.data_dir = tmp_path


def _game(tmp_path: Path) -> QuizifyGameState:
    """Three teams, the live-test shape: Sofa, Sessel, Teppich."""
    st = QuizifyGameState(runtime=_Runtime(tmp_path), entry_id="test")
    for name in ("Anna", "Dan", "Ben", "Cleo"):
        st.add_player(name, _ws())
    st.create_team("Sofa", "Anna")
    st.join_team(st.get_team_of("Anna")["team_id"], "Dan")
    st.create_team("Sessel", "Ben")
    st.create_team("Teppich", "Cleo")
    st.start_game(num_rounds=8, language="en", hot_seat_seed=7, lightning_seed=7)
    return st


def _team(st: QuizifyGameState, name: str):  # noqa: ANN202
    return next(t for t in st.team_registry.all_teams() if t.name == name)


def _handler_source() -> str:
    html = DASHBOARD.read_text(encoding="utf-8")
    start = html.index("function handleHotSeatResult(msg)")
    return html[start : html.index("\n        function ", start + 10)]


def _payload_block(source: str, message_type: str) -> str:
    marker = f'"type": "{message_type}"'
    start = source.index(marker)
    open_brace = source.rindex("{", 0, start)
    depth, j = 0, open_brace
    while True:
        if source[j] == "{":
            depth += 1
        elif source[j] == "}":
            depth -= 1
            if depth == 0:
                return source[open_brace : j + 1]
        j += 1


# ---------------------------------------------------------------------------
# The wire
# ---------------------------------------------------------------------------


def test_the_result_frame_declares_a_leaderboard() -> None:
    assert "leaderboard" in SERVER_FRAMES["hot_seat_result"].required


def test_the_result_broadcast_actually_builds_one() -> None:
    block = _payload_block(WEBSOCKET.read_text(encoding="utf-8"), "hot_seat_result")
    assert "serialize_leaderboard" in block, (
        "a name→number map is not something a board can build rows from"
    )
    assert "get_ranked_participants" in block, (
        "the rows the room can see: teams in team mode, players otherwise"
    )


def test_the_settlement_reaches_the_summary_as_an_answer_not_an_index(
    tmp_path: Path,
) -> None:
    """``correct_answer`` is the text, because the TV's grid is shuffled."""
    st = _game(tmp_path)
    _team(st, "Sofa").score = 42
    st.phase = GamePhase.ANSWER_REVEAL
    assert st.start_hot_seat_auction() is True
    hs = st.hot_seat
    assert hs is not None
    assert hs.record_bid("Anna", 100) is True
    assert hs.resolve_auction() is not None
    summary = hs.summary()
    assert summary["correct_answer"] == hs.question.answers[hs.correct_index].text
    assert summary["correct_answer"]


def test_the_leaderboard_is_the_post_settlement_standing(tmp_path: Path) -> None:
    """The live test's own numbers: Sofa stakes everything and times out.

    Built through the real game so the ordering is the settlement's, not the
    roster's — the screenshot's whole complaint is that the board showed the
    player who had just lost everything in first place.
    """
    from custom_components.quizify.server.serializers import serialize_leaderboard

    st = _game(tmp_path)
    _team(st, "Sofa").score = 42
    _team(st, "Sessel").score = 22
    _team(st, "Teppich").score = 20

    st.phase = GamePhase.ANSWER_REVEAL
    assert st.start_hot_seat_auction() is True
    hs = st.hot_seat
    assert hs is not None
    assert hs.record_bid("Anna", 100) is True
    assert hs.resolve_auction() is not None
    st.phase = GamePhase.HOT_SEAT
    # Nobody answers: the chair was bought either way (#653).
    st.finish_hot_seat()

    rows = serialize_leaderboard(st.get_ranked_participants())
    assert [(r["name"], r["score"]) for r in rows] == [
        ("Sessel", 22),
        ("Teppich", 20),
        ("Sofa", 0),
    ]


# ---------------------------------------------------------------------------
# The screen
# ---------------------------------------------------------------------------


def test_the_board_repaints_its_leaderboard_on_the_result() -> None:
    source = _handler_source()
    assert re.search(
        r"renderLeaderboard\(\s*els\.leaderboard\s*,\s*msg\.leaderboard\s*\)", source
    ), "handleHotSeatResult never touched els.leaderboard"


def test_the_board_shows_the_correct_answer_rather_than_an_empty_column() -> None:
    source = _handler_source()
    assert "msg.correct_answer" in source
    assert "els.answersGrid" in source, (
        "hotSeatFrame() empties the grid; the result has to put something back"
    )
    assert "escapeHtml(msg.correct_answer)" in source, (
        "pack text goes through the escaper like every other rendered string"
    )


@pytest.mark.parametrize("field", ["leaderboard", "correct_answer"])
def test_a_frame_without_the_new_field_still_renders(field: str) -> None:
    """An older server, or a settlement with no question, must not blank the
    board or throw — both reads are guarded, not assumed."""
    source = _handler_source()
    guard = f"msg.{field}"
    assert f"if ({guard})" in source or f"{guard}\n" in source or f"{guard} ?" in source
