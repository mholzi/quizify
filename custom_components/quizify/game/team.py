"""Teams (#365).

A team is a *participant*, not a grouping: once the game starts it answers
once, scores once and appears in the ranking exactly where a player would.
That decision (Markus, 2026-08-12) is what keeps this module small — there is
no per-capita arithmetic, no handicap and no balancing step to model.

What a team owns:

* its members, by player name (names are unique per game, see
  ``PlayerRegistry``),
* the answer currently standing for the round, and who set it — any member may
  change it until the clock stops, and the last change is the one that counts,
* a short lock after each change, so two members cannot flip the answer back
  and forth in the final seconds.

Scoring lives in the game state, not here: this module holds the state and the
rules about *changing* an answer, and stays free of the scoring engine so the
two can be tested apart.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field

#: Seconds during which a freshly changed team answer cannot be changed again.
#: Long enough to stop a tap war, short enough that the last second before the
#: buzzer still belongs to whoever is quickest (Markus, 2026-08-12).
ANSWER_CHANGE_LOCK_SECONDS = 2.0

#: Team names offered in the lobby. Suggestions, never a required field — the
#: whole point of the chosen lobby flow is that joining stays one tap.
SUGGESTED_TEAM_NAMES = ["Sofa", "Küche", "Balkon", "Garten", "Terrasse", "Flur"]

#: Colours a team can carry, in the Soft Parlor palette order used for player
#: dots, so a team reads like any other participant on the dashboard.
TEAM_COLORS = ["coral", "sage", "sky", "sun", "mauve", "brick"]


@dataclass
class Team:
    """One team: its members and the answer currently standing for the round."""

    name: str
    team_id: str = field(default_factory=lambda: str(uuid.uuid4())[:8])
    color: str = ""
    members: list[str] = field(default_factory=list)

    # ---- per-round answer state -------------------------------------
    #: Canonical answer index for the current round, or None while unset.
    current_answer: int | None = None
    #: Member who set the answer that currently stands.
    answer_by: str | None = None
    #: Wall-clock of the last change, used for the lock and for the speed
    #: bonus — the bonus keys on the *last* tap, so thinking is not free.
    answered_at: float | None = None
    #: How often the answer changed this round. Kept for diagnostics; the
    #: reveal screen deliberately does not show it (Markus, 2026-08-12).
    change_count: int = 0

    # ---- scoring ----------------------------------------------------
    score: int = 0
    streak: int = 0
    round_score: int = 0
    round_history: list[str] = field(default_factory=list)
    last_answer_correct: bool = False
    last_elapsed: float = 0.0

    # ---- award statistics -------------------------------------------
    # The end-of-game awards go to teams (Markus, 2026-08-12), and they are
    # computed by the *same* function that computes them for players — so a
    # team keeps the same tallies a player keeps. The per-award reading is
    # therefore not a rename but a consequence of what gets recorded here:
    # a team's answer time is the time of the tap that stood, its round score
    # is the one score it earned that round, and its streak is already a team
    # streak. ``freezes_used`` is filled from its members at award time, since
    # power-ups are still handed to people.
    round_scores: list[int] = field(default_factory=list)
    answer_times: list[float] = field(default_factory=list)
    hard_score: int = 0
    freezes_used: int = 0
    rounds_played: int = 0

    # ---- player-shaped fields ---------------------------------------
    # A team is fed to ``serialize_leaderboard`` in place of a player, so it
    # answers to the same attribute names. That is the whole reason the
    # dashboard, the reveal and the finale need no team-specific rendering
    # path: they receive rows and cannot tell the difference (#365).
    max_streak: int = 0
    powerups_used: int = 0
    submitted: bool = False
    is_admin: bool = False
    round_score_breakdown: dict = field(default_factory=dict)

    # ------------------------------------------------------------------
    # Membership
    # ------------------------------------------------------------------

    def add_member(self, player_name: str) -> None:
        """Add a player; joining twice is a no-op rather than a duplicate."""
        if player_name not in self.members:
            self.members.append(player_name)

    def remove_member(self, player_name: str) -> None:
        """Remove a player. An empty team is dissolved by the registry."""
        if player_name in self.members:
            self.members.remove(player_name)

    @property
    def is_empty(self) -> bool:
        return not self.members

    @property
    def size(self) -> int:
        return len(self.members)

    # ------------------------------------------------------------------
    # Answering
    # ------------------------------------------------------------------

    def can_change_answer(self, now: float | None = None) -> bool:
        """False while the post-change lock is still running."""
        if self.answered_at is None:
            return True
        now = time.time() if now is None else now
        return (now - self.answered_at) >= ANSWER_CHANGE_LOCK_SECONDS

    def lock_remaining(self, now: float | None = None) -> float:
        """Seconds left on the lock, 0 when the answer may be changed."""
        if self.answered_at is None:
            return 0.0
        now = time.time() if now is None else now
        return max(0.0, ANSWER_CHANGE_LOCK_SECONDS - (now - self.answered_at))

    def set_answer(
        self, answer_index: int, by_player: str, now: float | None = None
    ) -> bool:
        """Set the team's answer. Returns False while the lock is running.

        Re-tapping the answer that already stands is accepted as a no-op: a
        member confirming the current choice should never be told to wait.
        """
        now = time.time() if now is None else now
        if self.current_answer == answer_index and self.answer_by is not None:
            return True
        if not self.can_change_answer(now):
            return False
        if self.current_answer is not None:
            self.change_count += 1
        self.current_answer = answer_index
        self.answer_by = by_player
        self.answered_at = now
        return True

    def reset_round(self) -> None:
        """Clear the per-round answer state before the next question."""
        self.current_answer = None
        self.answer_by = None
        self.answered_at = None
        self.change_count = 0
        self.round_score = 0
        self.last_answer_correct = False
        self.last_elapsed = 0.0
        self.round_score_breakdown = {}
        self.submitted = False

    def to_dict(self) -> dict:
        """Wire shape. Mirrors a player entry closely on purpose (#365)."""
        return {
            "team_id": self.team_id,
            "name": self.name,
            "color": self.color,
            "members": list(self.members),
            "size": self.size,
            "score": self.score,
            "streak": self.streak,
        }


class TeamRegistry:
    """The teams of one game.

    Formation happens in the lobby only — once the game starts the set is
    frozen (Markus, 2026-08-12), which is why every mutating method here is
    called from lobby-phase handlers.
    """

    def __init__(self) -> None:
        self._teams: dict[str, Team] = {}
        self._next_color = 0

    # ---- lookup ------------------------------------------------------

    @property
    def teams(self) -> dict[str, Team]:
        return self._teams

    def get(self, team_id: str) -> Team | None:
        return self._teams.get(team_id)

    def get_by_member(self, player_name: str) -> Team | None:
        for team in self._teams.values():
            if player_name in team.members:
                return team
        return None

    def all_teams(self) -> list[Team]:
        return list(self._teams.values())

    @property
    def is_active(self) -> bool:
        """True once at least one team exists — the mode is opt-in per game."""
        return bool(self._teams)

    # ---- formation ---------------------------------------------------

    def create(self, name: str, founder: str) -> Team:
        """Open a team and put its founder in it.

        The name is taken as given (already trimmed by the caller); an empty
        one falls back to a suggestion rather than rejecting the request,
        because a nameless team is still a valid thing to want.
        """
        clean = (name or "").strip()[:24]
        if not clean:
            clean = SUGGESTED_TEAM_NAMES[len(self._teams) % len(SUGGESTED_TEAM_NAMES)]
        color = TEAM_COLORS[self._next_color % len(TEAM_COLORS)]
        self._next_color += 1
        team = Team(name=clean, color=color)
        team.add_member(founder)
        self._teams[team.team_id] = team
        return team

    def join(self, team_id: str, player_name: str) -> Team | None:
        """Move a player into a team, leaving their previous one first."""
        team = self._teams.get(team_id)
        if team is None:
            return None
        self.leave(player_name)
        team.add_member(player_name)
        return team

    def leave(self, player_name: str) -> None:
        """Remove a player from whatever team holds them; drop empty teams."""
        team = self.get_by_member(player_name)
        if team is None:
            return
        team.remove_member(player_name)
        if team.is_empty:
            self._teams.pop(team.team_id, None)

    def remove_player(self, player_name: str) -> None:
        """A player left the game entirely."""
        self.leave(player_name)

    def reset(self) -> None:
        self._teams.clear()
        self._next_color = 0

    def reset_round(self) -> None:
        for team in self._teams.values():
            team.reset_round()

    def to_list(self) -> list[dict]:
        return [t.to_dict() for t in self._teams.values()]
