"""The Hot Seat's reveal beat reads the chair's question, not the last round (#1092).

After settlement the driver fires the ``reveal`` beat, whose consumers — the
narrator and ``quizify_answer_revealed`` — read ``get_round_summary()``. The
Hot Seat runs out of ANSWER_REVEAL and ``finish_hot_seat`` never replaces that
summary, so the room heard the previous normal round's answer and who got it,
and the bus event carried that round's correct_count.

The driver now hands the beat the chair's own summary. The game's round summary
is left alone: once the detour resumes, ANSWER_REVEAL is that round's again.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from custom_components.quizify.game.phase_controller import GamePhase
from custom_components.quizify.game.questions import Answer, Question
from custom_components.quizify.game.state import (
    AnswerResult,
    QuizifyGameState,
    RoundSummary,
)
from custom_components.quizify.game_events import (
    EVENT_ANSWER_REVEALED,
    QuizifyEventEmitter,
)
from custom_components.quizify.server.websocket import QuizifyWebSocketHandler
from custom_components.quizify.tts import QuizifyTTSAnnouncer
from tests import test_house_plays_along_hot_seat_708 as hot_seat_708

# The 708 module's room and handler: four players, en, a fast reveal hold.
game = hot_seat_708.game
handler = hot_seat_708.handler
_run_one_auction = hot_seat_708._run_one_auction

_OLD = Question(
    id="old",
    question="Previous round?",
    answers=[
        Answer(text="Previous answer", correct=True),
        Answer(text="Nope", correct=False),
    ],
)


def _stale_round_summary(game: QuizifyGameState) -> RoundSummary:
    """The normal round the detour interrupts: two of four got it."""
    summary = RoundSummary(
        question=_OLD,
        correct_answer=_OLD.answers[0],
        fun_fact="",
        results=[
            AnswerResult("Ben", True, 100, 1, 100),
            AnswerResult("Cem", True, 100, 1, 100),
            AnswerResult("Dana", False, 0, 0, 0),
        ],
    )
    game._round_summary = summary
    return summary


def _wire(handler: QuizifyWebSocketHandler, game: QuizifyGameState):
    spoken: list[str] = []
    fired: list[tuple[str, dict[str, Any]]] = []
    announcer = QuizifyTTSAnnouncer(hass=None, game_state=game)
    announcer.configure(
        enabled=True,
        announce_question=False,
        announce_reveal=True,
        announce_standings=False,
    )
    announcer._speak = lambda message, **_k: spoken.append(message)  # type: ignore[method-assign]
    emitter = QuizifyEventEmitter(hass=None, game_state=game)
    emitter._fire = lambda event, data: fired.append((event, data))  # type: ignore[method-assign]
    handler.set_tts_announcer(announcer)
    handler.set_event_emitter(emitter)
    return spoken, fired


async def _answer_when_seated(game: QuizifyGameState, *, correctly: bool) -> None:
    for _ in range(400):
        hs = game.hot_seat
        if game.phase == GamePhase.HOT_SEAT and hs is not None and hs.question:
            order = hs._shuffle or list(range(len(hs.question.answers)))
            for i, original in enumerate(order):
                if hs.question.answers[original].correct == correctly:
                    hs.record_answer("Anna", i)
                    return
        await asyncio.sleep(0.005)


def _revealed(fired: list[tuple[str, dict[str, Any]]]) -> dict[str, Any]:
    events = [data for name, data in fired if name == EVENT_ANSWER_REVEALED]
    assert len(events) == 1
    return events[0]


@pytest.mark.asyncio
async def test_a_right_answer_is_narrated_and_counted_for_the_chair(
    handler: QuizifyWebSocketHandler, game: QuizifyGameState
) -> None:
    stale = _stale_round_summary(game)
    spoken, fired = _wire(handler, game)
    answering = asyncio.ensure_future(_answer_when_seated(game, correctly=True))

    await _run_one_auction(handler, game)
    await answering

    hs = game.hot_seat
    assert hs is not None and hs.question is not None and hs.answered is True
    right = hs.question.answers[hs.correct_index].text
    assert spoken[-1] == f"The correct answer is {right}. Anna got it right."
    assert "Previous answer" not in " ".join(spoken)
    event = _revealed(fired)
    assert event["correct_option_text"] == right
    assert (event["correct_count"], event["total_players"]) == (1, 1)
    # The interrupted round keeps its summary for when the detour resumes.
    assert game.get_round_summary() is stale


@pytest.mark.asyncio
async def test_an_unanswered_chair_is_a_miss_not_the_last_rounds_winners(
    handler: QuizifyWebSocketHandler, game: QuizifyGameState
) -> None:
    _stale_round_summary(game)
    spoken, fired = _wire(handler, game)

    await _run_one_auction(handler, game)

    hs = game.hot_seat
    assert hs is not None and hs.question is not None and hs.answered is None
    right = hs.question.answers[hs.correct_index].text
    assert spoken[-1] == f"The correct answer is {right}. Nobody got it this round."
    event = _revealed(fired)
    assert (event["correct_count"], event["total_players"]) == (0, 1)
