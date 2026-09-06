"""Regression tests for issue #253 — reconnect/mid-join snapshot vs per-player state.

The player-agnostic ``serialize_state_snapshot()`` disagreed with the per-player live
game on the scoring-critical path:

* QUESTION_ACTIVE / LIGHTNING: the snapshot emitted answers in CANONICAL order,
  but ``submit_answer`` maps the tapped index through the player's OWN shuffle.
  A reconnecting player rebuilt their buttons from the snapshot → the wrong
  answer was recorded ~2/3 of the time.
* ANSWER_REVEAL: the snapshot nested everything under ``round_summary`` and
  omitted ``all_answers``; the reveal view reads FLAT fields → empty reveal.

These tests pin the fix: ``RoundMessageBuilder.project_snapshot_for_player``
re-shapes a copy of the canonical snapshot into the recipient player's frame.
The decisive invariant is exercised with a real ``QuizifyGameState`` so it
catches drift between the snapshot, the shuffle store, and the submit path.
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
    runtime = _FakeRuntime(tmp_path)
    return QuizifyGameState(runtime=runtime, entry_id="test")


@pytest.fixture
def builder() -> RoundMessageBuilder:
    return RoundMessageBuilder()


def _start_round_with_shuffles(state: QuizifyGameState) -> None:
    """Drive into QUESTION_ACTIVE and seed canonical + per-player shuffles the
    way ``_start_next_question`` does, so the snapshot/submit contract is live.

    The canonical and Alice shuffles are FIXED, distinct, non-identity rotations
    (not ``random.shuffle``) so the contract holds deterministically: a random
    shuffle is occasionally identity or coincides with canonical, which made the
    'raw snapshot mis-orders' guard flake ~1-in-6."""
    state.add_player("Alice", _fake_ws())
    state.add_player("Bob", _fake_ws())
    # Pin a multiple-choice category: the mixed pool now includes #275 estimate
    # packs (no answers / no shuffle), which would make this shuffle-projection
    # contract test non-deterministic.
    state.start_game(
        category="geographie", language="de", num_rounds=3, difficulty="easy"
    )
    question = state.start_next_question()
    assert question is not None
    base = list(range(len(question.answers)))
    # Two distinct non-identity permutations (rotate by 1 vs by 2).
    canonical = base[1:] + base[:1]
    alice = base[2:] + base[:2]
    assert canonical != alice and canonical != base and alice != base
    # Canonical shuffle (admin/TV + submit fallback).
    state.set_round_shuffle(canonical, [question.answers[i].text for i in canonical])
    # Per-player shuffle — deliberately NOT for "Bob": he is the mid-round
    # reconnecter who never got one, so the projection must mint it.
    state.set_player_shuffle("Alice", alice)


# ---------------------------------------------------------------------------
# CRITICAL — QUESTION_ACTIVE answers must match the player's submit mapping
# ---------------------------------------------------------------------------


def test_question_snapshot_answers_match_player_submit_mapping(
    state: QuizifyGameState, builder: RoundMessageBuilder
) -> None:
    """The visible index the player taps must score the visible answer.

    This is the exact contract that was broken: the player rebuilds buttons
    from the snapshot ``answers``, taps the index of the answer text they want,
    and the server maps that index through ``get_player_shuffle`` in
    ``submit_answer``. So for EVERY visible position i, the projected answer
    text must equal the question's answer at ``get_player_shuffle[i]``.
    """
    _start_round_with_shuffles(state)
    question = state.get_current_question()
    player = state.get_player("Alice")

    snapshot = serialize_state_snapshot(state)
    projected = builder.project_snapshot_for_player(
        state, snapshot=snapshot, player=player
    )

    visible_answers = projected["question"]["answers"]
    shuffle = state.get_player_shuffle("Alice")  # what submit_answer uses

    assert len(visible_answers) == len(question.answers)
    for visible_index, visible_text in enumerate(visible_answers):
        original_index = shuffle[visible_index]
        assert visible_text == question.answers[original_index].text

    # End-to-end: tapping the visible position of the correct answer scores it.
    correct_text = next(a.text for a in question.answers if a.correct)
    tapped_visible = visible_answers.index(correct_text)
    original_index = state.get_player_shuffle("Alice")[tapped_visible]
    result = state.submit_answer("Alice", original_index)
    assert not isinstance(result, str)
    assert result.correct is True


def test_canonical_snapshot_still_mis_orders_for_a_player(
    state: QuizifyGameState, builder: RoundMessageBuilder
) -> None:
    """Guard the bug itself: the raw (un-projected) snapshot order does NOT
    line up with Alice's submit mapping whenever her shuffle differs from
    canonical — which is the case the projection exists to fix."""
    _start_round_with_shuffles(state)
    question = state.get_current_question()
    raw = serialize_state_snapshot(state)

    canonical_answers = raw["question"]["answers"]
    shuffle = state.get_player_shuffle("Alice")
    projected_for_alice = [question.answers[i].text for i in shuffle]
    # The two orders differ (the shuffles were randomised independently);
    # the raw snapshot would mis-score Alice's taps.
    assert canonical_answers != projected_for_alice


def test_late_joiner_gets_a_minted_shuffle_on_reconnect(
    state: QuizifyGameState, builder: RoundMessageBuilder
) -> None:
    """A mid-round joiner with no prior shuffle gets one created on demand,
    and the projected answers match that fresh shuffle (round-start parity)."""
    _start_round_with_shuffles(state)
    question = state.get_current_question()
    bob = state.get_player("Bob")
    assert "Bob" not in state.player_shuffles  # no shuffle before projection

    snapshot = serialize_state_snapshot(state)
    projected = builder.project_snapshot_for_player(
        state, snapshot=snapshot, player=bob
    )

    assert "Bob" in state.player_shuffles  # minted
    shuffle = state.get_player_shuffle("Bob")
    visible = projected["question"]["answers"]
    for i, text in enumerate(visible):
        assert text == question.answers[shuffle[i]].text


def test_projection_does_not_mutate_canonical_snapshot(
    state: QuizifyGameState, builder: RoundMessageBuilder
) -> None:
    """Admin/dashboard recipients still get canonical order — the projection
    works on a copy and leaves the source snapshot intact."""
    _start_round_with_shuffles(state)
    snapshot = serialize_state_snapshot(state)
    canonical_before = list(snapshot["question"]["answers"])

    builder.project_snapshot_for_player(
        state, snapshot=snapshot, player=state.get_player("Alice")
    )

    assert snapshot["question"]["answers"] == canonical_before


# ---------------------------------------------------------------------------
# MEDIUM — snapshot time_remaining must follow the player's own timer
# ---------------------------------------------------------------------------


def test_question_snapshot_time_remaining_uses_player_timer(
    state: QuizifyGameState, builder: RoundMessageBuilder
) -> None:
    """``time_remaining`` must come from the reconnecting player's own
    QuestionTimer (which tracks freeze/boost/pause), not the player-agnostic
    wall-clock ``time_remaining_for_snapshot()``. A time-boost added to one
    player's timer must lift their projected clock above the canonical one."""
    _start_round_with_shuffles(state)
    alice = state.get_player("Alice")
    timer = state.get_player_timer("Alice")
    assert timer is not None
    timer.add_time(15.0)  # time_boost power-up: extends only Alice's clock

    snapshot = serialize_state_snapshot(state)
    canonical_remaining = snapshot["question"]["time_remaining"]
    projected = builder.project_snapshot_for_player(
        state, snapshot=snapshot, player=alice
    )

    # Alice's boosted timer shows clearly more time than the wall-clock value.
    assert projected["question"]["time_remaining"] > canonical_remaining + 10


