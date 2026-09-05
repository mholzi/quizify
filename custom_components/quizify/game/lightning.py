"""Lightning Round — a fast bonus mode for Quizify (issue #42).

A self-contained game mode that runs *alongside* the normal game machine
without touching its scoring or phase logic. It is deliberately kept in
its own module (and the heavy lifting out of ``state.py``) so it doesn't
collide with the normal round engine, and so the rules that make it
distinct live in one place:

* **Fixed time per question** (default 15s, 5 questions ≈ 75s). A question
  ends on timeout OR when every connected player has answered — whichever
  comes first. There is **no reveal between questions**.
* **Fixed points per correct** answer (``LIGHTNING_POINTS_PER_CORRECT``).
  No speed bonus, no streak multiplier, no difficulty multiplier.
* **Power-ups are disabled** — the WS layer never assigns or accepts them
  while a lightning round is active.
* **A team answers together**, exactly as in a normal round (#552): one answer
  and one score per team, any member may change it until the clock stops, and
  the question therefore runs its full window instead of ending on the first
  tap. Per-player shuffles stay per player — only the *answer* is shared.
* **End screen** shows each player's total lightning score plus a compact
  recap of all questions: the *correct* answer per question (green), and —
  when the player got it wrong — their own wrong pick too, because there
  were no per-question reveals to show it live.

The class holds no asyncio itself: it exposes synchronous step methods
(``start``, ``record_answer``, ``advance``) that the WebSocket handler's
fast loop drives. That keeps it trivially unit-testable without an event
loop.
"""

from __future__ import annotations

import logging
import random
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # pragma: no cover - typing only
    from .questions import Question, QuestionBank

from .team import ANSWER_CHANGE_LOCK_SECONDS

_LOGGER = logging.getLogger(__name__)

# Decided rules (issue #42, greenlit 2026-06-09).
LIGHTNING_NUM_QUESTIONS = 5
LIGHTNING_SECONDS_PER_QUESTION = 15.0
LIGHTNING_POINTS_PER_CORRECT = 10


@dataclass
class LightningAnswer:
    """One entrant's answer to one lightning question.

    An entrant is a player or, in team mode, a whole team (#552) — which is
    why ``set_by`` exists: the team's answer belongs to the team, but the
    other members still want to see who put it there.
    """

    #: True if the entrant answered correctly, False if wrong, None if they
    #: never answered before the question's time ran out.
    correct: bool | None = None
    #: Original (unshuffled) answer index that was submitted, or None.
    answer_index: int | None = None
    #: Member who set the answer that currently stands (team mode only).
    set_by: str | None = None
    #: Wall-clock of the last change, for the short re-decision lock.
    answered_at: float | None = None


@dataclass
class LightningQuestionRecap:
    """Recap of a single lightning question for the end screen."""

    question_id: str
    question_text: str
    correct_answer: str
    #: entrant key (player name, or team id) -> "correct" | "wrong" | "miss"
    results: dict[str, str] = field(default_factory=dict)
    #: entrant key -> the answer text they picked (only kept when wrong, so
    #: the recap can show "You said X" beside the correct answer).
    chosen: dict[str, str] = field(default_factory=dict)


