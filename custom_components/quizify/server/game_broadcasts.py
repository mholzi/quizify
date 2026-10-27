"""Host-presence, finale and analytics follow-up broadcasts.

Moved out of ``QuizifyWebSocketHandler`` (issue #1124, part 5): module
functions that take the handler as their first argument, with one-line
delegates under the old names on the handler. ``_host_connected`` did not
move: it is the condition ``admin_auth.is_reset_authorized`` turns on, so it
stays next to the other authorization helpers in ``websocket.py``.
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

from custom_components.quizify.game.highlights import compute_superlatives
from custom_components.quizify.server.serializers import (
    resolve_pack_labels,
    serialize_finale,
)

if TYPE_CHECKING:  # pragma: no cover - typing only


    from custom_components.quizify.analytics import PlayerStanding
    from custom_components.quizify.game.state import QuizifyGameState
    from custom_components.quizify.server.websocket import QuizifyWebSocketHandler

# Same logger name as before the move (#1124), so a host's ``logger:`` config
# for ``custom_components.quizify.server.websocket`` still covers these lines.
_LOGGER = logging.getLogger("custom_components.quizify.server.websocket")


async def announce_host_presence(handler: QuizifyWebSocketHandler) -> None:
    """Broadcast the host-presence flag when, and only when, it changes.

    Called wherever an admin socket is added or dropped and wherever the
    host's own player row changes connection state. Broadcast rather than
    unicast: every phone in the room needs it, and the two screens that do
    not (the host page knows, the television does not care) ignore it.
    """
    game_state = handler._get_game_state()
    if game_state is None:
        return
    connected = handler._host_connected(game_state)
    if connected == handler._host_presence_sent:
        return
    previous = handler._host_presence_sent
    handler._host_presence_sent = connected
    try:
        await handler._conn.broadcast(
            # ``host_connected``, not ``connected`` (#848). One fact needs
            # one name: the snapshot has always carried it under this key,
            # and ``_rememberHostFlag`` in player-core.js — the only reader
            # of either — reads that key and nothing else. The frame #840
            # shipped said ``connected``, so on the two occasions the
            # departure DID leave the server the phone read
            # ``msg.host_connected``, found undefined, and kept the answer
            # it already had. A live-fixed broadcast alone would still not
            # have armed a single hatch.
            {"type": "host_presence", "host_connected": connected}
        )
    except BaseException:
        # The flag is a record of what actually went on the wire, so a
        # send that never happened must not be remembered as one. #848
        # was silent on all three of its reproductions partly because of
        # this: the broadcast died in a cancelled task with the flag
        # already flipped, and every later announcement deduped against a
        # value no phone had ever been told.
        handler._host_presence_sent = previous
        raise


def announce_host_presence_soon(handler: QuizifyWebSocketHandler) -> None:
    """Announce host presence from a task the closing socket cannot kill.

    ``_handle_disconnect`` runs inside the ``finally`` of ``handle()``, and
    by the time it does, aiohttp has already cancelled that request's task:
    the next ``await`` that yields to the loop raises ``CancelledError``.
    The announcement is exactly such an await — ``broadcast`` fans out with
    ``asyncio.gather``, which needs one loop iteration before its children
    run — so the six awaited call sites #840 added did reach this one, did
    flip ``_host_presence_sent``, and then died before a single byte left
    the server.

    That is #848 exactly as the live test measured it: the guest phone
    recorded ``host_presence {connected: true}`` when the host's tab
    opened, the HA log carried ``Admin disconnected, keeping game alive for
    120s`` when it closed, and no frame was ever sent for the close. A call
    site that runs is not a call site that reaches a phone.

    So the departure gets its own task, the same shape
    ``_schedule_admin_pause`` two screens down and
    ``ConnectionManager.schedule_admin_timeout`` already use in this very
    path, and for this very reason. Scheduling is synchronous, so nothing
    here can be cancelled out from under it; the coroutine re-reads
    ``_host_connected`` when it runs, so a host who came back in the
    meantime is never announced gone.
    """
    handler._host_presence_task = asyncio.ensure_future(
        handler._announce_host_presence()
    )
    handler._host_presence_task.add_done_callback(handler._log_task_exception)


def cancel_host_presence(handler: QuizifyWebSocketHandler) -> None:
    """Drop a pending host-presence announcement (integration teardown).

    Connection-scoped, not round-scoped: whether anybody is hosting the
    room has nothing to do with which round it is on. Only
    ``cleanup_game_tasks()`` reaches this.
    """
    if handler._host_presence_task is not None:
        handler._host_presence_task.cancel()
        handler._host_presence_task = None


async def dispatch_analytics_followups(handler: QuizifyWebSocketHandler) -> None:
    """Everything true only once the game is recorded (#624, #612, #613).

    One handler because one event: the season standing per player, the
    evening tally for the room and the end-screen duel all become correct
    at the same instant, and registering three handlers for the same event
    would make their order an accident of dict insertion.
    """
    await handler._dispatch_all_time_standings()
    await handler._dispatch_evening_tally()
    await handler._dispatch_end_head_to_head()


async def dispatch_evening_tally(handler: QuizifyWebSocketHandler) -> None:
    """Broadcast tonight's running score to the TV (#612).

    Rides the same ``analytics_recorded`` event as the season standing: the
    game that just finished is only part of the tally once it is written.

    Broadcast rather than per-player — unlike the season standing, this is
    the room's number, and the TV is the screen it belongs on.
    """
    gs = handler._get_game_state()
    if gs is None:
        return
    analytics = gs.stats_service
    if analytics is None:
        return
    tally = analytics.get_evening_tally()
    if tally is None:
        # One game so far, or no sitting in progress. Saying nothing beats
        # restating the podium that is already on screen.
        return
    await handler._conn.broadcast_to_admins_and_dashboards(
        {"type": "evening_tally", **tally}
    )


async def dispatch_all_time_standings(handler: QuizifyWebSocketHandler) -> None:
    """Send each player their own season standing, after the game landed.

    Fired by ``analytics_recorded`` (#624), not with the finale: the record
    is written in a detached task so a slow disk cannot delay the end
    screen, which means the finale goes out while the all-time table still
    describes the state BEFORE this game. A standing sent then would
    contradict the podium the player is looking at.

    Per-player rather than a broadcast, because the interesting number is
    the player's own rank. Fail-soft throughout: no analytics, an unknown
    name or a closed socket each just means no line, exactly as on join.
    """
    game_state = handler._get_game_state()
    if game_state is None:
        return
    sends = []
    for player in game_state.get_players():
        if not handler._conn.is_connection_open(player.connection_id):
            continue
        # The player's OWN name, the same key the join frame used (#932).
        # #923 moved this to the entrant, so a team member's phone showed
        # the team's row worded as a personal one: "1 win from 1 game"
        # replacing the "14 wins from 27 games" the same phone read in the
        # lobby minutes earlier. The all-time table is keyed by entrant
        # name, so a member with history is found here perfectly well —
        # their record simply does not move for a game the team was
        # recorded under, which is what one ledger (#923) means. A member
        # with no history of their own gets no line, exactly as they got
        # none in the lobby; the alternative is a team's record wearing a
        # person's wording.
        standing = handler._all_time_standing(player.name)
        if standing is None:
            continue
        sends.append(
            handler._conn.send_to_player(
                player,
                {"type": "all_time_update", "all_time": standing},
            )
        )
    if sends:
        await asyncio.gather(*sends)


def all_time_standing(
    handler: QuizifyWebSocketHandler,
    name: str,
) -> PlayerStanding | None:
    """This player's own all-time placing for the lobby line (#371).

    FAIL-SOFT by construction: no game state, no wired analytics, or a
    name that never finished a game all return ``None``, and the client
    simply renders no line. A join must never fail over a decoration.
    """
    gs = handler._get_game_state()
    if gs is None:
        return None
    analytics = gs.stats_service
    if analytics is None:
        return None
    return analytics.get_player_standing(name)


async def broadcast_finale(
    handler: QuizifyWebSocketHandler,
    game_state: QuizifyGameState,
) -> None:
    """Build and broadcast the finale message (podium + superlatives).

    ``end_game`` already computed and cached the podium + superlatives, so
    reuse them here instead of recomputing ``calculate_podium`` +
    ``compute_superlatives`` from scratch (#415). Falls back to a fresh
    compute only if this is ever reached without a populated cache (e.g. a
    direct call in a test) so behaviour is unchanged in that edge case.
    """
    # In team mode the finale is about teams (#365): podium, leaderboard
    # and awards all take the ranked participants, which are teams there
    # and players otherwise. `compute_superlatives` reads the same
    # attribute names, so the awards aggregate per team without a second
    # implementation — an award simply belongs to "Team Sofa" instead of
    # to Anna.
    all_players = game_state.get_ranked_participants()

    podium = game_state.get_finale_podium()
    if podium is None:
        from custom_components.quizify.game.scoring import (
            calculate_podium,  # noqa: PLC0415
        )

        podium = calculate_podium(all_players)

    cached_superlatives = game_state.get_finale_superlatives()
    superlatives = (
        cached_superlatives
        if cached_superlatives is not None
        else compute_superlatives(all_players)
    )
    awards = [s.to_dict() for s in superlatives]
    # Pack labels for the shareable card (#369). The FINALE snapshot needs
    # the identical list (#878), so the resolution lives next to the
    # serializers both paths share.
    packs = resolve_pack_labels(game_state)
    finale_msg = serialize_finale(
        podium,
        all_players,
        superlatives=awards,
        packs=packs,
        game_id=game_state.game_id,
    )
    await handler._conn.broadcast(finale_msg)
