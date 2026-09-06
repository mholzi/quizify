"""The Quizify WebSocket wire contract — declared here, checked by CI.

Every frame the server sends is a plain dict literal built where the event
happens (``websocket.py``, ``serializers.py``, ``round_message_builder.py``,
``connection.py``). This module does not build them and is not imported by
them: it *declares* their field sets, and ``tests/test_protocol.py`` reads the
real literals out of the source with ``ast`` and fails when a build site and
the declaration disagree.

That is the whole point of the file. Adding a key to a frame without adding it
here is a red test, not a silent divergence — which is what happened to
``all_time`` on ``joined`` and ``teams`` on the roster frame (#749).

What is NOT covered, so nobody reads more into a green run than is there:

* Only dict literals inside ``custom_components/quizify/`` that carry a
  literal ``"type"`` key, plus the ``d["k"] = v`` lines that add to the same
  local afterwards. A frame assembled some other way is invisible here.
  ``game_state`` is the live example: the snapshot in
  ``server/serializers.py::serialize_state_snapshot`` gets its ``type``
  stamped on by the
  caller, so the entry below describes only the leaderboard-refresh literal in
  ``round_message_builder.py``.
* Field *names*, not value types. Nothing here catches an int that turned into
  a string.
* Frames marked ``dynamic_keys`` merge a dict with ``**`` at the build site;
  only the keys spelled out in the literal can be checked.

The client→server direction is a flat set of type strings
(``CLIENT_MESSAGE_TYPES``); the same test pins it against the handler's real
``_DISPATCH`` table instead of grepping for a source pattern.

Dependency-free on purpose: stdlib only, no imports from the modules it
describes.
"""

from __future__ import annotations

from dataclasses import dataclass, field

# ---------------------------------------------------------------------------
# Server → Client frames
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class FrameSpec:
    """The declared shape of one server → client frame.

    ``required`` keys must be present at EVERY site that builds the frame;
    ``optional`` keys may be present at some and absent at others (a
    conditional ``payload["x"] = …`` after the literal, or a second builder
    that carries fewer fields). ``"type"`` itself is implied and is never
    listed.
    """

    required: frozenset[str]
    optional: frozenset[str] = field(default_factory=frozenset)
    #: True when the literal is merged with ``**something`` — the rest of the
    #: payload is a runtime dict and cannot be checked from the source.
    dynamic_keys: bool = False
    note: str = ""


def _spec(
    *required: str,
    optional: tuple[str, ...] = (),
    dynamic_keys: bool = False,
    note: str = "",
) -> FrameSpec:
    return FrameSpec(
        required=frozenset(required),
        optional=frozenset(optional),
        dynamic_keys=dynamic_keys,
        note=note,
    )


