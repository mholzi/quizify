"""Hot Seat auction — one player buys the chair, the room bets on them (#616).

A self-contained special mode, built the same way as ``lightning.py``: all the
rules live here as synchronous step methods, the WebSocket layer only drives
them. Nothing in this module touches asyncio, so every rule below is unit
testable without an event loop.

The shape was decided in full on 2026-08-24, and each decision closes a door
that the issue left open:

* **The chair is auctioned.** Players bid; the highest bid takes the seat and
  answers one question alone. Nobody is placed in the chair by rank, so the
  mode carries none of the "everyone stares at the weakest player" cost the
  issue itself flagged.

* **A bid is a SHARE of one's own score, 0-100 percent** — never an absolute
  number of points. Bid in points and the auction always goes to whoever is
  already ahead, which inverts the comeback lever this mode exists for. In
  percent everyone can commit everything, so the seat goes to whoever risks
  the most rather than whoever holds the most — and it is *cheapest* for the
  player in last place. This also matches how the wager finale already stakes
  (``PlayerSession.wager`` is a percent), so the game gains no second notion
  of "an amount you put down".

* **Bidding is blind.** One submission each, revealed together. Live bids on
  the TV would turn a short window into a race for the last tap.

* **Settlement is symmetric**, like the wager finale: right gives the stake,
  wrong takes it.

* **Winning means paying.** A seat holder who never answers loses the stake
  exactly as if they had answered wrongly. This is the one place the mode
  deliberately contradicts the finale, where an unanswered wager is left
  unsettled (#301) so a sleeping phone costs nothing. It cannot be inherited
  here: the stake buys the chair, so "bid, then let the clock run out" would
  mean taking the spotlight, clearing the field and paying nothing — which is
  what any locked screen does on its own, no bad intent required. (#653 brings
  the finale in line, so this ends up being one rule rather than two.)

* **The seat holder does not bet.** Otherwise they could buy the chair, bet
  on their own failure and answer wrongly on purpose, cashing the hedge for a
  question they never had to know.

* **It fires automatically, once per game**, at a randomly picked round — the
  same shape as the auto Lightning Round (#285), and skipped entirely below
  ``HOT_SEAT_MIN_PLAYERS`` because an auction needs bidders.

Ties go to the worse-placed player: equal appetite, and the mode is a comeback
lever, so the tie-break points the same way the mode does.
"""

from __future__ import annotations

import logging
import random
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover - typing only
    from .questions import Question, QuestionBank

_LOGGER = logging.getLogger(__name__)

# Decided rules (#616, 2026-08-24).
HOT_SEAT_AUCTION_SECONDS = 12.0
HOT_SEAT_ANSWER_SECONDS = 25.0
#: Below this, there is nobody to bid against — the detour is skipped.
HOT_SEAT_MIN_PLAYERS = 3

#: The two sides of a spectator bet.
BET_WILL = "will"
BET_WONT = "wont"
BET_SIDES = (BET_WILL, BET_WONT)


def stake_of(score: int, pct: int) -> int:
    """Absolute points a ``pct`` bid costs a player holding ``score``.

    Mirrors the wager finale's conversion (``scoring_engine`` computes
    ``int(bank * wager / 100)`` against a floor-zero bank), so a percentage
    means the same thing everywhere in the game. A negative score — reachable
    only transiently — banks as zero rather than inverting the stake.
    """
    bank = max(0, score)
    return int(bank * max(0, min(100, pct)) / 100)


@dataclass
class Bid:
    """One player's sealed bid for the chair."""

    name: str
    pct: int


@dataclass
class Bet:
    """One spectator's stake on the seat holder's outcome."""

    name: str
    side: str
    pct: int


