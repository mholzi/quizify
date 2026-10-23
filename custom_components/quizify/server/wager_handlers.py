"""Wager WebSocket handler — the final round's ``submit_wager`` (#656, #1116).

Moved out of ``QuizifyWebSocketHandler`` (issue #1116, the follow-up of #1108)
the same way the Hot Seat messages moved to ``hot_seat_handlers.py``: a module
function that takes the handler as its first argument. The handler keeps
``_handle_submit_wager`` as a one-line delegate, because the ``_DISPATCH``
table and the tests reach it by that name.

What stays on the handler: the betting window's deadline task
(``_open_wager_window`` / ``_start_wager_window`` / ``_cancel_wager_window`` /
``_close_wager_window``), which belongs to the #746 teardown registry, and the
window's fan-out (``_wager_out``). The game-side rules — who may still bet,
recording and locking a bet — are
:class:`~custom_components.quizify.game.wager_controller.WagerController`'s.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from custom_components.quizify.const import ERR_INVALID_ACTION, ERR_NOT_IN_GAME
from custom_components.quizify.game.state import GamePhase

if TYPE_CHECKING:  # pragma: no cover - typing only
    from aiohttp import web

    from custom_components.quizify.game.state import QuizifyGameState
    from custom_components.quizify.server.websocket import QuizifyWebSocketHandler


async def handle_submit_wager(
    handler: QuizifyWebSocketHandler,
    ws: web.WebSocketResponse,
    data: dict,
    game_state: QuizifyGameState,
) -> None:
    """Accept a player's wager for the final round. Only valid while the
    betting window is open (WAGER_ACTIVE). The wager is a PERCENT (0-100)
    of the player's current score — server translates to absolute points
    at evaluation time so the percentage stays meaningful even after a
    late-arriving reaction bonus shifts scores.

    Until #656 this accepted wagers during QUESTION_ACTIVE, i.e. with the
    question already on screen: a player who knew the answer could stake
    everything at no risk. The phase check below is the fix — once the
    question is out, the betting is over.
    """
    player = handler._player_for_ws(game_state, ws)
    if not player:
        await handler._conn.send_error(ws, ERR_NOT_IN_GAME, "Not in game")
        return

    if game_state.round != game_state.total_rounds:
        return  # only final round accepts a wager

    # Estimate finals are scored via submit_guess/_evaluate_estimate_round,
    # which NEVER read player.wager — only the MC path's ScoringEngine
    # resolves wagers. ACKing a wager here would confirm a bet that has no
    # effect on scoring. Reject it explicitly (and the serializer withholds
    # the wager UI for estimate finals, so a compliant client never sends
    # this). (#353.)
    #
    # Checked BEFORE the phase gate on purpose: an estimate final never
    # opens a betting window (#656), so the phase check below would
    # otherwise swallow this case silently and #353's explicit rejection
    # would quietly stop existing.
    question = game_state.get_current_question()
    if question is not None and question.is_estimate:
        await handler._conn.send_error(
            ws, ERR_INVALID_ACTION, "Wager not available on estimate rounds"
        )
        return

    if game_state.phase != GamePhase.WAGER_ACTIVE:
        return  # silent: betting window not open (or already closed)

    # A wager only makes sense *before* the answer is locked in — the wager
    # stakes points on getting that answer right. Once the answer has been
    # committed, accepting a new wager silently mutated the stake on an
    # already-locked answer (and the client never learned it was a no-op).
    # Reject explicitly so the player isn't misled. (#255.) In team mode
    # the commitment is the team's standing answer, since no member is
    # ever marked ``submitted`` there (#365/#804).
    if game_state.wager_is_locked(player.name):
        await handler._conn.send_error(
            ws, ERR_INVALID_ACTION, "Wager locked after answering"
        )
        return

    wager = data.get("wager")
    try:
        # wager is untyped client JSON (may be None / non-numeric); the
        # except below catches TypeError/ValueError from int() — the
        # arg-type mismatch is the intended defensive path.
        wager_int = int(wager)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        await handler._conn.send_error(ws, ERR_INVALID_ACTION, "Invalid wager")
        return
    if not 0 <= wager_int <= 100:
        await handler._conn.send_error(ws, ERR_INVALID_ACTION, "Wager must be 0-100")
        return

    # In team mode the bet belongs to the team and is staked against the
    # team's score (#804); any member may place it and the last one counts.
    game_state.record_wager(player.name, wager_int)
    await handler._conn.send(
        ws,
        {
            "type": "wager_accepted",
            "wager": wager_int,
        },
    )
    # Host/TV tally — how many bets are in, never how big they are (#656).
    await handler._conn.broadcast_to_admins_and_dashboards(
        handler._round_messages.build_wager_progress(game_state)
    )
    # Everyone in: close early rather than making a decided table sit out
    # the rest of the deadline.
    if not game_state.players_missing_wager():
        await handler._close_wager_window(game_state)
