"""#835 — in team mode the television counted people, and listed them too.

Found on real hardware during the v1.16.0-RC1 live test: four guests in three
teams (Sofa: Anna + Dan, Sessel: Ben, Teppich: Cleo), TV at 1280x720. Two
faults on one screen, both about the same confusion between a person and a
row on the leaderboard.

**The counter never left ``0/4``.** ``serialize_answer_progress`` counted
``player.submitted``, and ``submit_answer``'s team branch deliberately marks
nobody submitted — every member may keep changing the team's answer until the
clock stops (#365). So the numerator could never move. The denominator was
wrong too: ``4`` was the head count, not the number of things that have to
answer, so even a working counter would have counted to the wrong total.

**The leaderboard listed individuals on 0 during the question and teams at the
reveal.** ``build_game_state_with_leaderboard`` was the last leaderboard in the
codebase still serialized straight off the player list; every other builder
(the snapshot, both reveal paths) had already moved to
``get_ranked_participants()``. The board therefore flipped identity twice a
round: Anna/Ben/Cleo/Dan while the question was live, Sofa/Sessel/Teppich the
moment the reveal landed.
"""

from __future__ import annotations

import re
from pathlib import Path
from unittest.mock import MagicMock

from custom_components.quizify.game.state import QuizifyGameState
from custom_components.quizify.server.round_message_builder import RoundMessageBuilder
from custom_components.quizify.server.serializers import serialize_answer_progress

REPO = Path(__file__).resolve().parent.parent
WEBSOCKET = REPO / "custom_components" / "quizify" / "server" / "websocket.py"


def _ws() -> MagicMock:
    ws = MagicMock()
    ws.closed = False
    return ws


class _Runtime:
    def __init__(self, tmp_path: Path) -> None:
        self.data_dir = tmp_path


def _game(tmp_path: Path) -> QuizifyGameState:
    """The live test's own room: four people, three teams."""
    st = QuizifyGameState(runtime=_Runtime(tmp_path), entry_id="test")
    for name in ("Anna", "Ben", "Cleo", "Dan"):
        st.add_player(name, _ws())
    st.create_team("Sofa", "Anna")
    st.join_team(st.get_team_of("Anna")["team_id"], "Dan")
    st.create_team("Sessel", "Ben")
    st.create_team("Teppich", "Cleo")
    st.start_game(num_rounds=8, language="en")
    return st


def _team(st: QuizifyGameState, name: str):  # noqa: ANN202
    return next(t for t in st.team_registry.all_teams() if t.name == name)


def _progress(st: QuizifyGameState) -> dict:
    return serialize_answer_progress(st.get_players(), st.get_ranked_participants())


# ---------------------------------------------------------------------------
# The counter
# ---------------------------------------------------------------------------


def test_a_team_game_counts_teams_out_of_teams(tmp_path: Path) -> None:
    st = _game(tmp_path)
    assert _progress(st)["total"] == 3, "the 4 in 0/4 was the head count"
    assert [e["name"] for e in _progress(st)["players"]] == [
        "Sofa",
        "Sessel",
        "Teppich",
    ]


def test_the_counter_moves_when_a_team_locks_an_answer_in(tmp_path: Path) -> None:
    """The exact failure: three teams had answered and the TV read 0/4."""
    st = _game(tmp_path)
    assert _progress(st)["submitted"] == 0

    _team(st, "Sofa").set_answer(1, "Anna")
    assert _progress(st)["submitted"] == 1

    _team(st, "Sessel").set_answer(0, "Ben")
    _team(st, "Teppich").set_answer(2, "Cleo")
    progress = _progress(st)
    assert (progress["submitted"], progress["total"]) == (3, 3)


def test_an_estimate_guess_counts_the_same_as_an_answer(tmp_path: Path) -> None:
    """A team responds to an estimate round with ``current_guess`` (#602).

    Counting only ``current_answer`` would leave the estimate rounds on 0/N —
    the same bug in the other half of the game.
    """
    st = _game(tmp_path)
    _team(st, "Sofa").set_guess(42.0, "Anna")
    assert _progress(st)["submitted"] == 1


def test_a_team_is_present_while_any_member_is(tmp_path: Path) -> None:
    """The tracker greys out a row the room should stop waiting for."""
    st = _game(tmp_path)
    st.get_player("Anna").connected = False
    rows = {e["name"]: e["connected"] for e in _progress(st)["players"]}
    assert rows["Sofa"] is True, "Dan is still awake"

    st.get_player("Dan").connected = False
    rows = {e["name"]: e["connected"] for e in _progress(st)["players"]}
    assert rows["Sofa"] is False


def test_an_ordinary_game_is_untouched(tmp_path: Path) -> None:
    st = QuizifyGameState(runtime=_Runtime(tmp_path), entry_id="test")
    for name in ("Anna", "Ben", "Cleo"):
        st.add_player(name, _ws())
    st.start_game(num_rounds=8, language="en")

    st.get_player("Anna").submitted = True
    progress = _progress(st)
    assert (progress["submitted"], progress["total"]) == (1, 3)
    assert [e["name"] for e in progress["players"]] == ["Anna", "Ben", "Cleo"]
    # And with the argument omitted, byte-for-byte the old behaviour.
    assert serialize_answer_progress(st.get_players()) == progress


def test_a_solo_guest_keeps_their_own_row_beside_the_teams(tmp_path: Path) -> None:
    """A player who joined no team is a team of one, not an error state."""
    st = _game(tmp_path)
    st.add_player("Eva", _ws())
    names = [e["name"] for e in _progress(st)["players"]]
    assert "Eva" in names and len(names) == 4

    st.get_player("Eva").submitted = True
    assert _progress(st)["submitted"] == 1


def test_the_broadcast_passes_the_entrants(tmp_path: Path) -> None:
    """The serializer can only count what the caller hands it."""
    source = WEBSOCKET.read_text(encoding="utf-8")
    call = re.search(
        r"serialize_answer_progress\((.*?)\)\s*\n", source, re.DOTALL
    )
    assert call is not None
    assert "get_ranked_participants" in call.group(1)


# ---------------------------------------------------------------------------
# The leaderboard during the question
# ---------------------------------------------------------------------------


def test_the_live_leaderboard_shows_the_same_rows_the_reveal_does(
    tmp_path: Path,
) -> None:
    st = _game(tmp_path)
    payload = RoundMessageBuilder().build_game_state_with_leaderboard(
        st, players=st.get_players()
    )
    assert [row["name"] for row in payload["leaderboard"]] == [
        "Sofa",
        "Sessel",
        "Teppich",
    ]


def test_the_head_count_stays_a_head_count(tmp_path: Path) -> None:
    """``player_count`` is people; only the ranking became entrants."""
    st = _game(tmp_path)
    payload = RoundMessageBuilder().build_game_state_with_leaderboard(
        st, players=st.get_players()
    )
    assert payload["player_count"] == 4
    assert [row["name"] for row in payload["players"]] == [
        "Anna",
        "Ben",
        "Cleo",
        "Dan",
    ]


def test_an_ordinary_game_sees_no_change(tmp_path: Path) -> None:
    st = QuizifyGameState(runtime=_Runtime(tmp_path), entry_id="test")
    for name in ("Anna", "Ben"):
        st.add_player(name, _ws())
    st.start_game(num_rounds=8, language="en")
    payload = RoundMessageBuilder().build_game_state_with_leaderboard(
        st, players=st.get_players()
    )
    assert payload["players"] == payload["leaderboard"]
