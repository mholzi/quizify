"""Shareable result cards for Quizify.

Generates emoji grids and share text for end-of-game sharing.
"""

from __future__ import annotations

from typing import Any


def build_emoji_grid(
    player_name: str,
    player_score: int,
    round_results: list[str],
    category: str,
    total_rounds: int,
) -> str:
    """Build emoji grid for text sharing.

    Args:
        player_name: Player's display name
        player_score: Final score
        round_results: List of 'correct', 'wrong', or 'timeout' per round
        category: Category name
        total_rounds: Total rounds played

    Returns:
        Formatted text string ready for clipboard/sharing
    """
    emoji_map = {
        "correct": "\u2705",
        "wrong": "\u274c",
        "timeout": "\u23f1\ufe0f",
    }

    emoji_row = "".join(emoji_map.get(r, "\u2b1c") for r in round_results)
    correct_count = sum(1 for r in round_results if r == "correct")

    lines = [
        f"\U0001f9e0 Quizify \u2014 {category}",
        f"\U0001f451 {player_name}: {player_score} Pts.",
        "",
        emoji_row,
        f"  {correct_count}/{total_rounds} correct",
        "",
        "quizify.fun",
    ]

    return "\n".join(lines)


def build_share_text(
    player_name: str,
    player_score: int,
    round_results: list[str],
    category: str,
    total_rounds: int,
) -> str:
    """Build share text for clipboard copy (alias for build_emoji_grid)."""
    return build_emoji_grid(player_name, player_score, round_results, category, total_rounds)


def build_share_data(game_state: Any) -> dict[str, Any]:
    """Build complete share data for all players.

    Args:
        game_state: QuizifyGameState instance

    Returns:
        Dict with share_texts (per player name), category, total_rounds
    """
    category = game_state.category or "Mixed"
    total_rounds = game_state.round

    share_texts: dict[str, str] = {}
    for player in game_state.get_players():
        # Build round results from player's answer history
        round_results = _build_round_results(player, game_state)
        share_texts[player.name] = build_emoji_grid(
            player_name=player.name,
            player_score=player.score,
            round_results=round_results,
            category=category,
            total_rounds=total_rounds,
        )

    return {
        "share_texts": share_texts,
        "category": category,
        "total_rounds": total_rounds,
    }


def _build_round_results(player: Any, game_state: Any) -> list[str]:
    """Build round results list for a player.

    Since we don't store per-round history on the player,
    we approximate from score: if player has score > 0
    for rounds played, mark as correct/wrong proportionally.
    For now, return a simple list based on what we can infer.
    """
    # Without per-round tracking, we return a placeholder
    # that will be populated by the websocket layer
    return getattr(player, "round_history", [])
