"""Shared state serialization helpers for views and WebSocket handler."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from custom_components.quizify.const import DOMAIN

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

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
        "phase": "LOBBY",
        "can_join": True,
    }
