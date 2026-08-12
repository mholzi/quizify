"""What each phone is told while a team forms and answers (#365, part 2).

The design was drawn from both sides on purpose: the two players never see the
same screen. She opens the team and names it while he still sees nothing; then
her screen waits while his suddenly has a list; and his joining has to land on
*her* screen or she cannot tell whether it worked. That is a property of the
messages, not of the CSS, so it is pinned here.

The other thing pinned here is the one that cannot be seen by looking at a
single phone: every player sees the answers in their own shuffled order (#253),
so the standing team answer has to be re-addressed per member. Sending one
index to the whole team puts the dots on the wrong row for everybody but the
person who tapped.
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT))

from custom_components.quizify.game.state import (  # noqa: E402
    GamePhase,
    QuizifyGameState,
)
from custom_components.quizify.server.connection import ConnectionManager  # noqa: E402
from custom_components.quizify.server.websocket import (  # noqa: E402
    QuizifyWebSocketHandler,
)


class _FakeRuntime:
    def __init__(self, tmp_path: Path) -> None:
        self.data_dir = tmp_path

    async def run_in_executor(self, func, *args):  # noqa: ANN001, ANN002
        return func(*args)

    def create_task(self, coro):  # noqa: ANN001
        import asyncio

        return asyncio.ensure_future(coro)


def _ws() -> MagicMock:
    ws = MagicMock()
    ws.closed = False
    ws.send_json = AsyncMock()
    ws.close = AsyncMock()
    return ws


@pytest.fixture
def game(tmp_path: Path) -> QuizifyGameState:
    return QuizifyGameState(runtime=_FakeRuntime(tmp_path), entry_id="test")


@pytest.fixture
def h(game: QuizifyGameState, tmp_path: Path) -> QuizifyWebSocketHandler:
    """Handler with every outbound message recorded per socket."""
    runtime = _FakeRuntime(tmp_path)
    handler = QuizifyWebSocketHandler(
        runtime=runtime, game_state_provider=lambda: game
    )
    handler._conn = ConnectionManager(runtime, lambda: game)
    handler._get_game_state = lambda: game  # type: ignore[assignment]

    sent: dict[int, list[dict]] = {}
    broadcasts: list[dict] = []
    errors: dict[int, list[dict]] = {}

    async def _send(ws, message: dict) -> None:
        sent.setdefault(id(ws), []).append(message)

    async def _broadcast(message: dict) -> None:
        broadcasts.append(message)

    async def _send_error(ws, code, message) -> None:
        errors.setdefault(id(ws), []).append({"code": code, "message": message})

    handler._conn.send = _send  # type: ignore[assignment]
    handler._conn.broadcast = _broadcast  # type: ignore[assignment]
    handler._conn.send_error = _send_error  # type: ignore[assignment]
    handler._sent = sent  # type: ignore[attr-defined]
    handler._broadcasts = broadcasts  # type: ignore[attr-defined]
    handler._errors = errors  # type: ignore[attr-defined]
    return handler


def _seat(h: QuizifyWebSocketHandler, game: QuizifyGameState, name: str) -> MagicMock:
    ws = _ws()
    h._conn.add_connection(ws, is_admin=False, is_dashboard=False)
    game.add_player(name, ws)
    return ws


def _to(h: QuizifyWebSocketHandler, ws: MagicMock) -> list[dict]:
    return h._sent.get(id(ws), [])  # type: ignore[attr-defined]


def _of_type(messages: list[dict], wanted: str) -> list[dict]:
    return [m for m in messages if m.get("type") == wanted]


# ----------------------------------------------------------------------
# Formation, from both sides
# ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_opening_a_team_confirms_to_her_and_shows_up_for_him(
    h: QuizifyWebSocketHandler, game: QuizifyGameState
) -> None:
    anna_ws = _seat(h, game, "Anna")
    _seat(h, game, "Jan")

    await h._handle_create_team(anna_ws, {"name": "Sofa"}, game)

    joined = _of_type(_to(h, anna_ws), "team_joined")
    assert joined, "the founder needs to know her team exists"
    assert joined[0]["team"]["name"] == "Sofa"

    update = _of_type(h._broadcasts, "teams_update")  # type: ignore[attr-defined]
    assert update, "the other phones only get a list if it is broadcast"
    assert [t["name"] for t in update[-1]["teams"]] == ["Sofa"]


@pytest.mark.asyncio
async def test_his_joining_lands_on_her_screen(
    h: QuizifyWebSocketHandler, game: QuizifyGameState
) -> None:
    """Without this broadcast the founder cannot tell whether it worked."""
    anna_ws = _seat(h, game, "Anna")
    jan_ws = _seat(h, game, "Jan")
    await h._handle_create_team(anna_ws, {"name": "Sofa"}, game)
    team_id = game.get_team_of("Anna")["team_id"]
    h._broadcasts.clear()  # type: ignore[attr-defined]

    await h._handle_join_team(jan_ws, {"team_id": team_id}, game)

    update = _of_type(h._broadcasts, "teams_update")  # type: ignore[attr-defined]
    assert update, "a join must reach the room, not just the joiner"
    assert update[-1]["teams"][0]["members"] == ["Anna", "Jan"]


@pytest.mark.asyncio
async def test_the_last_member_out_dissolves_the_team_for_everyone(
    h: QuizifyWebSocketHandler, game: QuizifyGameState
) -> None:
    anna_ws = _seat(h, game, "Anna")
    await h._handle_create_team(anna_ws, {"name": "Sofa"}, game)

    await h._handle_leave_team(anna_ws, {}, game)

    assert _of_type(_to(h, anna_ws), "team_left")
    assert h._broadcasts[-1]["teams"] == []  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_a_latecomer_after_the_start_is_told_teams_are_set(
    h: QuizifyWebSocketHandler, game: QuizifyGameState
) -> None:
    """The lobby has to answer this itself, or the host will be asked."""
    anna_ws = _seat(h, game, "Anna")
    await h._handle_create_team(anna_ws, {"name": "Sofa"}, game)
    late_ws = _seat(h, game, "Late")
    game.phase = GamePhase.QUESTION_ACTIVE
    team_id = game.get_team_of("Anna")["team_id"]

    await h._handle_join_team(late_ws, {"team_id": team_id}, game)

    assert h._errors.get(id(late_ws)), "a refusal has to say something"  # type: ignore[attr-defined]
    assert game.get_team_of("Late") is None


@pytest.mark.asyncio
async def test_joining_a_team_that_just_dissolved_is_refused_not_recreated(
    h: QuizifyWebSocketHandler, game: QuizifyGameState
) -> None:
    anna_ws = _seat(h, game, "Anna")
    jan_ws = _seat(h, game, "Jan")
    await h._handle_create_team(anna_ws, {"name": "Sofa"}, game)
    stale_id = game.get_team_of("Anna")["team_id"]
    await h._handle_leave_team(anna_ws, {}, game)

    await h._handle_join_team(jan_ws, {"team_id": stale_id}, game)

    assert h._errors.get(id(jan_ws))  # type: ignore[attr-defined]
    assert game.team_registry.all_teams() == []


# ----------------------------------------------------------------------
# The standing answer, per member
# ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_the_standing_answer_reaches_every_member_in_their_own_order(
    h: QuizifyWebSocketHandler, game: QuizifyGameState
) -> None:
    """The heart of the question screen: the dots must sit on the same answer.

    Anna and Jan see the four answers in different orders. Anna taps the
    answer at *her* position 0; Jan's client must be told the position that
    same answer occupies for *him*, not the number Anna tapped.
    """
    anna_ws = _seat(h, game, "Anna")
    jan_ws = _seat(h, game, "Jan")
    await h._handle_create_team(anna_ws, {"name": "Sofa"}, game)
    await h._handle_join_team(
        jan_ws, {"team_id": game.get_team_of("Anna")["team_id"]}, game
    )
    game.start_game(category="picture-round-en", difficulty="easy", num_rounds=2,
                    language="en")
    game.start_next_question()
    game.set_player_shuffle("Anna", [2, 0, 1, 3])
    game.set_player_shuffle("Jan", [0, 1, 2, 3])

    await h._handle_submit_answer(anna_ws, {"answer_index": 0}, game)

    anna_msg = _of_type(_to(h, anna_ws), "team_answer")
    jan_msg = _of_type(_to(h, jan_ws), "team_answer")
    assert anna_msg and jan_msg, "both members see what stands"
    # Canonical answer 2: Anna's position 0, Jan's position 2.
    assert anna_msg[-1]["answer_index"] == 0
    assert jan_msg[-1]["answer_index"] == 2
    assert jan_msg[-1]["set_by"] == "Anna"


@pytest.mark.asyncio
async def test_a_team_tap_does_not_send_an_answer_result(
    h: QuizifyWebSocketHandler, game: QuizifyGameState
) -> None:
    """Nothing is scored at tap time, so nothing may claim points."""
    anna_ws = _seat(h, game, "Anna")
    await h._handle_create_team(anna_ws, {"name": "Sofa"}, game)
    game.start_game(category="picture-round-en", difficulty="easy", num_rounds=2,
                    language="en")
    game.start_next_question()
    game.set_player_shuffle("Anna", [0, 1, 2, 3])

    await h._handle_submit_answer(anna_ws, {"answer_index": 0}, game)

    assert _of_type(_to(h, anna_ws), "team_answer"), "the tap has to be accepted"
    assert not _of_type(_to(h, anna_ws), "answer_result")


@pytest.mark.asyncio
async def test_the_lock_is_sent_to_the_whole_team(
    h: QuizifyWebSocketHandler, game: QuizifyGameState
) -> None:
    """It is the team's brake — one member's tap quiets everyone's buttons."""
    anna_ws = _seat(h, game, "Anna")
    jan_ws = _seat(h, game, "Jan")
    await h._handle_create_team(anna_ws, {"name": "Sofa"}, game)
    await h._handle_join_team(
        jan_ws, {"team_id": game.get_team_of("Anna")["team_id"]}, game
    )
    game.start_game(category="picture-round-en", difficulty="easy", num_rounds=2,
                    language="en")
    game.start_next_question()
    # The per-player shuffles are handed out by the round broadcast, which
    # does not run in this harness — set them so the tap maps to an answer.
    game.set_player_shuffle("Anna", [0, 1, 2, 3])
    game.set_player_shuffle("Jan", [0, 1, 2, 3])

    await h._handle_submit_answer(anna_ws, {"answer_index": 1}, game)

    for ws in (anna_ws, jan_ws):
        msg = _of_type(_to(h, ws), "team_answer")[-1]
        assert msg["lock_seconds"] > 0