# ---------------------------------------------------------------------------
# HIGH — reveal snapshot must carry the FLAT round_summary shape + all_answers
# ---------------------------------------------------------------------------


def test_reveal_snapshot_is_flat_with_all_answers(
    state: QuizifyGameState, builder: RoundMessageBuilder
) -> None:
    """After evaluate_round the reconnect snapshot must expose the same flat
    fields the live ``round_summary`` broadcast does (player-reveal.js reads
    them flat), including a populated ``all_answers`` table — not a nested
    ``round_summary`` blob."""
    _start_round_with_shuffles(state)
    question = state.get_current_question()
    correct_idx = next(i for i, a in enumerate(question.answers) if a.correct)
    state.submit_answer("Alice", correct_idx)
    state.evaluate_round()
    assert state.phase == GamePhase.ANSWER_REVEAL

    snapshot = serialize_state_snapshot(state)
    # The raw snapshot nests under round_summary (the bug).
    assert "round_summary" in snapshot

    projected = builder.project_snapshot_for_player(
        state, snapshot=snapshot, player=state.get_player("Alice")
    )

    # Flat fields the reveal view reads.
    assert "round_summary" not in projected
    assert projected["correct_answer"]
    assert isinstance(projected["all_answers"], list)
    assert projected["all_answers"]  # populated, not empty
    names = {entry["player_name"] for entry in projected["all_answers"]}
    assert "Alice" in names
    alice_entry = next(
        e for e in projected["all_answers"] if e["player_name"] == "Alice"
    )
    assert alice_entry["correct"] is True
    # Other flat keys the client consumes.
    assert "leaderboard" in projected
    assert "players" in projected
    assert "fun_fact" in projected


