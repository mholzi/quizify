"""Single source of truth for the Quizify WebSocket protocol.

Every message that crosses the WS boundary has a typed dataclass here.
Server code that builds payloads should construct one of these and call
``.to_dict()`` instead of writing a raw dict literal. That way:

* New required fields can't be silently dropped (the dataclass requires
  them in the constructor).
* Clients reading the field can grep this file and find every message
  shape in one place — no more "where does ``best_streak`` come from?".
* The frontend JS keeps using plain objects, but the TypeScript-style
  comments below document the shape so a future ``.d.ts`` generator
  has somewhere to read from.

This file is intentionally dependency-free: only stdlib, only
dataclasses. It is imported by ``serializers.py`` and friends; cycles
would be expensive here, so don't import from those modules back into
this one.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Literal

# ---------------------------------------------------------------------------
# Server → Client message shapes
# ---------------------------------------------------------------------------


@dataclass
class JoinedMessage:
    """Confirms a player joined or reconnected.

    Sent unicast to the player whose WS just authenticated.
    Frontend dispatch: handleMessage 'joined' / 'reconnected'.
    """

    type: Literal["joined", "reconnected"]
    player_id: str  # canonical player name
    session_token: str  # opaque, used to resume after WS drop
    color: str  # CSS hex, e.g. "#E88A7F"
    is_admin: bool
    powerup: str | None = None  # currently held power-up type or None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class PlayerListMessage:
    """Broadcast when the lobby roster changes.

    The client uses this to render the lobby/in-game player chips.
    Each entry includes ``is_admin`` so the host can show admin controls
    in the player UI after admin-self-join.
    """

    type: Literal["player_joined", "player_left"]
    players: list[dict[str, Any]]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class QuestionStartedMessage:
    """One round's question, sent per-player so each phone gets its own
    shuffled A/B/C order (anti-cheat against couch-neighbour collusion).

    ``answers`` is the SHUFFLED list — the client renders these as
    buttons in order. The server tracks the per-player shuffle map so
    it can translate the player's submitted index back to the original.
    """

    type: Literal["question_started"]
    question_text: str
    answers: list[str]
    timer_duration: float
    round_num: int
    total_rounds: int
    category: str
    difficulty: str
    # Optional image URL for the question (issue #25). Empty string when
    # the question carries no image.
    image_url: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class TimerTickMessage:
    """Sub-second timer update sent per-player.

    Per-player because freeze and time-boost power-ups can change one
    player's remaining time without changing others'.
    """

    type: Literal["timer_tick"]
    remaining: float  # seconds, rounded to 1 decimal

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class AnswerResultMessage:
    """Unicast reply after a player submits an answer.

    The client uses this to lock the buttons and start the local "I
    answered" feedback. The full breakdown (speed/streak/difficulty)
    is also computed here so the reveal screen has data even if the
    player disconnects before round end.
    """

    type: Literal["answer_result"]
    correct: bool
    points_earned: int
    speed_bonus: int
    streak_bonus: int
    difficulty_multiplier: float
    new_streak: int = 0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class RoundSummaryMessage:
    """Broadcast at end-of-round. Carries the canonical (admin-view)
    correct answer + distribution; per-player customisation happens in
    the client which already has its own shuffled buttons.

    ``question_id`` is required by the 🚩 flag-question button — without
    it, players can't report ambiguous or wrong questions back to the
    pack maintainer.
    """

    type: Literal["round_summary"]
    correct_answer_index: int  # in the CANONICAL shuffle (admin's view)
    correct_answer: str  # the answer text — clients match on this
    question_id: str
    fun_fact: str
    leaderboard: list[dict[str, Any]]
    players: list[dict[str, Any]]
    round: int
    total_rounds: int
    last_round: bool
    all_answers: list[dict[str, Any]]
    answer_distribution: list[dict[str, Any]]
    question_text: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class FinaleMessage:
    """Broadcast when the game ends — podium + full leaderboard + superlatives."""

    type: Literal["finale"]
    podium: list[dict[str, Any]]  # [{rank, name, score}, ...]
    leaderboard: list[dict[str, Any]]  # full sorted board
    all_players: list[dict[str, Any]]  # alias of leaderboard (back-compat)
    superlatives: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        # asdict on dataclass-with-default-factory works but drops empty
        # lists/dicts here is intentional — clients tolerate missing keys.
        return asdict(self)


@dataclass
class GameStateMessage:
    """Full snapshot — sent to a freshly-connecting client so it can
    jump to the right view without waiting for the next round event."""

    type: Literal["game_state"]
    phase: str  # GamePhase enum value
    round: int
    total_rounds: int
    players: list[dict[str, Any]]
    leaderboard: list[dict[str, Any]] = field(default_factory=list)
    question: dict[str, Any] | None = None
    pause_reason: str | None = None  # only set when phase == PAUSED

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ErrorMessage:
    """Generic error reply. ``code`` is one of the ERR_* const strings
    in const.py; client looks up a localized message by code."""

    type: Literal["error"]
    code: str
    message: str  # human-readable English fallback

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# ---------------------------------------------------------------------------
# Client → Server message keys (no dataclasses — the server validates
# field by field in _handle_*). Listed here so a future schema-aware
# router can swap in a real validator.
# ---------------------------------------------------------------------------

#: Every message type the server's _handle_message dispatch accepts.
#: Kept as a frozenset so tests can assert dispatch coverage. NOTE:
#: "reaction" is BOTH a client→server message (player taps an emoji
#: button → server may award a +1 bonus during reveal) AND a server→
#: client broadcast (every other client sees the floating animation).
CLIENT_MESSAGE_TYPES: frozenset[str] = frozenset({
    "admin_connect",
    "join",
    "reconnect",
    "get_state",
    "submit_answer",
    "submit_wager",  # gameplay idea #3: final-round Jeopardy wager
    "reaction",      # gameplay idea #11: reveal-time appreciation bonus
    "use_powerup",
    "start_game",
    "next_question",
    "next_round",
    "end_game",
    "reset_game",
    "play_again",
    "pause_game",
    "resume_game",
    "admin_skip",
    "kick_player",
    # Lightning Round (issue #42): host starts the mode; players submit
    # answers via lightning_answer (separate from submit_answer so the
    # normal-round path stays untouched).
    "start_lightning",
    "start_lightning_questions",
    "lightning_answer",
    "end_lightning",
})


# ---------------------------------------------------------------------------
# Equivalent TypeScript declarations — kept hand-maintained as a comment
# block so a future generator has the right starting point. Update both
# sides when the dataclasses above change.
# ---------------------------------------------------------------------------

TYPESCRIPT_DECLARATIONS = """
// Hand-maintained mirror of server/protocol.py. Update both when the
// shape changes — there is no codegen yet.
//
// type WsServerMessage =
//   | { type: 'joined' | 'reconnected'; player_id: string; session_token: string;
//       color: string; is_admin: boolean; powerup: string | null; }
//   | { type: 'player_joined' | 'player_left'; players: Player[]; }
//   | { type: 'question_started'; question_text: string; answers: string[];
//       timer_duration: number; round_num: number; total_rounds: number;
//       category: string; difficulty: string; image_url?: string; }
//   | { type: 'timer_tick'; remaining: number; }
//   | { type: 'answer_result'; correct: boolean; points_earned: number;
//       speed_bonus: number; streak_bonus: number; difficulty_multiplier: number;
//       new_streak: number; }
//   | { type: 'round_summary'; correct_answer_index: number; correct_answer: string;
//       question_id: string; fun_fact: string; leaderboard: LeaderboardEntry[];
//       players: Player[]; round: number; total_rounds: number; last_round: boolean;
//       all_answers: AnswerEntry[]; answer_distribution: DistEntry[];
//       question_text: string; }
//   | { type: 'finale'; podium: PodiumEntry[]; leaderboard: LeaderboardEntry[];
//       all_players: LeaderboardEntry[]; superlatives: Superlative[]; }
//   | { type: 'game_state'; phase: GamePhase; round: number; total_rounds: number;
//       players: Player[]; leaderboard?: LeaderboardEntry[]; question?: QuestionState;
//       pause_reason?: 'admin_paused' | 'admin_disconnected'; }
//   | { type: 'error'; code: string; message: string; };
"""
