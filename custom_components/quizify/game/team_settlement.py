"""TeamSettlementController — a team's standing answer, scored (#365, #1116).

Extracted from ``QuizifyGameState`` (issue #1116, the follow-up of #1108). In
team mode a round is scored once per team, not once per member: the member
whose tap stands *carries* the team's answer (multiple choice) or guess
(estimate) through the ordinary player scoring path, and the result is written
back onto the team. This module holds that hand-off — picking the carrier,
settling the multiple-choice answers, seeding the estimate ranking with the
teams' guesses — and the shape a round the team never answered takes.

Like the Hot Seat and Lightning controllers it holds a reference to the owning
game, because settlement reads the roster, the round clock and the team
registry and scores through ``submit_answer``. The single booking primitive,
``QuizifyGameState._credit_team_round``, stays on the game: the estimate path
calls it too, and the two paths sharing one function is the point of #748.
``QuizifyGameState`` keeps every old private name as a one-line delegate.
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover - typing only
    from .player import PlayerSession
    from .state import QuizifyGameState
    from .team import Team


class TeamSettlementController:
    """Settles each team's standing answer or guess through one carrier."""

    def __init__(self, game: QuizifyGameState) -> None:
        """Bind to the owning game; the controller keeps no state of its own."""
        self._game = game

    def pick_carrier(self, team: Team, setter: str | None) -> PlayerSession | None:
        """The member who carries a team's response into the scoring path.

        Normally the member who set it. If they left between the tap and the
        buzzer the response still stands for the team, so it is handed to
        another member rather than dropping a round the team did answer.

        Connected members are preferred over disconnected ones (#601). The
        earlier version took the first member who had not submitted, which
        could be someone whose phone had simply locked — and during the
        re-entrant evaluation that bug allowed, handing the answer to that
        member scored the same round a second time.
        """
        game = self._game
        player = game._player_registry.get_player(setter) if setter else None
        if player is not None and not player.submitted:
            return player
        candidates = [
            p
            for name in team.members
            if (p := game._player_registry.get_player(name)) is not None
            and not p.submitted
        ]
        if not candidates:
            return None
        # Fall back to a disconnected member only when nobody is left online:
        # the team did answer, and losing the round because every phone went
        # dark would punish the wrong thing.
        return next((p for p in candidates if p.connected), candidates[0])

    def record_timeout(self, team: Team) -> None:
        """Book a round the team never answered (#748).

        The shape a missed round takes everywhere else: streak broken, a
        ``timeout`` history entry, the round's score in the round-score
        history, and the round still counted as played so the per-round
        averages divide by the right number. Shared by both settlement paths
        so a fourth copy cannot drift the way the scoring blocks did.

        On the final round a standing bet is resolved here as a loss (#653,
        #804) — the twin of the per-player timeout branch in
        ``_do_evaluate_round``; both go through
        :meth:`WagerController.settle_timeout` (#1116). A team that placed a
        bet and then let the clock run out pays it, because "bet, then sit
        still" must not be the cheapest way through the last question. The
        history records ``round_score`` rather than a literal zero for the
        same reason the player path does (#472): a pre-settlement reaction
        bonus (#800) or this very loss has already moved it.
        """
        team.streak = 0
        self._game._wager_controller.settle_timeout(team)
        team.round_history.append("timeout")
        team.round_scores.append(team.round_score)
        team.rounds_played += 1

    def settle_answers(self) -> None:
        """Score each team's standing answer once, through the player path.

        The member who set the answer carries it: their submission is scored
        with the team's streak and with the elapsed time of their *last* tap,
        and the result is written back to the team. Members who did not set it
        are left untouched — they neither score nor count as a timeout, because
        in team mode there is nothing individual left to reward or punish.

        A team that never answered breaks its streak and records a timeout,
        the same shape a player's missed round takes.
        """
        # Runtime import: ``state`` imports this module at load time.
        from .state import AnswerResult  # noqa: PLC0415

        game = self._game
        round_started = game._round_start_time or 0.0
        for team in game._team_registry.all_teams():
            if team.current_answer is None or team.answer_by is None:
                self.record_timeout(team)
                continue
            player = self.pick_carrier(team, team.answer_by)
            if player is None:
                self.record_timeout(team)
                continue

            elapsed = max(0.0, (team.answered_at or round_started) - round_started)
            # The scoring engine reads the player's streak; in team mode the
            # streak belongs to the team, so lend it for the call and take the
            # updated value back.
            player.streak = team.streak
            result = game.submit_answer(
                player.name,
                team.current_answer,
                elapsed_override=elapsed,
                _settling_team=True,
                # The final-round bet is the team's, staked against the team's
                # score (#804). Lent for the call the same way the streak is,
                # so the scoring engine keeps one wager rule for everybody.
                _team_wager=team.wager,
                _team_bank=team.score,
            )
            if isinstance(result, AnswerResult):
                # The milestone was earned on the team's streak (lent to the
                # carrier above), so it is tallied on the team (#923) — the
                # analytics rollup reads this counter off the entrant.
                if result.milestone_bonus:
                    team.streak_milestones_hit += 1
                game._credit_team_round(
                    team,
                    result.points_earned,
                    result.correct,
                    elapsed,
                    player.round_score_breakdown,
                )

    def settle_guesses(self) -> dict[str, str]:
        """Hand each team's standing guess to the member who set it (#602).

        The estimate path ranks *participants* by closeness to the true value.
        In team mode the participant is the team — so rather than teach
        ``calculate_estimate_scores`` about teams, the team's guess is carried
        into the ranking by one of its members, the same shape
        ``_settle_team_answers`` uses for multiple choice. The ranking then
        contains one entry per team plus any solo players, and the scoring
        function needs no change at all.

        Returns ``{team_id: carrier_name}`` so the results can be copied back
        onto the teams once the per-player pass has run.
        """
        game = self._game
        round_started = game._round_start_time or 0.0
        carriers: dict[str, str] = {}
        for team in game._team_registry.all_teams():
            if team.current_guess is None or team.guess_by is None:
                continue
            player = self.pick_carrier(team, team.guess_by)
            if player is None:
                continue
            player.current_guess = team.current_guess
            player.submitted = True
            player.submission_time = time.time()
            player.last_elapsed = max(
                0.0, (team.guessed_at or round_started) - round_started
            )
            carriers[team.team_id] = player.name
        return carriers