@pytest.mark.asyncio
async def test_the_snapshot_always_carries_the_teams_key(
    game: QuizifyGameState,
) -> None:
    """A reconnecting phone must tell "no teams" from "teams not sent"."""
    snapshot = game.get_state_snapshot()

    assert snapshot["teams"] == []


# ----------------------------------------------------------------------
# The reveal
# ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_every_member_reads_the_same_reveal(
    h: QuizifyWebSocketHandler, game: QuizifyGameState
) -> None:
    """The reveal is about the team, so both members see the same round.

    Found by playing it: a team scores once, so only the member whose tap
    stood was carrying a result. The other one's reveal read "time's up — no
    answer given" for a round their team had answered.
    """
    anna_ws = _seat(h, game, "Anna")
    jan_ws = _seat(h, game, "Jan")
    await h._handle_create_team(anna_ws, {"name": "Sofa"}, game)
    await h._handle_join_team(
        jan_ws, {"team_id": game.get_team_of("Anna")["team_id"]}, game
    )
    game.start_game(category="picture-round-en", difficulty="easy", num_rounds=2,
                    language="en")
    game.start_next_question()
    game.set_player_shuffle("Anna", [0, 1, 2, 3])
    game.set_player_shuffle("Jan", [2, 1, 0, 3])
    await h._handle_submit_answer(anna_ws, {"answer_index": 0}, game)
    game.evaluate_round()

    summary = h._round_messages.build_round_summary(game)
    rows = {r["player_name"]: r for r in summary["all_answers"]}

    assert rows["Jan"].get("no_answer") is not True, "Jan's team did answer"
    assert rows["Anna"]["answer_text"] == rows["Jan"]["answer_text"]
    assert rows["Anna"]["correct"] == rows["Jan"]["correct"]
    assert rows["Anna"]["points_earned"] == rows["Jan"]["points_earned"]
    # The one thing that stays personal: where the correct answer sits on
    # THIS phone's buttons. The two shuffles differ, so the values must too.
    assert (
        rows["Anna"]["correct_button_index"] != rows["Jan"]["correct_button_index"]
    ), "the correct-button hint is about each player's own button order"


