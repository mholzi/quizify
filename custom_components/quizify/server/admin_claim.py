"""Admin-crown decision for a player join (#208, #358, #389, #995, #1124).

Moved out of ``QuizifyWebSocketHandler._resolve_admin_claim`` (issue #1124)
and split into one helper per case, unchanged. The handler keeps
``_resolve_admin_claim`` as a one-line delegate, because ``_handle_join`` and
the tests reach it by that name.

Only the player-join path lives here. The admin-dashboard socket
(``?role=admin&token=...``) is authorized in ``admin_auth.py``
(``grant_admin``), and so are ``is_authorized_admin`` and
``is_reset_authorized``.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover - typing only
    from custom_components.quizify.game.player import PlayerSession
    from custom_components.quizify.game.state import QuizifyGameState
    from custom_components.quizify.server.websocket import QuizifyWebSocketHandler

# Same logger name as before the move (#1124): the crown warnings keep landing
# under ``custom_components.quizify.server.websocket``, where a host's
# ``logger:`` config and the #995 tests look for them.
_LOGGER = logging.getLogger("custom_components.quizify.server.websocket")


def resolve_admin_claim(
    handler: QuizifyWebSocketHandler,
    game_state: QuizifyGameState,
    player_obj: PlayerSession,
    name: str,
    data: dict,
) -> None:
    """Decide whether *player_obj* holds the admin crown after a join.

    The cases are early returns, not a nest, so the next security fix
    lands beside the case it guards rather than one level deeper (#985):

    * no claim, but the slot carries an inherited crown — strip it
      unless the join proves ownership (#389);
    * a *connected* admin under another name — refuse the claim (#208);
    * first claim (no admin at all) or a same-name reclaim — granted,
      token-free;
    * a *stale* admin under a DIFFERENT name — transfer the crown only
      against a valid admin token (#358).

    Admin-as-player: trust `is_admin: true` in the join message.

    This is the Beatify pattern (which has shipped without bugs
    for years). Earlier Quizify releases tried to cryptographically
    validate an admin token threaded through the player's join
    message; that pattern created a brittle state machine where
    browser sessionStorage and server token storage could drift
    apart with no in-product recovery (8 betas worth of bugs).

    Trust trade-off: a malicious client on the LAN could send
    `is_admin: true` and become admin. Mitigations:
      - The user's home LAN is generally trusted.
      - Nabu Casa already requires HA auth to reach the
        integration through its tunnel.
      - "First admin claims it" still applies; only one admin
        slot exists per game.
    The persisted admin token is still validated for the pure
    admin-dashboard WebSocket connect (`?role=admin&token=...`),
    which is the higher-stakes path. Player joins are simpler.
    See DESIGN.md for the full rationale.
    """
    if not data.get("is_admin"):
        _vet_inherited_crown(handler, player_obj, name, data)
        return

    if _other_admin_holds_crown(game_state, name):
        return

    stale_admin = game_state.get_admin()
    if stale_admin is None or _is_same_slot(stale_admin, name):
        # First claim (no admin at all) or a same-name reclaim: both stay
        # token-free (the documented Beatify trust trade-off).
        player_obj.is_admin = True
        return

    _transfer_crown_from_stale_admin(handler, stale_admin, player_obj, name, data)


def _has_valid_admin_token(handler: QuizifyWebSocketHandler, data: dict) -> bool:
    """True when the join carries a valid admin session token (#358, #389)."""
    claim_token = data.get("admin_token")
    return bool(claim_token and handler._conn.validate_admin_token(claim_token))


def _vet_inherited_crown(
    handler: QuizifyWebSocketHandler,
    player_obj: PlayerSession,
    name: str,
    data: dict,
) -> None:
    """No ``is_admin`` claim: strip a crown the slot inherited (#389)."""
    if not player_obj.is_admin:
        # No claim, no crown on the slot: nothing to decide.
        return

    # #389 (P2 security): silent crown inheritance via a LOBBY
    # name-rejoin. In LOBBY a disconnected player's slot can be
    # reclaimed by simply re-typing the same name, with NO session
    # token (PlayerRegistry.add_player, the ``phase_value ==
    # "LOBBY"`` reconnect branch). If that slot was the host's, the
    # reclaimer INHERITS is_admin — and because this join carries no
    # ``is_admin: true`` claim, the #358 crown-gating below never runs
    # to vet it. So an attacker who just types the host's exact name
    # in the plain player-join form, while the host is briefly
    # disconnected in the lobby, silently seizes the crown with no
    # token.
    #
    # (Reaching this branch means the join did NOT claim admin, yet
    # the resulting slot holds is_admin — a freshly added player
    # starts non-admin, so the crown can only have been inherited
    # from the reclaimed slot.)
    #
    # Strip the inherited crown unless the joiner proves ownership
    # with a valid admin session token — same proof and FAIL-SOFT
    # posture as #358 (never reject the join). The legit host's
    # admin-as-player tab always re-sends ``is_admin: true`` on join
    # (see player-core.js), so it takes the claim path below and is
    # unaffected here; a token holder still keeps the crown even on
    # this path.
    if _has_valid_admin_token(handler, data):
        return
    _LOGGER.warning(
        "Inherited admin crown stripped for %s: LOBBY "
        "name-rejoin without a valid admin token (#389)",
        name,
    )
    player_obj.is_admin = False


def _other_admin_holds_crown(game_state: QuizifyGameState, name: str) -> bool:
    """True (and logged) when a connected admin under another name exists (#208)."""
    # Single-admin invariant (#208): exactly one player may hold the
    # crown per game. Only grant admin if no *other* player is already
    # admin. A re-claim by the same name (e.g. the admin's redirect from
    # /quizify/admin to /quizify/player re-joining under the same name) is
    # idempotent and still granted; a claim by a *different* player while
    # an admin exists is rejected — the original admin keeps the crown.
    # Rejecting rather than taking over is the safer behaviour: it stops
    # any LAN client from seizing control mid-game by sending
    # `is_admin: true`.
    if not game_state.has_other_admin(name):
        return False
    _LOGGER.warning(
        "Admin claim rejected for %s: a different player "
        "already holds the single admin slot",
        name,
    )
    return True


def _is_same_slot(stale_admin: PlayerSession, name: str) -> bool:
    """True when *name* reclaims the admin's own slot (#995)."""
    # Crown-recovery (#207 regression of #209): if a *stale*
    # (disconnected) admin slot still lingers under a different
    # name — the host's old /admin slot during the
    # /admin -> /player redirect — demote it before crowning the
    # re-joining host. has_other_admin() no longer blocks on a
    # disconnected admin, so without this demotion two players
    # would briefly carry is_admin and break the #208 invariant.
    #
    # ``.lower()`` (#995), the fold ``get_player`` used to find
    # ``player_obj``: the host who types ``HOST`` reclaims the stored
    # ``Host`` slot, so the slot in front of us and ``stale_admin``
    # are the same object. Compared case-sensitively it read as a
    # crown *transfer* between two people and fell through to the
    # #358 token gate, which then denied the host their own crown
    # ("Crown transfer denied for HOST ... stale admin Host keeps the
    # crown" — about one slot). ``.lower()`` rather than
    # ``.casefold()`` on purpose: this must match what the lookup
    # did, and casefold matches names get_player does not (``Straße``
    # / ``STRASSE``), which would be the same split in the other
    # direction.
    return stale_admin.name.lower() == name.lower()


def _transfer_crown_from_stale_admin(
    handler: QuizifyWebSocketHandler,
    stale_admin: PlayerSession,
    player_obj: PlayerSession,
    name: str,
    data: dict,
) -> None:
    """Move the crown off a stale admin, only against a valid token (#358)."""
    # #358: transferring the crown from a stale admin to a
    # DIFFERENT name is the takeover vector. During the host's
    # reload / wifi blip the real admin is momentarily
    # disconnected (so has_other_admin() is False), and any LAN
    # client sending `is_admin: true` under a new name would
    # otherwise demote the host and seize control mid-game.
    # Require proof: a valid admin session token in the join.
    # Fail SOFT — no error, just no crown — so we don't
    # reintroduce the brittle token state machine DESIGN.md
    # warns about; the legit host still recovers via the
    # token-based reconnect path, which preserves is_admin on
    # their own slot.
    if not _has_valid_admin_token(handler, data):
        _LOGGER.warning(
            "Crown transfer denied for %s: no valid admin "
            "token; stale admin %s keeps the crown",
            name,
            stale_admin.name,
        )
        return

    stale_admin.is_admin = False
    player_obj.is_admin = True
    _LOGGER.info(
        "Crown transferred from stale admin %s to %s",
        stale_admin.name,
        name,
    )
