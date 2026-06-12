"""Shared state serialization helpers for views and WebSocket handler."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from custom_components.quizify.game.player import PlayerSession
    from custom_components.quizify.game.questions import Question
    from custom_components.quizify.game.state import QuizifyGameState


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
    return {
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
    }


def serialize_question_for_admin(
    question: Question,
    round_num: int,
    total_rounds: int,
    timer_duration: float,
) -> dict[str, Any]:
    """Serialize a question for admin (includes correct answer)."""
    correct_answer = ""
    for a in question.answers:
        if a.correct:
            correct_answer = a.text
            break

    return {
        "type": "question_started",
        "question_text": question.question,
        "correct_answer": correct_answer,
        "answers": [
            {"text": a.text, "correct": a.correct} for a in question.answers
        ],
        "timer_duration": timer_duration,
        "round_num": round_num,
        "total_rounds": total_rounds,
        "category": question.category,
        "difficulty": question.difficulty,
        "image_url": question.image_url,
    }


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
    result = {
        "type": "finale",
        "podium": podium_entries,
        "leaderboard": serialize_leaderboard(all_players),
        "all_players": serialize_leaderboard(all_players),
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
) -> dict[str, Any]:
    """Build round summary broadcast payload.

    ``correct_answer_index`` is in CANONICAL-shuffle space — the per-player
    reveal client uses it to highlight the right tile in its own shuffle.
    ``correct_answer_index_original`` is in question-JSON-order — the TV
    dashboard renders unshuffled answers (no per-spectator shuffle), so it
    needs the original index to colour the correct tile. Both are sent in
    the same payload to avoid a dashboard-only round_summary fork.
    """
    # Compute answer distribution from all_answers
    answer_distribution = _compute_answer_distribution(
        all_answers or [], num_answer_options
    )

    return {
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
    }


def _compute_answer_distribution(
    all_answers: list[dict[str, Any]], num_options: int
) -> list[dict[str, Any]]:
    """Compute per-option vote counts and percentages.

    Returns a list of dicts: [{"index": 0, "count": 3, "percent": 60}, ...]
    Includes a separate entry for no_answer (timeout) players.
    """
    counts = [0] * num_options
    no_answer_count = 0
    total = len(all_answers)

    for entry in all_answers:
        idx = entry.get("answer_index")
        if entry.get("no_answer") or idx is None:
            no_answer_count += 1
        elif isinstance(idx, int) and 0 <= idx < num_options:
            counts[idx] += 1

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
