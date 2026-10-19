"""The opener and the final-round call no longer swallow the question (#1091).

``start_next_question`` notifies the state callbacks, so the announcer spoke
"Quizify starting" (round 1) or "Final round!" (last round) the moment the
phase flipped; milliseconds later ``question_shown`` asked for the question and
the 5 s throttle dropped it. The Hot Seat had the same collision: the chair
line, a 4 s hold, then the chair's question one second inside the window.

These tests drive the real ``start_next_question`` -> ``announce_question``
order against an announcer whose throttle is never reset.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from custom_components.quizify.game.phase_controller import GamePhase
from custom_components.quizify.game.questions import Answer, Question
from custom_components.quizify.game.state import QuizifyGameState
from custom_components.quizify.tts import QuizifyTTSAnnouncer

PACK = "tts-1091"


class _Runtime:
    def __init__(self, tmp_path: Path) -> None:
        self.data_dir = tmp_path


class _FakeHass:
    def __init__(self) -> None:
        self.calls: list[dict] = []
        self.states = MagicMock()
        self.services = MagicMock()

        async def _async_call(domain, service, data, blocking=False):  # noqa: ANN001
            self.calls.append(dict(data))

        self.services.async_call = _async_call

    def async_create_task(self, coro):  # noqa: ANN001
        return asyncio.ensure_future(coro)


def _q(idx: int) -> Question:
    return Question(
        id=f"t{idx:02d}",
        question=f"Question {idx}?",
        answers=[
            Answer(text="right", correct=True),
            Answer(text="wrong a", correct=False),
        ],
        category=PACK,
        difficulty="medium",
        language="en",
    )


@pytest.fixture
def game(tmp_path: Path) -> QuizifyGameState:
    gs = QuizifyGameState(runtime=_Runtime(tmp_path), entry_id="test")
    gs.question_bank._categories = {PACK: [_q(i) for i in range(1, 6)]}
    gs.question_bank._loaded = True
    gs.add_player("Anna")
    gs.start_game(
        category=PACK,
        difficulty="medium",
        num_rounds=2,
        language="en",
        lightning_enabled=False,
        hot_seat_enabled=False,
        wager_enabled=False,
    )
    return gs


@pytest.fixture
def announcer(game: QuizifyGameState):
    hass = _FakeHass()
    t = QuizifyTTSAnnouncer(
        hass=hass,
        tts_entity_id="tts.cloud",
        media_player_entity_id="media_player.kitchen",
        game_state=game,
    )
    t.configure(
        enabled=True,
        announce_question=True,
        announce_options=False,
        announce_reveal=True,
        announce_standings=True,
    )
    t.attach()
    return t, hass


async def _next_round(game: QuizifyGameState, t: QuizifyTTSAnnouncer) -> str:
    """What the WS handler does: start the round, then report question_shown."""
    q = game.start_next_question()
    assert q is not None
    t.announce_question(q, game.round, game.total_rounds)
    await asyncio.sleep(0)
    return q.question


@pytest.mark.asyncio
async def test_round_one_speaks_the_opener_and_the_question(game, announcer) -> None:
    t, hass = announcer
    text = await _next_round(game, t)
    assert [c["message"] for c in hass.calls] == [
        f"Quizify starting. Good luck! Question 1 of 2: {text}"
    ]


@pytest.mark.asyncio
async def test_final_round_speaks_the_call_and_the_question(game, announcer) -> None:
    t, hass = announcer
    await _next_round(game, t)
    game.phase = GamePhase.ANSWER_REVEAL
    t._on_state_changed()
    text = await _next_round(game, t)  # well inside TTS_MIN_INTERVAL of round 1
    assert len(hass.calls) == 2
    assert hass.calls[1]["message"] == f"Final round! Question 2 of 2: {text}"


@pytest.mark.asyncio
async def test_question_toggle_off_still_speaks_the_opener(game, announcer) -> None:
    t, hass = announcer
    t._announce_question = False
    await _next_round(game, t)
    assert [c["message"] for c in hass.calls] == ["Quizify starting. Good luck!"]


@pytest.mark.asyncio
async def test_hot_seat_question_follows_the_chair_line(game, announcer) -> None:
    t, hass = announcer
    t.announce_hot_seat_started("Anna", 10)
    t.announce_question(_q(9), 3, 2)  # the driver's 4 s hold is < 5 s
    await asyncio.sleep(0)
    assert [c["message"] for c in hass.calls] == [
        "The chair goes to Anna for 10 points.",
        "Question 3 of 2: Question 9?",
    ]


@pytest.mark.asyncio
async def test_incidental_lines_stay_throttled_behind_a_question(
    game, announcer
) -> None:
    t, hass = announcer
    await _next_round(game, t)
    t.announce_milestone("Anna", 3)
    await asyncio.sleep(0)
    assert len(hass.calls) == 1
