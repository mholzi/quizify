"""Join, reconnect and team WebSocket handlers.

Moved out of ``QuizifyWebSocketHandler`` (issue #1124, part 5) the same way
the game commands moved to ``game_commands.py``: module functions that take
the handler as their first argument. The handler keeps ``_handle_join`` /
``_handle_reconnect`` / ``_handle_create_team`` / ``_handle_join_team`` /
``_handle_leave_team`` as one-line delegates, because the ``_DISPATCH`` table
and the tests reach them by those names.

Pure move. The session-token checks and the crown decision did not change:
``handle_join`` still hands the admin claim to ``handler._resolve_admin_claim``
(``admin_claim.py``), and the admin gate stays in ``_handle_message``.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from custom_components.quizify.const import (
    ERR_ALREADY_JOINED,
    ERR_INVALID_ACTION,
    ERR_NAME_INVALID,
    ERR_NOT_IN_GAME,
    ERR_TEAM_CLOSED,
)
from custom_components.quizify.game.player_registry import sanitize_player_name
from custom_components.quizify.game.state import (
    GamePhase,
)

if TYPE_CHECKING:  # pragma: no cover - typing only

    from aiohttp import web

    from custom_components.quizify.game.state import QuizifyGameState
    from custom_components.quizify.server.websocket import QuizifyWebSocketHandler

# Same logger name as before the move (#1124), so a host's ``logger:`` config
# for ``custom_components.quizify.server.websocket`` still covers these lines.
_LOGGER = logging.getLogger("custom_components.quizify.server.websocket")


async def handle_join(
    handler: QuizifyWebSocketHandler,
    ws: web.WebSocketResponse,
    data: dict,
    game_state: QuizifyGameState,
) -> None:
    """Handle player join."""
    # A reloading phone races its own old socket here (#448/#646): take
    # down any slot whose transport is already dead before deciding
    # whether this name is taken. ``reaped`` is what turns a collision
    # with the caller's own ghost into a reclaim rather than a refusal.
    reaped = handler._reap_closed_connections(game_state)
    connection_id = handler._conn.connection_id(ws)

    # Canonicalize ONCE, here, before anything is keyed on the name (#603).
    # The registry sanitizes on store; using the raw name afterwards meant
    # the session token was issued under a name the registry did not have
    # and every later `get_player(name)` missed. That silently cost the
    # host the crown and, after a wifi blip, made the player's score
    # unreachable — for any name holding a zero-width joiner, i.e. most
    # multi-codepoint emoji, and for a stray double space.
    name = sanitize_player_name(data.get("name", ""))

    if not name:
        await handler._conn.send_error(ws, ERR_NAME_INVALID, "Name is required")
        return

    # Defense in depth against a second self-join (#244, cousin of #207).
    #
    # The admin "Join as Player" flow registers the host as a player on
    # the SAME admin WebSocket. If the host taps "Join as Player" again,
    # the client opens the modal afresh and (with a different typed name)
    # would otherwise create a duplicate/ghost player from the one admin
    # session. The client now hides the button after the first join, but
    # a crafted message must not slip past either: if THIS connection
    # already holds a connected player under a different name, reject the
    # duplicate join. A re-join under the SAME name is idempotent and
    # handled by the reconnect path in PlayerRegistry.add_player, so it is
    # explicitly allowed here (no-op rejoin / lobby refresh).
    existing = handler._player_for_ws(game_state, ws)
    if (
        existing is not None
        and existing.connected
        and existing.name.lower() != name.lower()
    ):
        _LOGGER.warning(
            "Duplicate self-join rejected: connection already holds "
            "player %s, refusing second join as %s",
            existing.name,
            name,
        )
        await handler._conn.send_error(
            ws,
            ERR_ALREADY_JOINED,
            "Already joined as a player",
        )
        return

    # Auto-append number if name is taken
    #
    # #448: gate on ``is_active``, not on the name alone. After a reload
    # the old slot can linger with ``connected = True`` and a CLOSED
    # socket — the "stale connected flag" case ``_reap_closed_connections``
    # has just taken down above (it read ``ws.closed`` here before #882).
    # Renaming to "Name 2" at this point (before add_player ever sees the
    # original name) made the reclaim branch unreachable, spawning a
    # duplicate ghost with score 0. Falling through on a reaped slot lets
    # add_player reclaim the original name; genuinely live duplicates
    # (socket still open) still get the "Name 2" suffix.
    #
    # ``existing.connection_id != connection_id`` (#603): once the name is
    # canonicalized, an idempotent rejoin from the SAME connection — a
    # lobby refresh, the admin's redirect from /quizify/admin to
    # /quizify/player — matches an active slot that IS this connection.
    # Renaming it to "Name 2" would spawn a score-0 duplicate of the
    # player who is already sitting there.
    # Before canonicalization this never surfaced, because the raw name
    # differed from the stored one and the duplicate-self-join guard above
    # rejected the rejoin outright instead. Both behaviours were wrong; a
    # rejoin under the same name from the same socket is a no-op reclaim.
    original_name = name
    counter = 2
    while (
        (existing := game_state.get_player(name))
        and existing.is_active
        and existing.connection_id != connection_id
    ):
        name = f"{original_name} {counter}"
        counter += 1

    success, error_code = game_state.add_player(
        name, connection_id, reclaim_by_name=name in reaped
    )

    if success:
        # #1004: a reclaim matches the slot case-insensitively, so the
        # typed name ("HOSTIE") can differ from the slot's own ("Hostie").
        # From here on everything is keyed on the slot's name: the pending
        # removal, the session token, the power-up and lightning maps, and
        # above all the ``player_id`` the phone stores as its identity. The
        # phone matched that against the roster and, on a case-different
        # reclaim, listed its own slot as another player.
        player_obj = game_state.get_player(name)
        if player_obj is not None:
            name = player_obj.name

        # Cancel pending removal on reconnect
        handler._conn.cancel_pending_removal(name)

        # Generate session token for reconnect
        session_token = handler._conn.create_session_token(name)

        # Who wears the crown after this join is its own four-case
        # decision (#985); ``_resolve_admin_claim`` holds it.
        if player_obj is not None:
            handler._resolve_admin_claim(game_state, player_obj, name, data)

        # If a lightning round is mid-flight, register the late joiner so
        # they can score from the next question on (issue #42).
        if game_state.phase == GamePhase.LIGHTNING and game_state.lightning:
            game_state.lightning.add_player(name)

        # Cancel any deferred admin-disconnect pause: the admin's
        # redirect from /quizify/admin to /quizify/player took the
        # fresh-join path (no session token) instead of the
        # reconnect path. Same desired outcome — game keeps running.
        # Also clear the admin-disconnect grace armed by the closed admin
        # WS: the host is right here as a player, so nothing is "away".
        # (It no longer wipes the token on expiry — #725 — but leaving it
        # armed would keep has_pending_admin_disconnect() lying.)
        if player_obj and player_obj.is_admin:
            handler._cancel_admin_pause()
            handler._conn.cancel_admin_disconnect()
            # #842: the host is back on the wire as a player.
            await handler._announce_host_presence()

        # Send join confirmation with session token and assigned color
        powerup = game_state.get_player_powerup(name)
        await handler._conn.send(ws, {
            "type": "joined",
            "player_id": name,
            "powerup": powerup.value if powerup else None,
            "session_token": session_token,
            "color": player_obj.color if player_obj else "",
            "is_admin": player_obj.is_admin if player_obj else False,
            # All-time standing for THIS player only (#371, variant A).
            # Rides the join frame rather than the roster broadcast: it
            # is per-player data, it never changes mid-lobby, and putting
            # it in the coalesced roster frame would ship everyone's
            # history to every phone. ``None`` for a first-timer.
            "all_time": handler._all_time_standing(name),
        })

        # Send current state to the joining player. Project the
        # player-agnostic snapshot into THIS player's frame (#253): own
        # shuffle order for the answer buttons, own timer, flat reveal —
        # otherwise a mid-round joiner mis-scores their taps and sees an
        # empty reveal.
        state = handler._snapshot(game_state)
        if player_obj is not None:
            state = handler._round_messages.project_snapshot_for_player(
                game_state, snapshot=state, player=player_obj
            )
        state["type"] = "game_state"
        await handler._conn.send(ws, state)

        # Broadcast player list to everyone — coalesced (#453).
        handler._mark_roster_dirty("player_joined")

        # Narrate the join (#281). The host's own admin-as-player tab is
        # skipped inside the announcer so the room doesn't hear the host
        # announce themselves. Pre-game lobby joins narrate because the
        # admin pushes the TTS config on connect via ``configure_tts``.
        handler._player_joined(
            name, bool(player_obj.is_admin) if player_obj else False
        )
    else:
        # The client localizes off the structured ``code`` via
        # ``t('join.refused.<CODE>')``; the wire ``message`` is only the
        # English fallback for a bundle that has no key for the code.
        #
        # #729: every code ``add_player`` can return must have a fallback.
        # ``ERR_GAME_ENDED`` had none, so a guest who scanned a QR code
        # from a finished game got the bare "Failed to join" — no hint that
        # the game was over and the host had to start a new one. #812
        # moved that map to ``ERROR_FALLBACK_TEXT`` in const.py, where the
        # coverage is asserted against ``add_player`` itself rather than
        # against whichever local dict happened to come first in this file.
        if error_code:
            await handler._conn.send_error(ws, error_code)
        else:
            # A refusal with no code at all: nothing to localize, so say
            # the one thing that is certainly true.
            await handler._conn.send_error(
                ws, ERR_INVALID_ACTION, "Failed to join"
            )


async def handle_reconnect(
    handler: QuizifyWebSocketHandler,
    ws: web.WebSocketResponse,
    data: dict,
    game_state: QuizifyGameState,
) -> None:
    """Handle player reconnect with session token."""
    token = data.get("session_token", "")
    name = handler._conn.get_player_for_token(token)

    if not name:
        # Token not found — treat as unknown, client should show join form
        await handler._conn.send(ws, {"type": "reconnect_failed"})
        return

    player = game_state.get_player(name)
    if not player:
        # Player was fully removed — token stale
        handler._conn.revoke_token(token)
        await handler._conn.send(ws, {"type": "reconnect_failed"})
        return

    # Restore player connection. The id → socket map stays in the
    # connection manager (#882); the game model only learns the new
    # opaque handle.
    game_state.bind_player_connection(player, handler._conn.connection_id(ws))
    player.connected = True
    handler._conn.cancel_pending_removal(name)

    # If this is the admin returning from the intentional
    # /quizify/admin → /quizify/player redirect after Start,
    # cancel the deferred pause scheduled by _handle_disconnect.
    # Without this, the pause would fire ~4s after the redirect
    # completes — the user would see the question briefly, then
    # the paused-view, defeating the whole grace-period fix.
    # Also clear the admin-disconnect grace (#351) so the returning host
    # isn't still counted as away. Since #725 the grace no longer wipes
    # the persisted token, only reports "no admin socket attached".
    if player.is_admin:
        handler._cancel_admin_pause()
        handler._conn.cancel_admin_disconnect()
        # #842: same host, resumed rather than freshly joined.
        await handler._announce_host_presence()

    _LOGGER.info("Player session-reconnected: %s", name)

    # If we paused on admin disconnect and this is the admin coming
    # back, auto-resume the game so players don't sit on the paused
    # screen wondering. Resume only when WE caused the pause —
    # leave admin-initiated pauses alone.
    if (
        player.is_admin
        and game_state.phase == GamePhase.PAUSED
        and game_state.get_pause_reason() == "admin_disconnected"
        and game_state.resume()
    ):
        handler._start_timer_tick(game_state)
        # Broadcast the resumed state to EVERY player (#287). Without
        # this, the other players stay frozen on the "Host disconnected"
        # paused-view (player-core.js only leaves it on a game_state
        # message) while their timers tick down → scored as timeouts.
        # Per-player PROJECTED — same mechanism as the manual-resume
        # fix (#286) so answer buttons keep the right shuffle order.
        await handler._broadcast_state_projected(game_state)
        _LOGGER.info("Auto-resumed after admin reconnect")

    # Generate a fresh token and revoke old one
    new_token = handler._conn.rotate_session_token(token, name)

    # Send reconnect success with new token
    powerup = game_state.get_player_powerup(name)
    await handler._conn.send(ws, {
        "type": "reconnected",
        "player_id": name,
        "session_token": new_token,
        "powerup": powerup.value if powerup else None,
        # Same per-player standing as the join frame (#371) — a player who
        # reloads their phone in the lobby must not lose the line.
        "all_time": handler._all_time_standing(name),
    })

    # Send full game state, projected into THIS player's frame (#253):
    # the reconnect snapshot otherwise carries canonical answer order
    # (mis-scoring taps) and a nested round_summary the reveal can't read.
    state = handler._snapshot(game_state)
    state = handler._round_messages.project_snapshot_for_player(
        game_state, snapshot=state, player=player
    )
    state["type"] = "game_state"
    await handler._conn.send(ws, state)

    # Broadcast updated player list — coalesced (#453).
    handler._mark_roster_dirty("player_joined")


async def handle_create_team(
    handler: QuizifyWebSocketHandler,
    ws: web.WebSocketResponse,
    data: dict,
    game_state: QuizifyGameState,
) -> None:
    """Open a team and put the requesting player in it (lobby only)."""
    player = handler._player_for_ws(game_state, ws)
    if not player:
        await handler._conn.send_error(ws, ERR_NOT_IN_GAME, "Not in game")
        return

    team = game_state.create_team(str(data.get("name", ""))[:24], player.name)
    if team is None:
        # Teams are fixed once the game starts — a latecomer plays alone.
        await handler._conn.send_error(
            ws, ERR_TEAM_CLOSED, "Teams are set for this game"
        )
        return

    await handler._conn.send(ws, {"type": "team_joined", "team": team})
    await handler._round_out.send_teams_update(game_state)
    # #931: forming or dissolving a team changes who the entrants
    # are, so the duel underneath the roster is about different rows
    # now. Teams do not move the roster, so nothing else recomputes
    # it here and the old line would simply stay up.
    await handler._send_head_to_head(game_state)


async def handle_join_team(
    handler: QuizifyWebSocketHandler,
    ws: web.WebSocketResponse,
    data: dict,
    game_state: QuizifyGameState,
) -> None:
    """Join an existing team (lobby only)."""
    player = handler._player_for_ws(game_state, ws)
    if not player:
        await handler._conn.send_error(ws, ERR_NOT_IN_GAME, "Not in game")
        return

    team = game_state.join_team(str(data.get("team_id", "")), player.name)
    if team is None:
        # Either the game has started or the team dissolved while the
        # player was tapping it — the same answer either way: it is gone.
        await handler._conn.send_error(
            ws, ERR_TEAM_CLOSED, "That team is no longer open"
        )
        return

    await handler._conn.send(ws, {"type": "team_joined", "team": team})
    await handler._round_out.send_teams_update(game_state)
    # #931: forming or dissolving a team changes who the entrants
    # are, so the duel underneath the roster is about different rows
    # now. Teams do not move the roster, so nothing else recomputes
    # it here and the old line would simply stay up.
    await handler._send_head_to_head(game_state)


async def handle_leave_team(
    handler: QuizifyWebSocketHandler,
    ws: web.WebSocketResponse,
    data: dict,
    game_state: QuizifyGameState,
) -> None:
    """Leave the current team (lobby only). The last one out dissolves it."""
    player = handler._player_for_ws(game_state, ws)
    if not player:
        await handler._conn.send_error(ws, ERR_NOT_IN_GAME, "Not in game")
        return

    if not game_state.leave_team(player.name):
        await handler._conn.send_error(
            ws, ERR_TEAM_CLOSED, "Teams are set for this game"
        )
        return

    await handler._conn.send(ws, {"type": "team_left"})
    await handler._round_out.send_teams_update(game_state)
    # #931: forming or dissolving a team changes who the entrants
    # are, so the duel underneath the roster is about different rows
    # now. Teams do not move the roster, so nothing else recomputes
    # it here and the old line would simply stay up.
    await handler._send_head_to_head(game_state)
