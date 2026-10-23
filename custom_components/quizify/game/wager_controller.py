"""WagerController — the final round's betting window (#656, #804, #1116).

Extracted from ``QuizifyGameState`` (issue #1116, the follow-up of #1108). The
wager flow was spread over the game state, the WebSocket handler and the round
evaluation. This class holds the game side of it: whether a round opens a
betting window, who has still to bet, recording and locking a bet, and the
one rule both timeout paths share — a standing bet on a final round nobody
answered is paid as a loss.

What stays elsewhere, on purpose:

* The *winning/losing* arithmetic of an answered bet is ``ScoringEngine``'s
  (``wager_loss`` and the final-round override), reached from
  ``submit_answer``.
* The window's phase and clock are ``PhaseController``'s
  (``begin_wager_window``, ``wager_window_remaining``).
* The window's deadline task belongs to the WebSocket handler's #746 teardown
  registry; the inbound ``submit_wager`` message is validated in
  ``server/wager_handlers.py``.

Like the Hot Seat and Lightning controllers it holds a reference to the owning
game. ``QuizifyGameState`` keeps every public name it had (``wager_enabled``,
``record_wager``, ``players_missing_wager`` …) and ``_needs_wager_window`` as
one-line delegates, so callers and tests are unchanged.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from .phase_controller import GamePhase
from .scoring_engine import wager_loss

if TYPE_CHECKING:  # pragma: no cover - typing only
    from .participant import Participant
    from .questions import Question
    from .state import QuizifyGameState


class WagerController:
    """Owns the betting window's rules and the timeout settlement of a bet."""

    def __init__(self, game: QuizifyGameState) -> None:
        """Bind to the owning game. The window is on until a game says not."""
        self._game = game
        # #742: the host's toggle for the final round's betting window. Default
        # ON, so a game that says nothing about it plays as it did before. Not
        # a one-shot detour like Lightning or Hot Seat, so there is no target
        # round to draw: it is read where the mechanic happens, every round.
        self.enabled: bool = True

    def needs_window(self, question: Question) -> bool:
        """Whether this round opens with a betting window (#656).

        Mirrors exactly the rule the player payload advertises the wager UI
        by: the last round, and never an estimate final. Estimate rounds score
        through ``_evaluate_estimate_round``, which never reads
        ``player.wager`` (#353) — opening a window there would collect bets
        nothing settles.

        Team mode used to be excluded for a third reason (#668): there
        ``player.score`` is a by-product — the carrier of a given round
        receives the team's points personally and everyone else stays at zero
        — so the window advertised each member "your bank: X" against a
        meaningless number and settled only the carrier's bet against the
        carrier's shadow score. #804 fixes the mechanic instead of gating it:
        the bet now belongs to the team, is staked against the team score the
        television shows, and any member may place it.

        #742 adds a second exclusion, of a different kind: a host's choice
        rather than a mechanic that cannot settle. A table playing with
        children switches the betting window off, and the final question is
        played straight instead of one where "no answer costs you the stake".
        """
        if not self.enabled:
            return False
        game = self._game
        return game.round == game.total_rounds and not question.is_estimate

    def window_remaining(self) -> float:
        """Seconds left in the betting window (0.0 outside WAGER_ACTIVE)."""
        game = self._game
        if game.phase != GamePhase.WAGER_ACTIVE:
            return 0.0
        return game._phase_controller.wager_window_remaining()

    @property
    def window_duration(self) -> float:
        """How long a betting window stays open (#656)."""
        return self._game._phase_controller.wager_window_duration

    def players_missing(self) -> list[str]:
        """Connected participants who have not locked a bet yet (#656).

        The window closes early once this is empty, so a table that decides
        fast is not held for the full 20 seconds. Disconnected players are
        excluded — waiting on a phone that has left the room would strand the
        window until the deadline for everybody else.

        In team mode the answer is a team's name, not a member's (#804): one
        bet is placed per team, so waiting on each member in turn would hold
        the window open for people who have nothing left to do — and the host
        screen would name three players for one outstanding decision.
        """
        game = self._game
        if not game.team_mode:
            return [
                p.name
                for p in game._player_registry.players.values()
                if p.connected and p.wager is None
            ]
        connected = {
            p.name for p in game._player_registry.players.values() if p.connected
        }
        waiting: list[str] = []
        for participant in game.get_ranked_participants():
            if participant.wager is not None:
                continue
            members = participant.members or [participant.name]
            if any(m in connected for m in members):
                waiting.append(participant.name)
        return waiting

    def record(self, player_name: str, pct: int) -> bool:
        """Stake ``pct`` for whoever this player bets as (#804).

        Their team in team mode, themselves otherwise — the same row
        ``get_ranked_participant_for`` resolves for every other payout. Any
        member may set the team's bet and the last one counts, exactly like
        the standing answer; there is no change lock, because the window is a
        single decision the whole team can see the tally of, not a tap race in
        the last seconds of a question.

        Returns False for a name that is not in the game.
        """
        participant = self._game.get_ranked_participant_for(player_name)
        if participant is None:
            return False
        participant.wager = pct
        return True

    def is_locked(self, player_name: str) -> bool:
        """True once the bet's answer is already committed (#255/#804).

        A wager only makes sense *before* the answer is in — it stakes points
        on getting that answer right. For a solo player that is their own
        submission; for a team it is the standing answer, since no member is
        ever marked ``submitted`` in team mode (#365).
        """
        participant = self._game.get_ranked_participant_for(player_name)
        if participant is None:
            return False
        # Both a team and a player carry ``current_answer`` (#1107), so the
        # former ``hasattr`` guard here was always true: in team mode the
        # standing answer decides, for a solo row too.
        if self._game.team_mode:
            return participant.current_answer is not None
        return bool(participant.submitted)

    def settle_timeout(self, entrant: Participant) -> None:
        """Resolve a standing bet on an unanswered final round as a loss (#653, #804).

        Shared by the per-player timeout branch in ``_do_evaluate_round`` and
        the team one in :meth:`TeamSettlementController.record_timeout`, which
        used to carry a copy of this block each. A no-op outside the final
        round and when nothing is at stake (``wager_loss`` returns 0).

        ``round_score`` is reduced rather than set to ``-loss``: a pre-submit
        STEAL (#472) or a reaction bonus (#800) may already have folded points
        into it, and the reveal reports ``points_earned=round_score``.
        """
        game = self._game
        if game.round != game.total_rounds:
            return
        loss = wager_loss(entrant.score, entrant.wager)
        if loss:
            entrant.round_score -= loss
            entrant.score = max(0, entrant.score - loss)
