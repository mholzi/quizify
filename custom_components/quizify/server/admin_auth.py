"""Admin-dashboard socket authentication and admin authorization (#1124).

Moved out of ``QuizifyWebSocketHandler`` (issue #1124) unchanged:

- :func:`grant_admin`: the admin-grant rules for a ``role=admin`` socket
  (persisted token, bootstrap, fail soft) (#168, #359).
- :func:`handle_admin_connect`: the ``admin_connect`` reply that hands the
  admin page its session token (#356, #501, #895c).
- :func:`is_authorized_admin`: WS-level admin or the crowned player.
- :func:`is_reset_authorized`: the ``reset_game`` escape hatch (#207).

The handler keeps ``_grant_admin``, ``_handle_admin_connect``,
``_is_authorized_admin`` and ``_is_reset_authorized`` as one-line delegates,
because ``handle``, ``_handle_message`` and the tests reach them by those
names. The gates that *call* these checks (``_WS_ADMIN_ONLY``, the
``admin_required`` dispatch flag, the ``admin_auth`` frame, the ``reset_game``
branch) stay in ``websocket.py``, and so do the token store and its
validation in ``connection.py``. ``_host_connected`` stays on the handler too:
the host-presence snapshot and broadcast read it as well as the reset rule.

Calls back into the handler go through its method names
(``handler._is_authorized_admin``, ``handler._host_connected``), exactly as
``self.`` did before, so a test that patches one of them still sees it used.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from custom_components.quizify.server.serializers import (
    serialize_scoring_status,
    snapshot_house_entities,
    snapshot_tts_entities,
)

if TYPE_CHECKING:  # pragma: no cover - typing only
    from aiohttp import web

    from custom_components.quizify.game.state import QuizifyGameState
    from custom_components.quizify.server.websocket import QuizifyWebSocketHandler

# Same logger name as before the move (#1124): the admin-auth info/warning
# lines keep landing under ``custom_components.quizify.server.websocket``,
# where a host's ``logger:`` config looks for them.
_LOGGER = logging.getLogger("custom_components.quizify.server.websocket")


async def grant_admin(
    handler: QuizifyWebSocketHandler,
    role: str | None,
    admin_token: str | None,
    request: web.Request,
) -> bool:
    """Evaluate the admin-grant rules for a ``role=admin`` connection.

    Token-source-agnostic (#359): the token may arrive via the
    ``X-Quizify-Token`` header, the deprecated ``?token=`` query param, or
    a first-message ``admin_auth`` frame. FAIL SOFT — a bad/absent token
    never rejects the socket, it only withholds the admin grant, so the
    host can never lock itself out.
    """
    if role != "admin":
        return False
    # Ensure the persisted token is loaded before evaluating rules.
    # async_load_admin_token() is idempotent and cheap after first call.
    await handler._conn.async_load_admin_token()
    if admin_token and handler._conn.validate_admin_token(admin_token):
        _LOGGER.info("Admin authenticated with valid session token")
        return True
    if await handler._conn.try_bootstrap_admin():
        # Bootstrap: no token has ever been issued on this HA instance.
        # try_bootstrap_admin() grants + persists the token atomically
        # under a lock, so exactly one of two racing first-connections
        # wins (#168). The loser gets player role only.
        _LOGGER.warning(
            "ADMIN BOOTSTRAP: granting admin to first connection "
            "(ip=%s). Future restarts will require the persisted "
            "token. If this was NOT you, reset the integration.",
            request.remote,
        )
        return True
    if admin_token:
        # A token was presented but failed validation — this is the
        # interesting signal (real intrusion attempt or stale token).
        _LOGGER.warning(
            "Admin connection attempt with INVALID token rejected (ip=%s)",
            request.remote,
        )
    else:
        # No token presented and one is already on disk — the most common
        # cause is a fresh browser tab on the home LAN, not an attack.
        _LOGGER.debug(
            "Admin connection attempt without token (ip=%s)",
            request.remote,
        )
    return False


async def handle_admin_connect(
    handler: QuizifyWebSocketHandler,
    ws: web.WebSocketResponse,
    game_state: QuizifyGameState,
) -> None:
    """Send full state to admin on connect."""
    # Cancel admin disconnect task if reconnecting
    if handler._conn.has_pending_admin_disconnect():
        handler._conn.cancel_admin_disconnect()
        _LOGGER.info("Admin reconnected, cancelled disconnect timeout")

    # #895c: and the deferred host-disconnect pause, for the same reason.
    # ``_schedule_admin_pause`` is armed whenever the host's PLAYER socket
    # closes during a live question, and the two paths that cancelled it
    # again — ``_handle_join`` and the session reconnect — both look for a
    # returning *player*. A host who leaves the game to open
    # /quizify/admin comes back on an ADMIN socket instead, so nothing
    # cancelled the timer: four seconds later every phone in the room was
    # shown the "host disconnected" pause overlay while the host was
    # sitting on the admin page, watching the same game.
    handler._cancel_admin_pause()

    admin_token = handler._conn.get_or_create_admin_token()

    state = handler._snapshot(game_state)
    state["type"] = "game_state"
    state["join_url"] = "/quizify/player"
    state["admin_session_token"] = admin_token
    # Ride the TTS-engine + media-player lists for the narration dropdowns
    # (#281) on this already-authenticated admin frame, so the panel never
    # depends on the separate admin-token-gated /api/quizify/tts-entities
    # fetch racing the token's arrival (#356/#501). ``hass`` is None on the
    # standalone dev server → empty lists → the dropdowns' "configure in HA"
    # fallback, exactly like the HTTP endpoint.
    hass = getattr(handler._runtime, "hass", None)
    entities = snapshot_tts_entities(hass)
    state["tts_entities"] = entities["tts"]
    state["media_players"] = entities["media_players"]
    # Same trick for the "House Plays Along" panel's entity pickers (#494
    # Phase 4): lights + media players + scenes ride this authenticated
    # frame, so the panel never races the admin token against the parallel
    # token-gated /api/quizify/house-entities fetch. Sent as ONE nested dict
    # (not three top-level keys) to keep the frame's namespace tidy — the
    # panel reads state.house_entities.{lights,media_players,scenes}.
    state["house_entities"] = snapshot_house_entities(hass)
    await handler._conn.send(ws, state)
    # #1150: a reloaded host page has to find its "not in the scoring" rows.
    await handler._conn.send(ws, serialize_scoring_status(game_state))


def is_authorized_admin(
    handler: QuizifyWebSocketHandler,
    ws: web.WebSocketResponse,
    is_admin: bool,
    game_state: QuizifyGameState,
) -> bool:
    """Return True if the connection is authorized to perform admin actions."""
    player = handler._player_for_ws(game_state, ws)
    return is_admin or bool(player and player.is_admin)


def is_reset_authorized(
    handler: QuizifyWebSocketHandler,
    ws: web.WebSocketResponse,
    is_admin: bool,
    game_state: QuizifyGameState,
) -> bool:
    """Authorize a reset_game request.

    Stricter-than-nothing escape hatch (#207): a normal authorized admin
    may always reset; additionally, ANY connected client may reset when
    no *connected* admin currently holds the crown. The latter recovers
    the legitimate host from the orphaned-crown state left by the #209
    single-admin name race, where otherwise every admin-only action is
    silently rejected. Reset only ever returns the game to its initial
    state, so granting it in the admin-less case cannot escalate control.
    """
    if handler._is_authorized_admin(ws, is_admin, game_state):
        return True
    return not handler._host_connected(game_state)
