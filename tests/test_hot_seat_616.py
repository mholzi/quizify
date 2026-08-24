"""Hot Seat auction rules (#616).

Every decision from 2026-08-24 gets a test that fails if it is quietly
reversed later. The two that matter most are the ones that were arguments
before they were code: bidding a *share* rather than points (without it the
leader wins every auction, which inverts the mode), and paying on timeout
(without it the chair is free for anyone whose phone sleeps).
"""

from __future__ import annotations

import pytest

from custom_components.quizify.game.hot_seat import (
    BET_WILL,
    BET_WONT,
    HOT_SEAT_MIN_PLAYERS,
    HotSeatRound,
    stake_of,
)
from custom_components.quizify.game.questions import Answer, Question


class _Bank:
    """Minimal QuestionBank stand-in — the auction only needs a pool."""

    def __init__(self, questions: list[Question], queued: set[str] | None = None):
        self._questions = questions
        self._queued = queued or set()
        self.shown: list[str] = []
        self.dropped: set[str] = set()

    def load_all_categories(self) -> None:
        pass

    def build_pool(self, **_kwargs) -> list[Question]:
        return list(self._questions)

    def shown_this_game_ids(self) -> set[str]:
        return set()

    def remaining_queue_ids(self) -> set[str]:
        return set(self._queued)

    def record_shown(self, qid: str) -> None:
        self.shown.append(qid)

    def drop_from_queue(self, ids: set[str]) -> None:
        self.dropped |= ids


def _q(qid: str = "q1", correct_at: int = 0) -> Question:
    return Question(
        id=qid,
        question=f"Question {qid}?",
        answers=[
            Answer(text=f"opt{i}", correct=(i == correct_at)) for i in range(4)
        ],
        difficulty="hard",
    )


def _round(scores: dict[str, int], questions=None, queued=None) -> HotSeatRound:
    bank = _Bank(questions if questions is not None else [_q()], queued)
    return HotSeatRound(bank, scores)


SCORES = {"Anna": 96, "Ben": 84, "Cem": 71, "Dana": 58, "Eli": 39}


# ----------------------------------------------------------------------
# stake_of — the percent→points conversion the whole mode rests on
# ----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("score", "pct", "expected"),
    [
        (100, 50, 50),
        (39, 100, 39),
        (96, 40, 38),   # the case from the design doc: 40% of the leader
        (0, 100, 0),    # nothing to stake, no crash
        (-5, 100, 0),   # a transient negative banks as zero, never inverts
        (50, 150, 50),  # out-of-range clamps rather than overpaying
        (50, -10, 0),
    ],
)
def test_stake_of(score: int, pct: int, expected: int) -> None:
    assert stake_of(score, pct) == expected


# ----------------------------------------------------------------------
# The decision that makes the mode work: share, not points
# ----------------------------------------------------------------------


def test_last_place_can_outbid_the_leader() -> None:
    """The whole reason bids are percentages.

    Eli holds 39 points to Anna's 96 and still takes the chair by wanting it
    more. In absolute points this is impossible, which is why the absolute
    variant was rejected.
    """
    hs = _round(SCORES)
    assert hs.start() is True
    assert hs.record_bid("Anna", 40) is True
    assert hs.record_bid("Eli", 100) is True
    assert hs.resolve_auction() == "Eli"
    # And it costs him less in points than Anna offered.
    assert hs.winning_stake == 39
    assert stake_of(SCORES["Anna"], 40) == 38


def test_ties_go_to_the_worse_placed_player() -> None:
    hs = _round(SCORES)
    hs.start()
    hs.record_bid("Anna", 50)
    hs.record_bid("Cem", 50)
    hs.record_bid("Eli", 50)
    assert hs.resolve_auction() == "Eli"


def test_zero_bids_award_nobody() -> None:
    """A chair nobody wants is not a round — the driver plays on instead."""
    hs = _round(SCORES)
    hs.start()
    hs.record_bid("Anna", 0)
    hs.record_bid("Eli", 0)
    assert hs.resolve_auction() is None
    assert hs.settle() == {}


def test_one_bid_per_player() -> None:
    """Sealed means sealed: a second submission would reward the last tap."""
    hs = _round(SCORES)
    hs.start()
    assert hs.record_bid("Eli", 10) is True
    assert hs.record_bid("Eli", 90) is False
    assert hs.bids["Eli"].pct == 10


@pytest.mark.parametrize("bad", [-1, 101, 1000])
def test_bid_range_is_enforced(bad: int) -> None:
    hs = _round(SCORES)
    hs.start()
    assert hs.record_bid("Eli", bad) is False


def test_stranger_cannot_bid() -> None:
    hs = _round(SCORES)
    hs.start()
    assert hs.record_bid("Nobody", 50) is False


# ----------------------------------------------------------------------
# Settlement — symmetric, and unforgiving on timeout
# ----------------------------------------------------------------------


