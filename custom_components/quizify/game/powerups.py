"""Power-up system for Quizify."""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from enum import Enum

FREEZE_DURATION = 5.0  # seconds
TIME_BOOST_DURATION = 5.0  # seconds


class PowerUpType(str, Enum):
    """Available quiz power-up types."""

    JOKER = "joker"  # removes one wrong answer
    DOUBLE_POINTS = "double_points"  # this round counts double
    FREEZE = "freeze"  # opponent timer pauses 5s
    TIME_BOOST = "time_boost"  # own timer +5s


@dataclass
class PowerUpEffect:
    """Result of using a power-up."""

    type: PowerUpType
    source_player: str
    target_player: str | None = None
    joker_remove_index: int | None = None


class PowerUpManager:
    """Manages power-up inventory and usage for all players."""

    def __init__(self) -> None:
        """Initialize empty power-up state."""
        self._inventory: dict[str, PowerUpType] = {}  # player_id → held power-up
        self._double_points_active: dict[str, bool] = {}  # player_id → active this round

    def reset(self) -> None:
        """Clear all power-up state."""
        self._inventory.clear()
        self._double_points_active.clear()

    def reset_round(self) -> None:
        """Clear per-round power-up state (double points flags)."""
        self._double_points_active.clear()

    def assign_random_powerup(self, player_id: str) -> PowerUpType:
        """Assign a random power-up to a player, replacing any held one."""
        powerup = random.choice(list(PowerUpType))
        self._inventory[player_id] = powerup
        return powerup

    def has_powerup(self, player_id: str) -> bool:
        """Check if a player currently holds a power-up."""
        return player_id in self._inventory

    def get_powerup(self, player_id: str) -> PowerUpType | None:
        """Get the power-up a player holds, or None."""
        return self._inventory.get(player_id)

    def is_double_points_active(self, player_id: str) -> bool:
        """Check if double points is active for a player this round."""
        return self._double_points_active.get(player_id, False)

    def use_powerup(
        self,
        player_id: str,
        target_id: str | None,
        wrong_answer_indices: list[int] | None = None,
    ) -> PowerUpEffect | None:
        """Use the player's held power-up.

        Args:
            player_id: The player using the power-up.
            target_id: Target player (required for freeze).
            wrong_answer_indices: Indices of wrong answers (for joker).

        Returns:
            PowerUpEffect describing what happened, or None if no power-up held.
        """
        powerup = self._inventory.pop(player_id, None)
        if powerup is None:
            return None

        effect = PowerUpEffect(type=powerup, source_player=player_id)

        if powerup == PowerUpType.JOKER:
            if wrong_answer_indices:
                effect.joker_remove_index = random.choice(wrong_answer_indices)

        elif powerup == PowerUpType.DOUBLE_POINTS:
            self._double_points_active[player_id] = True

        elif powerup == PowerUpType.FREEZE:
            effect.target_player = target_id

        elif powerup == PowerUpType.TIME_BOOST:
            pass  # Timer extension handled by caller

        return effect