# ----------------------------------------------------------------------
# The lightning round (#552)
# ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_lightning_tap_reaches_the_team_not_the_scoreboard(
    h: QuizifyWebSocketHandler, game: QuizifyGameState
) -> None:
    """Same contract as a normal round, one screen further in.

    The tap sets the team's answer: no ``lightning_answer_result`` (which
    would lock the tapper's phone and claim right/wrong), and both members
    are told what stands — each in their own answer order.
    """
    anna_ws = _seat(h, game, "Anna")
    jan_ws = _seat(h, game, "Jan")
    await h._handle_create_team(anna_ws, {"name": "Sofa"}, game)
    await h._handle_join_team(
        jan_ws, {"team_id": game.get_team_of("Anna")["team_id"]}, game
    )
    game.start_lightning_round(category="picture-round-en", language="en")
    game.begin_lightning_questions()
    lr = game.lightning
    lr._shuffles["Anna"] = [2, 0, 1]
    lr._shuffles["Jan"] = [0, 1, 2]

    await h._handle_lightning_answer(anna_ws, {"answer_index": 0}, game)

    assert not _of_type(_to(h, anna_ws), "lightning_answer_result")
    anna_msg = _of_type(_to(h, anna_ws), "lightning_team_answer")
    jan_msg = _of_type(_to(h, jan_ws), "lightning_team_answer")
    assert anna_msg and jan_msg, "both members see the standing answer"
    # Canonical answer 2: Anna's position 0, Jan's position 2.
    assert anna_msg[-1]["answer_index"] == 0
    assert jan_msg[-1]["answer_index"] == 2
    assert jan_msg[-1]["set_by"] == "Anna"
    assert jan_msg[-1]["lock_seconds"] > 0


@pytest.mark.asyncio
async def test_a_solo_lightning_tap_is_unchanged(
    h: QuizifyWebSocketHandler, game: QuizifyGameState
) -> None:
    """A player in no team still gets the ordinary ack that locks her phone."""
    anna_ws = _seat(h, game, "Anna")
    mira_ws = _seat(h, game, "Mira")
    await h._handle_create_team(anna_ws, {"name": "Sofa"}, game)
    game.start_lightning_round(category="picture-round-en", language="en")
    game.begin_lightning_questions()
    game.lightning._shuffles["Mira"] = [0, 1, 2]

    await h._handle_lightning_answer(mira_ws, {"answer_index": 1}, game)

    assert _of_type(_to(h, mira_ws), "lightning_answer_result")
    assert not _of_type(_to(h, mira_ws), "lightning_team_answer")
