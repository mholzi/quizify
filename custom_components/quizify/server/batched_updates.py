"""Coalesced broadcasts: reactions, roster, answer progress, head-to-head.

Moved out of ``QuizifyWebSocketHandler`` (issue #1124, part 5): module
functions that take the handler as their first argument. The handler keeps
every old name as a one-line delegate, because the #746 task registry
(``_CONNECTION_SCOPED_TASKS``) names the cancellers as handler methods and the
tests call them on the handler. The buffers, dirty flags and flush tasks stay
attributes of the handler; only the code moved.
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

from custom_components.quizify.game.state import (
    GamePhase,
)
from custom_components.quizify.server.serializers import (
    serialize_answer_progress,
    serialize_leaderboard,
    serialize_player_list,
)

if TYPE_CHECKING:  # pragma: no cover - typing only


    from custom_components.quizify.game.state import QuizifyGameState
    from custom_components.quizify.server.websocket import QuizifyWebSocketHandler

# Same logger name as before the move (#1124), so a host's ``logger:`` config
# for ``custom_components.quizify.server.websocket`` still covers these lines.
_LOGGER = logging.getLogger("custom_components.quizify.server.websocket")


def enqueue_reaction(
    handler: QuizifyWebSocketHandler,
    player_name: str,
    emoji: str,
) -> None:
    """Buffer a visual reaction and ensure a flush task is pending (#304).

    Reactions are keyed on ``(player_name, emoji)`` so a player mashing the
    same emoji within a flush window collapses to a single broadcast; a
    ``dict`` preserves insertion order so distinct reactions flush in the
    order they first arrived. The flush task is (re)started lazily and runs
    exactly one window before draining.
    """
    handler._reaction_buffer[(player_name, emoji)] = None
    handler._ensure_reaction_flush()


def ensure_reaction_flush(handler: QuizifyWebSocketHandler) -> None:
    """(Re)arm the coalescing flush task if one isn't already pending.

    Shared by the visual-reaction buffer (#304) and the reveal
    reaction-bonus buffer (#416) so either kind of buffered event guarantees
    a flush within one window.
    """
    if handler._reaction_flush_task is None or handler._reaction_flush_task.done():
        handler._reaction_flush_task = handler._runtime.create_task(
            handler._flush_reactions_after_window()
        )


async def flush_reactions_after_window(handler: QuizifyWebSocketHandler) -> None:
    """Wait one coalescing window, then broadcast the buffered reactions.

    ONE ``reactions`` frame carrying every distinct ``(player, emoji)``
    buffered this window (#896). The window used to dedupe the buffer and
    then emit a separate ``reaction`` broadcast per surviving pair, which
    with the 15 msg/s limiter is ~107 ``json.dumps`` and ~1,070
    ``send_str`` per second while eight phones tap through a reveal, where
    one frame per window is ~7/s. ``reaction_bonus``, computed a few lines
    below in this same function, has batched since #416 — the two halves
    of one flush now agree on the wire shape.

    The clients keep their old single-``reaction`` branch for one release
    (see ``player-core.js``, ``admin.js``, ``dashboard.js``) so a phone
    holding a cached bundle from before this change is not left staring at
    a reveal with no reactions on it.
    """
    try:
        await asyncio.sleep(handler._REACTION_FLUSH_WINDOW)
    except asyncio.CancelledError:
        # Cancelled on cleanup — drop whatever was buffered (best-effort
        # eye-candy, nothing to persist) and re-raise so the task ends.
        handler._reaction_buffer.clear()
        handler._reaction_bonus_from.clear()
        handler._reaction_bonus_to.clear()
        raise
    buffered = list(handler._reaction_buffer)
    handler._reaction_buffer.clear()
    # Collect every broadcast for this window, then fan them all out in a
    # single gather (#416) instead of awaiting them one at a time.
    broadcasts = []
    if buffered:
        broadcasts.append(handler._conn.broadcast({
            "type": "reactions",
            "reactions": [
                {"emoji": emoji, "player_name": player_name}
                for player_name, emoji in buffered
            ],
        }))

    # #416: collapse the reveal reaction-bonus events buffered this window
    # into ONE ``reaction_bonus``. The leaderboard is serialized once, now —
    # after every point award in the window has settled — so it reflects the
    # whole batch. ``from_player`` keeps the existing single-name wire field
    # (first reactor) for the client toast; ``from_players`` carries the full
    # set for completeness.
    if handler._reaction_bonus_to:
        from_players = list(handler._reaction_bonus_from)
        to_players = list(handler._reaction_bonus_to)
        handler._reaction_bonus_from.clear()
        handler._reaction_bonus_to.clear()
        gs = handler._get_game_state()
        if gs is not None and from_players:
            broadcasts.append(handler._conn.broadcast({
                "type": "reaction_bonus",
                "from_player": from_players[0],
                "from_players": from_players,
                "to_players": to_players,
                "leaderboard": serialize_leaderboard(gs.get_ranked_participants()),
            }))

    if broadcasts:
        await asyncio.gather(*broadcasts)

    # Reactions/bonuses that arrived DURING the broadcasts above were
    # appended to the buffers, but _ensure_reaction_flush won't start a new
    # flush while this task is still running (it isn't None/done yet) — so
    # without this the tail would sit unbroadcast until some future event
    # happened to arrive (#354). Re-arm the flush ourselves so the buffers
    # always drain: one more window, then another pass. Loops until empty.
    if handler._reaction_buffer or handler._reaction_bonus_to:
        handler._reaction_flush_task = handler._runtime.create_task(
            handler._flush_reactions_after_window()
        )


def cancel_reaction_flush(handler: QuizifyWebSocketHandler) -> None:
    """Cancel any pending reaction-flush task (called on cleanup)."""
    if handler._reaction_flush_task is not None:
        handler._reaction_flush_task.cancel()
        handler._reaction_flush_task = None
    handler._reaction_buffer.clear()
    handler._reaction_bonus_from.clear()
    handler._reaction_bonus_to.clear()


def mark_roster_dirty(handler: QuizifyWebSocketHandler, event_type: str) -> None:
    """Flag the roster as changed and (re)arm the coalescing flush (#453).

    ``event_type`` is ``"player_joined"`` or ``"player_left"`` and only
    determines the wire ``type`` of the coalesced message (the join/leave
    animation the client plays); the ``players`` list itself is always the
    live roster serialized at flush time. Mixed joins+leaves in one window
    collapse to a single frame typed by the LAST event.
    """
    handler._roster_dirty = True
    handler._roster_last_type = event_type
    handler._ensure_roster_flush()


def ensure_roster_flush(handler: QuizifyWebSocketHandler) -> None:
    """(Re)arm the roster-coalescing flush task if none is pending."""
    if handler._roster_flush_task is None or handler._roster_flush_task.done():
        # Stored on self (no GC risk), matching the timer-tick / lightning /
        # admin-pause fire-and-forget pattern in this handler.
        handler._roster_flush_task = asyncio.ensure_future(
            handler._flush_roster_after_window()
        )
        handler._roster_flush_task.add_done_callback(handler._log_task_exception)


async def flush_roster_after_window(handler: QuizifyWebSocketHandler) -> None:
    """Wait one window, then broadcast ONE roster frame (#453).

    Mirrors ``_flush_reactions_after_window``: coalesces a burst of roster
    changes into a single ``player_joined`` / ``player_left`` carrying the
    current player list. Re-arms itself if more changes landed during the
    broadcast so the final roster always reaches every client.
    """
    try:
        await asyncio.sleep(handler._REACTION_FLUSH_WINDOW)
    except asyncio.CancelledError:
        handler._roster_dirty = False
        raise

    if not handler._roster_dirty:
        return

    event_type = handler._roster_last_type
    handler._roster_dirty = False
    gs = handler._get_game_state()
    if gs is not None:
        await handler._conn.broadcast({
            "type": event_type,
            "players": serialize_player_list(gs.get_players()),
            # A player leaving also leaves their team, and the last one out
            # dissolves it (#365) — so the roster frame carries the teams
            # too, or the lobby keeps showing a team nobody is in.
            "teams": gs.team_registry.to_list(),
        })
        await handler._send_head_to_head(gs)

    # A roster change that landed DURING the broadcast set _roster_dirty
    # again; _ensure_roster_flush won't start a new task while this one is
    # running, so re-arm here to drain the tail (mirrors #354).
    if handler._roster_dirty:
        handler._roster_flush_task = asyncio.ensure_future(
            handler._flush_roster_after_window()
        )
        handler._roster_flush_task.add_done_callback(handler._log_task_exception)


def mark_progress_dirty(handler: QuizifyWebSocketHandler) -> None:
    """Flag answer-progress as changed and (re)arm its coalescing flush."""
    handler._progress_dirty = True
    if handler._progress_flush_task is None or handler._progress_flush_task.done():
        handler._progress_flush_task = asyncio.ensure_future(
            handler._flush_progress_after_window()
        )
        handler._progress_flush_task.add_done_callback(handler._log_task_exception)


async def flush_progress_after_window(handler: QuizifyWebSocketHandler) -> None:
    """Wait one window, then broadcast ONE answer-progress frame (#619).

    Mirrors ``_flush_roster_after_window`` down to the tail re-arm: taps
    that land during the broadcast set the flag again, and no new task can
    start while this one runs.

    The list is serialized at flush time, not at tap time, so the frame
    always carries the room as it is when it goes out rather than as it was
    when the first tap of the window arrived.
    """
    try:
        await asyncio.sleep(handler._REACTION_FLUSH_WINDOW)
    except asyncio.CancelledError:
        handler._progress_dirty = False
        raise

    if not handler._progress_dirty:
        return

    handler._progress_dirty = False
    gs = handler._get_game_state()
    if gs is not None:
        # #835: the rows the room can see, not the head count. In team
        # mode the counter has to count teams out of teams — no member is
        # ever marked submitted, so counting people left the television on
        # 0/N for the whole round.
        await handler._conn.broadcast(
            serialize_answer_progress(
                gs.get_players(), gs.get_ranked_participants()
            )
        )

    if handler._progress_dirty:
        handler._progress_flush_task = asyncio.ensure_future(
            handler._flush_progress_after_window()
        )
        handler._progress_flush_task.add_done_callback(handler._log_task_exception)


def cancel_progress_flush(handler: QuizifyWebSocketHandler) -> None:
    """Cancel any pending answer-progress flush (called on cleanup)."""
    if handler._progress_flush_task is not None:
        handler._progress_flush_task.cancel()
        handler._progress_flush_task = None
    handler._progress_dirty = False


async def send_head_to_head(
    handler: QuizifyWebSocketHandler,
    game_state: QuizifyGameState,
) -> None:
    """Show the TV the duel between the two present regulars (#613).

    Lobby only: a rivalry line belongs before the game, and mid-game it
    would compete with the question for the same screen.

    To the TV and admin, never to the phones — this is a deliberate
    reversal of #371, which sends each player only their OWN standing.
    Putting two people's record in front of the room is a different call,
    made knowingly, and the phones stay out of it.
    """
    if game_state.phase != GamePhase.LOBBY:
        return
    await handler._broadcast_head_to_head(game_state, at="lobby")


async def dispatch_end_head_to_head(handler: QuizifyWebSocketHandler) -> None:
    """The duel again on the end screen, once the game is recorded (#613).

    Rides ``analytics_recorded`` rather than the finale, and that is the
    whole point: the game everyone just watched is part of the record only
    after it is written. Sent with the finale, this line would state the
    standing from BEFORE that game — the one number the room can see is
    wrong, because they just played it.

    It is also the better place for the line. In the lobby the duel shows
    a score from past evenings that surprises nobody; here it is the
    result of the evening in progress.
    """
    game_state = handler._get_game_state()
    if game_state is None or game_state.phase != GamePhase.FINALE:
        return
    await handler._broadcast_head_to_head(game_state, at="finale")


async def broadcast_head_to_head(
    handler: QuizifyWebSocketHandler,
    game_state: QuizifyGameState,
    *,
    at: str,
) -> None:
    """Compute the duel and put it on the TV. Shared by both placements.

    ``at`` tells the dashboard which line to fill; the payload is
    otherwise identical, so the two placements cannot drift apart in what
    they claim. Never a plain broadcast — that would reach every phone and
    undo #371 everywhere.
    """
    analytics = game_state.stats_service
    if analytics is None:
        return
    # #931: entrants, not people. ``get_ranked_participants`` is the list
    # the podium, the leaderboard and the recorded game (#923) are all
    # built from, and in solo mode it is exactly ``get_players()``. Asking
    # the people instead put "Anna 13 - 6 Cleo" under a podium that had
    # just declared team Sofa the winner: Anna never entered that game,
    # she is one of two members of something that did, and the pair
    # selection silently picked one of the two. The rule is therefore the
    # ranking's own: the duel compares whoever the game is between.
    duel = analytics.get_head_to_head(
        [p.name for p in game_state.get_ranked_participants()]
    )
    # #931: a frame either way, never silence. Fewer than two entrants, or
    # no pair of them has met twice -> the line has to LEAVE the screen
    # rather than stand from before. The lobby recomputes this on every
    # roster change and on every team change, and two entrants who were on
    # screen a moment ago are one team now. Both renderers already read a
    # frame without ``left``/``right`` as "hide", so this needs no new
    # element and no new text.
    await handler._conn.broadcast_to_admins_and_dashboards(
        {"type": "head_to_head", "at": at, **(duel or {})}
    )


def cancel_roster_flush(handler: QuizifyWebSocketHandler) -> None:
    """Cancel any pending roster-flush task (called on cleanup)."""
    if handler._roster_flush_task is not None:
        handler._roster_flush_task.cancel()
        handler._roster_flush_task = None
    handler._roster_dirty = False