SERVER_FRAMES: dict[str, FrameSpec] = {
    # --- join / reconnect -------------------------------------------------
    "joined": _spec(
        "player_id",
        "session_token",
        "color",
        "is_admin",
        "powerup",
        "all_time",
        note=(
            "Unicast to the player whose WS just authenticated. ``all_time`` "
            "is that one player's standing (#371) and rides this frame rather "
            "than the roster broadcast, which would ship everyone's history to "
            "every phone; ``None`` for a first-timer."
        ),
    ),
    "reconnected": _spec(
        "player_id",
        "session_token",
        "powerup",
        "all_time",
        note=(
            "The resume twin of ``joined`` — and deliberately NOT the same "
            "shape: colour and admin flag are already on the client that is "
            "reconnecting, so they are not resent."
        ),
    ),
    "reconnect_failed": _spec(
        note="Unknown or expired session token; the client falls back to join.",
    ),
    # --- lobby roster -----------------------------------------------------
    "player_joined": _spec(
        "players",
        "teams",
        note=(
            "Roster broadcast, coalesced over one window (#453) so a burst of "
            "joins is one frame. ``teams`` rides along because a player "
            "leaving also leaves their team and the last one out dissolves it "
            "(#365) — without it the lobby keeps showing a team nobody is in."
        ),
    ),
    "player_left": _spec(
        "players",
        "teams",
        note="Same builder and shape as ``player_joined``; only the animation differs.",
    ),
    "kicked": _spec(
        "reason",
        note="Unicast to the removed player before their socket is closed.",
    ),
    "game_reset": _spec(note="Broadcast when the host resets the game."),
    # --- teams (#365) -----------------------------------------------------
    "teams_update": _spec("teams"),
    "team_joined": _spec("team"),
    "team_left": _spec(),
    "team_answer": _spec(
        "team_id",
        "answer_index",
        "members",
        "set_by",
        "lock_seconds",
    ),
    # --- question / round -------------------------------------------------
    "question_started": _spec(
        "question_text",
        "answers",
        "timer_duration",
        "round_num",
        "total_rounds",
        "category",
        "difficulty",
        "image_url",
        "reveal_style",
        "question_type",
        optional=(
            # serialize_question_for_player only
            "player_score",
            "is_final_round",
            # serialize_question_for_admin only — the TV may show the answer
            "correct_answer",
            "estimate",
        ),
        note=(
            "Two builders: the per-player one (own shuffled ``answers``, so "
            "couch neighbours can't copy an index) and the admin/TV one, which "
            "adds ``correct_answer`` and drops the per-player fields. Anything "
            "only one of them sends is optional here."
        ),
    ),
    "timer_tick": _spec(
        "remaining",
        note="Per-player: freeze and time-boost change one player's clock only.",
    ),
    "answer_result": _spec(
        "correct",
        "points_earned",
        "speed_bonus",
        "streak_bonus",
        "difficulty_multiplier",
        "new_streak",
        "new_total",
        "milestone_bonus",
        "milestone_streak",
    ),
    "guess_accepted": _spec(
        note="Estimate-round ack so the client can lock its slider.",
    ),
    "answer_progress": _spec("submitted", "total", "players"),
    "round_summary": _spec(
        "correct_answer_index",
        "correct_answer_index_original",
        "correct_answer",
        "question_id",
        "question_text",
        "question_type",
        "fun_fact",
        "leaderboard",
        "players",
        "round",
        "total_rounds",
        "last_round",
        "all_answers",
        "answer_distribution",
        optional=("estimate", "next_image_url"),
        note=(
            "Three index spaces meet here — see the docstring on "
            "``serialize_round_summary``. ``question_id`` is what the 🚩 flag "
            "button POSTs back. ``estimate`` only on estimate rounds (#275). "
            "``next_image_url`` (#736) is the next round's picture, sent only "
            "when there is something worth warming during the reveal — absent "
            "rather than empty, so a client can test for the key."
        ),
    ),
    "finale": _spec(
        "podium",
        "leaderboard",
        "all_players",
        optional=("superlatives", "share"),
        note=(
            "``all_players`` is the same list object as ``leaderboard`` (#415); "
            "``superlatives`` is omitted when empty rather than sent as []."
        ),
    ),
    "evening_tally": _spec(
        dynamic_keys=True,
        note="The tally dict is spread into the frame (#612).",
    ),
    "head_to_head": _spec(
        "at",
        dynamic_keys=True,
        note="The duel dict is spread into the frame (#613).",
    ),
    "all_time_update": _spec("all_time"),
    "game_state": _spec(
        "phase",
        "round",
        "total_rounds",
        "players",
        "leaderboard",
        "player_count",
        optional=("lightning",),
        note=(
            "Declared for the leaderboard-refresh literal in "
            "``round_message_builder``. The full snapshot sent on connect and "
            "reconnect is built in "
            "``server/serializers.py::serialize_state_snapshot`` and "
            "typed by its caller, so it is outside what this file can check."
        ),
    ),
    # --- wager (#656) -----------------------------------------------------
    "wager_window": _spec(
        "round_num",
        "total_rounds",
        "category",
        "difficulty",
        "window_duration",
        "player_score",
        note=(
            "Per-player, because ``player_score`` is the bank the slider bets "
            "against. No question text: the bet is placed on the category "
            "alone, and the question follows in its own ``question_started``."
        ),
    ),
    "wager_progress": _spec(
        "round_num",
        "total_rounds",
        "locked_in",
        "player_count",
        "waiting_on",
        optional=("window_duration", "category", "difficulty"),
        note=(
            "Host/TV view: the tally and who the room waits for, never the "
            "amounts. The three optional fields mark the OPENING message; the "
            "refreshes sent as bets arrive omit them so the TV countdown is "
            "not restarted."
        ),
    ),
    "wager_accepted": _spec("wager"),
    # --- power-ups --------------------------------------------------------
    "powerup_assigned": _spec("powerup_type"),
    "powerup_applied": _spec(
        "powerup_type",
        "source_player",
        optional=(
            "target_player",
            "joker_remove_index",
            "freeze_duration",
            "stolen_points",
        ),
        note=(
            "One type string, three build sites: only the fields that power-up "
            "actually carries are sent, so everything past the source player "
            "is optional."
        ),
    ),
    # --- reactions --------------------------------------------------------
    "reaction": _spec("player_name", "emoji"),
    "reaction_bonus": _spec(
        "from_player",
        "from_players",
        "to_players",
        "leaderboard",
    ),
    # --- lightning round (#42, #285) --------------------------------------
    "lightning_splash": _spec("num_questions", "seconds_per_question"),
    "lightning_question": _spec(
        "index",
        "num_questions",
        "question_text",
        "answers",
        "category",
        "image_url",
        "seconds",
    ),
    "lightning_tick": _spec("index", "remaining"),
    "lightning_answer_result": _spec("index", "correct", "score"),
    "lightning_team_answer": _spec(
        "index",
        "answer_index",
        "members",
        "set_by",
        "lock_seconds",
    ),
    "lightning_recap": _spec("recap"),
    # --- hot seat (#616) --------------------------------------------------
    "hot_seat_auction": _spec("round_num", "total_rounds", "seconds", "players"),
    "hot_seat_auction_you": _spec("seconds", "score"),
    "hot_seat_bid_accepted": _spec("bid", "points"),
    "hot_seat_bid_count": _spec("count", "total"),
    "hot_seat_bet_accepted": _spec("bet", "side", "points"),
    "hot_seat_no_bids": _spec(),
    "hot_seat_awarded": _spec(
        "winner",
        "entrant",
        "bids",
        "stake",
        "pct",
        note=(
            "``winner`` is the PERSON in the chair; ``entrant`` is who pays — "
            "their team's name in team mode, the same person again otherwise "
            "(#804)."
        ),
    ),
    "hot_seat_question": _spec(
        "round_num",
        "total_rounds",
        "winner",
        "entrant",
        "question",
        "difficulty",
        "image_url",
        "seconds",
        optional=("answers", "you_are_seated", "you_are_seat_team", "score"),
    ),
    "hot_seat_answer_accepted": _spec(),
    "hot_seat_tick": _spec("phase", "remaining"),
    "hot_seat_result": _spec(
        "round_num",
        "total_rounds",
        "scores",
        "leaderboard",
        dynamic_keys=True,
        note=(
            "The settlement dict is spread into the frame. ``leaderboard`` is "
            "the post-settlement standing in the ordinary row shape (#833) — "
            "``scores`` is a name→number map no board can build rows from."
        ),
    ),
    # --- errors -----------------------------------------------------------
    "error": _spec(
        "code",
        "message",
        note=(
            "``code`` is one of the ERR_* constants in const.py; the client "
            "looks up a localized string by code and uses ``message`` — plain "
            "English — only as a fallback."
        ),
    ),
}


