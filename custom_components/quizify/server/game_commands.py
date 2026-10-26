"""Game-command WebSocket handlers: start, next, skip, end, pause, reset, kick.

Moved out of ``QuizifyWebSocketHandler`` (issue #1124, the follow-up of #1116)
the same way the wager, Hot Seat, lightning and lobby-settings messages moved:
module functions that take the handler as their first argument. The handler
keeps ``_handle_start_game`` / ``_after_start_game`` / ``_handle_next_question``
/ ``_advance_round`` / ``_handle_admin_skip`` / ``_handle_end_game`` /
``_handle_pause_game`` / ``_handle_resume_game`` / ``_handle_play_again`` /
``_handle_reset_game`` / ``_handle_kick_player`` and the public
``admin_action_*`` methods as one-line delegates, because the ``_DISPATCH``
table, the service layer and the tests reach them by those names.

The functions call each other through the handler (``handler._after_start_game``
and so on), not directly, so a test that patches a handler method still sees
every call. The admin gates stay where they were: ``_handle_message`` checks
admin rights before dispatching, and ``handle_reset_game`` still asks
``handler._is_reset_authorized``.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from typing import TYPE_CHECKING, Any

from custom_components.quizify.const import (
    ERR_GAME_ALREADY_STARTED,
    ERR_INVALID_ACTION,
    ERR_NO_QUESTIONS_REMAINING,
)
from custom_components.quizify.game.state import GamePhase
from custom_components.quizify.server.start_settings import _coerce_toggle

if TYPE_CHECKING:  # pragma: no cover - typing only
    from aiohttp import web

    from custom_components.quizify.game.state import QuizifyGameState
    from custom_components.quizify.server.websocket import QuizifyWebSocketHandler

# Same logger name as before the move (#1124), so a host's ``logger:`` config
# for ``custom_components.quizify.server.websocket`` still covers these lines.
_LOGGER = logging.getLogger("custom_components.quizify.server.websocket")


async def handle_start_game(
    handler: QuizifyWebSocketHandler,
    ws: web.WebSocketResponse,
    data: dict,
    game_state: QuizifyGameState,
) -> None:
    """Handle admin start_game command.

    NB: there is no MIN_PLAYERS check here. The admin-as-player flow in
    admin.js sends `start_game` BEFORE the admin's player tab has joined
    (the admin tab redirects to /quizify/player after firing start_game,
    and the player join lands a moment later). A MIN_PLAYERS>=1 check
    would block that flow entirely — my beta.4 fix did exactly that
    and broke admin-as-player. The original review-era concern about
    \"phantom rounds with zero players\" is self-correcting because the
    admin's player tab joins within ~1s of the redirect; if it doesn't,
    the round just runs no-answer which evaluates harmlessly.
    """
    # An explicit admin "Spiel starten" is a fresh-start intent. If a
    # previous game is still lingering in a non-LOBBY phase (a solo game
    # the admin never formally reset, or a finished FINALE), the old phase
    # guard silently rejected this start and left the OLD game's
    # category/language running — the new settings never applied. Markus
    # 2026-05-31: picked Geographie/DE but kept seeing the previous English
    # mixed game. Reset to LOBBY first (keeps connected players) so the
    # fresh settings always take effect. Mirrors _handle_play_again.
    # #671, generalized in #746: unconditional, and the whole set. No
    # round-scoped task has any business surviving into a new game
    # whatever phase we start from, and "there cannot be one in LOBBY" is
    # the same assumption that let the hot-seat task outlive four teardown
    # paths in the first place. Cancelling a task that isn't there costs
    # nothing; the reset_to_lobby below stays phase-gated because it
    # touches state, not tasks.
    handler._cancel_round_tasks()

    if game_state.phase != GamePhase.LOBBY:
        game_state.reset_to_lobby()

    raw_category = data.get("category")
    difficulty = data.get("difficulty")
    num_rounds = data.get("num_rounds", 10)
    language = data.get("language", "de")
    timer_duration = data.get("timer_duration")
    # Auto Lightning Round toggle (#285), default ON. Coerce truthily so a
    # missing key, a JSON bool, or a "0"/"false" string all resolve
    # sanely; only an explicit false-y value disables it.
    lightning_enabled = _coerce_toggle(data.get("lightning_enabled"), default=True)
    # Hot Seat auction toggle (#616), same coercion, same default.
    hot_seat_enabled = _coerce_toggle(data.get("hot_seat_enabled"), default=True)
    # Power-ups and the final-round wager (#742), same coercion, same
    # default. A host who wants a plain round can now have one.
    powerups_enabled = _coerce_toggle(data.get("powerups_enabled"), default=True)
    wager_enabled = _coerce_toggle(data.get("wager_enabled"), default=True)

    # Validate num_rounds the same way as timer_duration (#303): the WS
    # value reaches start_game raw, and total_rounds drives
    # ``self.round >= self.total_rounds``. A non-int makes that comparison
    # raise TypeError every start_next_question (game wedged); 0/negative
    # jumps straight to FINALE. Coerce to int + clamp to 1..50, fall back
    # to the default of 10 on anything unparseable.
    try:
        num_rounds = int(num_rounds)
    except (TypeError, ValueError):
        num_rounds = 10
    num_rounds = max(1, min(50, num_rounds))

    # category may be None (mixed), a string (single), or a list (multi)
    if isinstance(raw_category, list):
        category = None
        categories = raw_category if raw_category else None
    else:
        category = raw_category or None
        categories = None

    # Validate timer_duration: must be a positive int in a sane range
    # if provided, otherwise fall back to difficulty-based default.
    timer_value: int | None = None
    if timer_duration is not None:
        try:
            timer_value = int(timer_duration)
            if timer_value < 5 or timer_value > 300:
                timer_value = None
        except (TypeError, ValueError):
            timer_value = None

    try:
        game_state.start_game(
            category=category,
            categories=categories,
            difficulty=difficulty,
            num_rounds=num_rounds,
            language=language,
            timer_duration=timer_value,
            lightning_enabled=lightning_enabled,
            hot_seat_enabled=hot_seat_enabled,
            powerups_enabled=powerups_enabled,
            wager_enabled=wager_enabled,
        )
    except ValueError as err:
        # start_game raises two distinct ValueErrors: a wrong-phase
        # "already started" and an empty-pack "no questions". Surface the
        # right code instead of always reporting ALREADY_STARTED (#308):
        # an empty/missing pack is a NO_QUESTIONS_REMAINING condition.
        code = (
            ERR_NO_QUESTIONS_REMAINING
            if str(err) == ERR_NO_QUESTIONS_REMAINING
            else ERR_GAME_ALREADY_STARTED
        )
        await handler._conn.send_error(ws, code, str(err))
        return

    # Socket-independent continuation (TTS config, grace, first question).
    # Shared with the HA ``quizify.start_game`` service so the WS path and
    # the service path drive one implementation (#367).
    await handler._after_start_game(
        game_state,
        data.get("tts"),
        house_config=data.get("house"),
    )


async def after_start_game(
    handler: QuizifyWebSocketHandler,
    game_state: QuizifyGameState,
    tts_config: dict | None = None,
    house_config: dict | None = None,
) -> None:
    """Continue a just-started game: TTS + house config, grace, first question.

    Socket-independent core split out of ``_handle_start_game`` (#367) so
    the ``quizify.start_game`` service reuses the identical sequence without
    synthesizing a fake admin connection. Nothing here touches a specific
    ``ws`` — it only reads/mutates ``game_state`` and fans out over the
    shared connection layer, exactly as the admin path did inline.
    """
    # Apply per-game TTS narration settings (#281). The toggles ride the
    # start_game payload (like lightning_enabled), persisted in admin
    # localStorage — not config-entry options. No-op when no announcer is
    # wired (standalone dev server, HA without a TTS entity).
    #
    # An ABSENT block is SKIPPED, exactly like the house block below (#744).
    # ``_apply_tts_config`` reads the master as ``bool(tts.get("enabled"))``,
    # so feeding it ``{}`` turns narration OFF — which is what starting the
    # game by voice or from the Lovelace card used to do: the host set the
    # narrator up in the lobby over ``configure_tts``, said "Hey Nabu, start
    # the quiz", and the quizmaster went silent for the whole game. Skipping
    # leaves whatever the lobby-time push installed, which is the only
    # non-destructive reading of "no per-game override".
    if tts_config:
        handler._apply_tts_config(tts_config)

    # Apply the "House Plays Along" settings the same way (#494 Phase 4) —
    # the admin panel rides them on the start_game payload under ``house``,
    # alongside ``tts``. Same absent-means-leave-alone rule, for the same
    # reason: ``{}`` would silently disarm lights/SFX/events that the host
    # had switched on in the config-entry options.
    if house_config:
        handler._apply_house_config(house_config)

    # Snapshot the game_id start_game just minted (#352). We're about to
    # yield the loop for the grace sleep below; another admin socket can
    # slip in a reset_game (game_id -> None) or a second start_game (new
    # game_id) during that window. Both re-arm state under us, so we must
    # re-validate before firing the stale continuation.
    started_game_id = game_state.game_id

    # Grace period before round 1's timer starts.
    # The admin-as-player flow redirects the admin tab from /quizify/admin
    # to /quizify/player AFTER sending start_game. That navigation +
    # WebSocket reconnect + i18n init typically costs ~1.5-2.5s, during
    # which the timer is already ticking on the server. Without a buffer
    # the admin lands on round 1 with ~25s left on a 30s timer — much
    # harder to land a fast answer, and especially harsh for the player
    # who just spent time typing their name in the modal. Subsequent
    # rounds (Next Round button click) don't have this gap since the
    # admin is already on the player view.
    await asyncio.sleep(handler.START_REDIRECT_GRACE)

    # Re-validate after waking (#352): if a concurrent reset_game or a
    # second start_game landed during the grace window, game_id changed
    # (or phase moved past LOBBY) — firing the first question now would
    # push a round into a reset/zero-player lobby or double-advance and
    # wedge the round. Bail instead; the winning command owns the game.
    if game_state.game_id != started_game_id or game_state.phase != GamePhase.LOBBY:
        _LOGGER.info(
            "start_game grace continuation aborted: game changed during "
            "grace window (game_id %s -> %s, phase %s)",
            started_game_id,
            game_state.game_id,
            game_state.phase,
        )
        return

    # Start the first question
    await handler._start_next_question(game_state)


async def admin_action_start_game(
    handler: QuizifyWebSocketHandler,
    game_state: QuizifyGameState,
    preset: dict[str, Any] | None = None,
    overrides: dict[str, Any] | None = None,
) -> None:
    """Start a game from outside the socket — HA-service entry point (#367).

    Callable without an admin WebSocket connection so hosts can start the
    quiz via Assist voice, a Zigbee remote or a dashboard button. Only valid
    from the LOBBY phase: unlike ``_handle_start_game`` (which force-resets a
    lingering game because the admin explicitly re-picked settings in the
    UI), the service refuses to nuke a game already in progress — a voice
    "start the quiz" mid-round should be a clear error, not a silent wipe.
    Raises ``ValueError`` (game already started / no questions) for the
    caller to translate; on success runs the same continuation as the admin
    path.

    ``preset`` is a record from :class:`~server.preset_store.PresetStore`
    and ``overrides`` the explicit service fields; both are optional and
    both are resolved by :meth:`resolve_start_settings` on top of the host's
    last-used settings (#744). It used to call the bare
    ``game_state.start_game()`` and hand the host the factory game — mixed
    packs, medium, 10 rounds, German — no matter what they had set up.
    """
    if game_state.phase != GamePhase.LOBBY:
        raise ValueError(ERR_GAME_ALREADY_STARTED)
    settings = handler.resolve_start_settings(game_state, preset, overrides)
    # start_game() re-raises ERR_GAME_ALREADY_STARTED /
    # ERR_NO_QUESTIONS_REMAINING which the service surfaces to the host.
    game_state.start_game(**settings)
    # No ``tts`` block: the narration config the host set up in the lobby
    # over ``configure_tts`` is left exactly as it is (#744).
    await handler._after_start_game(game_state)


async def handle_next_question(
    handler: QuizifyWebSocketHandler,
    ws: web.WebSocketResponse,
    game_state: QuizifyGameState,
) -> None:
    """Handle admin next_question command."""
    err = await handler._advance_round(game_state)
    if err is not None:
        await handler._conn.send_error(ws, err, "Cannot advance now")


async def advance_round(
    handler: QuizifyWebSocketHandler,
    game_state: QuizifyGameState,
) -> str | None:
    """Advance to the next question — socket-independent core (#367).

    Shared by the admin ``next_question`` / ``next_round`` WS handler and
    the ``quizify.next_round`` HA service so both drive one implementation.
    Returns ``None`` after a successful advance, or an error-code string
    when the current phase forbids advancing (the caller decides how to
    surface it — a WS error frame vs a ``ServiceValidationError``).
    """
    # Auto Lightning Round recap (#285): the host's normal advance after a
    # mid-game lightning recap resumes the paused main game. resume_after_
    # lightning() flips LIGHTNING_RECAP→ANSWER_REVEAL and restores the
    # round counter, so the start_next_question below lands on the
    # originally-scheduled round.
    if game_state.phase == GamePhase.LIGHTNING_RECAP:
        # DELIBERATE SUBSET (#746): not ``_cancel_round_tasks()``. The
        # game is not ending here — the host is settling one detour and
        # resuming the round it interrupted. Killing the admin-pause task
        # would swallow a genuine host disconnect that is still pending.
        handler._cancel_lightning_loop()
        if game_state.resume_after_lightning():
            await handler._start_next_question(game_state)
        return None

    # Hot Seat reveal (#616): the host's advance settles the detour and
    # returns to the round the auction interrupted.
    if game_state.phase == GamePhase.HOT_SEAT_REVEAL:
        # DELIBERATE SUBSET (#746), same reasoning as the branch above:
        # one detour settles, the game carries on.
        handler._cancel_hot_seat_loop()
        if game_state.resume_after_hot_seat():
            await handler._start_next_question(game_state)
        return None

    if game_state.phase not in (GamePhase.LOBBY, GamePhase.ANSWER_REVEAL):
        return ERR_INVALID_ACTION

    # #298: a stray next_round/next_question in a *real* lobby (stale admin
    # tab, double-fire) must NOT advance — start_game doesn't change phase,
    # so an un-started game sits in LOBBY with game_id is None. Advancing
    # from there either ends an empty queue (FINALE with an empty podium,
    # round=0) or starts a round with no game_id and scores never reset.
    # The legitimate internal start→first-question path goes through
    # _start_next_question directly (see _handle_start_game), so gating the
    # public handler here doesn't break it.
    if game_state.phase == GamePhase.LOBBY and game_state.game_id is None:
        return ERR_INVALID_ACTION

    await handler._start_next_question(game_state)
    return None


async def admin_action_next_round(
    handler: QuizifyWebSocketHandler,
    game_state: QuizifyGameState,
) -> None:
    """Advance to the next question — HA-service entry point (#367).

    Mirrors the admin "Next" button (``next_round`` → ``_handle_next_
    question``). Raises ``ValueError`` when the phase forbids advancing so
    the service can surface a clear ``ServiceValidationError`` instead of
    silently no-opping.
    """
    err = await handler._advance_round(game_state)
    if err is not None:
        raise ValueError(err)


async def handle_admin_skip(
    handler: QuizifyWebSocketHandler,
    ws: web.WebSocketResponse,
    game_state: QuizifyGameState,
) -> None:
    """Handle admin skip — abandon the live question right now (#311).

    ``admin_skip`` used to route to ``_handle_next_question``, which only
    accepts LOBBY/ANSWER_REVEAL, so mid-question it was a dead no-op: a
    broken/offensive question forced the room to wait out the timer (pause
    can't advance either). The fix: during QUESTION_ACTIVE, evaluate the
    round immediately — locking in whatever answers are already in and
    transitioning to ANSWER_REVEAL — so the admin can then advance past it.
    Outside QUESTION_ACTIVE it behaves like the normal advance.
    """
    if game_state.phase == GamePhase.WAGER_ACTIVE:
        # #656: mid-window, skip means "stop waiting for bets" — the
        # question has not been asked yet, so there is nothing to abandon.
        # Close the window and ask it; anyone who has not bet has no bet.
        await handler._close_wager_window(game_state)
        return
    if game_state.phase == GamePhase.QUESTION_ACTIVE:
        # Stop the countdown and evaluate now. evaluate_round()'s
        # state-machine event (_fire_broadcast("round_evaluated")) drives
        # the summary broadcast, same as the timer-expiry auto-evaluate.
        # DELIBERATE SUBSET (#746): a skip cuts the QUESTION short, it
        # does not end the round or the game. Only the clock stops.
        handler._cancel_timer_tick()
        game_state.evaluate_round()
        return
    await handler._handle_next_question(ws, game_state)


async def handle_end_game(
    handler: QuizifyWebSocketHandler,
    ws: web.WebSocketResponse,
    game_state: QuizifyGameState,
) -> None:
    """Handle admin end_game command."""
    await handler.admin_action_end_game(game_state)


async def admin_action_end_game(
    handler: QuizifyWebSocketHandler,
    game_state: QuizifyGameState,
) -> None:
    """End the game now — socket-independent core (#367).

    Shared by the admin ``end_game`` WS handler and the ``quizify.end_game``
    HA service.
    """
    # #746: this used to cancel the tick and nothing else. ``end_game()``
    # has no phase guard, so the admin can end the game mid-detour — and a
    # surviving lightning loop then ran ``lr.advance()`` after the finale
    # had already been broadcast and the analytics recorded, changing the
    # scoreboard after the end screen was up. The hot-seat loop and the
    # deferred admin pause were free to outlive the finale for the same
    # reason. The game is over: every round-scoped task goes.
    handler._cancel_round_tasks()
    # end_game() fires the ``game_ended`` state event, which the
    # BroadcastDispatcher routes to _broadcast_finale — that's the single
    # finale broadcast source. Calling _broadcast_finale here too would
    # double-broadcast the podium. (#255.) end_game() is idempotent, so a
    # repeated admin end-game is a harmless no-op.
    game_state.end_game()


async def handle_pause_game(
    handler: QuizifyWebSocketHandler,
    ws: web.WebSocketResponse,
    game_state: QuizifyGameState,
) -> None:
    """Pause the current question. No-op if not in QUESTION_ACTIVE."""
    # Silent no-op on a non-pausable phase — the admin UI can call this
    # anytime. The service path (admin_action_pause) instead surfaces the
    # False as a ServiceValidationError.
    await handler.admin_action_pause(game_state)


async def admin_action_pause(
    handler: QuizifyWebSocketHandler,
    game_state: QuizifyGameState,
) -> bool:
    """Pause the current question — socket-independent core (#367).

    Returns ``True`` if the game was paused, ``False`` if the phase wasn't
    pausable (only QUESTION_ACTIVE is). Shared by the admin ``pause_game``
    WS handler and the ``quizify.pause`` HA service.
    """
    if not game_state.pause(reason="admin_paused"):
        return False
    # Stop sending tick updates while paused.
    # DELIBERATE SUBSET (#746): a pause freezes the clock and nothing
    # else — the round is still live and resume() restarts it.
    handler._cancel_timer_tick()
    # pause_reason rides along in the snapshot itself since #703, so it
    # is identical here and on every reconnect.
    state = handler._snapshot(game_state)
    state["type"] = "game_state"
    await handler._conn.broadcast(state)
    return True


async def handle_resume_game(
    handler: QuizifyWebSocketHandler,
    ws: web.WebSocketResponse,
    game_state: QuizifyGameState,
) -> None:
    """Resume from PAUSED → restart timer ticks and broadcast state."""
    await handler.admin_action_resume(game_state)


async def admin_action_resume(
    handler: QuizifyWebSocketHandler,
    game_state: QuizifyGameState,
) -> bool:
    """Resume a paused game — socket-independent core (#367).

    Returns ``True`` if the game was resumed, ``False`` if it wasn't paused.
    Shared by the admin ``resume_game`` WS handler and the ``quizify.resume``
    HA service.
    """
    if not game_state.resume():
        return False
    # Fan out per-player PROJECTED snapshots (#286): the raw snapshot
    # carries canonical answer order, so a plain broadcast here mis-scored
    # ~2/3 of taps after resume because the players re-render their answer
    # buttons from it while submit_answer maps through their own shuffle.
    await handler._broadcast_state_projected(game_state)
    # Restart the per-player tick loop.
    handler._start_timer_tick(game_state)
    return True


async def handle_play_again(
    handler: QuizifyWebSocketHandler,
    ws: web.WebSocketResponse,
    game_state: QuizifyGameState,
) -> None:
    """Restart with the previous game's settings — one-tap rematch.

    Cheaper than full reset_game + admin re-enters everything: keeps
    the existing players, just resets scores and starts the next game
    with the cached settings. If we don't have a snapshot yet (server
    never saw a start_game on this instance), fall back to reset.
    """
    if not game_state.last_settings:
        await handler._handle_reset_game(ws, game_state)
        return
    settings = game_state.last_settings
    # #407 (follow-up to #362), via the #746 registry: nothing from the
    # finished game may run into the rematch — a stale pause task would
    # pause round 1, a lingering lightning loop would broadcast stale
    # frames over it.
    handler._cancel_round_tasks()
    # Reset to LOBBY first so start_game's phase guard passes; keeps
    # players (reset_to_lobby leaves connected players in place).
    game_state.reset_to_lobby()
    try:
        game_state.start_game(**settings)
    except ValueError as err:
        await handler._conn.send_error(ws, ERR_GAME_ALREADY_STARTED, str(err))
        return
    # Snapshot the freshly-minted game_id before the grace sleep (#352) —
    # same concurrency guard as _handle_start_game: a reset_game or a
    # second start/play_again from another admin socket during the grace
    # window re-arms state, and the stale continuation must not fire.
    started_game_id = game_state.game_id
    # Same redirect grace as start_game — admin tab is still on the finale
    # view and needs to redirect/reconnect before round 1's timer ticks.
    await asyncio.sleep(handler.START_REDIRECT_GRACE)
    if game_state.game_id != started_game_id or game_state.phase != GamePhase.LOBBY:
        _LOGGER.info(
            "play_again grace continuation aborted: game changed during "
            "grace window (game_id %s -> %s, phase %s)",
            started_game_id,
            game_state.game_id,
            game_state.phase,
        )
        return
    await handler._start_next_question(game_state)


async def handle_reset_game(
    handler: QuizifyWebSocketHandler,
    ws: web.WebSocketResponse,
    game_state: QuizifyGameState,
) -> None:
    """Handle admin reset_game command (truly fresh lobby).

    reset_to_lobby on its own keeps players in the registry (so the
    finale's "Play again — same settings" path can reuse them). The
    explicit reset_game button means "wipe everything" — so we ALSO:
      1. Close every player WebSocket. Stale/abandoned connections
         (test bots, dead phones) get pruned. Real clients see the
         close and reconnect into a fresh lobby on their own.
      2. Drop every player from the registry via clear_all_players()
         so phantom names like "sdfsd 2" don't survive the reset.
      3. Wipe per-player session tokens so the reconnect path can't
         resurrect a cleared player under their old slot.
    """
    # #362 + #746: every round-scoped task, in one call. A deferred
    # admin-disconnect pause surviving this would undo the reset the
    # moment it fires into the fresh lobby.
    handler._cancel_round_tasks()
    # Cancel any pending admin-disconnect timer and stale player-removal
    # tasks from the finished game.
    await handler._conn.cleanup()
    # Wipe player session tokens to prevent cross-game reuse.
    handler._conn.clear_all_player_tokens()

    # Snapshot the live player WSes BEFORE clearing the registry — we
    # need them both to broadcast the reset signal and to close them
    # afterwards. The host who pressed reset is frequently an
    # admin-as-player (joined the lobby on the admin WS), so their own
    # socket is in this list. That's exactly why ordering matters here
    # (issue #207): if we close the sockets first, the broadcasts below
    # never reach the host (or any real player) and their UI stays
    # frozen on the now-stale lobby. So: clear state → broadcast the
    # reset to everyone still connected → only THEN close the sockets.
    stale_wses = [
        sock
        for sock in (
            handler._conn.socket_for(p.connection_id)
            for p in game_state.get_players()
        )
        if sock is not None
    ]

    # Drop every player from the registry (full wipe — not just score reset).
    game_state.clear_all_players()
    game_state.reset_to_lobby()

    # Tell every currently-connected client to reset its view to the
    # initial screen (admin → setup, players → join). This MUST run
    # while the sockets are still open.
    await handler._conn.broadcast({"type": "game_reset"})

    state = handler._snapshot(game_state)
    state["type"] = "game_state"
    await handler._conn.broadcast(state)

    # Now close the (snapshotted) player sockets so abandoned/stale
    # connections actually die. Real clients have already received the
    # reset above and will reconnect into the fresh lobby on their own.
    for pws in stale_wses:
        # Best-effort cleanup — a socket that's already gone is fine.
        with contextlib.suppress(Exception):
            await pws.close()


async def handle_kick_player(
    handler: QuizifyWebSocketHandler,
    ws: web.WebSocketResponse,
    data: dict,
    game_state: QuizifyGameState,
) -> None:
    """Remove a player from the lobby. LOBBY-only — kicking mid-game
    would orphan their score and surprise everyone watching the TV.
    Admins can always end the game first if they want a hard reset.
    """
    if game_state.phase != GamePhase.LOBBY:
        await handler._conn.send_error(
            ws, ERR_INVALID_ACTION, "Players can only be kicked from the lobby"
        )
        return

    target_name = (data.get("player_name") or data.get("name") or "").strip()
    if not target_name:
        await handler._conn.send_error(ws, ERR_INVALID_ACTION, "Missing player_name")
        return

    target = game_state.get_player(target_name)
    if not target:
        await handler._conn.send_error(ws, ERR_INVALID_ACTION, "Player not found")
        return

    if target.is_admin:
        await handler._conn.send_error(ws, ERR_INVALID_ACTION, "Cannot kick the admin")
        return

    # Tell the target first, then close. The message is what puts the
    # "you were removed" screen on their phone (#750) — the close alone
    # is indistinguishable from a flat battery or a dead wifi hop, and
    # for two years that is exactly what a removed guest saw. Order
    # matters: send before close, or the client never reads it. We don't
    # rely on the closed event reaching us — remove_player flushes state
    # immediately and the WS cleanup path is idempotent.
    target_ws = handler._conn.socket_for(target.connection_id)
    game_state.remove_player(target.name)
    handler._conn.clear_player_tokens(target.name)

    if target_ws is not None and not target_ws.closed:
        try:
            await target_ws.send_json(
                {"type": "kicked", "reason": "removed_by_admin"}
            )
            await target_ws.close()
        except Exception as err:  # noqa: BLE001
            _LOGGER.debug("Closing kicked player WS raised: %s", err)

    _LOGGER.info("Admin kicked player: %s", target.name)

    # Coalesced roster broadcast (#453).
    handler._mark_roster_dirty("player_left")
