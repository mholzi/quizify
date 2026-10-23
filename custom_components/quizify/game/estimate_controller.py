"""EstimateController — scoring and revealing an estimate round (#275, #1116).

Extracted from ``QuizifyGameState`` (issue #1116, the follow-up of #1108). An
estimate round is not scored per answer like multiple choice: every guess is
ranked by its distance to the true value once the round ends, the points are
written onto the players (and, in team mode, onto the teams whose guess a
member carried), and the reveal gets a number-line block. That evaluation, the
reveal block and the team write-back live here. The round-closing tail —
summary, phase, calibrator, stats — stays ``QuizifyGameState._close_round``,
shared with the multiple-choice path (#810).

Like the Hot Seat and Lightning controllers it holds a reference to the owning
game and reaches back through it for the roster, the team registry and the
shared bookkeeping helpers. ``QuizifyGameState`` keeps every old private name
(``_evaluate_estimate_round`` …) as a one-line delegate.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from .questions import Answer, Question
from .scoring import calculate_estimate_scores, estimate_within_calibration_band
from .types import Difficulty

if TYPE_CHECKING:  # pragma: no cover - typing only
    from .state import QuizifyGameState, RoundSummary

# The evaluation's log line keeps the logger it was written under before the
# move, so a host's ``logger:`` filter for ``game.state`` still shows it.
_LOGGER = logging.getLogger(__name__.rsplit(".", 1)[0] + ".state")


class EstimateController:
    """Scores an estimate round by closeness and builds its reveal."""

    def __init__(self, game: QuizifyGameState) -> None:
        """Bind to the owning game; the controller keeps no state of its own."""
        self._game = game

    def evaluate_round(self, question: Question) -> RoundSummary:
        """Evaluate an estimate round by closeness (#275).

        Ranks every player who submitted a guess by absolute distance to the
        true value, awards difficulty-scaled, rank-decayed points (closest =
        full, exact = bonus), applies them to player scores, and builds a
        RoundSummary whose ``estimate`` block carries the number-line reveal
        data. Non-guessers score 0 and break their streak, same as an MC
        timeout. The round-closing tail (summary, phase, calibrator, stats,
        broadcast) is ``_close_round``, shared with the MC path — this method
        does the scoring and nothing else (#810).
        """
        game = self._game
        answer_val = (
            question.estimate_answer if question.estimate_answer is not None else 0.0
        )
        try:
            diff_enum = Difficulty(question.difficulty)
        except ValueError:
            diff_enum = Difficulty.MEDIUM

        # Team mode (#602): settle each team's standing guess onto a member
        # before the ranking is built, so the ranking sees one entry per team
        # (plus any solo players) instead of every individual member.
        carriers = game._settle_team_guesses() if game.team_mode else {}

        guesses: dict[str, float] = {
            p.name: p.current_guess
            for p in game._player_registry.players.values()
            if p.submitted and p.current_guess is not None
        }
        scores = calculate_estimate_scores(guesses, answer_val, diff_enum)

        # Apply scoring + per-round bookkeeping to every player.
        for player in game._player_registry.players.values():
            entry = scores.get(player.name)
            if entry is None:
                # No guess this round → score 0, streak breaks (mirrors MC
                # timeout). A player who guessed but somehow isn't in ``scores``
                # can't happen, but this branch also covers the not-submitted
                # case cleanly.
                player.streak = 0
                player.record_round_result("timeout")
                player.round_score = 0
                player.round_score_breakdown = {}
                player.round_scores.append(0)
            else:
                points = entry["points"]
                # Closeness scoring has no streak/speed concept; surface the
                # estimate-specific breakdown so the reveal can show distance.
                # Streak only advances on an EXACT hit — a non-exact guess is
                # recorded as "wrong" below, so growing the streak on it would
                # let a wrong guess step past a STREAK_MILESTONES value without
                # ever paying the milestone (only MC awards them) (#408). Keep
                # streak bookkeeping in agreement with the recorded correctness.
                if entry["exact"]:
                    player.streak += 1
                    if player.streak > player.max_streak:
                        player.max_streak = player.streak
                else:
                    player.streak = 0
                player.round_score = points
                player.round_score_breakdown = {
                    "speed_bonus": 0,
                    "streak_bonus": 0,
                    "difficulty_multiplier": 1.0,
                    "double_points": False,
                    "estimate_distance": entry["distance"],
                    "estimate_rank": entry["rank"],
                    "estimate_exact": entry["exact"],
                }
                # Participant's total, not the carrier's (#923) — the twin
                # of the guard in ``submit_answer``. The team is credited from
                # ``round_score`` in ``_apply_estimate_results_to_teams``, so
                # the running total here would be a second ledger.
                if game._keeps_own_score(player.name):
                    player.score += points
                    if player.score < 0:
                        player.score = 0
                if diff_enum == Difficulty.HARD:
                    player.hard_score += points
                player.last_answer_correct = entry["exact"]
                # #1084: a non-exact guess that still scored is a hit for
                # the finale card; the recorded result stays exact-only.
                player.record_round_result(
                    "correct" if entry["exact"] else "wrong",
                    scored_hit=points > 0,
                )
                player.round_scores.append(points)
            player.rounds_played += 1

        if game.team_mode:
            self.apply_results_to_teams(carriers, scores)

        # A synthetic "correct answer" so the rest of the pipeline (which reads
        # ``RoundSummary.correct_answer.text``) keeps working for estimate
        # rounds. The text is the true value with its unit.
        answer_text = self.format_value(question, answer_val)
        correct_answer = Answer(text=answer_text, correct=True)

        estimate_block = self.build_reveal(question, answer_val, scores, carriers)

        # Two correctness readings, on purpose (#810).
        #
        # ``exact`` is what the reveal and the per-question stats have always
        # meant by correct on an estimate round, and stays that way: the number
        # line either landed on the value or it did not.
        #
        # The auto-difficulty calibrator needs a different reading. Distance
        # zero on a slider is close to unattainable, so feeding it ``exact``
        # would score every estimate round 0/N and march an auto-mode game down
        # to EASY — the reason the calibrator feed was better off missing than
        # naively copied. It gets "did the guess land inside the band" instead,
        # which is the judgement the reveal's number line already invites.
        exact_correct = {name: bool(entry["exact"]) for name, entry in scores.items()}
        span = (
            question.estimate_max - question.estimate_min
            if question.estimate_max is not None and question.estimate_min is not None
            else None
        )
        close_correct = {
            name: estimate_within_calibration_band(entry["distance"], span)
            for name, entry in scores.items()
        }

        _LOGGER.info(
            "Estimate round %d evaluated (answer=%s, %d guesses), "
            "transitioning to ANSWER_REVEAL",
            game.round,
            answer_val,
            len(guesses),
        )

        return game._close_round(
            question,
            correct_answer,
            exact_correct,
            estimate=estimate_block,
            calibration_correctness=close_correct,
        )

    @staticmethod
    def format_value(question: Question, value: float) -> str:
        """Format an estimate value for display (drop trailing .0, add unit)."""
        text = str(int(value)) if value == int(value) else str(value)
        unit = question.estimate_unit
        return f"{text} {unit}".strip() if unit else text

    def build_reveal(
        self,
        question: Question,
        answer_val: float,
        scores: dict[str, dict],
        carriers: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        """Assemble the number-line reveal block for an estimate round (#275).

        Carries the true value, the slider range/unit, and every CONNECTED
        player's guess + distance + awarded points + rank, plus the winner
        (rank 1). Both the player reveal and the TV dashboard render the
        number line from this. The closest player(s) are rank 1; ties share it.

        In team mode the entrant is the team (#962, the rule #923 applied to
        podium, sensors and events): one row per team, named after the team,
        with ``team_id`` and ``members`` so a phone can find its own row even
        when two teams share a name (#759). A member who did not move the
        slider is not "without a guess" — only a team that never guessed is.
        A guest who joined no team keeps a row of their own.
        """
        game = self._game
        guesses_out: list[dict[str, Any]] = []
        winner_name = ""
        best_rank: int | None = None
        if game.team_mode:
            carriers = carriers or {}
            for team in game._team_registry.all_teams():
                members = list(team.members)
                if not any(
                    (p := game._player_registry.get_player(m)) is not None
                    and p.connected
                    for m in members
                ):
                    continue
                carrier_name = carriers.get(team.team_id)
                carrier = (
                    game._player_registry.get_player(carrier_name)
                    if carrier_name
                    else None
                )
                entry = scores.get(carrier_name) if carrier_name else None
                row: dict[str, Any] = {
                    "player_name": team.name,
                    "team_id": team.team_id,
                    "members": members,
                    "color": team.color,
                }
                if entry is None or carrier is None:
                    row.update(
                        {
                            "guess": None,
                            "distance": None,
                            "points": 0,
                            "rank": None,
                            "exact": False,
                            "no_guess": True,
                        }
                    )
                else:
                    rank = entry["rank"]
                    if best_rank is None or rank < best_rank:
                        best_rank = rank
                        winner_name = team.name
                    row.update(
                        {
                            "guess": carrier.current_guess,
                            "distance": entry["distance"],
                            "points": entry["points"],
                            "rank": rank,
                            "exact": entry["exact"],
                            "no_guess": False,
                        }
                    )
                guesses_out.append(row)
        for player in game._player_registry.players.values():
            if not player.connected:
                continue
            if (
                game.team_mode
                and game._team_registry.get_by_member(player.name) is not None
            ):
                continue
            entry = scores.get(player.name)
            if entry is None or player.current_guess is None:
                guesses_out.append(
                    {
                        "player_name": player.name,
                        "color": player.color,
                        "guess": None,
                        "distance": None,
                        "points": 0,
                        "rank": None,
                        "exact": False,
                        "no_guess": True,
                    }
                )
                continue
            rank = entry["rank"]
            if best_rank is None or rank < best_rank:
                best_rank = rank
                winner_name = player.name
            guesses_out.append(
                {
                    "player_name": player.name,
                    "color": player.color,
                    "guess": player.current_guess,
                    "distance": entry["distance"],
                    "points": entry["points"],
                    "rank": rank,
                    "exact": entry["exact"],
                    "no_guess": False,
                }
            )

        return {
            "answer": answer_val,
            "answer_text": self.format_value(question, answer_val),
            "min": question.estimate_min,
            "max": question.estimate_max,
            "unit": question.estimate_unit,
            "step": question.estimate_step,
            "guesses": guesses_out,
            "winner": winner_name,
        }

    def apply_results_to_teams(
        self, carriers: dict[str, str], scores: dict[str, dict]
    ) -> None:
        """Copy each carrier's estimate result onto their team (#602).

        Which member carried the guess is this method's only concern; the
        bookkeeping itself belongs to ``_credit_team_round``, shared with
        ``_settle_team_answers`` (#748). This used to be a hand-kept "mirror"
        of that block, and it had already drifted — only this copy floored the
        team total at zero. A team that never guessed records a timeout, which
        is what a missed round looks like everywhere else.
        """
        game = self._game
        for team in game._team_registry.all_teams():
            carrier_name = carriers.get(team.team_id)
            player = (
                game._player_registry.get_player(carrier_name) if carrier_name else None
            )
            entry = scores.get(carrier_name) if carrier_name else None
            if player is None or entry is None:
                game._record_team_timeout(team)
                continue

            # "Correct" on an estimate round means an EXACT hit — a near miss
            # is recorded as "wrong", so the shared streak rule cannot grow a
            # streak past a milestone that was never paid (#408).
            game._credit_team_round(
                team,
                player.round_score,
                bool(entry["exact"]),
                player.last_elapsed,
                player.round_score_breakdown,
            )
