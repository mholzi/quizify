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
    #: ``time.monotonic()`` of the last change, used for the lock and for the
    #: speed bonus — the bonus keys on the *last* tap, so thinking is not free.
    #: MUST stay on the same clock as ``PhaseController.round_start_time``
    #: (#600): the speed bonus is ``answered_at - round_start_time``, and a
    #: wall clock here made that difference the seconds since 1970.
    answered_at: float | None = None
    #: The team's standing guess for an estimate round, and who set it (#602).
    #: Same rules as ``current_answer``: any member may change it until the
    #: clock stops, the last change counts, and the change lock applies.
    current_guess: float | None = None
    guess_by: str | None = None
    guessed_at: float | None = None
    #: How often the answer changed this round. Kept for diagnostics; the
    #: reveal screen deliberately does not show it (Markus, 2026-08-12).
    change_count: int = 0

    #: The team's stake on the final question, as a PERCENT of its score
    #: (#804). Any member may set it and the last one counts, the same rule
    #: the standing answer follows — but unlike the answer it is not settled
    #: by a carrier: the bet belongs to the team, is staked against the score
    #: the television shows, and is paid into the same row. Reset each round
    #: alongside the answer, mirroring ``PlayerSession.wager``.
    wager: int | None = None

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

    #: round_number -> count of +1 reveal reaction bonuses this team has
    #: RECEIVED that round, capped per round (#800). The team is the ranked
    #: participant, so in team mode the bonus has to land here — crediting the
    #: one member who happened to carry the answer moved a shadow score nobody
    #: can see. Mirrors ``PlayerSession._reaction_bonuses_received`` field for
    #: field, including being cleared by ``reset_for_new_game``: round numbers
    #: restart at 1 each game, so a team at the cap in round N of the old game
    #: would otherwise be blocked in round N of the new one (#167).
    _reaction_bonuses_received: dict[int, int] = field(default_factory=dict)

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
        now = time.monotonic() if now is None else now
        return (now - self.answered_at) >= ANSWER_CHANGE_LOCK_SECONDS

    def lock_remaining(self, now: float | None = None) -> float:
        """Seconds left on the lock, 0 when the answer may be changed."""
        if self.answered_at is None:
            return 0.0
        now = time.monotonic() if now is None else now
        return max(0.0, ANSWER_CHANGE_LOCK_SECONDS - (now - self.answered_at))

    def set_answer(
        self, answer_index: int, by_player: str, now: float | None = None
    ) -> bool:
        """Set the team's answer. Returns False while the lock is running.

        Re-tapping the answer that already stands is accepted as a no-op: a
        member confirming the current choice should never be told to wait.
        """
        now = time.monotonic() if now is None else now
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

    def set_guess(
        self, guess: float, by_player: str, now: float | None = None
    ) -> bool:
        """Set the team's guess for an estimate round (#602).

        The mirror of ``set_answer``: any member may re-guess until the clock
        stops, the last guess is the one that scores, and the same lock stops
        two members from flipping the number back and forth. Returns False
        while the lock is running.

        The lock reads ``answered_at``, which both setters share — a team has
        one standing response per round whatever its shape, so a guess and an
        answer must not be able to dodge each other's brake.
        """
        now = time.monotonic() if now is None else now
        if self.current_guess == guess and self.guess_by is not None:
            return True
        if not self.can_change_answer(now):
            return False
        if self.current_guess is not None:
            self.change_count += 1
        self.current_guess = guess
        self.guess_by = by_player
        self.guessed_at = now
        self.answered_at = now
        return True

    def reset_round(self) -> None:
        """Clear the per-round answer state before the next question."""
        self.current_answer = None
        self.answer_by = None
        self.answered_at = None
        self.current_guess = None
        self.guess_by = None
        self.guessed_at = None
        self.change_count = 0
        self.wager = None
        self.round_score = 0
        self.last_answer_correct = False
        self.last_elapsed = 0.0
        self.round_score_breakdown = {}
        self.submitted = False

    def add_reaction_bonus(self, round_num: int, cap: int) -> bool:
        """Award a +1 reveal reaction bonus for ``round_num``, respecting ``cap``.

        The team-side twin of ``PlayerSession.add_reaction_bonus`` (#800). A
        team may receive at most ``cap`` bonuses per round; once at the cap the
        call is a no-op. Returns ``True`` when a bonus was actually granted.
        """
        received = self._reaction_bonuses_received.get(round_num, 0)
        if received >= cap:
            return False
        self._reaction_bonuses_received[round_num] = received + 1
        self.score += 1
        self.round_score += 1
        return True

    # ------------------------------------------------------------------
    # Game lifecycle
    # ------------------------------------------------------------------

    def reset_for_new_game(self) -> None:
        """Clear every game-level tally so a rematch starts this team at zero.

        The twin of ``PlayerSession.reset_for_new_game`` (#799). Teams survive
        ``reset_to_lobby`` on purpose — the one-tap rematch keeps the room
        exactly as it stands — but until this existed nothing but the explicit
        reset-game button cleared their scores, so game 2 opened with game 1's
        leaderboard, awards counted the old rounds and the streak bonus carried
        over.
        """
        self.score = 0
        self.streak = 0
        self.max_streak = 0
        self.round_history = []
        self.round_scores = []
        self.answer_times = []
        self.hard_score = 0
        self.freezes_used = 0
        self.powerups_used = 0
        self.rounds_played = 0
        self._reaction_bonuses_received = {}
        self.reset_round()

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

    def reset_for_new_game(self) -> None:
        """Zero every team's game-level tallies, keeping the teams themselves.

        The registry-level counterpart to the per-player loop in
        ``start_game`` / ``reset_to_lobby`` (#799). Membership is untouched:
        the rematch is meant to keep the room, only the scoreboard restarts.
        """
        for team in self._teams.values():
            team.reset_for_new_game()

    def to_list(self) -> list[dict]:
        return [t.to_dict() for t in self._teams.values()]
