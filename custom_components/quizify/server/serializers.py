"""Shared state serialization helpers for views and WebSocket handler."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from custom_components.quizify.const import DOMAIN

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

    from custom_components.quizify.game.player import PlayerSession
    from custom_components.quizify.game.questions import Answer, Question
    from custom_components.quizify.game.state import QuizifyGameState


def get_game_state(hass: HomeAssistant) -> QuizifyGameState | None:
    """Look up the active QuizifyGameState from hass.data."""
    return hass.data.get(DOMAIN, {}).get("game")


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
        "can_join": game_state.phase.value in ("LOBBY", "QUESTION_ACTIVE", "ANSWER_REVEAL"),
    }


def serialize_question_for_player(
    question: Question,
    shuffled_answers: list[str],
    round_num: int,
    total_rounds: int,
    timer_duration: float,
) -> dict[str, Any]:
    """Serialize a question for player broadcast (no correct flag)."""
    return {
        "type": "question_started",
        "question_text": question.question,
        "answers": shuffled_answers,
        "timer_duration": timer_duration,
        "round_num": round_num,
        "total_rounds": total_rounds,
        "category": question.category,
        "difficulty": question.difficulty,
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
    }


def serialize_leaderboard(players: list[PlayerSession]) -> list[dict[str, Any]]:
    """Build sorted leaderboard from player list."""
    sorted_players = sorted(players, key=lambda p: p.score, reverse=True)
    result = []
    for i, p in enumerate(sorted_players):
        breakdown = p.round_score_breakdown if hasattr(p, "round_score_breakdown") else {}
        # Determine if this player answered correctly this round
        last_result = p.round_history[-1] if p.round_history else None
        result.append({
            "rank": i + 1,
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
    share_texts: dict[str, str] | None = None,
    superlatives: list[dict[str, str]] | None = None,
) -> dict[str, Any]:
    """Build finale payload with podium and full leaderboard."""
    result = {
        "type": "finale",
        "podium": [
            {"rank": i + 1, "name": p.name, "score": p.score}
            for i, p in enumerate(podium)
        ],
        "leaderboard": serialize_leaderboard(all_players),
        "all_players": serialize_leaderboard(all_players),
    }
    if share_texts:
        result["share_texts"] = share_texts
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
) -> dict[str, Any]:
    """Build round summary broadcast payload."""
    # Compute answer distribution from all_answers
    answer_distribution = _compute_answer_distribution(all_answers or [], num_answer_options)

    return {
        "type": "round_summary",
        "correct_answer_index": correct_answer_index,
        "correct_answer": correct_answer_text,
        "fun_fact": fun_fact,
        "leaderboard": leaderboard,
        "round": round_num,
        "total_rounds": total_rounds,
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

    distribution = []
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
