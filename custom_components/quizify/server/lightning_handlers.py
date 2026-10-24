"""Lightning-round WebSocket handler: the ``lightning_answer`` message (#1124).

Moved out of ``QuizifyWebSocketHandler`` the same way ``submit_wager`` moved to
``wager_handlers.py`` and the Hot Seat messages to ``hot_seat_handlers.py``: a
module function that takes the handler as its first argument. The handler keeps
``_handle_lightning_answer`` as a one-line delegate, because the ``_DISPATCH``
table and the tests reach it by that name.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from custom_components.quizify.const import ERR_INVALID_ACTION, ERR_NOT_IN_GAME
from custom_components.quizify.game.state import GamePhase

if TYPE_CHECKING:  # pragma: no cover - typing only
    from aiohttp import web

    from custom_components.quizify.game.state import QuizifyGameState
    from custom_components.quizify.server.websocket import QuizifyWebSocketHandler


async def handle_lightning_answer(
    handler: QuizifyWebSocketHandler,
    ws: web.WebSocketResponse,
    data: dict,
    game_state: QuizifyGameState,
) -> None:
    """Record a player's answer to the current lightning question."""
    if game_state.phase != GamePhase.LIGHTNING:
        return  # silent — window closed
    lr = game_state.lightning
    if lr is None:
        return
    player = handler._player_for_ws(game_state, ws)
    if not player:
        await handler._conn.send_error(ws, ERR_NOT_IN_GAME, "Not in game")
        return

    shuffled_index = data.get("answer_index")
    if not isinstance(shuffled_index, int):
        await handler._conn.send_error(ws, ERR_INVALID_ACTION, "Invalid answer index")
        return

    # #405: a tap fired at ~0s can land AFTER the loop's advance() has
    # already armed the NEXT question (new clock + fresh shuffles). Without
    # a question-index guard the stale tap is recorded against the next
    # question through a random shuffle, and record_answer's one-per-Q rule
    # then silently drops the player's real answer to that question. The
    # client stamps every tap with the index it currently holds; reject the
    # message when that no longer matches the live question. Index-less
    # messages (older clients) are still accepted, but only while the
    # current question's window is genuinely open — once it has expired the
    # race window is exactly where a stale index-less tap would land wrong.
    stamped_index = data.get("index")
    if isinstance(stamped_index, int):
        if stamped_index != lr.index:
            return  # stale tap for an already-advanced question — drop it
    elif lr.time_remaining() <= 0:
        return  # index-less legacy tap after the window closed — drop it

    result = lr.record_answer(player.name, shuffled_index)
    if result is None:
        return  # rejected (already answered / locked / expired) — stay silent

    # Team mode (#552): the tap set the TEAM's answer and scored nothing
    # yet, so it must not lock the tapper's buttons or claim right/wrong —
    # a teammate may still change it. Every member is told what stands, in
    # their own answer order.
    if lr.team_mode and lr.entrant_for(player.name) != player.name:
        await handler._lightning_out.send_lightning_team_answer(
            game_state, lr, player.name
        )
        return

    await handler._lightning_out.send_lightning_answer_result(
        ws, lr, player.name, correct=bool(result)
    )