def test_correct_answer_pays_the_stake() -> None:
    hs = _round(SCORES)
    hs.start()
    hs.record_bid("Eli", 100)
    hs.resolve_auction()
    order = hs._shuffle
    hs.record_answer("Eli", order.index(0))  # index 0 is the correct one
    assert hs.settle()["Eli"] == 39


def test_wrong_answer_costs_the_stake() -> None:
    hs = _round(SCORES)
    hs.start()
    hs.record_bid("Eli", 100)
    hs.resolve_auction()
    order = hs._shuffle
    hs.record_answer("Eli", order.index(1))  # index 1 is wrong
    assert hs.settle()["Eli"] == -39


def test_no_answer_costs_the_stake_too() -> None:
    """#653's rule, applied here first.

    The finale leaves an unanswered wager unsettled so a sleeping phone costs
    nothing. An auction cannot inherit that: the chair was bought either way,
    and "bid then sit out" would otherwise be free — which is what a locked
    screen does on its own.
    """
    hs = _round(SCORES)
    hs.start()
    hs.record_bid("Eli", 100)
    hs.resolve_auction()
    assert hs.answered is None  # nobody ever answered
    assert hs.settle()["Eli"] == -39


def test_loss_never_pushes_below_the_bank() -> None:
    hs = _round({"Anna": 10, "Ben": 10, "Cem": 4})
    hs.start()
    hs.record_bid("Anna", 50)
    assert hs.resolve_auction() == "Anna"
    assert hs.settle()["Anna"] == -5


def test_a_broke_player_cannot_block_the_chair() -> None:
    """100 % of nothing is the highest bid in the room and costs nothing.

    Found by a test that meant to check something else: a player on zero
    points outbids everyone on percentage, stakes nothing, wins nothing, and
    the only thing that happens is that nobody else gets the chair.
    """
    hs = _round({"Anna": 10, "Ben": 10, "Cem": 0})
    hs.start()
    hs.record_bid("Cem", 100)
    hs.record_bid("Anna", 50)
    assert hs.resolve_auction() == "Anna"


def test_an_all_broke_room_awards_nobody() -> None:
    hs = _round({"Anna": 0, "Ben": 0, "Cem": 0})
    hs.start()
    hs.record_bid("Anna", 100)
    hs.record_bid("Ben", 100)
    assert hs.resolve_auction() is None


def test_settle_is_idempotent() -> None:
    hs = _round(SCORES)
    hs.start()
    hs.record_bid("Eli", 50)
    hs.resolve_auction()
    first = hs.settle()
    assert hs.settle() is first


# ----------------------------------------------------------------------
# Spectator bets
# ----------------------------------------------------------------------


def test_correct_prediction_pays() -> None:
    hs = _round(SCORES)
    hs.start()
    hs.record_bid("Eli", 50)
    hs.resolve_auction()
    assert hs.record_bet("Anna", BET_WILL, 25) is True
    assert hs.record_bet("Ben", BET_WONT, 50) is True
    hs.record_answer("Eli", hs._shuffle.index(0))  # correct
    deltas = hs.settle()
    assert deltas["Anna"] == stake_of(96, 25)     # backed him, right
    assert deltas["Ben"] == -stake_of(84, 50)     # backed against, wrong


def test_bet_against_pays_on_a_miss() -> None:
    hs = _round(SCORES)
    hs.start()
    hs.record_bid("Eli", 50)
    hs.resolve_auction()
    hs.record_bet("Ben", BET_WONT, 50)
    hs.record_answer("Eli", hs._shuffle.index(1))  # wrong
    assert hs.settle()["Ben"] == stake_of(84, 50)


def test_a_timeout_settles_the_bets_as_a_miss() -> None:
    hs = _round(SCORES)
    hs.start()
    hs.record_bid("Eli", 50)
    hs.resolve_auction()
    hs.record_bet("Ben", BET_WONT, 50)
    assert hs.settle()["Ben"] == stake_of(84, 50)


def test_the_seat_holder_cannot_bet() -> None:
    """Otherwise: buy the chair, back your own failure, answer wrongly, profit."""
    hs = _round(SCORES)
    hs.start()
    hs.record_bid("Eli", 50)
    hs.resolve_auction()
    assert hs.record_bet("Eli", BET_WONT, 100) is False


def test_betting_is_optional() -> None:
    hs = _round(SCORES)
    hs.start()
    hs.record_bid("Eli", 50)
    hs.resolve_auction()
    hs.record_answer("Eli", hs._shuffle.index(0))
    deltas = hs.settle()
    assert set(deltas) == {"Eli"}  # nobody who abstained is touched


def test_bets_close_once_the_answer_is_in() -> None:
    hs = _round(SCORES)
    hs.start()
    hs.record_bid("Eli", 50)
    hs.resolve_auction()
    hs.record_answer("Eli", hs._shuffle.index(0))
    assert hs.record_bet("Anna", BET_WILL, 50) is False


def test_one_bet_per_spectator() -> None:
    hs = _round(SCORES)
    hs.start()
    hs.record_bid("Eli", 50)
    hs.resolve_auction()
    assert hs.record_bet("Anna", BET_WILL, 10) is True
    assert hs.record_bet("Anna", BET_WONT, 90) is False


