"""#853 — in team mode the television's answer distribution counted heads.

Found on real hardware during the v1.16.0-RC2 live test, on the screen #835
had just fixed. Team **Sofa** (Anna + Cleo) answered *Argentina*; **Dan**,
playing alone, answered *Brazil*. Two entrants, one answer each. The
television read::

    2/2                      <- entrants, correct since #835
    A  Argentina   67%       <- 2 of 3 people
    B  Brazil      33%       <- 1 of 3 people
    C  Croatia      0%

``_compute_answer_distribution`` divides by ``len(all_answers)``, and
``all_answers`` is per player — every member of a team carries the team's row
(#365), because the phone reveal reads its own row out of it for the answer it
gave and the points it earned. So a team of two was two votes: 67/33 where
50/50 happened, and a large team's pick dominating a chart the room reads as
"what did we all choose". The counter directly above the bars had counted the
other way since #835, which is what made it visible.

The rows stay per player. The distribution now collapses them onto the thing
that actually answers — the team id, or the player's own name when they play
alone — which is the same entrant the counter above it counts.
"""

from __future__ import annotations

import re
from pathlib import Path
from unittest.mock import MagicMock

from custom_components.quizify.game.state import QuizifyGameState
from custom_components.quizify.server.round_message_builder import RoundMessageBuilder
from custom_components.quizify.server.serializers import (
    _compute_answer_distribution,
    serialize_round_summary,
)

REPO = Path(__file__).resolve().parent.parent
BUILDER = (
    REPO / "custom_components" / "quizify" / "server" / "round_message_builder.py"
)


class _Runtime:
    def __init__(self, tmp_path: Path) -> None:
        self.data_dir = tmp_path


def _ws() -> MagicMock:
    ws = MagicMock()
    ws.closed = False
    return ws


def _open_round(st: QuizifyGameState):  # noqa: ANN202
    """What ``_emit_question`` does, minus the sockets.

    The canonical shuffle has to be stored or ``num_answer_options`` is zero
    and there are no bars to count; the per-player shuffles are the identity
    here so a submitted button index is also the question-JSON index.
    """
    question = st.start_next_question()
    assert question is not None
    order = list(range(len(question.answers)))
    st.set_round_shuffle(order, [question.answers[i].text for i in order])
    for player in st.get_players():
        st.set_player_shuffle(player.name, list(order))
    return question


def _live_test_room(tmp_path: Path) -> QuizifyGameState:
    """The room from the screenshot: a team of two and one solo guest."""
    st = QuizifyGameState(runtime=_Runtime(tmp_path), entry_id="test")
    for name in ("Anna", "Cleo", "Dan"):
        st.add_player(name, _ws())
    st.create_team("Sofa", "Anna")
    st.join_team(st.get_team_of("Anna")["team_id"], "Cleo")
    st.start_game(num_rounds=8, language="en")
    return st


def _distribution(st: QuizifyGameState) -> dict[int, dict]:
    summary = RoundMessageBuilder().build_round_summary(st)
    assert summary is not None
    return {d["index"]: d for d in summary["answer_distribution"]}


# ---------------------------------------------------------------------------
# The screen from the live test
# ---------------------------------------------------------------------------


def test_a_team_of_two_is_one_vote(tmp_path: Path) -> None:
    """67/33 is what the room was shown; 50/50 is what happened."""
    st = _live_test_room(tmp_path)
    _open_round(st)
    st.submit_answer("Anna", 0)
    st.submit_answer("Dan", 1)
    st.evaluate_round()

    dist = _distribution(st)
    assert (dist[0]["count"], dist[0]["percent"]) == (1, 50)
    assert (dist[1]["count"], dist[1]["percent"]) == (1, 50)
    assert (dist[2]["count"], dist[2]["percent"]) == (0, 0)


def test_the_bars_count_what_the_counter_above_them_counts(tmp_path: Path) -> None:
    """#835 made the tally entrants; the chart under it has to agree."""
    st = _live_test_room(tmp_path)
    _open_round(st)
    st.submit_answer("Anna", 0)
    st.submit_answer("Dan", 1)
    st.evaluate_round()

    dist = _distribution(st)
    votes = sum(d["count"] for d in dist.values())
    assert votes == len(st.get_ranked_participants()) == 2


