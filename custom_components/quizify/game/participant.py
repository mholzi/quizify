"""What a ranked row is, whether a team or a player (#1107).

In team mode the ranking holds :class:`~.team.Team` objects next to the solo
:class:`~.player.PlayerSession`s of guests who joined no team (#365). Both are
handed to the same leaderboard, podium, award and finale code, which used to
take them as ``Any`` (or as ``list[PlayerSession]``, which a team is not) and
probe the difference with ``getattr`` defaults. That let a read of a
player-only attribute such as ``round_hits`` or ``joined_late`` pass mypy and
then raise ``AttributeError`` on the first team-mode finale.

:class:`Participant` is the attribute set both classes genuinely carry — no
more. Code typed against it is checked: an attribute only one class has is a
mypy error, and has to be reached through an explicit ``isinstance`` narrowing.

A structural :class:`~typing.Protocol` rather than a base class on purpose:
both classes stay plain dataclasses with their own field order and defaults,
and nothing about their construction, reset or snapshot changes.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol


class Participant(Protocol):
    """A leaderboard row: a team, or a player who keeps a row of their own."""

    # ---- identity -----------------------------------------------------
    name: str
    color: str
    is_admin: bool

    @property
    def team_id(self) -> str | None:
        """The team's id, or ``None`` for a player.

        Together with ``name`` this is the entrant key (#804/#759): a team's
        id, a solo player's name.
        """
        ...

    @property
    def members(self) -> Sequence[str]:
        """The people behind the row (#969): a team's members, a player's self."""
        ...

    # ---- per-round state ----------------------------------------------
    submitted: bool
    current_answer: int | None
    current_guess: float | None
    wager: int | None
    round_score: int
    round_score_breakdown: dict
    last_answer_correct: bool
    last_elapsed: float

    # ---- whole-game state ---------------------------------------------
    score: int
    streak: int
    max_streak: int
    round_history: list[str]
    round_scores: list[int]
    answer_times: list[float]
    hard_score: int
    freezes_used: int
    powerups_used: int
    rounds_played: int
    streak_milestones_hit: int

    def add_reaction_bonus(self, round_num: int, cap: int) -> bool:
        """Credit a +1 reveal reaction bonus, at most ``cap`` per round (#800)."""
        ...