class LightningRound:
    """Runs one fast 5-question lightning round.

    Lifecycle, driven by the WS handler:

        lr = LightningRound(question_bank, players, language=..., category=...)
        lr.start()                       # builds the queue, arms question 1
        # loop:
        q = lr.current_question          # broadcast to players
        ...players answer via record_answer(name, idx)...
        # when timeout OR all answered:
        more = lr.advance()              # scores the question, arms the next
        # when advance() returns False the round is over → lr.build_recap()
    """

    def __init__(
        self,
        question_bank: QuestionBank,
        player_names: list[str],
        *,
        language: str = "de",
        category: str | None = None,
        categories: list[str] | None = None,
        difficulty: str | None = None,
        num_questions: int = LIGHTNING_NUM_QUESTIONS,
        seconds_per_question: float = LIGHTNING_SECONDS_PER_QUESTION,
        points_per_correct: int = LIGHTNING_POINTS_PER_CORRECT,
        teams: list[dict[str, Any]] | None = None,
    ) -> None:
        self._bank = question_bank
        self._players = list(player_names)
        self.language = language
        self.category = category
        self.categories = categories
        self.difficulty = difficulty
        self.num_questions = num_questions
        self.seconds_per_question = seconds_per_question
        self.points_per_correct = points_per_correct

        self._questions: list[Question] = []
        self.index: int = -1  # -1 before start; 0-based once running
        self.active: bool = False
        self.finished: bool = False
        self._question_start: float | None = None

        # Who is actually playing this round (#552). In an ordinary game the
        # entrants ARE the players; in team mode a team is one entrant and its
        # members map onto it. Teams are frozen in the lobby, so this mapping
        # is built once and never changes mid-round.
        #
        # `teams` is a snapshot of `TeamRegistry.to_list()`; taking a snapshot
        # rather than the registry keeps this module free of the game state.
        #
        # The key is the TEAM ID, not the team name (#728). Nothing stops two
        # teams from carrying the same name — ``TeamRegistry.create`` takes the
        # name as typed, and its empty-name fallback hands out the same
        # suggestion again once a team has dissolved. Keyed by name, two
        # "Sofa"s collapsed into one entrant: one team's tap overwrote the
        # other's answer, and a single correct answer paid 20 instead of 10,
        # because ``_entrants`` held the key twice while ``scores`` held it
        # once. The name lives beside the key in ``_entrant_names`` — it is
        # what the phones and the TV render, so no id ever reaches a screen.
        self._entrant_of: dict[str, str] = {}
        self._entrants: list[str] = []
        self._entrant_names: dict[str, str] = {}
        for team in teams or []:
            key = team.get("team_id") or team.get("name") or ""
            if not key or key in self._entrant_names:
                continue
            self._entrants.append(key)
            self._entrant_names[key] = team.get("name") or key
            for member in team.get("members", []):
                self._entrant_of[member] = key
        self.team_mode = bool(self._entrants)
        for name in self._players:
            if name not in self._entrant_of and name not in self._entrant_names:
                self._entrants.append(name)
                self._entrant_names[name] = name

        # scores keyed by ENTRANT (player name, or team id in team mode);
        # recap rows in question order.
        self.scores: dict[str, int] = dict.fromkeys(self._entrants, 0)
        self._recaps: list[LightningQuestionRecap] = []

        # Memoized end-screen payload (#455). Once the round is finished the
        # recap is immutable, but get_state_snapshot() rebuilds it on EVERY
        # join / reconnect / get_state while the phase is LIGHTNING_RECAP.
        # Cache the built dict the first time build_recap() runs post-finish
        # and reuse it thereafter. A fresh LightningRound instance (each detour
        # makes a new one; resume/reset drop the old) starts with an empty
        # cache, so no cross-round staleness is possible; start() also clears
        # it defensively for a reused instance.
        self._recap_cache: dict[str, Any] | None = None

        # Per-question answer matrix: index -> {player_name -> LightningAnswer}.
        self._answers: dict[int, dict[str, LightningAnswer]] = {}
        # Per-player shuffle for the CURRENT question (name -> shuffled->orig).
        self._shuffles: dict[str, list[int]] = {}

    # ------------------------------------------------------------------
    # Setup
    # ------------------------------------------------------------------

    def start(self, *, reserve: int = 0) -> bool:
        """Build the question queue and arm the first question.

        ``reserve`` is how many questions the *main* game still needs (its
        remaining rounds). Questions the main queue is holding are only
        claimed while more than ``reserve`` of them remain — see #544, where a
        5-round Easy game on the 17-question picture pack (7 easy) had its
        last five easy questions taken by this detour and ended after round 2.
        Lightning is a bonus; it must not spend the main game's questions.

        Returns False if no questions are available (caller should abort).
        """
        # Build a PRIVATE question pool for the lightning round (#350).
        #
        # This used to call ``self._bank.reset(...)`` + ``get_next_question()``,
        # which rebuilt and consumed the *shared* game queue mid-game. Two
        # bugs fell out of that: (1) it destroyed the main game's queue
        # position/ordering, risking re-shown questions on resume, and (2)
        # when the host picked "auto" difficulty, ``reset`` filtered on
        # ``q.difficulty == "auto"`` — a difficulty no question carries — so
        # the queue came back EMPTY and the fallback ended the game right at
        # the auto-lightning round. We now pull from a private pool and never
        # touch the bank's ``_queue``/``_queue_index``.
        #
        # Cache hit — the bank is preloaded off-loop at setup (#258).
        self._bank.load_all_categories()

        # "auto" is a group-adaptive *mode*, not a per-question difficulty;
        # no question has difficulty=="auto". Map it to None so the pool spans
        # all difficulties instead of coming back empty.
        difficulty = None if self.difficulty in (None, "auto") else self.difficulty

        # Prefer questions the main game hasn't shown this session — the
        # history-aware ordering (never-shown first) already does this, but
        # excluding the shown-this-game set keeps a repeat out of the pool
        # entirely even in small packs.
        pool = self._bank.build_pool(
            category=self.category,
            categories=self.categories,
            difficulty=difficulty,
            language=self.language,
            exclude_ids=self._bank.shown_this_game_ids(),
        )

        # Lightning is fast tap-an-answer (3 options, 15s each). Estimate
        # questions (#275) have no answer grid and the lightning view has no
        # slider, so an estimate question would render as an empty card —
        # skip them. Iterating a finite pool can't spin, so no attempt bound
        # is needed (an all-estimate pool simply yields no questions).
        # How much of the main game's pending queue may be spent here (#544).
        # Questions outside that queue are free — they were never promised to
        # a later round. ``spare`` is computed once, before anything is taken,
        # so the budget can't drift as the loop consumes.
        queued_ids = self._bank.remaining_queue_ids()
        spare = max(0, len(queued_ids) - reserve)
        claimed_from_queue = 0

        for q in pool:
            if len(self._questions) >= self.num_questions:
                break
            if getattr(q, "is_estimate", False):
                continue
            if q.id in queued_ids:
                if claimed_from_queue >= spare:
                    # The main game needs this one for a later round.
                    continue
                claimed_from_queue += 1
            self._questions.append(q)
            self._bank.record_shown(q.id)

        # Claim the chosen questions out of the main game's pending queue so
        # they aren't shown again when the normal round flow resumes (#285
        # mid-game auto-lightning). No-op when the queue is empty (e.g. a
        # lightning round started from the lobby).
        if self._questions:
            self._bank.drop_from_queue({q.id for q in self._questions})

        if not self._questions:
            if pool and spare == 0:
                # Distinct from an empty pool: questions exist, they are just
                # spoken for by the main game's remaining rounds (#544). The
                # caller treats False as "skip the detour, play on".
                _LOGGER.info(
                    "Lightning round skipped: all %d pending questions are "
                    "reserved for the main game's remaining %d rounds",
                    len(queued_ids),
                    reserve,
                )
            else:
                _LOGGER.warning("Lightning round: no questions available")
            return False

        self.num_questions = len(self._questions)
        self.active = True
        self.finished = False
        self._recap_cache = None  # #455: fresh round → drop any stale recap
        self.index = 0
        self._arm_current()
        _LOGGER.info(
            "Lightning round started: %d questions, %.0fs each",
            self.num_questions,
            self.seconds_per_question,
        )
        return True

    def entrant_for(self, name: str) -> str:
        """Who this player scores as: their team's id, or their own name."""
        return self._entrant_of.get(name, name)

    def display_name(self, entrant: str) -> str:
        """The name the room reads for an entrant key (#728).

        Entrants are keyed by team id so two teams called the same thing stay
        two entrants. Every frame a phone or the TV renders resolves the key
        through here, so what people see is still the team's name.
        """
        return self._entrant_names.get(entrant, entrant)

    def display_name_for_player(self, name: str) -> str:
        """The name this player scores under: their team's, or their own."""
        return self.display_name(self.entrant_for(name))

    def score_for(self, name: str) -> int:
        """This player's standing — their team's, in team mode."""
        return self.scores.get(self.entrant_for(name), 0)

    def standing_answer(self, name: str) -> LightningAnswer | None:
        """The answer currently recorded for this player's entrant."""
        return self._answers.get(self.index, {}).get(self.entrant_for(name))

    def members_of(self, name: str) -> list[str]:
        """Everyone who shares this player's entrant (just them, if solo)."""
        entrant = self.entrant_for(name)
        if entrant == name:
            return [name]
        return [p for p in self._players if self._entrant_of.get(p) == entrant]

    def add_player(self, name: str) -> None:
        """Register a late-joining player so they can score from now on.

        They enter as their own entrant: teams are formed in the lobby and
        frozen from the start of the game (#365), so somebody arriving during
        a lightning round plays for themselves.
        """
        if name not in self.scores:
            self.scores[name] = 0
        if name not in self._entrants:
            self._entrants.append(name)
        self._entrant_names.setdefault(name, name)
        if name not in self._players:
            self._players.append(name)
        # Give them a shuffle for the in-flight question so they can answer
        # it (and the test helpers / broadcast see a real per-player order).
        q = self.current_question
        if q is not None and name not in self._shuffles:
            order = list(range(len(q.answers)))
            random.shuffle(order)
            self._shuffles[name] = order

    # ------------------------------------------------------------------
    # Per-question arming / shuffles
    # ------------------------------------------------------------------

    def _arm_current(self) -> None:
        """Prepare the current question: reset answers + per-player shuffles."""
        self._question_start = time.monotonic()
        self._answers[self.index] = {}
        self._shuffles = {}
        q = self.current_question
        if q is None:
            return
        for name in self._players:
            order = list(range(len(q.answers)))
            random.shuffle(order)
            self._shuffles[name] = order

    @property
    def current_question(self) -> Question | None:
        if not self.active or not (0 <= self.index < len(self._questions)):
            return None
        return self._questions[self.index]

    def ensure_shuffle(self, name: str) -> list[int]:
        """Return this player's shuffle for the current Q, creating one if missing.

        A player reconnecting mid-lightning never ran through ``_arm_current``
        for the in-flight question, so they hold no shuffle. Without one, a
        reconnect snapshot falls back to canonical order while
        ``record_answer`` still maps the tapped index through this shuffle
        (issue #253) — mis-scoring the tap. Creating the shuffle lazily here
        keeps the snapshot answers and the submit mapping consistent.
        """
        q = self.current_question
        if q is None:
            return []
        existing = self._shuffles.get(name)
        if existing:
            return existing
        order = list(range(len(q.answers)))
        random.shuffle(order)
        self._shuffles[name] = order
        return order

    def shuffled_answers_for(self, name: str) -> list[str]:
        """Answer texts in this player's shuffled order for the current Q."""
        q = self.current_question
        if q is None:
            return []
        order = self._shuffles.get(name) or list(range(len(q.answers)))
        return [q.answers[i].text for i in order]

    def restart_clock(self) -> None:
        """Reset the current question's countdown to its full window.

        Called by the driver *after* the question is broadcast so the clock
        the players see lines up with the server's window (the question was
        armed — including shuffles — when it became current; this only
        touches the timestamp, not the shuffles or answers).
        """
        self._question_start = time.monotonic()

    def time_remaining(self) -> float:
        """Seconds left on the current question (>= 0)."""
        if self._question_start is None:
            return self.seconds_per_question
        elapsed = time.monotonic() - self._question_start
        return max(0.0, self.seconds_per_question - elapsed)

    def is_expired(self) -> bool:
        return self.time_remaining() <= 0.0

    # ------------------------------------------------------------------
    # Answering
    # ------------------------------------------------------------------

    def record_answer(
        self, name: str, shuffled_index: int, *, now: float | None = None
    ) -> bool | None:
        """Record an answer to the current question.

        ``shuffled_index`` is the button index in the *player's own* shuffle,
        which is why the mapping happens here rather than at the call site.

        In team mode the answer belongs to the team and any member may change
        it until the clock stops (#552) — subject to the same short lock as a
        normal round, so two members cannot flip it back and forth. A solo
        player's answer stays final the moment they tap it.

        Returns True/False for correct/wrong, or None if the answer was
        rejected (no active question, already answered, locked, expired, bad
        index).
        """
        q = self.current_question
        if q is None or self.is_expired():
            return None
        now = time.time() if now is None else now
        entrant = self.entrant_for(name)
        is_team = entrant != name
        bucket = self._answers.setdefault(self.index, {})
        standing = bucket.get(entrant)
        if standing is not None:
            if not is_team:
                return None  # one answer per question — the base rule
            if (
                standing.answered_at is not None
                and (now - standing.answered_at) < ANSWER_CHANGE_LOCK_SECONDS
            ):
                return None  # the brake, same as a normal round
        order = self._shuffles.get(name) or list(range(len(q.answers)))
        if not 0 <= shuffled_index < len(order):
            return None
        original_index = order[shuffled_index]
        correct = (
            0 <= original_index < len(q.answers)
            and q.answers[original_index].correct
        )
        bucket[entrant] = LightningAnswer(
            correct=correct,
            answer_index=original_index,
            set_by=name,
            answered_at=now,
        )
        return correct

    def all_connected_answered(self, connected_names: list[str]) -> bool:
        """True if every currently-connected player has answered this Q.

        Always False in team mode (#552). Ending the question as soon as
        everyone has answered would close it on the *first* member's tap, and
        the answer the team was supposed to agree on would be whatever one
        person hit first — the same trap the normal round fell into. In team
        mode the question runs its clock.
        """
        if not connected_names or self.team_mode:
            return False
        bucket = self._answers.get(self.index, {})
        return all(self.entrant_for(name) in bucket for name in connected_names)

    # ------------------------------------------------------------------
    # Advancing
    # ------------------------------------------------------------------

    def advance(self) -> bool:
        """Score the current question and arm the next one.

        Returns True if another question is now active, False if the round
        has finished (recap is ready via :meth:`build_recap`).
        """
        if not self.active:
            return False

        q = self.current_question
        if q is not None:
            self._score_current(q)

        self.index += 1
        if self.index >= len(self._questions):
            self.active = False
            self.finished = True
            self.index = len(self._questions) - 1
            _LOGGER.info("Lightning round finished")
            return False

        self._arm_current()
        return True

    def _score_current(self, q: Question) -> None:
        """Award fixed points for the current question and record the recap."""
        correct_answer = next(
            (a.text for a in q.answers if a.correct), ""
        )
        bucket = self._answers.get(self.index, {})
        recap = LightningQuestionRecap(
            question_id=q.id,
            question_text=q.question,
            correct_answer=correct_answer,
        )
        for entrant in self._entrants:
            ans = bucket.get(entrant)
            if ans is None or ans.correct is None:
                recap.results[entrant] = "miss"
            elif ans.correct:
                recap.results[entrant] = "correct"
                self.scores[entrant] = (
                    self.scores.get(entrant, 0) + self.points_per_correct
                )
            else:
                recap.results[entrant] = "wrong"
                if (
                    ans.answer_index is not None
                    and 0 <= ans.answer_index < len(q.answers)
                ):
                    recap.chosen[entrant] = q.answers[ans.answer_index].text
        self._recaps.append(recap)

    # ------------------------------------------------------------------
    # Output
    # ------------------------------------------------------------------

    def leaderboard(self) -> list[dict[str, Any]]:
        """Lightning-only leaderboard, sorted high→low.

        ``entrant_id`` is the key the recap grid is indexed by; ``name`` is
        what the row prints. Two teams sharing a name are two rows (#728) —
        which is the point: they are two teams.
        """
        ranked = sorted(
            self.scores.items(), key=lambda kv: kv[1], reverse=True
        )
        return [
            {
                "rank": i + 1,
                "entrant_id": entrant,
                "name": self.display_name(entrant),
                "score": score,
            }
            for i, (entrant, score) in enumerate(ranked)
        ]

    def build_recap(self) -> dict[str, Any]:
        """Full end-screen payload: per-player totals + per-question grid.

        Memoized once the round is finished (#455): the recap is immutable
        after ``advance()`` sets ``self.finished``, so the every-join/reconnect
        rebuild during LIGHTNING_RECAP is pure wasted work. Build once, cache,
        and hand back the same dict thereafter. A pre-finish call (shouldn't
        happen — build_recap is only read at LIGHTNING_RECAP) is left
        uncached so a mid-round change can't be frozen in.
        """
        if self.finished and self._recap_cache is not None:
            return self._recap_cache

        recap = {
            "num_questions": self.num_questions,
            "points_per_correct": self.points_per_correct,
            "leaderboard": self.leaderboard(),
            # entrant key -> the name to print for it (#728). ``results`` and
            # ``chosen`` are keyed by entrant, and a team's key is its id;
            # without this map the host screen would chip out raw ids.
            "names": {e: self.display_name(e) for e in self._entrants},
            "questions": [
                {
                    "question_id": r.question_id,
                    "question_text": r.question_text,
                    "correct_answer": r.correct_answer,
                    "results": dict(r.results),
                    "chosen": dict(r.chosen),
                }
                for r in self._recaps
            ],
        }

        if self.finished:
            self._recap_cache = recap
        return recap
