"""The Hot Seat detour belongs in the state contract (#664).

Everything about the detour (#616) was driven by one-shot events. Those are
never re-sent, so a reload during the detour fell through to a snapshot that
named a HOT_SEAT phase and carried no hot-seat data:

* the television kept the previous round's reveal frozen on it for the whole
  detour, sealed bids and all — the one moment the mode was built to put on
  the big screen;
* a phone that reloaded landed on the lobby, because the client's default
  case is the join view;
* and if that phone belonged to the seat holder, they could not return to the
  question. On expiry ``settle()`` charges the stake as a loss (#653), so a
  page reload cost them their entire bid.

Lightning got this treatment in #221/#296. These tests pin the same three
things for the Hot Seat, plus the two rules the block must not break: the
question stays hidden while bidding is open, and the bids stay sealed until
the auction closes.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from custom_components.quizify.game.phase_controller import GamePhase
from custom_components.quizify.game.state import QuizifyGameState
from custom_components.quizify.server.round_message_builder import (
    RoundMessageBuilder,
)


def _ws() -> MagicMock:
    ws = MagicMock()
    ws.closed = False
    return ws


class _Runtime:
    def __init__(self, tmp_path: Path) -> None:
        self.data_dir = tmp_path


@pytest.fixture
def game(tmp_path: Path) -> QuizifyGameState:
    st = QuizifyGameState(runtime=_Runtime(tmp_path), entry_id="test")
    for name in ("Anna", "Ben", "Mira"):
        st.add_player(name, _ws())
    st.start_game(category="picture-round-en", difficulty="easy",
                  num_rounds=5, language="en")
    for p in st.get_players():
        p.score = 40
    st.phase = GamePhase.ANSWER_REVEAL
    assert st.start_hot_seat_auction()
    return st


def _snapshot(game: QuizifyGameState) -> dict:
    return game.get_state_snapshot()


def _for_player(game: QuizifyGameState, name: str) -> dict:
    builder = RoundMessageBuilder()
    return builder.project_snapshot_for_player(
        game, snapshot=_snapshot(game), player=game.get_player(name)
    )


# ---------------------------------------------------------------------------
# The block exists at all — the hole this issue is about
# ---------------------------------------------------------------------------


def test_auction_phase_carries_a_hot_seat_block(game: QuizifyGameState) -> None:
    snap = _snapshot(game)
    assert snap["phase"] == GamePhase.HOT_SEAT_AUCTION.value
    assert snap["hot_seat"]["stage"] == "auction"
    assert snap["hot_seat"]["time_remaining"] > 0


def test_the_awarded_chair_reconnects_straight_into_the_question(
    game: QuizifyGameState,
) -> None:
    """No separate "bids are landing" stage, and that is on purpose.

    ``resolve_auction`` starts the answer clock as it awards the chair, so
    the live flow's four-second bid reveal is already being charged to the
    seat holder's window. A reconnect during it must land on the question,
    not on a reveal the player is paying for.
    """
    game.hot_seat.record_bid("Anna", 50)
    game.close_hot_seat_auction()
    block = _snapshot(game)["hot_seat"]
    assert block["stage"] == "question"
    assert block["question"]["text"]


def test_settled_detour_carries_its_summary(game: QuizifyGameState) -> None:
    game.hot_seat.record_bid("Anna", 50)
    game.close_hot_seat_auction()
    game.finish_hot_seat()
    block = _snapshot(game)["hot_seat"]
    assert block["stage"] == "result"
    assert block["summary"]


# ---------------------------------------------------------------------------
# What the block must NOT leak
# ---------------------------------------------------------------------------


def test_the_question_is_withheld_while_bidding_is_open(
    game: QuizifyGameState,
) -> None:
    """Bidding is a bet on yourself, not on a question you have read."""
    assert "question" not in _snapshot(game)["hot_seat"]


def test_bids_stay_sealed_until_the_auction_closes(
    game: QuizifyGameState,
) -> None:
    game.hot_seat.record_bid("Anna", 50)
    game.hot_seat.record_bid("Ben", 20)
    block = _snapshot(game)["hot_seat"]
    assert block["bid_count"] == 2
    assert "bids" not in block, "a reconnect must not be a way to read the auction"

    game.close_hot_seat_auction()
    assert len(_snapshot(game)["hot_seat"]["bids"]) == 2


# ---------------------------------------------------------------------------
# The player projection
# ---------------------------------------------------------------------------


def test_each_player_gets_their_own_bank_and_not_the_others(
    game: QuizifyGameState,
) -> None:
    game.get_player("Ben").score = 10
    game.hot_seat.scores["Ben"] = 10
    block = _for_player(game, "Ben")["hot_seat"]
    assert block["own_bank"] == 10
    assert "banks" not in block


def test_a_reconnecting_bidder_sees_their_own_bid(game: QuizifyGameState) -> None:
    game.hot_seat.record_bid("Anna", 50)
    assert _for_player(game, "Anna")["hot_seat"]["you_bid"] == 50
    assert _for_player(game, "Ben")["hot_seat"]["you_bid"] is None


def test_the_seat_holder_gets_the_question_in_their_own_shuffle(
    game: QuizifyGameState,
) -> None:
    game.hot_seat.record_bid("Anna", 50)
    game.close_hot_seat_auction()
    game.hot_seat.start_answer_clock()

    block = _for_player(game, "Anna")["hot_seat"]
    assert block["you_are_seated"] is True
    assert block["question"]["answers"] == game.hot_seat.shuffled_answers()


def test_spectators_get_the_question_text_but_no_answer_buttons(
    game: QuizifyGameState,
) -> None:
    """They stake on the seat holder; they do not answer."""
    game.hot_seat.record_bid("Anna", 50)
    game.close_hot_seat_auction()
    game.hot_seat.start_answer_clock()

    block = _for_player(game, "Ben")["hot_seat"]
    assert block["you_are_seated"] is False
    assert block["question"]["text"]
    assert "answers" not in block["question"]


def test_the_canonical_snapshot_keeps_answers_for_the_television(
    game: QuizifyGameState,
) -> None:
    game.hot_seat.record_bid("Anna", 50)
    game.close_hot_seat_auction()
    game.hot_seat.start_answer_clock()
    q = _snapshot(game)["hot_seat"]["question"]
    assert q["answers"] == [a.text for a in game.hot_seat.question.answers]


# ---------------------------------------------------------------------------
# The clients
# ---------------------------------------------------------------------------


_WWW = (
    Path(__file__).resolve().parent.parent
    / "custom_components" / "quizify" / "www"
)


@pytest.mark.parametrize(
    "path",
    ["js/player-core.js", "dashboard.html"],
    ids=["player", "television"],
)
def test_both_clients_handle_every_hot_seat_phase(path: str) -> None:
    """The server may not name a phase no client can render.

    A grep rather than a render: the point of #664 is that these three phase
    names existed on the wire and in neither switch, and that absence is
    exactly what a string search catches.
    """
    src = (_WWW / path).read_text(encoding="utf-8")
    for phase in ("HOT_SEAT_AUCTION", "HOT_SEAT_REVEAL"):
        assert phase in src, f"{path} has no case for {phase}"
    assert "'HOT_SEAT'" in src, f"{path} has no case for HOT_SEAT"
