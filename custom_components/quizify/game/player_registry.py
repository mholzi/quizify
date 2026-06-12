"""Player registry for Quizify — manages player lifecycle and lookups."""

from __future__ import annotations

import logging
import unicodedata
from typing import TYPE_CHECKING, Any

from ..const import (
    ERR_GAME_ENDED,
    ERR_GAME_FULL,
    ERR_NAME_INVALID,
    ERR_NAME_TAKEN,
    MAX_NAME_LENGTH,
    MAX_PLAYERS,
    MIN_NAME_LENGTH,
)

if TYPE_CHECKING:
    from aiohttp import web

from .player import PLAYER_COLORS, PlayerSession

_LOGGER = logging.getLogger(__name__)


def _sanitize_name(raw: str) -> str:
    """Normalize and strip unsafe characters from a player name.

    Defends against impersonation via:
    - Unicode case-folding mismatches ("Alıce" vs "Alice")
    - Right-to-left override / zero-width characters that render
      identical but compare different (\u202e, \u200b, etc.)
    - Control characters
    - Trailing/leading whitespace

    (#15 in logical review.)
    """
    # NFKC normalizes compatibility-equivalent characters.
    n = unicodedata.normalize("NFKC", raw or "").strip()
    # Strip formatting / invisible category Cf characters (RTL overrides,
    # zero-width joiners, etc.) and all other control chars (Cc).
    n = "".join(
        ch for ch in n
        if unicodedata.category(ch) not in ("Cc", "Cf", "Cs", "Co", "Cn")
    )
    # Collapse consecutive whitespace to a single space.
    n = " ".join(n.split())
    return n