# ---------------------------------------------------------------------------
# Client → Server message types
# ---------------------------------------------------------------------------

#: Types that never reach ``QuizifyWebSocketHandler._DISPATCH``: they are
#: handled before it, on their own authorization paths.
OUT_OF_BAND_CLIENT_TYPES: frozenset[str] = frozenset({
    "admin_connect",  # first frame of an ?role=admin socket
    "admin_auth",     # token frame that grants that socket the admin role
    "reset_game",     # allowed from a non-admin socket in specific states
})

#: Every message type the server accepts from a client. The dispatched ones
#: must equal ``_DISPATCH.keys()`` exactly — the test imports the handler and
#: compares, so a type added to one side and not the other is a red run.
#: NOTE: "reaction" travels in BOTH directions — a player taps an emoji
#: (client→server, may earn a +1 during reveal) and every other client sees
#: the floating animation (server→client).
CLIENT_MESSAGE_TYPES: frozenset[str] = OUT_OF_BAND_CLIENT_TYPES | frozenset({
    "join",
    "reconnect",
    "get_state",
    "submit_answer",
    "submit_wager",  # gameplay idea #3: final-round Jeopardy wager
    "reaction",      # gameplay idea #11: reveal-time appreciation bonus
    "use_powerup",
    # Hot Seat auction (#616): sealed bid for the chair, optional spectator
    # stake on the outcome, and the seat holder's single answer.
    "hot_seat_bid",
    "hot_seat_bet",
    "hot_seat_answer",
    "start_game",
    "next_question",
    "next_round",
    "end_game",
    "play_again",
    "pause_game",
    "resume_game",
    "admin_skip",
    "kick_player",
    # Host configuration (#494, #281), gated on WS-level admin (#724) because
    # both reach Home Assistant service calls with host-supplied entity ids.
    "configure_tts",
    "configure_house",
    # The host's language pick, pushed while the lobby is still open (#776).
    # Without it GameState.language stays "de" until start_game and every
    # phone that joins an English game is stamped German on arrival.
    "set_language",
    # Lightning Round (issue #42 mechanics, #285 auto-trigger): the round now
    # fires automatically mid-game — there is no host start/end action any
    # more. Players still submit via lightning_answer (separate from
    # submit_answer so the normal-round path stays untouched).
    "lightning_answer",
    # Teams (#365): formed by the players themselves in the lobby, so these
    # are player messages, not host ones. The server refuses them outside the
    # lobby — a latecomer joins alone.
    "create_team",
    "join_team",
    "leave_team",
})