def test_reveal_raw_snapshot_carries_question_for_dashboard(
    state: QuizifyGameState,
) -> None:
    """#296: a pure dashboard socket (no player session) gets the RAW snapshot,
    which keeps the nested ``round_summary``. Previously that blob lacked the
    question text + answers, so a TV that (re)connected during the reveal had
    nothing to render and showed a blank question view. The raw reveal snapshot
    must carry ``question_text``, the ``answers`` list and a correct index the
    dashboard can highlight with.

    #521: those answers are emitted in the ROUND-SHUFFLE order, not
    question-JSON order — most packs keep the correct answer first in the file,
    so a JSON-ordered grid put it on tile A every round. ``answers`` must match
    the live ``question_started`` payload, and ``correct_answer_index`` must
    address that same order."""
    _start_round_with_shuffles(state)
    question = state.get_current_question()
    correct_idx = next(i for i, a in enumerate(question.answers) if a.correct)
    state.submit_answer("Alice", correct_idx)
    state.evaluate_round()
    assert state.phase == GamePhase.ANSWER_REVEAL

    rs = serialize_state_snapshot(state)["round_summary"]
    assert rs["question_text"] == question.question
    # Round-shuffle order, i.e. what the TV drew while the question was live.
    assert rs["answers"] == [question.answers[i].text for i in state.shuffle_map]
    # Same texts, just reordered — nothing gained or dropped in the mapping.
    assert sorted(rs["answers"]) == sorted(a.text for a in question.answers)
    # The highlight index addresses the shuffled grid the dashboard renders.
    assert rs["answers"][rs["correct_answer_index"]] == (
        question.answers[correct_idx].text
    )
    # The question-JSON index stays in the payload for pre-#521 clients.
    assert rs["correct_answer_index_original"] == correct_idx
    assert question.answers[rs["correct_answer_index_original"]].correct is True


# ---------------------------------------------------------------------------
# CRITICAL — Lightning answers must match the player's lightning submit mapping
# ---------------------------------------------------------------------------


def test_lightning_snapshot_answers_match_player_shuffle(
    state: QuizifyGameState, builder: RoundMessageBuilder
) -> None:
    """Same scoring contract for lightning: the projected lightning answers
    must line up with the shuffle ``record_answer`` uses for that player."""
    state.add_player("Alice", _fake_ws())
    assert state.start_lightning_round() is True
    assert state.begin_lightning_questions() is True
    assert state.phase == GamePhase.LIGHTNING

    lr = state.lightning
    q = lr.current_question
    assert q is not None
    alice = state.get_player("Alice")

    snapshot = serialize_state_snapshot(state)
    projected = builder.project_snapshot_for_player(
        state, snapshot=snapshot, player=alice
    )

    visible = projected["lightning"]["question"]["answers"]
    shuffle = lr.ensure_shuffle("Alice")
    assert len(visible) == len(q.answers)
    for i, text in enumerate(visible):
        assert text == q.answers[shuffle[i]].text

    # Tapping the visible position of the correct answer records correct.
    correct_text = next(a.text for a in q.answers if a.correct)
    tapped_visible = visible.index(correct_text)
    assert lr.record_answer("Alice", tapped_visible) is True


def test_lightning_late_joiner_gets_minted_shuffle(
    state: QuizifyGameState, builder: RoundMessageBuilder
) -> None:
    """A player reconnecting mid-lightning with no shuffle gets one created and
    the projected answers match it."""
    state.add_player("Alice", _fake_ws())
    assert state.start_lightning_round() is True
    assert state.begin_lightning_questions() is True
    lr = state.lightning
    q = lr.current_question
    assert q is not None

    # Simulate a brand-new mid-round joiner registered after arming.
    state.add_player("Zoe", _fake_ws())
    lr.add_player("Zoe")
    # Force the "no shuffle yet" state to prove on-demand minting.
    lr._shuffles.pop("Zoe", None)
    assert "Zoe" not in lr._shuffles

    snapshot = serialize_state_snapshot(state)
    projected = builder.project_snapshot_for_player(
        state, snapshot=snapshot, player=state.get_player("Zoe")
    )

    assert "Zoe" in lr._shuffles
    visible = projected["lightning"]["question"]["answers"]
    shuffle = lr.ensure_shuffle("Zoe")
    for i, text in enumerate(visible):
        assert text == q.answers[shuffle[i]].text