class PlayerRegistry:
    """Manages player add/remove, lookups, and sessions."""

    def __init__(self) -> None:
        """Initialize empty registry."""
        self.players: dict[str, PlayerSession] = {}
        self._sessions: dict[str, str] = {}  # session_id → player_name

    def reset(self) -> None:
        """Clear all players and sessions."""
        self.players.clear()
        self._sessions.clear()

    def add_player(
        self,
        name: str,
        ws: web.WebSocketResponse,
        phase_value: str,
        average_score_fn: callable,
    ) -> tuple[bool, str | None]:
        """Add a player to the game.

        Returns:
            (success, error_code) - error_code is None on success
        """
        # Validate + sanitize name (Unicode NFKC, strip control/format chars).
        name = _sanitize_name(name)
        if not name or len(name) < MIN_NAME_LENGTH:
            return False, ERR_NAME_INVALID
        if len(name) > MAX_NAME_LENGTH:
            return False, ERR_NAME_INVALID

        # Reject END state
        if phase_value == "END":
            return False, ERR_GAME_ENDED

        # Check for reconnection - case-insensitive match.
        #
        # Security (#7 in logical review): during gameplay phases, a
        # disconnected player's slot can ONLY be reclaimed via the
        # session-token-based `reconnect` message, NEVER by re-typing the
        # name in the join form. Otherwise an attacker on the same LAN
        # could impersonate a disconnected player and inherit their score
        # by guessing their name.
        #
        # In LOBBY (pre-game) phase, name-based reconnect is still allowed
        # because scores are all zero \u2014 impersonation is cosmetic, and the
        # UX win of "refresh the page, type the same name" is meaningful.
        for existing_name, existing_player in self.players.items():
            if existing_name.lower() == name.lower():
                if not existing_player.connected:
                    if phase_value != "LOBBY":
                        return False, ERR_NAME_TAKEN
                    existing_player.ws = ws
                    existing_player.connected = True
                    _LOGGER.info("Player reconnected by name (lobby): %s", existing_name)
                    return True, None
                # Stale connected flag — the browser reloaded but
                # _handle_disconnect hasn't fired yet, so the slot still looks
                # taken. If the old WS is genuinely dead, allow takeover so
                # the user isn't stuck staring at "Name taken" while their own
                # ghost session lingers (Beatify #646).
                if existing_player.ws is None or existing_player.ws.closed:
                    _LOGGER.info(
                        "Player %s: stale connected flag, old WS closed — allowing rejoin",
                        existing_name,
                    )
                    existing_player.ws = ws
                    existing_player.connected = True
                    return True, None
                return False, ERR_NAME_TAKEN

        # Check player limit
        if len(self.players) >= MAX_PLAYERS:
            return False, ERR_GAME_FULL

        # Determine if late joiner
        joined_late = phase_value != "LOBBY"
        initial_score = average_score_fn() if joined_late else 0

        # Assign a unique color from the palette (cycle if more than palette size)
        used_colors = {p.color for p in self.players.values()}
        available = [c for c in PLAYER_COLORS if c not in used_colors]
        color = available[0] if available else PLAYER_COLORS[len(self.players) % len(PLAYER_COLORS)]

        # Add new player
        player = PlayerSession(
            name=name, ws=ws, score=initial_score, streak=0, joined_late=joined_late, color=color
        )
        self.players[name] = player
        self._sessions[player.session_id] = name

        _LOGGER.info(
            "Player joined: %s (total: %d, late: %s)",
            name,
            len(self.players),
            joined_late,
        )
        return True, None

    def get_player(self, name: str) -> PlayerSession | None:
        """Get player by name (case-insensitive)."""
        player = self.players.get(name)
        if player is not None:
            return player
        name_lower = name.lower()
        for existing_name, existing_player in self.players.items():
            if existing_name.lower() == name_lower:
                return existing_player
        return None

    def get_player_by_session_id(self, session_id: str) -> PlayerSession | None:
        """Get player by session ID."""
        name = self._sessions.get(session_id)
        return self.players.get(name) if name else None

    def get_player_by_ws(self, ws: web.WebSocketResponse) -> PlayerSession | None:
        """Get player by WebSocket connection."""
        for player in self.players.values():
            if player.ws == ws:
                return player
        return None

    def remove_player(self, name: str) -> None:
        """Remove player from game."""
        if name in self.players:
            player = self.players[name]
            self._sessions.pop(player.session_id, None)
            del self.players[name]
            _LOGGER.info("Player removed: %s", name)

    def get_players_state(self) -> list[dict[str, Any]]:
        """Get player list for state broadcast."""
        return [
            {
                "name": p.name,
                "score": p.score,
                "connected": p.connected,
                "streak": p.streak,
                "is_admin": p.is_admin,
                "submitted": p.submitted,
                "color": p.color,
            }
            for p in self.players.values()
        ]

    def all_submitted(self) -> bool:
        """Check if all genuinely-active players have submitted their answer.

        Uses ``is_active`` (connected + WS open) rather than the raw
        ``connected`` flag so a stale ghost (closed WebSocket whose
        _handle_disconnect hasn't fired yet) can't block early reveal for
        the whole room.

        Late-joiners (who entered the game mid-round) are excluded \u2014
        otherwise a new player arriving after most answers are in would
        force the round to run the full timer duration even though all the
        actual participants are done.
        """
        participants = [
            p for p in self.players.values()
            if p.is_active and not p.joined_late
        ]
        if not participants:
            return False
        return all(p.submitted for p in participants)

    def get_average_score(self) -> int:
        """Average score for seeding late joiners.

        Uses only players who have completed at least one round so that
        joining late doesn't inherit a 0-inflated average from other
        late joiners who themselves haven't played yet.
        """
        scored_players = [p for p in self.players.values() if p.rounds_played > 0]
        if not scored_players:
            return 0
        total = sum(p.score for p in scored_players)
        return round(total / len(scored_players))

    def clear_all_sessions(self) -> None:
        """Wipe session_id \u2192 name map (for clean game reset)."""
        count = len(self._sessions)
        self._sessions.clear()
        if count:
            _LOGGER.info("Cleared %d player sessions", count)

    def set_admin(self, name: str) -> bool:
        """Set a player as admin."""
        player = self.players.get(name)
        if player:
            player.is_admin = True
            _LOGGER.info("Admin set: %s", name)
            return True
        return False

    def get_admin(self) -> PlayerSession | None:
        """Return the current admin player, if any.

        Enforces the single-admin invariant: there is at most one admin
        per game. Returns the first player flagged ``is_admin`` (insertion
        order), or ``None`` if no player currently holds the crown.
        """
        for player in self.players.values():
            if player.is_admin:
                return player
        return None

    def has_other_admin(self, name: str) -> bool:
        """Return True if a *connected* player other than ``name`` is admin.

        Used to reject a second admin-claim: the first claimant keeps the
        crown, later claims by a different player are denied. A re-claim by
        the same player (e.g. the admin's redirect from /admin to /player
        re-joining under the same name) is idempotent and not blocked.

        Crown-recovery (#207 regression of #209): a *disconnected/stale*
        admin slot must NOT block the legitimate host's re-claim. When the
        host's /admin -> /player redirect (or any reload) takes the
        fresh-join path, the still-open old admin slot momentarily holds
        the crown under a disambiguated name ("Host 2"); the strict
        name-only check then denied the host admin, and once the stale
        slot was pruned NOBODY held the crown — so every admin-only action
        (reset/pause/skip/resume, which share one auth guard) was silently
        rejected and the client swallowed the error. We therefore only let
        a *connected* admin block the claim. A live admin still keeps the
        crown (the #208 anti-takeover guarantee is preserved).
        """
        admin = self.get_admin()
        return admin is not None and admin.name != name and admin.connected
