"""Shared state serialization helpers for views and WebSocket handler."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from custom_components.quizify.game.highlights import compute_superlatives
from custom_components.quizify.game.phase_controller import GamePhase
from custom_components.quizify.game.scoring import calculate_podium

if TYPE_CHECKING:
    from custom_components.quizify.game.player import PlayerSession
    from custom_components.quizify.game.questions import Question
    from custom_components.quizify.game.state import QuizifyGameState


def _entity_options(hass: Any, domain: str) -> list[dict[str, str]]:
    """Build the dropdown options for one entity domain.

    Shared by :func:`snapshot_tts_entities` and :func:`snapshot_house_entities`
    so both panels' dropdowns get the identical item shape and ordering:
    ``{entity_id, friendly_name}``, sorted case-insensitively by friendly_name,
    with the entity_id standing in when an entity carries no friendly_name.

    Callers guarantee ``hass`` is not None (both snapshots short-circuit to
    empty lists on the standalone dev server before reaching here).
    """
    items = [
        {
            "entity_id": state.entity_id,
            "friendly_name": state.attributes.get("friendly_name", state.entity_id),
        }
        for state in hass.states.async_all(domain)
    ]
    items.sort(key=lambda e: e["friendly_name"].lower())
    return items


def snapshot_tts_entities(hass: Any) -> dict[str, list[dict[str, str]]]:
    """List the TTS engines + media players for the admin narration dropdowns.

    Backs BOTH the admin-token-gated ``/api/quizify/tts-entities`` HTTP endpoint
    and the admin-connect WebSocket payload. Delivering the same lists over the
    already-authenticated admin socket lets the dropdowns populate without a
    separate token-gated fetch racing the admin token's arrival (#356 gated the
    endpoint; the HTTP fetch first fires at page-init, before the token lands,
    so it 401s and the dropdowns fall back to "None found"). The WS carries the
    lists directly, so there is no race.

    ``hass`` is None on the standalone dev server — both lists come back empty
    and the dropdowns show their "configure in HA" fallback.

    Returns ``{"tts": [...], "media_players": [...]}`` where each item is
    ``{entity_id, friendly_name}``, sorted by friendly_name.
    """
    if hass is None:
        return {"tts": [], "media_players": []}

    return {
        "tts": _entity_options(hass, "tts"),
        "media_players": _entity_options(hass, "media_player"),
    }


def snapshot_house_entities(hass: Any) -> dict[str, list[dict[str, str]]]:
    """List the lights + media players + scenes for the "House Plays Along" panel.

    Backs BOTH the admin-token-gated ``/api/quizify/house-entities`` HTTP
    endpoint and the admin-connect WebSocket payload (#494 Phase 4). The WS
    piggyback is the primary path for the same reason as the TTS lists (#502):
    the token-gated fetch fires at page-init, *before* the admin token arrives
    over the socket, so a lone HTTP path races the token and 401s — the panel's
    entity pickers would then show "None found". The admin-connect frame is
    already authenticated, so riding it removes the race entirely; the HTTP
    endpoint stays for parity/refresh.

    The three lists back the three per-panel entity overrides: the light
    entities the choreography drives, the media_player the SFX cues play on,
    and the scene fired for the winner finale. Each override is optional — an
    empty selection falls back to the config-entry option (party lights /
    media player / finale scene).

    ``hass`` is None on the standalone dev server — all three lists come back
    empty and the pickers show their "configure in HA" fallback.

    Returns ``{"lights": [...], "media_players": [...], "scenes": [...]}`` where
    each item is ``{entity_id, friendly_name}``, sorted by friendly_name.
    """
    if hass is None:
        return {"lights": [], "media_players": [], "scenes": []}

    return {
        "lights": _entity_options(hass, "light"),
        "media_players": _entity_options(hass, "media_player"),
        "scenes": _entity_options(hass, "scene"),
    }


def build_game_status_response(
    game_state: QuizifyGameState | None,
    game_id: str | None,
) -> dict[str, Any]:
    """Build the game-status JSON payload."""
    if not game_id or not game_state or game_state.game_id != game_id:
        return {
            "exists": False,
            "phase": None,
            "can_join": False,
        }

    return {
        "exists": True,
        "phase": game_state.phase.value,
        # WAGER_ACTIVE joins like a live round (#656) — the betting window is
        # part of the final round, and locking newcomers out of it would put a
        # dead spot in the one place a late guest is most likely to arrive.
        "can_join": game_state.phase.value
        in ("LOBBY", "WAGER_ACTIVE", "QUESTION_ACTIVE", "ANSWER_REVEAL"),
    }


def serialize_wager_window(
    question: Question,
    round_num: int,
    total_rounds: int,
    window_duration: float,
    player_score: int,
) -> dict[str, Any]:
    """Serialize the final round's betting window (#656).

    Deliberately withholds ``question_text`` and ``answers``: the bet is
    placed on the category alone, the way a Jeopardy final is. Sending the
    text here would reproduce the bug this window exists to fix — with the
    question in hand, a player who knows the answer stakes everything at no
    risk. The text only leaves the server with ``question_started``, after
    the window has closed.
    """
    return {
        "type": "wager_window",
        "round_num": round_num,
        "total_rounds": total_rounds,
        "category": question.category,
        "difficulty": question.difficulty,
        "window_duration": window_duration,
        "player_score": player_score,
    }


def serialize_question_for_player(
    question: Question,
    shuffled_answers: list[str],
    round_num: int,
    total_rounds: int,
    timer_duration: float,
    is_final_round: bool = False,
    player_score: int = 0,
) -> dict[str, Any]:
    """Serialize a question for player broadcast (no correct flag).

    ``is_final_round`` tells the client to render the wager UI before
    enabling the answer buttons (gameplay idea #3). ``player_score`` is
    the per-player current score so the wager picker can show "Wagering
    25 of 92 points" without an extra fetch.
    """
    payload: dict[str, Any] = {
        "type": "question_started",
        "question_text": question.question,
        "answers": shuffled_answers,
        "timer_duration": timer_duration,
        "round_num": round_num,
        "total_rounds": total_rounds,
        "category": question.category,
        "difficulty": question.difficulty,
        "image_url": question.image_url,
        # #434: how the dashboard/player should uncover that image.
        "reveal_style": question.reveal_style,
        "is_final_round": is_final_round,
        "player_score": player_score,
        "question_type": question.type,
    }
    # Estimate questions (#275): ship the slider range/unit, NEVER the answer.
    # The client renders a slider instead of the 3-answer grid.
    if question.is_estimate:
        payload["answers"] = []
        payload["estimate"] = {
            "min": question.estimate_min,
            "max": question.estimate_max,
            "unit": question.estimate_unit,
            "step": question.estimate_step,
        }
        # Estimate finals score via _evaluate_estimate_round, which never reads
        # player.wager — a wager here can't be resolved. Never advertise the
        # wager UI for an estimate final so the client doesn't offer a bet the
        # server would reject. (#353.)
        payload["is_final_round"] = False
    return payload


def _apply_display_order(items: list[Any], order: list[int] | None) -> list[Any]:
    """Reorder ``items`` by ``order``, or return them untouched.

    Guards a malformed/stale map (wrong length, out-of-range index) by falling
    back to the original order — a mis-ordered answer grid is a worse failure
    than an unshuffled one.
    """
    if not order or len(order) != len(items):
        return list(items)
    if sorted(order) != list(range(len(items))):
        return list(items)
    return [items[i] for i in order]


def serialize_question_for_admin(
    question: Question,
    round_num: int,
    total_rounds: int,
    timer_duration: float,
    display_order: list[int] | None = None,
) -> dict[str, Any]:
    """Serialize a question for admin (includes correct answer).

    ``display_order`` is the round's canonical shuffle (``shuffle_map``). The
    admin and the TV/cast dashboard share this payload and render the answer
    tiles in the order they arrive, so without it the grid would sit in
    question-JSON order — and most shipped packs put the correct answer first
    in the file, which put it on tile A of the big screen every round (#521).
    Passing the shuffle also lines the tiles up with ``correct_answer_index``
    and with the option order the TTS narrator speaks.
    """
    correct_answer = ""
    for a in question.answers:
        if a.correct:
            correct_answer = a.text
            break

    ordered = _apply_display_order(question.answers, display_order)

    payload: dict[str, Any] = {
        "type": "question_started",
        "question_text": question.question,
        "correct_answer": correct_answer,
        "answers": [{"text": a.text, "correct": a.correct} for a in ordered],
        "timer_duration": timer_duration,
        "round_num": round_num,
        "total_rounds": total_rounds,
        "category": question.category,
        "difficulty": question.difficulty,
        "image_url": question.image_url,
        "reveal_style": question.reveal_style,
        "question_type": question.type,
    }
    # Estimate questions (#275): the admin/TV gets the slider range + the true
    # value (the host screen may legitimately show the answer). The player
    # serializer above withholds it.
    if question.is_estimate:
        payload["estimate"] = {
            "min": question.estimate_min,
            "max": question.estimate_max,
            "unit": question.estimate_unit,
            "step": question.estimate_step,
            "answer": question.estimate_answer,
        }
    return payload


def strip_answer_for_dashboard(payload: dict[str, Any]) -> dict[str, Any]:
    """Return the admin question payload with every trace of the answer removed.

    The TV connects as ``role=dashboard``, which takes no token at all (#604),
    so whatever this payload carries is readable by anyone who can reach the
    integration while the question is still running. The per-player answer
    shuffle exists to make copying hard; shipping the answer to an
    unauthenticated role walks around it with one query parameter.

    The answer hides in three places, and missing any one of them leaves the
    hole open:

    * ``correct_answer`` — the plain text,
    * ``correct`` on every option — a boolean that names the right tile,
    * ``estimate.answer`` — the true value on estimate rounds (#275).

    Everything the TV actually renders survives: the canonical shuffled order
    from #521, the option texts, and the shape ``{"text": …}`` that
    ``dashboard.html`` reads. It never looks at ``correct`` before the reveal —
    the reveal runs off ``round_summary``'s ``correct_answer_index`` — so this
    costs the big screen nothing.
    """
    stripped = dict(payload)
    stripped.pop("correct_answer", None)
    stripped["answers"] = [
        {"text": a["text"]} if isinstance(a, dict) else {"text": a}
        for a in payload.get("answers", [])
    ]
    estimate = payload.get("estimate")
    if isinstance(estimate, dict):
        stripped["estimate"] = {
            k: v for k, v in estimate.items() if k != "answer"
        }
    return stripped


def serialize_leaderboard(players: list[PlayerSession]) -> list[dict[str, Any]]:
    """Build sorted leaderboard from player list.

    Ties share a rank (#308): two players on the same score get the same rank
    and the next rank skips accordingly (standard competition ranking —
    1,1,3,…), instead of breaking ties arbitrarily by dict/insertion order and
    showing one of two equal players as strictly ahead.
    """
    sorted_players = sorted(players, key=lambda p: p.score, reverse=True)
    result = []
    prev_score: int | None = None
    rank = 0
    for i, p in enumerate(sorted_players):
        # Competition ranking: same score → same rank; the next distinct score
        # jumps to position i+1.
        if prev_score is None or p.score != prev_score:
            rank = i + 1
            prev_score = p.score
        breakdown = (
            p.round_score_breakdown if hasattr(p, "round_score_breakdown") else {}
        )
        # Determine if this player answered correctly this round
        last_result = p.round_history[-1] if p.round_history else None
        result.append({
            "rank": rank,
            # Stable identity of the row, beside the name that gets printed
            # (#759, the same shape #728 gave the lightning leaderboard). Two
            # teams may legitimately carry the same name — the name is free
            # text on purpose — and by name they were one row to every client
            # that looked one up: the "you" highlight lit both, the FLIP
            # animation and the rank-delta memo collapsed them into one.
            # A player's own name is already unique per game, so for a player
            # the entrant id is simply their name.
            "entrant_id": getattr(p, "team_id", None) or p.name,
            "name": p.name,
            "score": p.score,
            "streak": p.streak,
            "round_score": p.round_score,
            "correct": last_result == "correct",
            "missed_round": last_result == "timeout",
            "speed_bonus": breakdown.get("speed_bonus", 0),
            "streak_bonus": breakdown.get("streak_bonus", 0),
            "difficulty_multiplier": breakdown.get("difficulty_multiplier", 1.0),
            "double_points": breakdown.get("double_points", False),
            "color": p.color,
            "is_admin": p.is_admin,
            "submitted": p.submitted,
            # Whole-game stats — read by the finale screen
            # (player-end.js looks for these exact keys; without them the
            # "BESTE SERIE / GESPIELTE RUNDEN / POWER-UPS GENUTZT" cards
            # all displayed 0 for everyone).
            "best_streak": p.max_streak,
            "rounds_played": len(p.round_history),
            "powerups_used": p.powerups_used,
        })
    return result


def serialize_state_snapshot(game_state: QuizifyGameState) -> dict[str, Any]:
    """Build the full client state snapshot (#747).

    This is what a phone, a television or the admin page is handed on join, on
    reconnect, on ``get_state`` and after a resume — the one payload that has to
    agree with every live broadcast, because a client that reconnects mid-round
    renders from this instead of from the frame it missed.

    It used to live on ``QuizifyGameState`` as ``get_state_snapshot``, which put
    the client wire format inside the domain object and duplicated keys the
    per-player serializers in this module already emitted — ``image_url``,
    ``reveal_style``, ``question_type``, ``estimate``, ``time_remaining``. Every
    drift between the two builders cost a bug: #297 (the snapshot leaderboard
    had fewer fields than the live broadcast, so a FINALE reconnect locked the
    admin out), #434 (``reveal_style`` missing, so a reconnecting dashboard
    snapped a progressive-reveal picture to sharp), #521 (snapshot answers
    unshuffled, so the correct tile sat first), #253 (nested vs flat
    ``round_summary``). Two builders in two layers is the shape those share, so
    there is one now, here, next to the serializers it has to agree with — and
    ``game/state.py`` no longer imports the server layer to build a payload.

    ``RoundMessageBuilder.project_snapshot_for_player`` still re-shapes the
    result per recipient (own shuffle, own timer); this is the canonical frame
    it projects from.
    """
    snapshot: dict[str, Any] = {
        "phase": game_state.phase.value,
        "game_id": game_state.game_id,
        "round": game_state.round,
        "total_rounds": game_state.total_rounds,
        "category": game_state.category,
        "difficulty": game_state.difficulty,
        # The player client uses this to sync its UI locale with the
        # game language (player-core.js handleGameState). Without it
        # the UI stayed at the browser locale (e.g., German chrome
        # over English questions if the host picked EN but the guest's
        # phone is DE). Live-test Mai-27.
        "language": game_state.language,
        "players": game_state.get_players_state(),
        "leaderboard": serialize_leaderboard(game_state.get_ranked_participants()),
        # Always present, empty in an ordinary game (#365). A reconnecting
        # phone has to be able to tell "no teams" from "teams not sent" —
        # otherwise a member who drops mid-game comes back without their
        # team indicator and believes they are playing alone.
        "teams": game_state.team_registry.to_list(),
    }

    question = game_state.get_current_question()

    if game_state.phase == GamePhase.WAGER_ACTIVE and question:
        q = question
        # #656: the betting window carries category and difficulty and
        # NOTHING else. A phone that reconnects here must not be handed
        # the question text — that text has not been sent to anyone yet,
        # and a player who could read it while betting would be betting
        # on a certainty. The remaining seconds are the room's, not a
        # fresh window, so a reconnect can't buy extra thinking time.
        connected = [p for p in game_state.get_players() if p.connected]
        snapshot["wager"] = {
            "category": q.category,
            "difficulty": q.difficulty,
            "window_remaining": round(game_state.wager_window_remaining(), 1),
            "window_duration": game_state.wager_window_duration,
            # The tally, so a TV reconnecting mid-window shows the bets
            # already in rather than restarting the count at zero.
            "locked_in": len(connected) - len(game_state.players_missing_wager()),
            "player_count": len(connected),
        }

    if game_state.phase == GamePhase.QUESTION_ACTIVE and question:
        q = question
        # Calculate time remaining for mid-round joiners
        remaining = game_state.question_time_remaining_for_snapshot()
        # Canonical-shuffle order (#521), matching the live
        # ``question_started`` payload. Emitting question-JSON order here
        # meant a dashboard reconnecting mid-question rebuilt its grid
        # unshuffled — and most packs keep the correct answer first in the
        # file. ``shuffled_answers`` is empty only before the first
        # question of a game, where the fallback is the same list anyway.
        snapshot["question"] = {
            "id": q.id,
            "text": q.question,
            "answers": (
                list(game_state.shuffled_answers)
                if len(game_state.shuffled_answers) == len(q.answers)
                else [a.text for a in q.answers]
            ),
            "difficulty": q.difficulty,
            "category": q.category,
            "image_url": q.image_url,
            # #434: a dashboard that reconnects mid-question needs both the
            # style and the remaining time below to resume the blur at the
            # right point instead of snapping to sharp.
            "reveal_style": q.reveal_style,
            # #730/#731: unconditionally, not only for estimates. The live
            # ``question_started`` always carries the type, so a snapshot
            # that only carries it sometimes is a field the restore path
            # cannot forward without knowing which case it is in — exactly
            # the asymmetry the parity test now forbids.
            "question_type": q.type,
            "time_limit": game_state.round_duration,
            "time_remaining": round(remaining, 1),
        }
        # Estimate questions (#275) carry slider metadata instead of
        # answers so a reconnecting player rebuilds the slider, not the
        # 3-answer grid.
        if q.is_estimate:
            snapshot["question"]["estimate"] = {
                "min": q.estimate_min,
                "max": q.estimate_max,
                "unit": q.estimate_unit,
                "step": q.estimate_step,
            }

    round_summary = game_state.get_round_summary()
    if game_state.phase == GamePhase.ANSWER_REVEAL and round_summary:
        s = round_summary
        q = s.question
        # Round-shuffle answer order, mirroring the QUESTION_ACTIVE
        # snapshot's ``question.answers``. A TV/dashboard that (re)connects
        # during the reveal has no live ``question`` block to render, so
        # without these fields its question view was blank (#296).
        # Both the order and the highlight index moved from question-JSON
        # order to the round shuffle in #521 — in JSON order most packs
        # keep the correct answer first, which put it on tile A every
        # round. ``correct_answer_index_original`` stays in the payload for
        # clients cached from before that change.
        correct_idx_original = next(
            (i for i, a in enumerate(q.answers) if a.correct), -1
        )
        order = game_state.shuffle_map
        if len(order) == len(q.answers) and sorted(order) == list(
            range(len(q.answers))
        ):
            reveal_answers = [q.answers[i].text for i in order]
            if correct_idx_original >= 0:
                correct_idx_display = order.index(correct_idx_original)
            else:
                correct_idx_display = -1
        else:
            # No usable shuffle (pre-first-question, or a malformed map):
            # a mis-ordered grid is worse than an unshuffled one.
            reveal_answers = [a.text for a in q.answers]
            correct_idx_display = correct_idx_original
        snapshot["round_summary"] = {
            "question_text": q.question,
            "category": q.category,
            "image_url": q.image_url,
            "answers": reveal_answers,
            "correct_answer_index": correct_idx_display,
            "correct_answer_index_original": correct_idx_original,
            "correct_answer": s.correct_answer.text,
            "fun_fact": s.fun_fact,
            "results": [
                {
                    "player_id": r.player_id,
                    "correct": r.correct,
                    "points_earned": r.points_earned,
                    "new_streak": r.new_streak,
                    "new_total": r.new_total,
                }
                for r in s.results
            ],
        }
        # Estimate reveal data (#275) so a reconnect during the reveal
        # rebuilds the number line instead of an empty answer grid.
        if s.estimate is not None:
            snapshot["round_summary"]["question_type"] = q.type
            snapshot["round_summary"]["estimate"] = s.estimate

    if game_state.phase == GamePhase.FINALE:
        # Use cached values computed once in end_game()
        podium = game_state.get_finale_podium() or calculate_podium(
            game_state.get_ranked_participants()
        )
        snapshot["podium"] = [
            {"name": p.name, "score": p.score, "rank": i + 1}
            for i, p in enumerate(podium)
        ]
        cached_awards = game_state.get_finale_superlatives()
        awards = (
            cached_awards
            if cached_awards is not None
            else compute_superlatives(game_state.get_ranked_participants())
        )
        if awards:
            snapshot["superlatives"] = [s.to_dict() for s in awards]

    lightning = game_state.lightning
    if game_state.phase == GamePhase.LIGHTNING and lightning is not None:
        lr = lightning
        lq = lr.current_question
        snapshot["lightning"] = {
            "index": lr.index,
            "num_questions": lr.num_questions,
            "time_remaining": round(lr.time_remaining(), 1),
            "seconds_per_question": lr.seconds_per_question,
            "leaderboard": lr.leaderboard(),
            # True while the intro splash ("Bolt Burst", #201) is still
            # showing and the first question hasn't been broadcast.
            "splash_pending": game_state.lightning_splash_pending,
        }
        if lq is not None:
            # Canonical (admin/TV) answer order; players get their own
            # shuffle pushed via the lightning_question event.
            snapshot["lightning"]["question"] = {
                "text": lq.question,
                "answers": [a.text for a in lq.answers],
                "category": lq.category,
                "image_url": lq.image_url,
            }

    if game_state.phase == GamePhase.LIGHTNING_RECAP and lightning is not None:
        snapshot["lightning_recap"] = lightning.build_recap()

    hot_seat = game_state.hot_seat
    # #664: the Hot Seat detour belongs in the contract like every other
    # phase. Without this block a reconnecting phone got a snapshot naming
    # a HOT_SEAT phase and no hot-seat data, fell through the client's
    # default case onto the lobby, and — if it belonged to the seat holder
    # — could never get back to the question, which #653 then charges as a
    # lost stake. The TV had the same hole, with the previous round's
    # reveal frozen on it for the whole detour.
    if (
        game_state.phase
        in (
            GamePhase.HOT_SEAT_AUCTION,
            GamePhase.HOT_SEAT,
            GamePhase.HOT_SEAT_REVEAL,
        )
        and hot_seat is not None
    ):
        hs = hot_seat
        if game_state.phase == GamePhase.HOT_SEAT_AUCTION:
            stage = "auction"
        elif game_state.phase == GamePhase.HOT_SEAT_REVEAL:
            stage = "result"
        else:
            # There is deliberately no separate "the bids are landing"
            # stage. ``resolve_auction`` starts the answer clock at the
            # moment the chair is awarded, so the live flow's four-second
            # bid reveal is already being paid for out of the seat
            # holder's window. Someone who reconnects during that hold is
            # better served by the question than by a reveal they are
            # being charged for — and worse, without a question to look
            # at they would burn the clock reading a scoreboard.
            stage = "question"
        block: dict[str, Any] = {
            "stage": stage,
            "time_remaining": round(hs.time_remaining(), 1),
            "auction_seconds": hs.auction_seconds,
            "answer_seconds": hs.answer_seconds,
            # The banks the bids are percentages of — a snapshot taken when
            # the auction opened, not the live scores. Keyed by ENTRANT
            # (#804), which is what the per-player projection in
            # ``round_message_builder`` reads it by; it never leaves the
            # server in this shape.
            "banks": dict(hs.scores),
            # Count only. The auction is sealed until it closes, and a
            # reconnect must not be a way to read it early.
            "bid_count": len(hs.bids),
            "bidder_count": len(hs.scores),
            # The person in the chair, matching the live ``hot_seat_awarded``
            # frame so the phone's restore path and the live path agree.
            "winner": hs.seat_holder,
            "entrant": hs.winner_name,
        }
        if hs.winner is not None:
            block["pct"] = hs.winning_pct
            block["stake"] = hs.winning_stake
            block["bids"] = hs.reveal()
        # The question is withheld during the auction on purpose: bidding
        # is meant to be a bet on yourself, not on a question you have
        # already read.
        if stage in ("question", "result") and hs.question is not None:
            block["question"] = {
                "text": hs.question.question,
                # Canonical order — admin and TV. The seat holder's own
                # shuffle is projected in round_message_builder.
                "answers": [a.text for a in hs.question.answers],
                "category": hs.question.category,
                # #730: the live ``hot_seat_question`` sends this to every
                # phone already, so withholding it here bought no secrecy
                # — it only left the restore path with one more field it
                # could never forward.
                "difficulty": hs.question.difficulty,
                "image_url": hs.question.image_url,
            }
        if stage == "result":
            block["summary"] = hs.summary()
        snapshot["hot_seat"] = block

    if game_state.phase == GamePhase.PAUSED:
        # #703: the reason used to be attached by the two pause
        # *broadcasts* only, so any phone that reconnected (or joined, or
        # asked for state) during a pause got a snapshot without it. The
        # client derives both the title and the 60s reset affordance from
        # this field, so a guest who reloaded during a host-gone pause was
        # told "the host will resume" and lost the only way out (#299) —
        # on exactly the phones that had just reconnected.
        snapshot["pause_reason"] = game_state.get_pause_reason()

    return snapshot


def serialize_player_list(players: list[PlayerSession]) -> list[dict[str, Any]]:
    """Build player list for broadcast.

    NB: must include `is_admin` so the client can show admin controls
    (Start Game button in the lobby, Skip/Next/End during gameplay).
    Without it, admin-as-player can't drive the game from the player tab.
    """
    return [
        {
            "name": p.name,
            "score": p.score,
            "streak": p.streak,
            "connected": p.connected,
            "color": p.color,
            "is_admin": p.is_admin,
        }
        for p in players
    ]


def _entrant_has_answered(entrant: Any) -> bool:
    """Whether one leaderboard row has a response standing this round (#835).

    A player is ``submitted`` the moment they tap — the tap is final. A team
    never is: ``submit_answer``'s team branch deliberately marks nobody
    submitted, because every member may keep changing the team's answer until
    the clock stops (#365). So a team has answered when it has a standing
    answer or guess, which is exactly the thing that will score.
    """
    if getattr(entrant, "team_id", None) is not None:
        return (
            getattr(entrant, "current_answer", None) is not None
            or getattr(entrant, "current_guess", None) is not None
        )
    return bool(entrant.submitted)


def serialize_answer_progress(
    players: list[PlayerSession],
    entrants: list[Any] | None = None,
) -> dict[str, Any]:
    """Who has answered this round, in the shape the phone tracker expects (#619).

    Deliberately its own payload rather than a field on ``serialize_player_list``:
    that list rides every join/leave frame, and per-round answer state has no
    business in a roster message that also reaches the lobby.

    The field names are not a free choice — ``renderSubmissionTracker`` was
    written long before anything sent it data and already reads ``name``,
    ``submitted`` and ``connected`` off each entry. The issue proposed a plain
    list of names; that renderer could not have drawn it.

    ``entrants`` is ``game_state.get_ranked_participants()`` — the rows the
    room can see, teams in team mode and players otherwise (#835). Counting
    people in team mode got the counter wrong twice over: nobody is ever
    marked submitted in a team game, so the television read ``0/4`` for the
    whole round, and the ``4`` was the head count rather than the number of
    things that have to answer. Omitted, this falls back to ``players``, which
    outside team mode is the same list.

    Scores are omitted on purpose. This goes out mid-question, and a live score
    beside each name would leak who just answered correctly.
    """
    rows: list[Any] = players if entrants is None else entrants
    connected_by_name = {p.name: p.connected for p in players}

    def _connected(row: Any) -> bool:
        members = getattr(row, "members", None)
        if members is None:
            return bool(row.connected)
        # A team is present while any of it is: the tracker greys out a row
        # the room should stop waiting for, and a team with one phone still
        # awake is not that.
        return any(connected_by_name.get(m, False) for m in members)

    entries = [
        {
            "name": r.name,
            "submitted": _entrant_has_answered(r),
            "connected": _connected(r),
        }
        for r in rows
    ]
    return {
        "type": "answer_progress",
        "players": entries,
        "submitted": sum(1 for e in entries if e["submitted"]),
        "total": len(entries),
    }


def build_share_payload(
    all_players: list[PlayerSession],
    packs: list[str] | None = None,
) -> dict[str, Any]:
    """Per-player run summary for the shareable result card (#369).

    Rides the finale message only — it is sent once per game, so the per-round
    detail costs nothing on the hot round-summary path.

    ``results`` is ``PlayerSession.round_history`` verbatim: one of
    ``correct`` / ``wrong`` / ``timeout`` per round the player took part in.
    Late joiners have a shorter list than the game had rounds; the client
    renders what it gets rather than padding, so a card never claims the
    player sat through a round they missed.

    Power-ups are a per-game COUNT, not a per-round marker — the game never
    records which round a power-up was spent in. The card therefore shows
    "N power-ups" as a line, and the round strip stays a truthful
    correct/wrong/timeout sequence.
    """
    ranked = sorted(all_players, key=lambda p: p.score, reverse=True)
    total = len(ranked)
    entries: list[dict[str, Any]] = []
    rank = 0
    prev: int | None = None
    for i, p in enumerate(ranked):
        # Competition ranking, identical to serialize_leaderboard: equal
        # scores share a rank so two tied players don't get 1st/2nd by
        # sort order. A shared card must not invent a placing.
        if prev is None or p.score != prev:
            rank = i + 1
            prev = p.score
        history = list(p.round_history)
        entries.append({
            "name": p.name,
            "rank": rank,
            "total_players": total,
            "score": p.score,
            "rounds": len(history),
            "correct": history.count("correct"),
            "results": history,
            "powerups": p.powerups_used,
        })
    return {"packs": list(packs or []), "players": entries}


def serialize_finale(
    podium: list[PlayerSession],
    all_players: list[PlayerSession],
    superlatives: list[dict[str, str]] | None = None,
    packs: list[str] | None = None,
) -> dict[str, Any]:
    """Build finale payload with podium and full leaderboard."""
    # Shared-rank podium (#308): equal scores share a rank, same as the
    # leaderboard, so two tied finalists aren't shown 1st/2nd arbitrarily.
    podium_entries = []
    _prev: int | None = None
    _rank = 0
    for i, p in enumerate(podium):
        if _prev is None or p.score != _prev:
            _rank = i + 1
            _prev = p.score
        podium_entries.append({"rank": _rank, "name": p.name, "score": p.score})
    # ``leaderboard`` and ``all_players`` carry the identical sorted list —
    # serialize it once instead of twice (#415).
    lb = serialize_leaderboard(all_players)
    result: dict[str, Any] = {
        "type": "finale",
        "podium": podium_entries,
        "leaderboard": lb,
        "all_players": lb,
    }
    if superlatives:
        result["superlatives"] = superlatives
    # Shareable result cards (#369). Always present so the client can render
    # the share block without probing for the key.
    result["share"] = build_share_payload(all_players, packs)
    return result


def serialize_round_summary(
    correct_answer_index: int,
    correct_answer_text: str,
    fun_fact: str,
    leaderboard: list[dict[str, Any]],
    round_num: int,
    total_rounds: int,
    all_answers: list[dict[str, Any]] | None = None,
    question_text: str = "",
    num_answer_options: int = 3,
    players: list[dict[str, Any]] | None = None,
    last_round: bool = False,
    question_id: str = "",
    correct_answer_index_original: int = -1,
    question_type: str = "multiple_choice",
    estimate: dict[str, Any] | None = None,
    display_order: list[int] | None = None,
    next_image_url: str | None = None,
    entrant_of: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Build round summary broadcast payload.

    Three index spaces meet here, so they are named rather than implied:

    * ``correct_answer_index`` — CANONICAL-shuffle space. The per-player
      reveal client uses it to highlight the right tile in its own shuffle,
      and since #521 the TV grid is rendered in this same order, so the
      dashboard uses it too.
    * ``correct_answer_index_original`` — question-JSON order. Retained for
      clients cached from before #521, which still render an unshuffled grid.
      New clients prefer ``correct_answer_index``.
    * ``all_answers[].answer_index`` — question-JSON order, unchanged. The
      player reveal maps it through its own shuffle, so moving it would break
      that path for no gain.

    ``answer_distribution`` is emitted in CANONICAL space so the #151 bars
    attach to the tiles the dashboard actually drew, and — given
    ``entrant_of``, a player-name → team-id map — it counts one vote per
    entrant rather than one per head (#853). ``all_answers`` itself stays
    per player: the phone reveal reads its own row out of it.

    ``next_image_url`` is the picture the NEXT round will show (#736), so
    clients can warm it while the reveal is on screen instead of after
    ``question_started``, with the countdown already running. It is a *hint*
    and nothing more: the key is omitted when there is nothing to warm, and a
    client that ignores it behaves exactly as before. Critically it is not the
    round's own image — a client must never render it, only fetch it, or a
    progressive-reveal question (#434) would be given away a round early.
    """
    # Compute answer distribution from all_answers. `answer_index` on those
    # entries is question-JSON order; the bars hang off the canonical grid,
    # so the mapping happens here rather than in the client.
    answer_distribution = _compute_answer_distribution(
        all_answers or [], num_answer_options, display_order, entrant_of
    )

    summary: dict[str, Any] = {
        "type": "round_summary",
        "correct_answer_index": correct_answer_index,
        "correct_answer_index_original": correct_answer_index_original,
        "correct_answer": correct_answer_text,
        # question_id flows to the client so the 🚩 flag-question button
        # can POST it back to /api/quizify/flag-question for the pack
        # maintainer to triage.
        "question_id": question_id,
        "fun_fact": fun_fact,
        "leaderboard": leaderboard,
        # `players` is required by the reveal client to determine
        # admin-as-player for showing the Next Round button. Without
        # it, currentPlayer.is_admin can't be resolved and the
        # button stays hidden. (Was a real bug before this version.)
        "players": players or leaderboard,
        "round": round_num,
        "total_rounds": total_rounds,
        # `last_round` flag so the reveal can swap the Next Round
        # button label to "Final Results" on the last round.
        "last_round": last_round,
        "all_answers": all_answers or [],
        "answer_distribution": answer_distribution,
        "question_text": question_text,
        "question_type": question_type,
    }
    # Estimate rounds (#275): attach the number-line reveal block so both the
    # player reveal and the TV dashboard can plot every guess.
    if estimate is not None:
        summary["estimate"] = estimate
    # #736: only present when there is something to warm. Absent beats a null
    # here — every existing payload assertion in the suite stays exact, and a
    # client can test one thing (`if (msg.next_image_url)`) rather than two.
    if next_image_url:
        summary["next_image_url"] = next_image_url
    return summary


def _dedupe_by_entrant(
    all_answers: list[dict[str, Any]],
    entrant_of: dict[str, str] | None,
) -> list[dict[str, Any]]:
    """One row per entrant, in the order the rows arrived (#853).

    ``all_answers`` is per PLAYER and has to stay that way — the phone reveal
    reads its own row out of it for the answer it gave, the points it earned
    and its own ``correct_button_index``. In team mode every member carries
    the same team row (#365), so counting the list counts heads.
    """
    if not entrant_of:
        return all_answers
    seen: set[str] = set()
    rows: list[dict[str, Any]] = []
    for entry in all_answers:
        name = entry.get("player_name")
        key = entrant_of.get(name, name) if isinstance(name, str) else None
        if key is not None:
            if key in seen:
                continue
            seen.add(key)
        rows.append(entry)
    return rows


def _compute_answer_distribution(
    all_answers: list[dict[str, Any]],
    num_options: int,
    display_order: list[int] | None = None,
    entrant_of: dict[str, str] | None = None,
) -> list[dict[str, Any]]:
    """Compute per-option vote counts and percentages.

    Returns a list of dicts: [{"index": 0, "count": 3, "percent": 60}, ...]
    Includes a separate entry for no_answer (timeout) players.

    ``answer_index`` on the incoming entries is question-JSON order.
    ``display_order`` (the round's canonical shuffle) maps each vote onto the
    tile the dashboard actually rendered (#521); without it the bars would
    count the right votes against the wrong answers.

    ``entrant_of`` maps a player's name onto the thing that actually answers —
    their team's id in team mode, their own name otherwise (#853). A team has
    exactly one answer (#365) but a row per member, so without this a team of
    two was two votes: Sofa on Argentina and Dan alone on Brazil read 67/33 on
    the television instead of the 50/50 that happened, and a large team's pick
    dominated a chart the room reads as "what did we all choose". It is also
    what the counter above the bars has counted since #835 — one entrant, one
    vote — so the two numbers on that screen now agree.
    """
    rows = _dedupe_by_entrant(all_answers, entrant_of)
    counts = [0] * num_options
    no_answer_count = 0
    total = len(rows)

    # original index -> position in the rendered grid
    to_display: dict[int, int] = {}
    if (
        display_order
        and len(display_order) == num_options
        and sorted(display_order) == list(range(num_options))
    ):
        to_display = {orig: pos for pos, orig in enumerate(display_order)}

    for entry in rows:
        idx = entry.get("answer_index")
        if entry.get("no_answer") or idx is None:
            no_answer_count += 1
        elif isinstance(idx, int) and 0 <= idx < num_options:
            counts[to_display.get(idx, idx)] += 1

    distribution: list[dict[str, Any]] = []
    for i, count in enumerate(counts):
        distribution.append({
            "index": i,
            "count": count,
            "percent": round(count / total * 100) if total > 0 else 0,
        })

    if no_answer_count > 0:
        distribution.append({
            "index": None,
            "count": no_answer_count,
            "percent": round(no_answer_count / total * 100) if total > 0 else 0,
            "no_answer": True,
        })

    return distribution