@pytest.mark.parametrize("side", ["maybe", "", None, "WILL"])
def test_bet_side_must_be_one_of_two(side) -> None:
    hs = _round(SCORES)
    hs.start()
    hs.record_bid("Eli", 50)
    hs.resolve_auction()
    assert hs.record_bet("Anna", side, 50) is False


# ----------------------------------------------------------------------
# Answering
# ----------------------------------------------------------------------


def test_only_the_seat_holder_may_answer() -> None:
    hs = _round(SCORES)
    hs.start()
    hs.record_bid("Eli", 50)
    hs.resolve_auction()
    assert hs.record_answer("Anna", 0) is None
    assert hs.answered is None


def test_one_answer_only() -> None:
    hs = _round(SCORES)
    hs.start()
    hs.record_bid("Eli", 50)
    hs.resolve_auction()
    hs.record_answer("Eli", hs._shuffle.index(1))  # wrong, locked in
    assert hs.record_answer("Eli", hs._shuffle.index(0)) is None
    assert hs.answered is False


def test_answer_index_is_mapped_through_the_shuffle() -> None:
    """The tap is a button position, not a question index — #253's lesson."""
    hs = _round(SCORES)
    hs.start()
    hs.record_bid("Eli", 50)
    hs.resolve_auction()
    correct_button = hs._shuffle.index(0)
    assert hs.record_answer("Eli", correct_button) is True
    assert hs.answer_index == 0


@pytest.mark.parametrize("bad", [-1, 4, 99])
def test_out_of_range_taps_are_refused(bad: int) -> None:
    hs = _round(SCORES)
    hs.start()
    hs.record_bid("Eli", 50)
    hs.resolve_auction()
    assert hs.record_answer("Eli", bad) is None


def test_shuffled_answers_are_a_permutation() -> None:
    hs = _round(SCORES)
    hs.start()
    assert sorted(hs.shuffled_answers()) == sorted(
        a.text for a in hs.question.answers
    )


# ----------------------------------------------------------------------
# Setup guards
# ----------------------------------------------------------------------


def test_too_few_players_skips_the_auction() -> None:
    hs = _round({"Anna": 10, "Ben": 10})
    assert len(hs.scores) < HOT_SEAT_MIN_PLAYERS
    assert hs.start() is False


def test_estimate_questions_are_skipped() -> None:
    """A bet on "will they get it" means nothing against a slider."""
    est = _q("est")
    est.answers = []
    est.type = "estimate"
    ok = _q("mc")
    hs = _round(SCORES, questions=[est, ok])
    assert hs.start() is True
    assert hs.question.id == "mc"


def test_a_question_owed_to_a_later_round_is_left_alone() -> None:
    """#544's rule: a bonus mode must not spend the main game's queue."""
    q = _q("only")
    hs = _round(SCORES, questions=[q], queued={"only"})
    assert hs.start(reserve=1) is False
    assert hs.question is None


def test_a_spare_queued_question_may_be_claimed() -> None:
    q = _q("spare")
    hs = _round(SCORES, questions=[q], queued={"spare"})
    assert hs.start(reserve=0) is True
    assert hs.question.id == "spare"
    assert "spare" in hs._bank.dropped


def test_no_questions_available_skips() -> None:
    hs = _round(SCORES, questions=[])
    assert hs.start() is False


# ----------------------------------------------------------------------
# Reporting
# ----------------------------------------------------------------------


def test_reveal_lists_every_bid_highest_first() -> None:
    hs = _round(SCORES)
    hs.start()
    hs.record_bid("Anna", 10)
    hs.record_bid("Eli", 90)
    hs.record_bid("Cem", 50)
    rows = hs.reveal()
    assert [r["name"] for r in rows] == ["Eli", "Cem", "Anna"]
    assert rows[0]["points"] == stake_of(39, 90)


def test_summary_carries_the_deltas() -> None:
    hs = _round(SCORES)
    hs.start()
    hs.record_bid("Eli", 100)
    hs.resolve_auction()
    hs.record_bet("Anna", BET_WILL, 50)
    hs.record_answer("Eli", hs._shuffle.index(0))
    summary = hs.summary()
    assert summary["winner"] == "Eli"
    assert summary["winner_pct"] == 100
    assert summary["answered"] is True
    assert summary["deltas"]["Eli"] == 39
    assert summary["bets"][0]["name"] == "Anna"
    assert summary["bets"][0]["delta"] == stake_of(96, 50)


def test_all_bid_lets_the_window_close_early() -> None:
    hs = _round(SCORES)
    hs.start()
    connected = ["Anna", "Eli"]
    assert hs.all_bid(connected) is False
    hs.record_bid("Anna", 10)
    assert hs.all_bid(connected) is False
    hs.record_bid("Eli", 20)
    assert hs.all_bid(connected) is True


def test_all_bid_is_false_with_nobody_connected() -> None:
    hs = _round(SCORES)
    hs.start()
    assert hs.all_bid([]) is False
