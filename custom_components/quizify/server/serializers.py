"""Shared state serialization helpers for views and WebSocket handler."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

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
        "can_join": game_state.phase.value
        in ("LOBBY", "QUESTION_ACTIVE", "ANSWER_REVEAL"),
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


def serialize_finale(
    podium: list[PlayerSession],
    all_players: list[PlayerSession],
    superlatives: list[dict[str, str]] | None = None,
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
    result = {
        "type": "finale",
        "podium": podium_entries,
        "leaderboard": lb,
        "all_players": lb,
    }
    if superlatives:
        result["superlatives"] = superlatives
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
    attach to the tiles the dashboard actually drew.
    """
    # Compute answer distribution from all_answers. `answer_index` on those
    # entries is question-JSON order; the bars hang off the canonical grid,
    # so the mapping happens here rather than in the client.
    answer_distribution = _compute_answer_distribution(
        all_answers or [], num_answer_options, display_order
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
    return summary


def _compute_answer_distribution(
    all_answers: list[dict[str, Any]],
    num_options: int,
    display_order: list[int] | None = None,
) -> list[dict[str, Any]]:
    """Compute per-option vote counts and percentages.

    Returns a list of dicts: [{"index": 0, "count": 3, "percent": 60}, ...]
    Includes a separate entry for no_answer (timeout) players.

    ``answer_index`` on the incoming entries is question-JSON order.
    ``display_order`` (the round's canonical shuffle) maps each vote onto the
    tile the dashboard actually rendered (#521); without it the bars would
    count the right votes against the wrong answers.
    """
    counts = [0] * num_options
    no_answer_count = 0
    total = len(all_answers)

    # original index -> position in the rendered grid
    to_display: dict[int, int] = {}
    if (
        display_order
        and len(display_order) == num_options
        and sorted(display_order) == list(range(num_options))
    ):
        to_display = {orig: pos for pos, orig in enumerate(display_order)}

    for entry in all_answers:
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