class HotSeatRound:
    """One auction plus the single question it pays for.

    Lifecycle, driven from outside:

    ``start()`` → ``record_bid()`` × n → ``resolve_auction()`` →
    ``record_bet()`` × n → ``record_answer()`` (or nothing, on timeout) →
    ``settle()``.
    """

    def __init__(
        self,
        bank: QuestionBank,
        scores: dict[str, int],
        *,
        language: str | None = None,
        category: str | None = None,
        categories: list[str] | None = None,
        difficulty: str | None = None,
        auction_seconds: float = HOT_SEAT_AUCTION_SECONDS,
        answer_seconds: float = HOT_SEAT_ANSWER_SECONDS,
    ) -> None:
        """Initialize with a snapshot of who is playing and what they hold.

        ``scores`` is a snapshot on purpose: bids are percentages of the score
        a player had when the auction opened, so a late-arriving reaction
        bonus cannot silently change what a sealed bid costs.
        """
        self._bank = bank
        self.scores = dict(scores)
        self.language = language
        self.category = category
        self.categories = categories
        self.difficulty = difficulty
        self.auction_seconds = auction_seconds
        self.answer_seconds = answer_seconds

        self.question: Question | None = None
        self.bids: dict[str, Bid] = {}
        self.bets: dict[str, Bet] = {}
        self.winner: str | None = None
        self.answered: bool | None = None  # True/False once answered, else None
        self.answer_index: int | None = None
        self.settled: dict[str, int] | None = None

        self._shuffle: list[int] = []
        self._clock_start: float | None = None
        self._window: float = auction_seconds

    # ------------------------------------------------------------------
    # Setup
    # ------------------------------------------------------------------

    def start(self, *, reserve: int = 0) -> bool:
        """Claim one question for the chair. False means "skip the detour".

        ``reserve`` is how many questions the main game still owes its rounds.
        Exactly as in the Lightning Round (#544), a question already queued for
        a later round is only taken when more than ``reserve`` of them remain —
        a bonus mode must never spend the main game's questions and end it
        early.
        """
        if len(self.scores) < HOT_SEAT_MIN_PLAYERS:
            _LOGGER.info(
                "Hot seat skipped: %d players, needs %d",
                len(self.scores),
                HOT_SEAT_MIN_PLAYERS,
            )
            return False

        self._bank.load_all_categories()

        # "auto" is a group-adaptive mode, not a per-question difficulty; no
        # question carries it, so filtering on it returns an empty pool.
        difficulty = None if self.difficulty in (None, "auto") else self.difficulty
        pool = self._bank.build_pool(
            category=self.category,
            categories=self.categories,
            difficulty=difficulty,
            language=self.language,
            exclude_ids=self._bank.shown_this_game_ids(),
        )

        queued_ids = self._bank.remaining_queue_ids()
        spare = max(0, len(queued_ids) - reserve)

        for q in pool:
            # An estimate question (#275) has no answer grid, and a bet on
            # "will they get it" has no meaning against a slider — the seat
            # view would render an empty card. Skip them.
            if getattr(q, "is_estimate", False):
                continue
            if q.id in queued_ids and spare <= 0:
                continue  # promised to a later round
            self.question = q
            self._bank.record_shown(q.id)
            self._bank.drop_from_queue({q.id})
            break

        if self.question is None:
            _LOGGER.info("Hot seat skipped: no question available")
            return False

        order = list(range(len(self.question.answers)))
        random.shuffle(order)
        self._shuffle = order
        self.start_auction_clock()
        return True

    # ------------------------------------------------------------------
    # Clock — one primitive, two windows
    # ------------------------------------------------------------------

    def start_auction_clock(self) -> None:
        """(Re)start the bidding window."""
        self._clock_start = time.monotonic()
        self._window = self.auction_seconds

    def start_answer_clock(self) -> None:
        """(Re)start the seat holder's answer window."""
        self._clock_start = time.monotonic()
        self._window = self.answer_seconds

    def time_remaining(self) -> float:
        """Seconds left on whichever window is running (>= 0)."""
        if self._clock_start is None:
            return self._window
        return max(0.0, self._window - (time.monotonic() - self._clock_start))

    def is_expired(self) -> bool:
        """True once the running window has elapsed."""
        return self.time_remaining() <= 0.0

    # ------------------------------------------------------------------
    # Auction
    # ------------------------------------------------------------------

    def record_bid(self, name: str, pct: int) -> bool:
        """Record a sealed bid. False if rejected.

        One bid per player: the window is blind, so allowing a second
        submission would let a client re-bid on nothing but its own nerves and
        make "sealed" a matter of who tapped last.
        """
        if self.winner is not None or name not in self.scores:
            return False
        if name in self.bids:
            return False
        if not isinstance(pct, int) or not 0 <= pct <= 100:
            return False
        self.bids[name] = Bid(name=name, pct=pct)
        return True

    def all_bid(self, connected: list[str]) -> bool:
        """True once every connected player has bid — lets the driver cut short."""
        eligible = [n for n in connected if n in self.scores]
        return bool(eligible) and all(n in self.bids for n in eligible)

    def resolve_auction(self) -> str | None:
        """Award the chair. Returns the winner, or None if nobody bid above 0.

        Ranked by percentage, not by the points that percentage buys — that is
        the whole point of bidding a share. Ties go to the player with the
        *lowest* score; equal appetite decided in favour of whoever needs the
        comeback, which is the direction the mode exists to push.
        """
        if self.winner is not None:
            return self.winner
        # A bid must cost something. Ranking on percentage alone lets a player
        # sitting on zero points bid 100 % — nominally the highest bid in the
        # room, actually a stake of nothing — and take the chair away from
        # someone offering real points. They would neither win nor lose
        # anything themselves, so the only effect is to block the round. With
        # nothing to pay you cannot buy the chair.
        live = [
            b
            for b in self.bids.values()
            if b.pct > 0 and stake_of(self.scores.get(b.name, 0), b.pct) > 0
        ]
        if not live:
            return None
        live.sort(key=lambda b: (-b.pct, self.scores.get(b.name, 0), b.name))
        self.winner = live[0].name
        self.start_answer_clock()
        return self.winner

    @property
    def winning_pct(self) -> int:
        """The winning bid as a percentage (0 when the auction found nobody)."""
        if self.winner is None:
            return 0
        return self.bids[self.winner].pct

    @property
    def winning_stake(self) -> int:
        """The winning bid in points, against the snapshot score."""
        if self.winner is None:
            return 0
        return stake_of(self.scores.get(self.winner, 0), self.winning_pct)

    def reveal(self) -> list[dict]:
        """Every bid, for the simultaneous TV reveal — highest first."""
        rows = [
            {
                "name": b.name,
                "pct": b.pct,
                "points": stake_of(self.scores.get(b.name, 0), b.pct),
            }
            for b in self.bids.values()
        ]
        rows.sort(key=lambda r: (-r["pct"], self.scores.get(r["name"], 0), r["name"]))
        return rows

    # ------------------------------------------------------------------
    # Spectator bets
    # ------------------------------------------------------------------

    def record_bet(self, name: str, side: str, pct: int) -> bool:
        """Record a spectator's stake on the outcome. False if rejected.

        The seat holder is refused here rather than filtered later: they could
        otherwise back their own failure and then supply it, which turns a
        question they cannot answer into a profit.
        """
        if self.winner is None or self.answered is not None:
            return False
        if name == self.winner or name not in self.scores:
            return False
        if name in self.bets:
            return False
        if side not in BET_SIDES:
            return False
        if not isinstance(pct, int) or not 0 <= pct <= 100:
            return False
        self.bets[name] = Bet(name=name, side=side, pct=pct)
        return True

    # ------------------------------------------------------------------
    # The question
    # ------------------------------------------------------------------

    def shuffled_answers(self) -> list[str]:
        """Answer texts in the seat holder's shuffled order."""
        if self.question is None:
            return []
        order = self._shuffle or list(range(len(self.question.answers)))
        return [self.question.answers[i].text for i in order]

    def record_answer(self, name: str, shuffled_index: int) -> bool | None:
        """Record the seat holder's answer. None if the answer was refused."""
        if self.question is None or self.winner is None:
            return None
        if name != self.winner or self.answered is not None:
            return None
        if self.is_expired():
            return None
        order = self._shuffle or list(range(len(self.question.answers)))
        if not 0 <= shuffled_index < len(order):
            return None
        original = order[shuffled_index]
        self.answer_index = original
        self.answered = bool(self.question.answers[original].correct)
        return self.answered

    @property
    def correct_index(self) -> int | None:
        """Index of the correct answer in canonical order."""
        if self.question is None:
            return None
        for i, a in enumerate(self.question.answers):
            if a.correct:
                return i
        return None

    # ------------------------------------------------------------------
    # Settlement
    # ------------------------------------------------------------------

    def settle(self) -> dict[str, int]:
        """Point deltas for everyone involved. Idempotent.

        An unanswered question settles as a miss — see the module docstring:
        the chair was bought either way.
        """
        if self.settled is not None:
            return self.settled

        deltas: dict[str, int] = {}
        if self.winner is None:
            self.settled = deltas
            return deltas

        got_it = self.answered is True
        seat_score = self.scores.get(self.winner, 0)
        stake = stake_of(seat_score, self.winning_pct)
        deltas[self.winner] = stake if got_it else -min(stake, max(0, seat_score))

        for bet in self.bets.values():
            predicted = (bet.side == BET_WILL) == got_it
            score = self.scores.get(bet.name, 0)
            amount = stake_of(score, bet.pct)
            deltas[bet.name] = amount if predicted else -min(amount, max(0, score))

        self.settled = deltas
        return deltas

    def summary(self) -> dict:
        """Everything the reveal screen needs, after ``settle()``."""
        deltas = self.settle()
        return {
            "winner": self.winner,
            "winner_pct": self.winning_pct,
            "winner_stake": self.winning_stake,
            "answered": self.answered,
            "correct_index": self.correct_index,
            "answer_index": self.answer_index,
            "bids": self.reveal(),
            "bets": [
                {
                    "name": b.name,
                    "side": b.side,
                    "pct": b.pct,
                    "points": stake_of(self.scores.get(b.name, 0), b.pct),
                    "delta": deltas.get(b.name, 0),
                }
                for b in self.bets.values()
            ],
            "deltas": deltas,
        }
