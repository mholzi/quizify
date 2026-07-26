"""Regression tests for issue #521 — the TV/cast grid leaked the answer.

``_emit_question`` computes a per-round shuffle and stores it via
``set_round_shuffle``. That shuffle reached the TTS narration and the reveal
index, but the payload the admin and the TV dashboard render their grid from
was built straight off ``question.answers`` — question-JSON order. In 16 of the
26 shipped packs the correct answer sits at index 0 of *every* question, so on
those packs the correct answer was tile A on the big screen for every question
of every game. The phones were never affected: they get a genuine per-player
shuffle, so nobody was mis-scored — it leaked to whoever watched the TV.

The fix threads the shuffle that already exists (``shuffle_map``) into the
admin/dashboard payload, the reveal's answer-distribution bars (#151) and the
reconnect snapshots, instead of introducing a second one. These tests pin all
four surfaces plus the guard that a malformed map falls back rather than
mis-ordering the grid.
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
    _compute_answer_distribution,
    serialize_question_for_admin,
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
    runtime = _FakeRuntime(tmp_path)
    return QuizifyGameState(runtime=runtime, entry_id="test")


@pytest.fixture
def builder() -> RoundMessageBuilder:
    return RoundMessageBuilder()


def _start_round(state: QuizifyGameState):
    """Drive into QUESTION_ACTIVE with a FIXED non-identity canonical shuffle.

    Pinning the permutation (rotate-by-1) instead of ``random.shuffle`` matters
    here: a random shuffle is identity often enough that a leak test would pass
    by luck. ``geographie`` is a multiple-choice pack — the mixed pool now
    includes #275 estimate questions, which carry no answers to shuffle.
    """
    state.add_player("Alice", _fake_ws())
    state.start_game(
        category="geographie", language="de", num_rounds=3, difficulty="easy"
    )
    question = state.start_next_question()
    assert question is not None
    n = len(question.answers)
    assert n >= 3
    order = [(i + 1) % n for i in range(n)]
    state.set_round_shuffle(order, [question.answers[i].text for i in order])
    return question


# ---------------------------------------------------------------------------
# The live admin/TV payload
# ---------------------------------------------------------------------------


def test_admin_payload_rides_the_round_shuffle(
    state: QuizifyGameState, builder: RoundMessageBuilder
) -> None:
    """The grid the TV draws must follow ``shuffle_map``, not JSON order."""
    question = _start_round(state)
    msg = builder.build_admin_question(state, question=question)

    texts = [a["text"] for a in msg["answers"]]
    assert texts == [question.answers[i].text for i in state.shuffle_map]
    # Same answers, reordered — the mapping must not drop or duplicate one.
    assert sorted(texts) == sorted(a.text for a in question.answers)
    # And the correct flag travels with its own text, not with a position.
    for entry, orig in zip(msg["answers"], state.shuffle_map, strict=True):
        assert entry["correct"] is question.answers[orig].correct


def test_correct_answer_is_not_always_the_first_tile(
    state: QuizifyGameState, builder: RoundMessageBuilder
) -> None:
    """The leak itself: with the correct answer first in the JSON, an
    unshuffled payload puts it on tile A. Under a rotate-by-1 shuffle it must
    move off tile A."""
    question = _start_round(state)
    # Force the pathological pack layout: correct answer at index 0. The
    # question objects come from the shared QuestionBank, so the swap is undone
    # even if an assertion fails — otherwise this would bleed into other tests.
    correct_pos = next(i for i, a in enumerate(question.answers) if a.correct)
    question.answers[0], question.answers[correct_pos] = (
        question.answers[correct_pos],
        question.answers[0],
    )
    try:
        assert question.answers[0].correct is True
        msg = builder.build_admin_question(state, question=question)
        assert msg["answers"][0]["correct"] is False
        assert any(a["correct"] for a in msg["answers"])
    finally:
        question.answers[0], question.answers[correct_pos] = (
            question.answers[correct_pos],
            question.answers[0],
        )


def test_tts_options_match_the_rendered_grid(
    state: QuizifyGameState, builder: RoundMessageBuilder
) -> None:
    """The narrator speaks ``shuffled_answers``; the TV renders the payload.
    A spoken "B" has to name the tile the room is looking at."""
    question = _start_round(state)
    msg = builder.build_admin_question(state, question=question)
    assert [a["text"] for a in msg["answers"]] == list(state.shuffled_answers)


# ---------------------------------------------------------------------------
# Reconnect snapshots — the two paths that rebuild a grid without a live event
# ---------------------------------------------------------------------------


def test_active_question_snapshot_matches_the_live_grid(
    state: QuizifyGameState, builder: RoundMessageBuilder
) -> None:
    """A dashboard reconnecting mid-question rebuilds from the snapshot; it
    must land on the same order the live payload used."""
    question = _start_round(state)
    live = builder.build_admin_question(state, question=question)
    snap = state.get_state_snapshot()["question"]
    assert snap["answers"] == [a["text"] for a in live["answers"]]


def test_reveal_snapshot_index_addresses_its_own_answers(
    state: QuizifyGameState,
) -> None:
    """At the reveal the snapshot ships both the answers and the index used to
    colour them — they have to be in the same space."""
    question = _start_round(state)
    correct_idx = next(i for i, a in enumerate(question.answers) if a.correct)
    state.submit_answer("Alice", correct_idx)
    state.evaluate_round()
    assert state.phase == GamePhase.ANSWER_REVEAL

    rs = state.get_state_snapshot()["round_summary"]
    assert rs["answers"][rs["correct_answer_index"]] == (
        question.answers[correct_idx].text
    )
    assert rs["correct_answer_index_original"] == correct_idx


# ---------------------------------------------------------------------------
# #151 distribution bars — votes must land on the tile they were cast for
# ---------------------------------------------------------------------------


def test_distribution_bars_follow_the_displayed_order() -> None:
    """``all_answers[].answer_index`` is question-JSON order. Mapping it into
    display space is what keeps a vote attached to the answer it was cast for."""
    # Grid shows [orig 2, orig 0, orig 1]; both players voted for original 2.
    order = [2, 0, 1]
    all_answers = [
        {"player_name": "Alice", "answer_index": 2},
        {"player_name": "Bob", "answer_index": 2},
    ]
    dist = _compute_answer_distribution(all_answers, 3, order)
    by_index = {d["index"]: d["count"] for d in dist if "index" in d}
    # Original 2 is drawn as tile 0.
    assert by_index[0] == 2
    assert by_index[1] == 0
    assert by_index[2] == 0


def test_distribution_without_a_map_is_unchanged() -> None:
    """No shuffle passed (estimate rounds, legacy callers) → identity."""
    all_answers = [{"player_name": "Alice", "answer_index": 2}]
    dist = _compute_answer_distribution(all_answers, 3, None)
    by_index = {d["index"]: d["count"] for d in dist if "index" in d}
    assert by_index[2] == 1


def test_no_answer_players_still_counted_with_a_map() -> None:
    """Timeouts have no ``answer_index``; the mapping must not swallow them."""
    all_answers = [
        {"player_name": "Alice", "answer_index": 0},
        {"player_name": "Bob", "answer_index": None, "no_answer": True},
    ]
    dist = _compute_answer_distribution(all_answers, 3, [1, 2, 0])
    no_answer = [d for d in dist if d.get("no_answer")]
    assert len(no_answer) == 1
    assert no_answer[0]["count"] == 1


# ---------------------------------------------------------------------------
# Guards — a broken map must degrade to JSON order, never to a wrong order
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "bad_order",
    [
        None,
        [],
        [0, 1],  # too short
        [0, 1, 2, 3],  # too long
        [0, 1, 1],  # duplicate index
        [0, 1, 5],  # out of range
    ],
)
def test_malformed_shuffle_falls_back_to_json_order(bad_order) -> None:
    """A mis-ordered grid mislabels every tile; an unshuffled one only leaks.
    When the map cannot be trusted, fall back rather than reorder."""

    class _A:
        def __init__(self, text: str, correct: bool) -> None:
            self.text = text
            self.correct = correct

    class _Q:
        question = "Q?"
        category = "geo"
        difficulty = "easy"
        image_url = None
        type = "multiple_choice"
        is_estimate = False
        answers = [_A("a", True), _A("b", False), _A("c", False)]

    payload = serialize_question_for_admin(
        question=_Q(),
        round_num=1,
        total_rounds=3,
        timer_duration=30.0,
        display_order=bad_order,
    )
    assert [a["text"] for a in payload["answers"]] == ["a", "b", "c"]
