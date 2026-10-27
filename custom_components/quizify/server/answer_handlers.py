"""Answer, reaction and power-up WebSocket handlers.

Moved out of ``QuizifyWebSocketHandler`` (issue #1124, part 5) the same way
the game commands moved to ``game_commands.py``: module functions that take
the handler as their first argument. The handler keeps
``_handle_submit_answer`` / ``_handle_reaction`` / ``_handle_use_powerup`` as
one-line delegates, because the ``_DISPATCH`` table and the tests reach them
by those names. The functions call each other and the coalescers through the
handler, so a test that patches a handler method still sees every call.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from custom_components.quizify.const import (
    ERR_INVALID_ACTION,
    ERR_NOT_IN_GAME,
)
from custom_components.quizify.game.powerups import (
    FREEZE_DURATION,
    PowerUpEffect,
    PowerUpType,
)
from custom_components.quizify.game.scoring import is_streak_active
from custom_components.quizify.game.state import (
    AnswerResult,
    GamePhase,
    TeamAnswerAck,
)
from custom_components.quizify.game.team import Team

if TYPE_CHECKING:  # pragma: no cover - typing only

    from aiohttp import web

    from custom_components.quizify.game.state import QuizifyGameState
    from custom_components.quizify.server.websocket import QuizifyWebSocketHandler

# Same logger name as before the move (#1124), so a host's ``logger:`` config
# for ``custom_components.quizify.server.websocket`` still covers these lines.
_LOGGER = logging.getLogger("custom_components.quizify.server.websocket")


async def handle_submit_answer(
    handler: QuizifyWebSocketHandler,
    ws: web.WebSocketResponse,
    data: dict,
    game_state: QuizifyGameState,
) -> None:
    """Handle answer submission from player."""
    # ``all_submitted()`` decides early reveal off ``connected`` (#882);
    # reap first so a room-mate whose socket died a moment ago cannot hold
    # the whole room on the full timer.
    handler._reap_closed_connections(game_state)
    player = handler._player_for_ws(game_state, ws)
    if not player:
        await handler._conn.send_error(ws, ERR_NOT_IN_GAME, "Not in game")
        return

    if game_state.phase != GamePhase.QUESTION_ACTIVE:
        # Silently ignore submit_answer when not in question phase
        return

    # Estimate rounds (#275): the player submits a numeric ``guess`` on the
    # same message instead of an ``answer_index``. Closeness is ranked at
    # round evaluation, so there's no per-answer ``answer_result`` to send —
    # an ``ack`` lets the client lock the slider on a confirmed submit.
    current_q = game_state.get_current_question()
    if current_q is not None and getattr(current_q, "is_estimate", False):
        guess_raw = data.get("guess")
        if not isinstance(guess_raw, (int, float)) or isinstance(guess_raw, bool):
            await handler._conn.send_error(ws, ERR_INVALID_ACTION, "Invalid guess")
            return
        err = game_state.submit_guess(player.name, float(guess_raw))
        if err is None:
            # #939: a guess moves the room's "N/M answered" exactly like a
            # tap does (#619) — solo and team guess alike. Without this the
            # tracker sat on 0/N for every estimate round.
            handler._mark_progress_dirty()
            await handler._conn.send(ws, {"type": "guess_accepted"})
        else:
            # Fallback text comes from ERROR_FALLBACK_TEXT (#812).
            await handler._conn.send_error(ws, err)
        return

    shuffled_index = data.get("answer_index")
    if shuffled_index is None or not isinstance(shuffled_index, int):
        await handler._conn.send_error(ws, ERR_INVALID_ACTION, "Invalid answer index")
        return

    # Map shuffled index back to original index — use the player's
    # own shuffle (anti-cheat per-player), falling back to canonical.
    player_shuffle = game_state.get_player_shuffle(player.name)
    if 0 <= shuffled_index < len(player_shuffle):
        original_index = player_shuffle[shuffled_index]
    else:
        await handler._conn.send_error(
            ws, ERR_INVALID_ACTION, "Answer index out of range"
        )
        return

    result = game_state.submit_answer(player.name, original_index)

    if isinstance(result, TeamAnswerAck):
        # Team mode (#365): the tap set the team's answer, it did not score
        # anything. Every member — the setter included — gets the standing
        # answer in their own answer order, so the dots appear on the same
        # question for all of them.
        await handler._round_out.send_team_answer(
            game_state, result, setter=player.name
        )
        handler._mark_progress_dirty()
        return

    if isinstance(result, AnswerResult):
        # #619: the room can see who it is waiting for. Coalesced, so a
        # simultaneous tap-storm is one frame, not one per tap.
        handler._mark_progress_dirty()
        await handler._conn.send(ws, {
            "type": "answer_result",
            "correct": result.correct,
            "points_earned": result.points_earned,
            "speed_bonus": result.speed_bonus,
            "streak_bonus": result.streak_bonus,
            "difficulty_multiplier": result.difficulty_multiplier,
            "difficulty_bonus": result.difficulty_bonus,
            "double_bonus": result.double_bonus,
            "new_streak": result.new_streak,
            # #1066: whether that streak scores yet — the reveal headline
            # asks this instead of keeping a threshold of its own.
            "streak_active": is_streak_active(result.new_streak),
            "new_total": result.new_total,
            "milestone_bonus": result.milestone_bonus,
            "milestone_streak": result.milestone_streak,
        })
        # A milestone is announced out loud and on the HA bus — not over
        # the socket (#750). There used to be a ``streak_milestone``
        # broadcast here "so the TV/admin view can flash", but no surface
        # ever grew that flash, and the phone toast is driven off
        # ``milestone_streak`` in the answer_result above (#1066; it used
        # to match ``new_streak`` against a list of its own). A message
        # with no reader is worse than no message: it reads like a contract.
        if result.milestone_bonus:
            # ONE milestone from the dispatch point (#789); the fan-out to
            # the announcer and the HA bus lives in _streak_milestone.
            handler._streak_milestone(
                player.name, result.milestone_streak, result.milestone_bonus
            )
        # NB: round-summary broadcast is fired exclusively by
        # state._fire_broadcast("round_evaluated") \u2192 broadcast_state().
        # Do NOT broadcast here \u2014 that would double-fire when the timer
        # path races with all-submitted (#3 in logical review).
    elif isinstance(result, str):
        # Fallback text comes from ERROR_FALLBACK_TEXT (#812).
        await handler._conn.send_error(ws, result)


async def handle_reaction(
    handler: QuizifyWebSocketHandler,
    ws: web.WebSocketResponse,
    data: dict,
    game_state: QuizifyGameState,
) -> None:
    """Broadcast an emoji reaction. If the reaction comes in during
    ANSWER_REVEAL, also award a +1 bonus to each correct answerer
    from that round — cheap "audience appreciation" mechanic. The
    reactor themselves can only grant one such bonus per round
    (tracked on PlayerSession.reaction_bonuses_given), and each
    correct answerer caps at _REACTION_BONUS_CAP_PER_ROUND incoming
    bonuses so a 6-player room can't pile 5 free points on the
    leader every reveal."""
    reactor = handler._player_for_ws(game_state, ws)
    if not reactor:
        return  # silent: reactions are best-effort, not a hard error

    emoji = data.get("emoji", "")
    if not isinstance(emoji, str) or not (1 <= len(emoji) <= 8):
        return  # ignore malformed

    # Buffer the visual reaction instead of broadcasting it inline (#304).
    # A flush task drains the buffer once per ~150ms window; identical
    # (player, emoji) pairs within a window collapse to one broadcast,
    # which is where the amplification came from. Floating animations work
    # in any phase, same as before — just batched.
    handler._enqueue_reaction(reactor.name, emoji)

    # Bonus path: only during reveal, only once per round per reactor.
    if game_state.phase != GamePhase.ANSWER_REVEAL:
        return
    summary = game_state.get_round_summary()
    if summary is None:
        return
    round_num = game_state.round
    if round_num in reactor.reaction_bonuses_given:
        return  # already granted a bonus this round
    reactor.reaction_bonuses_given.add(round_num)

    # Award +1 for each correct answer this round, respecting the per-round
    # incoming cap.
    #
    # #800: the points go to the *ranked participant*, not to the name on
    # the AnswerResult. In team mode the results are still per player — the
    # member whose tap carried the team's answer is the "correct" one — so
    # crediting them moved a shadow score (#668/#669) that no screen shows,
    # while the leaderboard this very broadcast carries never budged. The
    # recipient is therefore the team when there is one, and the player
    # only when they are solo.
    reactor_participant = game_state.get_ranked_participant_for(reactor.name)
    bonus_recipients: list[str] = []
    credited: list[Any] = []
    for result in summary.results:
        if not result.correct:
            continue
        recipient = game_state.get_ranked_participant_for(result.player_id)
        if recipient is None or recipient is reactor_participant:
            continue  # can't tip your own hat — nor your own team's
        if any(recipient is seen for seen in credited):
            continue  # one team, several correct members → one bonus
        credited.append(recipient)
        # Per-round inbound counter (separate from `reaction_bonuses_given`
        # which tracks OUTGOING bonuses). Encapsulated on the participant so
        # it is reset per game in reset_for_new_game() (#167) and the cap
        # logic lives with the state it guards (#364).
        if not recipient.add_reaction_bonus(
            round_num, handler._REACTION_BONUS_CAP_PER_ROUND
        ):
            continue  # recipient already at the per-round cap
        # ``to_players`` stays a list of PLAYER names: it is what each
        # phone matches itself against to raise the "+1 from X" toast. A
        # credited team hands over its whole roster, so every member is
        # told about the point their team just earned.
        if isinstance(recipient, Team):
            bonus_recipients.extend(recipient.members)
        else:
            bonus_recipients.append(recipient.name)

    if bonus_recipients:
        # #449: the bonus above mutated recipient scores, but the #414
        # round-summary memo (built for the reveal broadcast) still holds
        # the pre-bonus leaderboard. Invalidate it so any join/reconnect/
        # get_state during this same ANSWER_REVEAL re-serializes the fresh,
        # post-bonus leaderboard instead of serving the stale cache.
        game_state.invalidate_round_summary_msg()
        # #416: defer + coalesce the leaderboard broadcast into the shared
        # ~150ms flush window instead of emitting a full-leaderboard frame
        # per reactor right here. The points are already awarded above; the
        # flush builds one ``reaction_bonus`` reflecting the whole batch.
        handler._reaction_bonus_from[reactor.name] = None
        for recipient_name in bonus_recipients:
            handler._reaction_bonus_to[recipient_name] = None
        # The visual reaction above already armed the flush task, but arm it
        # defensively so a bonus can never sit undrained.
        handler._ensure_reaction_flush()


async def handle_use_powerup(
    handler: QuizifyWebSocketHandler,
    ws: web.WebSocketResponse,
    data: dict,
    game_state: QuizifyGameState,
) -> None:
    """Handle power-up usage."""
    player = handler._player_for_ws(game_state, ws)
    if not player:
        await handler._conn.send_error(ws, ERR_NOT_IN_GAME, "Not in game")
        return

    target_id = data.get("target_player_id")
    result = game_state.use_powerup(player.name, target_id)

    if isinstance(result, PowerUpEffect):
        # Broadcast power-up application to all
        effect_data: dict[str, Any] = {
            "type": "powerup_applied",
            "powerup_type": result.type.value,
            "source_player": result.source_player,
            "target_player": result.target_player,
        }

        # For joker, send the removed answer to the using player only
        if (
            result.type == PowerUpType.JOKER
            and result.joker_remove_index is not None
        ):
            # Map the canonical original index to THIS player's shuffled
            # position. The buttons are rendered in the per-player shuffle
            # order, so mapping through the canonical shuffle_map would
            # disable the wrong button — potentially the CORRECT answer
            # (#254). get_player_shuffle falls back to the canonical map
            # when the player has no per-player shuffle.
            player_shuffle = game_state.get_player_shuffle(player.name)
            shuffled_remove_idx = None
            for shuffled_idx, orig_idx in enumerate(player_shuffle):
                if orig_idx == result.joker_remove_index:
                    shuffled_remove_idx = shuffled_idx
                    break

            await handler._conn.send(ws, {
                "type": "powerup_applied",
                "powerup_type": "joker",
                "source_player": result.source_player,
                "joker_remove_index": shuffled_remove_idx,
            })
            # Public broadcast w/o joker_remove_index — opponents get a
            # "Player X used Joker" toast (the removed-answer index is
            # per-shuffle so it stays in the private send above).
            await handler._conn.broadcast({
                "type": "powerup_applied",
                "powerup_type": "joker",
                "source_player": result.source_player,
            })
        elif result.type == PowerUpType.STEAL:
            effect_data["stolen_points"] = result.stolen_points
            await handler._conn.broadcast(effect_data)
        else:
            # FREEZE: tell the target how long the lockout lasts so the
            # client can show a countdown overlay (#300). Server is the
            # authority — it rejects submits via ERR_FROZEN regardless of
            # what the client does with this.
            if result.type == PowerUpType.FREEZE:
                effect_data["freeze_duration"] = FREEZE_DURATION
            await handler._conn.broadcast(effect_data)
    elif isinstance(result, str):
        await handler._conn.send_error(ws, result, "Power-up not available")