def test_the_second_member_does_not_vote_again(tmp_path: Path) -> None:
    """Cleo re-taps the team's answer. It is still one answer (#365)."""
    st = _live_test_room(tmp_path)
    _open_round(st)
    st.submit_answer("Anna", 0)
    st.submit_answer("Dan", 1)
    st.evaluate_round()
    before = _distribution(st)[0]["count"]

    st2 = _live_test_room(tmp_path)
    _open_round(st2)
    st2.submit_answer("Anna", 0)
    st2.submit_answer("Cleo", 0)
    st2.submit_answer("Dan", 1)
    st2.evaluate_round()
    assert _distribution(st2)[0]["count"] == before == 1


def test_a_silent_team_is_one_missing_vote(tmp_path: Path) -> None:
    """The no-answer bucket divides by entrants too, or the percentages
    would not add up to a hundred."""
    st = _live_test_room(tmp_path)
    _open_round(st)
    st.submit_answer("Dan", 1)
    st.evaluate_round()

    dist = _distribution(st)
    assert (dist[1]["count"], dist[1]["percent"]) == (1, 50)
    assert (dist[None]["count"], dist[None]["percent"]) == (1, 50)


def test_the_rows_themselves_stay_per_player(tmp_path: Path) -> None:
    """Collapsing ``all_answers`` would break the phone reveal (#365/#308)."""
    st = _live_test_room(tmp_path)
    _open_round(st)
    st.submit_answer("Anna", 0)
    st.submit_answer("Dan", 1)
    st.evaluate_round()

    summary = RoundMessageBuilder().build_round_summary(st)
    assert [r["player_name"] for r in summary["all_answers"]] == [
        "Anna",
        "Cleo",
        "Dan",
    ]


def test_an_ordinary_game_is_untouched(tmp_path: Path) -> None:
    st = QuizifyGameState(runtime=_Runtime(tmp_path), entry_id="test")
    for name in ("Anna", "Ben", "Cleo"):
        st.add_player(name, _ws())
    st.start_game(num_rounds=8, language="en")
    _open_round(st)
    st.submit_answer("Anna", 0)
    st.submit_answer("Ben", 0)
    st.submit_answer("Cleo", 1)
    st.evaluate_round()

    dist = _distribution(st)
    assert (dist[0]["count"], dist[0]["percent"]) == (2, 67)
    assert (dist[1]["count"], dist[1]["percent"]) == (1, 33)


# ---------------------------------------------------------------------------
# The seam
# ---------------------------------------------------------------------------


def test_the_map_reaches_the_serializer(tmp_path: Path) -> None:
    """The serializer can only divide by what the builder hands it."""
    source = BUILDER.read_text(encoding="utf-8")
    call = re.search(r"msg = serialize_round_summary\((.*?)\n        \)", source, re.S)
    assert call is not None
    assert "entrant_of=entrant_of" in call.group(1), (
        "build_round_summary stopped naming the entrants — the bars are back "
        "to counting heads"
    )


def test_teams_are_keyed_by_id_not_by_name(tmp_path: Path) -> None:
    """#728's reason: nothing stops two teams from sharing a name."""
    st = QuizifyGameState(runtime=_Runtime(tmp_path), entry_id="test")
    for name in ("Anna", "Ben"):
        st.add_player(name, _ws())
    st.create_team("Sofa", "Anna")
    st.create_team("Sofa", "Ben")
    st.start_game(num_rounds=8, language="en")
    _open_round(st)
    st.submit_answer("Anna", 0)
    st.submit_answer("Ben", 1)
    st.evaluate_round()

    dist = _distribution(st)
    assert (dist[0]["count"], dist[1]["count"]) == (1, 1), (
        "two same-named teams collapsed into one entrant"
    )


def test_the_argument_is_optional_and_changes_nothing_when_absent() -> None:
    """Every caller outside team mode, and forty tests, pass three arguments."""
    rows = [
        {"player_name": "Anna", "answer_index": 0},
        {"player_name": "Ben", "answer_index": 0},
        {"player_name": "Cleo", "answer_index": 1},
    ]
    assert _compute_answer_distribution(rows, 2) == _compute_answer_distribution(
        rows, 2, None, None
    )
    assert serialize_round_summary(
        correct_answer_index=0,
        correct_answer_text="A",
        fun_fact="",
        leaderboard=[],
        round_num=1,
        total_rounds=5,
        all_answers=rows,
        num_answer_options=2,
    )["answer_distribution"] == _compute_answer_distribution(rows, 2)
