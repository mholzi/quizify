"""HotSeatController — the Hot Seat detour's orchestration (#616, #1108).

Extracted from ``QuizifyGameState`` (issue #1108, continuing the split started
in #184/#188). The auction's *rules* already lived in ``hot_seat.py``
(:class:`HotSeatRound`); what stayed behind in ``state.py`` was the glue around
it — arming the target round, the once-per-game guard, the phase transitions
into and out of the detour, writing the settlement back onto the ranked
participants and building the reveal's :class:`RoundSummary`. That glue lives
here now.

Unlike :class:`PhaseController` this controller cannot be blind to the game: a
detour reads the roster, the question bank and the standings, and flips the
game's phase and round counter. It therefore holds a reference to the owning
``QuizifyGameState`` and reaches back through it, while owning its own six
fields outright. ``QuizifyGameState`` keeps every public name it had
(``hot_seat``, ``start_hot_seat_auction``, ``finish_hot_seat`` …) as a thin
delegate, so callers and tests are unchanged.
"""

from __future__ import annotations

import random
from typing import TYPE_CHECKING

from .phase_controller import GamePhase

if TYPE_CHECKING:  # pragma: no cover - typing only
    from .hot_seat import HotSeatRound
    from .state import QuizifyGameState, RoundSummary


class HotSeatController:
    """Owns the Hot Seat auction's arming, phase transitions and settlement."""

    def __init__(self, game: QuizifyGameState) -> None:
        """Bind to the owning game; nothing is armed until ``arm``."""
        self._game = game
        # Hot Seat auction (#616) — the second self-contained detour, armed
        # exactly like the Lightning Round and deliberately never in the same
        # round as it (see _pick_hot_seat_round).
        self._round: HotSeatRound | None = None
        self._enabled: bool = True
        self._target_round: int | None = None
        self._fired: bool = False
        self._round_to_resume: int = 0
        self._rng: random.Random = random.Random()

    def arm(self, num_rounds: int, *, enabled: bool, seed: int | None) -> None:
        """Arm the auction for a new game (#616): same shape as Lightning,
        same draw-once rule. Must run after the Lightning target is picked."""
        self._enabled = enabled
        self._fired = False
        self._round_to_resume = 0
        if seed is not None:
            self._rng = random.Random(seed)
        self._target_round = self._pick_hot_seat_round(num_rounds)

    def reset(self) -> None:
        """Forget the detour and its arming (reset to lobby)."""
        self._round = None
        self._target_round = None
        self._fired = False
        self._round_to_resume = 0

    def _pick_hot_seat_round(self, num_rounds: int) -> int | None:
        """Pick the round the Hot Seat auction fires before (#616).

        Same window as the Lightning Round — rounds ``3 … num_rounds-1`` — for
        the same two reasons: the game should establish itself first, and the
        last question stays the climax rather than being replaced by a detour.

        The draw additionally avoids the Lightning target. Two detours in one
        round would mean the second fires immediately after the first's recap,
        with no normal question between them; the round would read as broken
        even though both modes behaved correctly.
        """
        if not self._enabled:
            return None
        low, high = 3, num_rounds - 1
        if high < low:
            return None  # window empty → short game, no auction
        lightning_round = self._game.lightning_target_round
        candidates = [r for r in range(low, high + 1) if r != lightning_round]
        if not candidates:
            # A 4-round game has a one-round window that Lightning already
            # owns. Lightning was armed first, so it keeps it.
            return None
        return candidates[self._rng.randrange(len(candidates))]

    @property
    def hot_seat_target_round(self) -> int | None:
        """The round the Hot Seat auction fires before, or None (#616)."""
        return self._target_round

    @property
    def hot_seat(self) -> HotSeatRound | None:
        """The active HotSeatRound, or None."""
        return self._round

    @property
    def in_hot_seat_detour(self) -> bool:
        """True while the auction is interrupting a live game (#616)."""
        return self._round_to_resume > 0

    def should_trigger_hot_seat(self) -> bool:
        """True iff the Hot Seat auction should fire right now (#616).

        Consulted by the WS round-advance path before it starts the next
        normal question, exactly like ``should_trigger_lightning``. Fires once
        per game, only from a between-rounds phase.
        """
        if self._fired or self._target_round is None:
            return False
        # #669 gated this off in team mode, because bids were a percentage of
        # the bidder's own ``player.score`` — the shadow value #668 is about,
        # zero for everyone but the round's carrier — and the settlement wrote
        # its deltas there too, while the leaderboard, podium and awards all
        # read ``get_ranked_participants()``. #804 removes the gate by fixing
        # both ends: the auction now bids and settles per ENTRANT, so a team
        # stakes the score the television shows and is paid into the same row.
        if self._game.phase not in (GamePhase.LOBBY, GamePhase.ANSWER_REVEAL):
            return False
        return self._game.round + 1 == self._target_round

    def start_hot_seat_auction(self) -> bool:
        """Open the sealed bidding window. False means "skip, play on".

        False is a normal outcome, not an error: too few players to hold an
        auction, or no question the main game can spare (#544). Either way the
        once-per-game flag is burned so the attempt is not retried every round.
        """
        from .hot_seat import HotSeatRound  # local import — avoid cycle

        if self._game.phase not in (GamePhase.LOBBY, GamePhase.ANSWER_REVEAL):
            return False

        # Keyed by ENTRANT (#804): a team id in team mode, a player name
        # otherwise. ``get_ranked_participants`` is the same list the
        # leaderboard, the podium and the awards read, so the banks the bids
        # are shares of are the numbers on the television — and the deltas
        # ``finish_hot_seat`` writes back land on the same rows.
        scores = {
            self._game._entrant_key(participant): participant.score
            for participant in self._game.get_ranked_participants()
        }
        hs = HotSeatRound(
            self._game.question_bank,
            scores,
            language=self._game.language,
            category=self._game.category,
            categories=self._game.categories,
            difficulty=self._game.difficulty,
            teams=self._game.team_registry.to_list(),
        )
        # Same reservation rule as Lightning (#544): never spend a question the
        # main game still owes a later round.
        reserve = max(0, self._game.total_rounds - self._game.round)
        self._fired = True
        if not hs.start(reserve=reserve):
            return False

        self._round = hs
        self._round_to_resume = self._game.round
        self._game.phase = GamePhase.HOT_SEAT_AUCTION
        self._game._notify_state_callbacks()
        return True

    def close_hot_seat_auction(self) -> str | None:
        """Award the chair and move into the question. Returns the winner.

        None means nobody bid above zero — the driver treats that as "no
        auction happened" and resumes the normal round, because a chair
        nobody wanted is not a round worth playing.
        """
        if self._game.phase != GamePhase.HOT_SEAT_AUCTION or self._round is None:
            return None
        winner = self._round.resolve_auction()
        if winner is None:
            return None
        self._game.phase = GamePhase.HOT_SEAT
        self._game._notify_state_callbacks()
        return winner

    def finish_hot_seat(self) -> dict[str, int]:
        """Settle the round and move to the reveal. Returns the point deltas.

        Applying the deltas is this method's job rather than the caller's, so
        an unanswered question cannot quietly skip settlement — that is the
        exact failure this mode had to design against (#653).
        """
        if self._round is None:
            return {}
        deltas = self._round.settle()
        for entrant, delta in deltas.items():
            # The key is an entrant, so the payout follows whoever the ranking
            # is about (#804): the team, or the player who joined none. The
            # floor at zero is the rule every other score in state.py keeps.
            participant = self._game._participant_by_entrant(entrant)
            if participant is None:
                continue
            participant.score += delta
            if participant.score < 0:
                participant.score = 0
        if self._game.phase == GamePhase.HOT_SEAT:
            self._game.phase = GamePhase.HOT_SEAT_REVEAL
            # Mid-game: persist, but keep this game's shown set (#1019).
            self._game._flush_history(end_of_game=False)
            self._game._notify_state_callbacks()
        return deltas

    def hot_seat_round_summary(self) -> RoundSummary | None:
        """The settled chair's question as a :class:`RoundSummary` (#1092).

        Handed to the reveal beat instead of ``get_round_summary()``, which
        still holds the normal round before the detour — the narrator read
        that round's answer and the bus event carried its correct_count. Not
        stored on the game: once the detour resumes, ANSWER_REVEAL belongs to
        that normal round again. One result, the seat holder's.
        """
        hs = self._round
        if hs is None or hs.question is None or hs.settled is None:
            return None
        index = hs.correct_index
        if index is None:
            return None
        from .state import AnswerResult, RoundSummary  # local — avoid cycle

        results: list[AnswerResult] = []
        if hs.winner is not None:
            participant = self._game._participant_by_entrant(hs.winner)
            results.append(
                AnswerResult(
                    player_id=hs.seat_holder or hs.winner,
                    correct=hs.answered is True,
                    points_earned=hs.settled.get(hs.winner, 0),
                    new_streak=getattr(participant, "streak", 0),
                    new_total=getattr(participant, "score", 0),
                )
            )
        return RoundSummary(
            question=hs.question,
            correct_answer=hs.question.answers[index],
            fun_fact=hs.question.fun_fact,
            results=results,
            leaderboard=self._game.get_standings(),
        )

    def resume_after_hot_seat(self) -> bool:
        """Return to the paused main game after the reveal (#616).

        Mirrors ``resume_after_lightning``: flips back to ANSWER_REVEAL and
        restores the round counter so the next advance lands on the
        originally-scheduled round.
        """
        if (
            self._game.phase != GamePhase.HOT_SEAT_REVEAL
            or not self.in_hot_seat_detour
        ):
            return False
        self._game.round = self._round_to_resume
        self._round_to_resume = 0
        self._round = None
        self._game.phase = GamePhase.ANSWER_REVEAL
        self._game._notify_state_callbacks()
        return True

    def abort_hot_seat(self) -> None:
        """Drop an auction that found no bidder and return to the main game."""
        if self._round is None:
            return
        self._game.round = self._round_to_resume or self._game.round
        self._round_to_resume = 0
        self._round = None
        if self._game.phase in (GamePhase.HOT_SEAT_AUCTION, GamePhase.HOT_SEAT):
            self._game.phase = GamePhase.ANSWER_REVEAL
        self._game._notify_state_callbacks()
