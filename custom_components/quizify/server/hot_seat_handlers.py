"""Hot Seat WebSocket handlers — bid, bet and answer (#616, #1108).

Moved out of ``QuizifyWebSocketHandler`` (issue #1108) so the auction's three
player messages live beside each other instead of between transport, auth and
house configuration. They are reached exactly as before: through the handler's
``_DISPATCH`` table, which passes the handler itself as the first argument.
The handler keeps owning the auction's task (``_start_hot_seat`` /
``_start_hot_seat_loop`` / ``_cancel_hot_seat_loop``) and its teardown
registry; these functions only validate one inbound message and record it on
the live :class:`~custom_components.quizify.game.hot_seat.HotSeatRound`.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from custom_components.quizify.const import ERR_INVALID_ACTION, ERR_NOT_IN_GAME
from custom_components.quizify.game.state import GamePhase

if TYPE_CHECKING:  # pragma: no cover - typing only
    from aiohttp import web

    from custom_components.quizify.game.state import QuizifyGameState
    from custom_components.quizify.server.websocket import QuizifyWebSocketHandler


async def handle_hot_seat_bid(
    handler: QuizifyWebSocketHandler,
    ws: web.WebSocketResponse,
    data: dict,
    game_state: QuizifyGameState,
) -> None:
    """Accept one sealed bid for the chair (#616).

    The bid is a PERCENT of the bidder's own score, exactly like a finale
    wager — bidding absolute points would hand every auction to whoever is
    already ahead, which is the opposite of what this mode is for.

    In team mode the bidder is the team (#804): one bid, staked against
    the team's score, and the member who places it takes the chair.
    """
    player = handler._player_for_ws(game_state, ws)
    if not player:
        await handler._conn.send_error(ws, ERR_NOT_IN_GAME, "Not in game")
        return
    if game_state.phase != GamePhase.HOT_SEAT_AUCTION:
        return  # silent: the window is shut
    hs = game_state.hot_seat
    if hs is None:
        return

    try:
        pct = int(data.get("bid"))  # type: ignore[arg-type]
    except (TypeError, ValueError):
        await handler._conn.send_error(ws, ERR_INVALID_ACTION, "Invalid bid")
        return
    if not 0 <= pct <= 100:
        await handler._conn.send_error(ws, ERR_INVALID_ACTION, "Bid must be 0-100")
        return
    if not hs.record_bid(player.name, pct):
        await handler._conn.send_error(ws, ERR_INVALID_ACTION, "Bid already placed")
        return

    await handler._hot_seat_out.send_hot_seat_bid_accepted(
        ws, hs, player.name, pct=pct
    )


async def handle_hot_seat_bet(
    handler: QuizifyWebSocketHandler,
    ws: web.WebSocketResponse,
    data: dict,
    game_state: QuizifyGameState,
) -> None:
    """Accept a spectator's optional stake on the seat holder (#616)."""
    player = handler._player_for_ws(game_state, ws)
    if not player:
        await handler._conn.send_error(ws, ERR_NOT_IN_GAME, "Not in game")
        return
    if game_state.phase != GamePhase.HOT_SEAT:
        return  # silent: betting window shut
    hs = game_state.hot_seat
    if hs is None:
        return
    if hs.is_on_seat_team(player.name):
        # Refused rather than ignored: betting against yourself and then
        # answering wrongly on purpose turns a question you cannot answer
        # into a profit. In team mode the refusal covers the seat holder's
        # whole team (#804) — they stake the same purse, so the hedge is
        # available to any of them.
        await handler._conn.send_error(
            ws, ERR_INVALID_ACTION, "The hot seat does not bet"
        )
        return

    side = data.get("side")
    if not isinstance(side, str):
        # Same refusal the round itself issues for an unknown side — this
        # only moves it forward of the call so `side` reaches `record_bet`
        # as a `str` (#986). A missing or non-string "side" already ended
        # here with exactly this message, via `record_bet` returning False.
        await handler._conn.send_error(ws, ERR_INVALID_ACTION, "Bet not accepted")
        return
    try:
        pct = int(data.get("bet"))  # type: ignore[arg-type]
    except (TypeError, ValueError):
        await handler._conn.send_error(ws, ERR_INVALID_ACTION, "Invalid bet")
        return
    if not 0 <= pct <= 100:
        await handler._conn.send_error(ws, ERR_INVALID_ACTION, "Bet must be 0-100")
        return
    if not hs.record_bet(player.name, side, pct):
        await handler._conn.send_error(ws, ERR_INVALID_ACTION, "Bet not accepted")
        return

    await handler._hot_seat_out.send_hot_seat_bet_accepted(
        ws, hs, player.name, side=side, pct=pct
    )


async def handle_hot_seat_answer(
    handler: QuizifyWebSocketHandler,
    ws: web.WebSocketResponse,
    data: dict,
    game_state: QuizifyGameState,
) -> None:
    """Record the seat holder's single answer (#616)."""
    player = handler._player_for_ws(game_state, ws)
    if not player:
        await handler._conn.send_error(ws, ERR_NOT_IN_GAME, "Not in game")
        return
    if game_state.phase != GamePhase.HOT_SEAT:
        return
    hs = game_state.hot_seat
    # The chair holds a person: in team mode the member who placed the
    # team's bid, not the team that pays for it (#804).
    if hs is None or player.name != hs.seat_holder:
        return

    try:
        idx = int(data.get("answer"))  # type: ignore[arg-type]
    except (TypeError, ValueError):
        await handler._conn.send_error(ws, ERR_INVALID_ACTION, "Invalid answer")
        return
    result = hs.record_answer(player.name, idx)
    if result is None:
        return
    await handler._hot_seat_out.send_hot_seat_answer_accepted(ws)
