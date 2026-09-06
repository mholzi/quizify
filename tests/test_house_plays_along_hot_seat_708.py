"""The house plays along in the Hot Seat too (#708, via the #788 drivers).

``_notify_tts_*`` / ``_notify_house_*`` were called from the normal-round path
only. Every detour mode therefore ran in silence: the narrator said nothing
while the chair answered, no ``quizify_question_shown`` reached the bus, and
the "time running out" beat that drives the faster light pulse never fired —
so the room kept whatever colour the previous phase had left it.

#788 gives each mode's driver a ``MilestoneSink``, which is what makes "walk
the same path as a normal round" something a mode can *do* rather than
something someone has to remember. These tests pin the Hot Seat half of that:
the chair's question is an ordinary question as far as the house is concerned,
and so is its settlement.

Two deliberate limits, both of them the mode's rules rather than an oversight:

  * the spoken **options** are withheld. Only the seat holder has answer
    buttons; reading A/B/C/D to the room would hand the spectators a board the
    mode does not give them.
  * the **Lightning Round is not wired here.** ``announce_question`` reads a
    question aloud, and five of those inside seventy-five seconds would talk
    over the mode they are meant to accompany. Lightning needs its own phrases,
    its own light recipe and its own bus event — the remaining half of #708.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT))

from custom_components.quizify.game.phase_controller import GamePhase  # noqa: E402
from custom_components.quizify.game.state import QuizifyGameState  # noqa: E402
from custom_components.quizify.server.connection import (  # noqa: E402
    ConnectionManager,
)
from custom_components.quizify.server.websocket import (  # noqa: E402
    QuizifyWebSocketHandler,
)


class _FakeRuntime:
    def __init__(self, tmp_path: Path) -> None:
        self.data_dir = tmp_path

    def create_task(self, coro):  # noqa: ANN001, ANN202
        return asyncio.ensure_future(coro)

    async def run_in_executor(self, func, *args):  # noqa: ANN001, ANN202
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, func, *args)


def _ws() -> MagicMock:
    ws = MagicMock()
    ws.closed = False
    ws.send_json = AsyncMock()
    ws.close = AsyncMock()
    return ws


class _Announcer:
    """Records what the quizmaster was asked to say."""

    def __init__(self) -> None:
        self.questions: list[tuple] = []
        self.countdowns: list[float] = []
        self.reveals: int = 0

    def announce_question(self, question, round_no, total_rounds, options=None):  # noqa: ANN001, ANN201
        self.questions.append((question, round_no, total_rounds, options))

    def announce_countdown(self, seconds_remaining):  # noqa: ANN001, ANN201
        self.countdowns.append(seconds_remaining)

    def announce_reveal(self, game_state):  # noqa: ANN001, ANN201
        self.reveals += 1


class _Emitter:
    """Records what reached the Home Assistant bus."""

    def __init__(self) -> None:
        self.questions: list[tuple] = []
        self.time_running_out: list[float] = []
        self.reveals: int = 0

    def notify_question_shown(self, question, round_no, total_rounds):  # noqa: ANN001, ANN201
        self.questions.append((question, round_no, total_rounds))

    def notify_time_running_out(self, seconds_remaining):  # noqa: ANN001, ANN201
        self.time_running_out.append(seconds_remaining)

    def notify_answer_revealed(self, game_state):  # noqa: ANN001, ANN201
        self.reveals += 1


@pytest.fixture
def game(tmp_path: Path) -> QuizifyGameState:
    st = QuizifyGameState(runtime=_FakeRuntime(tmp_path), entry_id="test")
    for name in ("Anna", "Ben", "Cem", "Dana"):
        st.add_player(name, _ws())
    st.start_game(num_rounds=6, language="en", hot_seat_seed=7, lightning_seed=7)
    return st


@pytest.fixture
def handler(
    game: QuizifyGameState, tmp_path: Path
) -> QuizifyWebSocketHandler:
    h = QuizifyWebSocketHandler(
        runtime=_FakeRuntime(tmp_path), game_state_provider=lambda: game
    )
    h._conn = ConnectionManager(_FakeRuntime(tmp_path), lambda: game)
    h._conn.broadcast = AsyncMock()
    h._conn.broadcast_to_admins_and_dashboards = AsyncMock()
    h._conn.send = AsyncMock()
    # The reveal hold is four seconds of television; the beat is not what is
    # under test here.
    h.HOT_SEAT_REVEAL_HOLD = 0.0
    return h


async def _run_one_auction(
    handler: QuizifyWebSocketHandler, game: QuizifyGameState
) -> None:
    """Open an auction, place one bid, and let the driver run to settlement."""
    game.phase = GamePhase.ANSWER_REVEAL
    # A bid is a share of what the bidder holds, so a room on zero has nothing
    # to bid with and the auction correctly finds no winner.
    for score, player in enumerate(game.get_players(), start=1):
        player.score = score * 20
    assert game.start_hot_seat_auction() is True, "the auction refused to open"
    hs = game.hot_seat
    assert hs is not None
    # Sub-second windows: this test is about which hooks fire, not about how
    # long a real auction lasts.
    hs.auction_seconds = 0.05
    hs.answer_seconds = 0.4
    hs.start_auction_clock()
    assert hs.record_bid("Anna", 50) is True

    handler._start_hot_seat_loop(game)
    for _ in range(200):
        await asyncio.sleep(0.01)
        if game.phase == GamePhase.HOT_SEAT_REVEAL:
            break
    handler._cancel_hot_seat_loop()
    assert game.phase == GamePhase.HOT_SEAT_REVEAL, (
        "the auction never reached settlement — the test setup drifted"
    )


@pytest.mark.asyncio
async def test_the_narrator_reads_the_seat_holders_question(
    handler: QuizifyWebSocketHandler, game: QuizifyGameState
) -> None:
    announcer = _Announcer()
    handler._tts_announcer = announcer  # type: ignore[assignment]

    await _run_one_auction(handler, game)

    assert announcer.questions, (
        "the quizmaster said nothing while the chair answered — #708"
    )
    question, round_no, total_rounds, options = announcer.questions[0]
    assert question is not None and question.question
    assert (round_no, total_rounds) == (game.round, game.total_rounds)
    # The spectators do not get an answer board, so they do not get one read
    # to them either.
    assert options is None


@pytest.mark.asyncio
async def test_the_hot_seat_question_reaches_the_event_bus(
    handler: QuizifyWebSocketHandler, game: QuizifyGameState
) -> None:
    emitter = _Emitter()
    handler.set_event_emitter(emitter)  # type: ignore[arg-type]

    await _run_one_auction(handler, game)

    assert emitter.questions, (
        "no quizify_question_shown fired for the chair's question — #708"
    )
    assert emitter.reveals == 1, (
        "the settlement never reached quizify_answer_revealed — #708"
    )


@pytest.mark.asyncio
async def test_the_chairs_clock_drives_the_time_running_out_beat(
    handler: QuizifyWebSocketHandler, game: QuizifyGameState
) -> None:
    """The beat the faster light pulse and the spoken warning hang off.

    Both consumers keep their own once-per-window guard, so the driver pushes
    every tick and lets them decide — same contract as the normal round.
    """
    announcer = _Announcer()
    emitter = _Emitter()
    handler._tts_announcer = announcer  # type: ignore[assignment]
    handler.set_event_emitter(emitter)  # type: ignore[arg-type]

    await _run_one_auction(handler, game)

    assert announcer.countdowns, (
        "the chair's answer window pushed no countdown beat — #708"
    )
    assert emitter.time_running_out, (
        "the chair's answer window pushed nothing to the bus — #708"
    )
    # The auction is sealed; a countdown there would be narrating a window
    # nobody is answering in.
    assert max(announcer.countdowns) <= 0.4


@pytest.mark.asyncio
async def test_a_missing_consumer_never_stalls_the_mode(
    handler: QuizifyWebSocketHandler, game: QuizifyGameState
) -> None:
    """A broken narrator is not a reason to strand a game in the auction."""

    class _Exploding:
        def announce_question(self, *_a, **_k):  # noqa: ANN002, ANN003, ANN201
            raise RuntimeError("tts entity is gone")

        def announce_countdown(self, *_a, **_k):  # noqa: ANN002, ANN003, ANN201
            raise RuntimeError("tts entity is gone")

        def announce_reveal(self, *_a, **_k):  # noqa: ANN002, ANN003, ANN201
            raise RuntimeError("tts entity is gone")

    handler._tts_announcer = _Exploding()  # type: ignore[assignment]

    await _run_one_auction(handler, game)
    assert game.phase == GamePhase.HOT_SEAT_REVEAL
