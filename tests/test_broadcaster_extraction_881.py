"""The drivers' fan-out is pinned to its recipients, not just to its shape (#881).

#788 gave every driver a narrow ``Broadcaster`` protocol and then handed it the
whole 5k-line WebSocket handler, so the contract was an annotation and nothing
else. #881 moved the fan-out coroutines and their frame literals into one small
class per protocol in ``server/broadcasters.py``.

That is a refactor, and the only thing worth testing about a refactor is that
nothing moved. What could silently break here is not a payload key — the frame
schemas already have tests — but *who receives which copy*: the lightning
question is a different list per phone, the hot seat's question is three
different payloads (chair / spectators / television), and a timer tick is
addressed to the subset whose displayed second changed. All of that used to be
expressed inside methods on the handler, addressed off ``self._conn``. If the
extraction dropped or widened one recipient the schema tests would still pass
and the room would be wrong.

So these tests drive the REAL drivers through the REAL broadcasters over a REAL
:class:`ConnectionManager`, with one recording socket per seat in the room, and
assert per socket what arrived. A frame that loses a recipient, gains one, or
reaches the television with the answer in it fails here.
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from typing import Any

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT))

from custom_components.quizify.game.drivers import (  # noqa: E402
    HotSeatDriver,
    LightningDriver,
    NormalRoundDriver,
)
from custom_components.quizify.game.state import (  # noqa: E402
    GamePhase,
    QuizifyGameState,
)
from custom_components.quizify.server.broadcasters import (  # noqa: E402
    HotSeatBroadcaster,
    LightningBroadcaster,
    RoundBroadcaster,
)
from custom_components.quizify.server.connection import ConnectionManager  # noqa: E402
from custom_components.quizify.server.round_message_builder import (  # noqa: E402
    RoundMessageBuilder,
)
from custom_components.quizify.server.websocket import (  # noqa: E402
    QuizifyWebSocketHandler,
)


class _Runtime:
    def __init__(self, tmp_path: Path) -> None:
        self.data_dir = tmp_path

    def create_task(self, coro: Any) -> asyncio.Task:
        """The history flush schedules through the runtime (game/state.py)."""
        return asyncio.ensure_future(coro)


class _Sock:
    """One seat in the room, recording every frame that reaches it.

    Implements both delivery paths the connection manager uses: ``send_json``
    for a single addressed send and ``send_str`` for the pre-serialized
    broadcast path. Recording both is the point — a frame that moved from one
    path to the other would change who gets it.
    """

    def __init__(self, label: str) -> None:
        self.label = label
        self.closed = False
        self.frames: list[dict] = []

    async def send_json(self, message: dict) -> None:
        self.frames.append(message)

    async def send_str(self, payload: str) -> None:
        self.frames.append(json.loads(payload))

    def types(self) -> list[str]:
        return [f.get("type") for f in self.frames]

    def of_type(self, frame_type: str) -> list[dict]:
        return [f for f in self.frames if f.get("type") == frame_type]

    def one(self, frame_type: str) -> dict:
        found = self.of_type(frame_type)
        assert len(found) == 1, (
            f"{self.label} got {len(found)} {frame_type} frames, expected 1"
        )
        return found[0]


class _Room:
    """A game plus the sockets watching it, wired to a real ConnectionManager."""

    def __init__(self, tmp_path: Path, players: list[str]) -> None:
        self.game = QuizifyGameState(runtime=_Runtime(tmp_path), entry_id="t")
        self.conn = ConnectionManager(_Runtime(tmp_path), lambda: self.game)
        self.socks: dict[str, _Sock] = {}
        # Register the socket with the manager BEFORE seating the player: since
        # #882 the game layer holds an opaque connection id, and the manager is
        # the only thing that can turn it back into this socket.
        for name in players:
            sock = _Sock(name)
            self.socks[name] = sock
            self.conn.add_connection(sock, is_admin=False, is_dashboard=False)
            self.game.add_player(name, self.conn.connection_id(sock))
        self.admin = _Sock("admin")
        self.dashboard = _Sock("dashboard")
        self.socks["admin"] = self.admin
        self.socks["dashboard"] = self.dashboard

        self.conn.add_connection(self.admin, is_admin=True, is_dashboard=False)
        self.conn.add_connection(self.dashboard, is_admin=False, is_dashboard=True)

    def who_got(self, frame_type: str) -> set[str]:
        return {
            label for label, sock in self.socks.items() if sock.of_type(frame_type)
        }


def _messages() -> RoundMessageBuilder:
    return RoundMessageBuilder()


# ---------------------------------------------------------------------------
# Lightning: one list per phone, the canonical one to the television
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_lightning_driver_reaches_every_seat_through_the_broadcaster(
    tmp_path: Path,
) -> None:
    """The real ``LightningDriver`` against the real ``LightningBroadcaster``.

    Pins the split the mode is built on: each phone gets its OWN shuffled
    option list (#253/#286 — the tap is scored through that shuffle, so one
    shared order mis-scores the room), while the host and the television get
    the canonical order. The tick and the recap are room-wide and must reach
    every socket, phones included.
    """
    room = _Room(tmp_path, ["Anna", "Ben", "Cleo"])
    assert room.game.start_lightning_round() is True
    lr = room.game.lightning
    assert lr is not None
    lr._questions = lr._questions[:1]
    lr.num_questions = 1
    lr.seconds_per_question = 0.3
    question = lr.current_question
    canonical = [a.text for a in question.answers]

    driver = LightningDriver(
        LightningBroadcaster(room.conn, _messages()),
        splash_grace=0.0,
        splash_hold=0.0,
    )
    await driver.run(room.game, auto_dismiss_splash=False)

    assert room.game.phase == GamePhase.LIGHTNING_RECAP

    # The question: one copy each, to every seat — nobody is skipped.
    assert room.who_got("lightning_question") == {
        "Anna",
        "Ben",
        "Cleo",
        "admin",
        "dashboard",
    }
    for name in ("Anna", "Ben", "Cleo"):
        frame = room.socks[name].one("lightning_question")
        assert sorted(frame["answers"]) == sorted(canonical), (
            f"{name}'s options are not this question's options"
        )
        assert frame["index"] == lr.index
        assert frame["num_questions"] == lr.num_questions
    # The two screens that render an unshuffled board get the canonical order.
    for label in ("admin", "dashboard"):
        assert room.socks[label].one("lightning_question")["answers"] == canonical

    # The clock and the recap are the room's, not one seat's.
    for label in room.socks:
        assert room.socks[label].of_type("lightning_tick"), (
            f"{label} never saw the lightning clock"
        )
    assert room.who_got("lightning_recap") == set(room.socks)


@pytest.mark.asyncio
async def test_lightning_question_carries_each_players_own_shuffle(
    tmp_path: Path,
) -> None:
    """The per-phone order is the one the scorer will read the tap through.

    Asserted against ``shuffled_answers_for`` rather than "is a permutation",
    because a permutation of the right words in the wrong player's order is
    exactly the bug (#286) — and it is the assertion the old handler method
    was never given.
    """
    room = _Room(tmp_path, ["Anna", "Ben", "Cleo"])
    assert room.game.start_lightning_round() is True
    lr = room.game.lightning
    assert lr is not None

    await LightningBroadcaster(room.conn, _messages()).send_lightning_question(
        room.game, lr
    )

    for name in ("Anna", "Ben", "Cleo"):
        frame = room.socks[name].one("lightning_question")
        assert frame["answers"] == lr.shuffled_answers_for(name)


@pytest.mark.asyncio
async def test_a_disconnected_phone_is_not_addressed(tmp_path: Path) -> None:
    """The connected check moved with the loop; prove it came along."""
    room = _Room(tmp_path, ["Anna", "Ben"])
    assert room.game.start_lightning_round() is True
    lr = room.game.lightning
    assert lr is not None
    room.game.get_player("Ben").connected = False
    room.conn.remove_connection(room.socks["Ben"])

    await LightningBroadcaster(room.conn, _messages()).send_lightning_question(
        room.game, lr
    )

    assert room.socks["Anna"].of_type("lightning_question")
    assert room.socks["Ben"].frames == []


# ---------------------------------------------------------------------------
# Normal round: the tick is addressed, not sprayed
# ---------------------------------------------------------------------------


def _started_game(room: _Room) -> None:
    room.game.start_game(language="de", num_rounds=3, lightning_enabled=False)
    room.game.start_next_question()
    assert room.game.phase == GamePhase.QUESTION_ACTIVE


@pytest.mark.asyncio
async def test_normal_round_driver_ticks_reach_players_and_the_television(
    tmp_path: Path,
) -> None:
    """The real ``NormalRoundDriver`` against the real ``RoundBroadcaster``.

    Every connected player has their own countdown (power-ups move it, #4), so
    each gets an addressed frame; the shared minimum goes to the host and the
    television on the one broadcast. Both halves have to arrive — this is the
    frame the whole room reads the clock from.
    """
    room = _Room(tmp_path, ["Anna", "Ben"])
    _started_game(room)

    driver = NormalRoundDriver(
        RoundBroadcaster(room.conn, _messages()),
        milestones=None,
        tick_interval=0.02,
    )
    task = asyncio.ensure_future(driver.run(room.game))
    await asyncio.sleep(0.12)
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)

    assert room.who_got("timer_tick") == {"Anna", "Ben", "admin", "dashboard"}
    for label in ("Anna", "Ben", "admin", "dashboard"):
        tick = room.socks[label].of_type("timer_tick")[0]
        assert isinstance(tick["remaining"], (int, float))
        assert set(tick) == {"type", "remaining"}


@pytest.mark.asyncio
async def test_a_timer_tick_addresses_only_the_named_players(
    tmp_path: Path,
) -> None:
    """The coalescing (#413) hands the broadcaster a SUBSET, and the subset is
    the whole point: a player whose displayed second did not change gets no
    frame, and one who is not in the map must not be reached by someone else's.
    """
    room = _Room(tmp_path, ["Anna", "Ben", "Cleo"])
    _started_game(room)

    await RoundBroadcaster(room.conn, _messages()).send_timer_tick(
        room.game, {"Anna": 7.4}, None
    )

    assert room.who_got("timer_tick") == {"Anna"}
    assert room.socks["Anna"].one("timer_tick") == {
        "type": "timer_tick",
        "remaining": 7.4,
    }

    # And the dashboard half on its own: the shared countdown reaches the two
    # screens and no phone.
    for sock in room.socks.values():
        sock.frames.clear()
    await RoundBroadcaster(room.conn, _messages()).send_timer_tick(
        room.game, {}, 5.0
    )
    assert room.who_got("timer_tick") == {"admin", "dashboard"}


# ---------------------------------------------------------------------------
# Hot Seat: three different payloads, and the television must not learn the
# answer (#604)
# ---------------------------------------------------------------------------


def _hot_seat_room(tmp_path: Path) -> _Room:
    room = _Room(tmp_path, ["Anna", "Ben", "Cleo"])
    room.game.start_game(
        language="en", num_rounds=8, hot_seat_seed=7, lightning_enabled=False
    )
    # A percentage of nothing buys nothing, so the auction needs a room with
    # points on the board before anyone can outbid anyone.
    for name, score in (("Anna", 40), ("Ben", 25), ("Cleo", 10)):
        room.game.get_player(name).score = score
    room.game.phase = GamePhase.ANSWER_REVEAL
    assert room.game.start_hot_seat_auction() is True
    return room


@pytest.mark.asyncio
async def test_hot_seat_driver_splits_the_question_three_ways(
    tmp_path: Path,
) -> None:
    """The real ``HotSeatDriver`` against the real ``HotSeatBroadcaster``.

    The chair answers, the spectators stake, the television watches — three
    audiences, three payloads off one dict. What must survive the move: only
    the seat holder gets answer buttons, only the admin socket gets
    ``correct_index`` (a dashboard takes no token, #604), and the settlement
    reaches everyone.
    """
    room = _hot_seat_room(tmp_path)
    hs = room.game.hot_seat
    assert hs is not None
    hs.answer_seconds = 0.2
    # Every connected player bids, so the auction closes on the first poll.
    assert hs.record_bid("Anna", 80) is True
    assert hs.record_bid("Ben", 10) is True
    assert hs.record_bid("Cleo", 5) is True

    resumed: list[Any] = []

    async def _resume(game_state: Any) -> None:
        resumed.append(game_state)

    driver = HotSeatDriver(
        HotSeatBroadcaster(
            room.conn, _messages(), resume_normal_question=_resume
        ),
        milestones=None,
        reveal_hold=0.0,
    )
    await driver.run(room.game)

    assert resumed == [], "somebody bid, so the detour must not be abandoned"
    seat = hs.seat_holder
    assert seat in ("Anna", "Ben", "Cleo")
    spectators = [n for n in ("Anna", "Ben", "Cleo") if n != seat]

    # The chair.
    chair_frame = room.socks[seat].one("hot_seat_question")
    assert chair_frame["you_are_seated"] is True
    assert chair_frame["answers"] == hs.shuffled_answers()
    assert "correct_index" not in chair_frame

    # The spectators: the question, no buttons, no answer.
    for name in spectators:
        frame = room.socks[name].one("hot_seat_question")
        assert frame["you_are_seated"] is False
        assert frame["answers"] == []
        assert "correct_index" not in frame

    # The host sees the correct tile; the television must not (#604).
    admin_frame = room.admin.one("hot_seat_question")
    assert admin_frame["correct_index"] == hs.correct_index
    assert admin_frame["answers"] == hs.shuffled_answers()
    tv_frame = room.dashboard.one("hot_seat_question")
    assert "correct_index" not in tv_frame, (
        "a dashboard needs no token — it must not learn the answer early"
    )
    assert tv_frame["answers"] == hs.shuffled_answers()

    # The room-wide beats reach every seat.
    assert room.who_got("hot_seat_awarded") == set(room.socks)
    assert room.who_got("hot_seat_result") == set(room.socks)
    for label in room.socks:
        assert room.socks[label].of_type("hot_seat_tick")


@pytest.mark.asyncio
async def test_an_auction_nobody_bid_on_leaves_the_detour(tmp_path: Path) -> None:
    """The escape hatch is not a frame, and it still has to work.

    ``resume_normal_question`` is the one protocol member the broadcaster does
    not own — it drives the round rather than describing it, so the handler
    keeps it and the broadcaster forwards. Pinned because a forward that goes
    nowhere leaves the game parked in an empty auction.
    """
    room = _hot_seat_room(tmp_path)
    hs = room.game.hot_seat
    assert hs is not None
    hs.auction_seconds = 0.05

    resumed: list[Any] = []

    async def _resume(game_state: Any) -> None:
        resumed.append(game_state)

    driver = HotSeatDriver(
        HotSeatBroadcaster(
            room.conn, _messages(), resume_normal_question=_resume
        ),
        milestones=None,
        reveal_hold=0.0,
    )
    await driver.run(room.game)

    assert room.who_got("hot_seat_no_bids") == set(room.socks)
    assert resumed == [room.game]


# ---------------------------------------------------------------------------
# The wiring: the handler holds the broadcasters instead of being them
# ---------------------------------------------------------------------------


def test_the_handler_hands_drivers_the_broadcaster_not_itself(
    tmp_path: Path,
) -> None:
    """The regression #881 is about.

    Every driver used to be constructed with ``self``. Type-checking cannot
    catch a relapse — the handler satisfied the protocols structurally, which
    is exactly why the narrow contract meant nothing — so it is pinned here by
    identity.
    """
    game = QuizifyGameState(runtime=_Runtime(tmp_path), entry_id="t")
    handler = QuizifyWebSocketHandler(
        runtime=_Runtime(tmp_path), game_state_provider=lambda: game
    )
    outs = [
        handler._lightning_out,
        handler._hot_seat_out,
        handler._round_out,
        handler._wager_out,
    ]
    for out in outs:
        assert out is not handler
        assert not isinstance(out, QuizifyWebSocketHandler)
    # And each one is stable, so a driver armed twice talks to the same object.
    assert handler._lightning_out is outs[0]
    assert handler._round_out is outs[2]


def test_the_broadcasters_follow_a_swapped_connection_manager(
    tmp_path: Path,
) -> None:
    """Held by provider, not captured once.

    The handler's connection manager is replaced after construction on several
    paths (and by much of this suite). A broadcaster that captured the original
    would keep writing into a manager nobody is listening to — silently, since
    every send swallows its errors.
    """
    game = QuizifyGameState(runtime=_Runtime(tmp_path), entry_id="t")
    handler = QuizifyWebSocketHandler(
        runtime=_Runtime(tmp_path), game_state_provider=lambda: game
    )
    first = handler._round_out._conn
    replacement = ConnectionManager(_Runtime(tmp_path), lambda: game)
    handler._conn = replacement

    assert handler._round_out._conn is replacement
    assert handler._round_out._conn is not first
    assert handler._lightning_out._conn is replacement
