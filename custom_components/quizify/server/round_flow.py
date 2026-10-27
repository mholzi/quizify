"""Round flow: question delivery, timers, betting window, detour-mode loops.

Moved out of ``QuizifyWebSocketHandler`` (issue #1124, part 5): module
functions that take the handler as their first argument. The handler keeps
every old name as a one-line delegate, because the #746 task registry
(``_ROUND_SCOPED_TASKS``) names the cancellers as handler methods, the
broadcasters are wired to ``handler._continue_normal_question`` and
``handler._close_wager_window``, and the tests call or patch them on the
handler. The functions call each other through the handler for the same
reason. The task attributes stay on the handler.
"""

from __future__ import annotations

import asyncio
import logging
import random
from typing import TYPE_CHECKING, Any

from custom_components.quizify.const import (
    WAGER_WINDOW_DURATION,
)
from custom_components.quizify.game.drivers import (
    HotSeatDriver,
    LightningDriver,
    NormalRoundDriver,
    WagerWindowDriver,
)
from custom_components.quizify.game.phase_controller import TICK_INTERVAL
from custom_components.quizify.game.state import (
    GamePhase,
)
from custom_components.quizify.server.serializers import (
    strip_answer_for_dashboard,
)

if TYPE_CHECKING:  # pragma: no cover - typing only
    from collections.abc import Callable

    from custom_components.quizify.game.state import QuizifyGameState
    from custom_components.quizify.server.websocket import QuizifyWebSocketHandler

# Same logger name as before the move (#1124), so a host's ``logger:`` config
# for ``custom_components.quizify.server.websocket`` still covers these lines.
_LOGGER = logging.getLogger("custom_components.quizify.server.websocket")


async def start_auto_lightning(
    handler: QuizifyWebSocketHandler,
    game_state: QuizifyGameState,
) -> None:
    """Fire the auto Lightning Round mid-game (#285).

    Replaces the retired host-manual ``start_lightning`` entry: there is
    no host tap. We detour out of the current round (``auto=True``
    remembers it), broadcast the intro splash, then the loop auto-advances
    out of the splash after a grace and runs the fast question loop. After
    the recap the host's normal advance resumes the main game.
    """
    # Stop any normal-round timer first — the two loops are mutually
    # exclusive.
    handler._cancel_timer_tick()

    started = game_state.start_lightning_round(
        # Reuse the running game's own pack/difficulty/language selection.
        category=game_state.category,
        difficulty=game_state.difficulty,
        language=game_state.language,
        auto=True,
    )
    if not started:
        # No questions available for the lightning queue, or wrong phase.
        # Fall back to the normal round so the game never wedges.
        _LOGGER.warning(
            "Auto lightning round could not start; continuing normal game"
        )
        await handler._continue_normal_question(game_state)
        return

    # Broadcast a phase-entry state so every client switches to the
    # lightning view, then the intro splash ("Bolt Burst", #201).
    state = handler._snapshot(game_state)
    state["type"] = "game_state"
    await handler._conn.broadcast(state)
    await handler._lightning_out.send_lightning_splash(game_state)

    # Auto-advance: no host tap. The loop itself dismisses the splash after
    # the grace and starts question 1.
    handler._start_lightning_loop(game_state, auto_dismiss_splash=True)


async def continue_normal_question(
    handler: QuizifyWebSocketHandler,
    game_state: QuizifyGameState,
) -> None:
    """Start the next normal question without re-checking the LR trigger.

    Used by the auto-LR fallback path (LR couldn't start) so we don't
    recurse back into should_trigger_lightning() — the flag is already
    burned, so this is just the plain question start.
    """
    question = game_state.start_next_question()
    if question is None:
        return
    await handler._deliver_question(game_state, question)


def start_lightning_loop(
    handler: QuizifyWebSocketHandler,
    game_state: QuizifyGameState,
    *,
    auto_dismiss_splash: bool = False,
) -> None:
    """Arm the Lightning Round driver (#42/#788).

    The loop itself — the fixed window, the all-answered short-circuit, the
    phase re-checks and the recap — lives in
    :class:`~custom_components.quizify.game.drivers.LightningDriver`. What
    stays here is the task: creating it, parking it on the attribute the
    #746 registry tears down, and surfacing its exceptions.
    """
    handler._cancel_lightning_loop()
    driver = LightningDriver(
        handler._lightning_out,
        milestones=handler,
        splash_grace=handler.LIGHTNING_SPLASH_GRACE,
        splash_hold=handler.AUTO_LIGHTNING_SPLASH_HOLD,
    )
    handler._lightning_task = asyncio.ensure_future(
        driver.run(game_state, auto_dismiss_splash=auto_dismiss_splash)
    )
    handler._lightning_task.add_done_callback(handler._log_task_exception)


def cancel_lightning_loop(handler: QuizifyWebSocketHandler) -> None:
    if handler._lightning_task is not None:
        handler._lightning_task.cancel()
        handler._lightning_task = None


def cancel_hot_seat_loop(handler: QuizifyWebSocketHandler) -> None:
    if handler._hot_seat_task is not None:
        handler._hot_seat_task.cancel()
        handler._hot_seat_task = None


async def start_hot_seat(
    handler: QuizifyWebSocketHandler,
    game_state: QuizifyGameState,
) -> bool:
    """Open the auction and drive it to the reveal. False means "skipped".

    A False here is ordinary — too few players, or every remaining
    question is spoken for by the main game (#544) — and the caller falls
    through to the normal round rather than ending it.
    """
    handler._cancel_timer_tick()
    if not game_state.start_hot_seat_auction():
        _LOGGER.info("Hot seat auction skipped; continuing normal game")
        return False

    hs = game_state.hot_seat
    if hs is None:
        return False

    state = handler._snapshot(game_state)
    state["type"] = "game_state"
    await handler._conn.broadcast(state)
    await handler._hot_seat_out.send_hot_seat_auction(game_state, hs)

    handler._start_hot_seat_loop(game_state)
    return True


def start_hot_seat_loop(
    handler: QuizifyWebSocketHandler,
    game_state: QuizifyGameState,
) -> None:
    """Arm the Hot Seat driver (#616/#788).

    Auction → reveal → question → settlement is
    :class:`~custom_components.quizify.game.drivers.HotSeatDriver`; the task
    and the #746 attribute stay here.
    """
    handler._cancel_hot_seat_loop()
    driver = HotSeatDriver(
        handler._hot_seat_out,
        milestones=handler,
        reveal_hold=handler.HOT_SEAT_REVEAL_HOLD,
    )
    handler._hot_seat_task = asyncio.ensure_future(driver.run(game_state))
    handler._hot_seat_task.add_done_callback(handler._log_task_exception)


async def broadcast_state_projected(
    handler: QuizifyWebSocketHandler,
    game_state: QuizifyGameState,
    *,
    extra: dict[str, Any] | None = None,
) -> None:
    """Fan out a ``game_state`` snapshot, projected per recipient.

    Each connected PLAYER gets the snapshot projected into their own frame
    (own shuffled answer order, own timer, flat reveal) via
    ``project_snapshot_for_player`` — the same mechanism the join/reconnect
    path uses (#253). Sending the raw canonical snapshot to players would
    mis-score ~2/3 of their taps because ``submit_answer`` maps the tapped
    index through the player's OWN shuffle (#286). Pure admin/dashboard
    sockets (no player session) get the canonical snapshot untouched.

    ``extra`` merges extra top-level keys (e.g. ``pause_reason``) into every
    message. Used by the resume path (#286 #287) so a resumed game reaches
    every screen with correctly-ordered answer buttons.
    """
    base = handler._snapshot(game_state)

    # Per-player projected sends.
    player_ws: set[Any] = set()
    sends = []
    for player in game_state.get_players():
        if not player.connected:
            continue
        player_sock = handler._conn.socket_for(player.connection_id)
        if player_sock is not None:
            player_ws.add(player_sock)
        msg = handler._round_messages.project_snapshot_for_player(
            game_state, snapshot=base, player=player
        )
        msg["type"] = "game_state"
        if extra:
            msg.update(extra)
        sends.append(handler._conn.send_to_player(player, msg))

    # Raw snapshot for pure admin/dashboard sockets — but skip any socket
    # that is also a player (admin-as-player already got its projected
    # copy above, and must NOT see canonical order mid-question).
    raw = dict(base)
    raw["type"] = "game_state"
    if extra:
        raw.update(extra)
    for ws, _is_admin in handler._conn.iter_admin_and_dashboard_ws():
        if ws.closed or ws in player_ws:
            continue
        sends.append(handler._conn.send(ws, raw))

    if sends:
        await asyncio.gather(*sends)


async def start_next_question(
    handler: QuizifyWebSocketHandler,
    game_state: QuizifyGameState,
) -> None:
    """Start the next question: shuffle answers, broadcast, start timer ticks."""
    handler._cancel_timer_tick()

    # Auto Lightning Round (#285): exactly once per game, when the game is
    # about to enter the pre-picked target round, detour into the fast
    # Lightning Round first. The normal round resumes after the recap (the
    # host's next advance lands on resume_after_lightning → ANSWER_REVEAL →
    # this same path, which by then sees _lightning_fired and proceeds).
    if game_state.should_trigger_lightning():
        await handler._start_auto_lightning(game_state)
        return

    # Hot Seat auction (#616): the same detour shape, one round later or
    # earlier — never the same round, the draw excludes it. A failed start
    # (too few players, no spare question) falls through to the normal
    # question so the game can never wedge on a skipped bonus.
    if game_state.should_trigger_hot_seat() and await handler._start_hot_seat(
        game_state
    ):
        return

    question = game_state.start_next_question()
    if question is None:
        # Game ended (no more questions or round limit reached).
        # start_next_question() already called end_game(), which fired the
        # ``game_ended`` event → dispatcher → _broadcast_finale (the single
        # finale broadcast source). No direct broadcast here. (#255.)
        return

    await handler._deliver_question(game_state, question)


async def deliver_question(
    handler: QuizifyWebSocketHandler,
    game_state: QuizifyGameState,
    question: Any,
) -> None:
    """Send a freshly-started question — or open its betting window (#656).

    On an MC final ``start_next_question`` parks the state machine in
    WAGER_ACTIVE instead of QUESTION_ACTIVE: bets first, question after.
    Every path that starts a question goes through here, so none of them
    can emit a question into a phase that has no timers — which would
    exit the tick loop on its first iteration and hang the round.
    """
    if game_state.phase == GamePhase.WAGER_ACTIVE:
        await handler._open_wager_window(game_state, question)
        return
    await handler._emit_question(game_state, question)


async def open_wager_window(
    handler: QuizifyWebSocketHandler,
    game_state: QuizifyGameState,
    question: Any,
) -> None:
    """Announce the betting window and arm its deadline (#656).

    The frames are the WagerBroadcaster's (#881); what stays here is the
    one task that guarantees the window ends — with or without the
    players — because the task belongs to the #746 registry.
    """
    await handler._wager_out.send_wager_window(game_state, question)
    handler._start_wager_window(game_state)


def start_wager_window(
    handler: QuizifyWebSocketHandler,
    game_state: QuizifyGameState,
) -> None:
    """Arm the task that closes the betting window at the deadline (#656).

    The deadline is what keeps the final round from hanging on a player who
    never bets — an AFK phone, or a room that all walked out. It is the
    same class of hang as #586, so it gets an unconditional timer rather
    than a condition that depends on clients behaving. The wait itself is
    :class:`~custom_components.quizify.game.drivers.WagerWindowDriver`.
    """
    handler._cancel_wager_window()
    driver = WagerWindowDriver(
        handler._wager_out,
        duration=WAGER_WINDOW_DURATION,
        milestones=handler,
    )
    handler._wager_window_task = asyncio.create_task(driver.run(game_state))


def cancel_wager_window(handler: QuizifyWebSocketHandler) -> None:
    """Cancel the pending betting-window deadline, if any.

    Never cancels the *calling* task. ``_close_wager_window`` clears the
    deadline before doing its work, and the deadline itself is one of its
    two callers — so a blind ``cancel()`` here kills the very coroutine
    that is closing the window, and the CancelledError lands on the first
    await inside ``_emit_question``. The question then never goes out and
    the final round hangs on the betting screen with a countdown reading
    zero. Found on the live server, not by a unit test; there is one for
    it now.
    """
    task = handler._wager_window_task
    handler._wager_window_task = None
    if task is not None and not task.done() and task is not asyncio.current_task():
        task.cancel()


async def close_wager_window(
    handler: QuizifyWebSocketHandler,
    game_state: QuizifyGameState,
) -> None:
    """Close the betting window: arm the timers, then send the question.

    ``arm_round_timers`` returns False when the window is already shut,
    which is what makes the two closing triggers safe to race — the last
    player locking in, and the deadline. Whoever arrives second finds the
    phase already flipped and returns without re-emitting the question.
    """
    handler._cancel_wager_window()
    question = game_state.get_current_question()
    if question is None or not game_state.arm_round_timers():
        return
    await handler._emit_question(game_state, question)


async def emit_question(
    handler: QuizifyWebSocketHandler,
    game_state: QuizifyGameState,
    question: Any,
) -> None:
    """Shuffle, broadcast and arm the timer for an already-started question.

    Split out from ``_start_next_question`` (#285) so the auto-LR fallback
    path can reuse the identical emission without re-running the LR
    trigger check.
    """
    # Canonical shuffle — used by admin/dashboard and as a fallback.
    indices = list(range(len(question.answers)))
    random.shuffle(indices)
    shuffled_texts = [question.answers[i].text for i in indices]
    game_state.set_round_shuffle(indices, shuffled_texts)

    # Per-player shuffles: each phone sees A/B/C in its own order
    # (anti-cheat — couch neighbours can't shout "B!"). The
    # submit_answer path uses player.name to look this up.
    game_state.clear_player_shuffles()
    players_now = game_state.get_players()
    for player in players_now:
        player_indices = list(range(len(question.answers)))
        random.shuffle(player_indices)
        game_state.set_player_shuffle(player.name, player_indices)

    # Send question per-player so each gets their own shuffled order.
    # The builder assembles each payload (own shuffle); the handler still
    # owns the send and the connected-player skip (#189).
    # #742: this flag is what makes the phone render the wager UI, so it
    # has to agree with ``_needs_wager_window`` — with the toggle off the
    # server opens no window, and a client offered a bet it cannot place
    # would sit on a slider nothing accepts.
    is_final = (
        game_state.wager_enabled
        and game_state.round == game_state.total_rounds
    )
    # Build every per-player payload, then fan out in parallel (#258) so a
    # single slow client can't delay the question reaching the rest.
    question_sends = []
    for player in players_now:
        if not player.connected:
            continue
        player_msg = handler._round_messages.build_player_question(
            game_state, question=question, player=player, is_final=is_final
        )
        question_sends.append(handler._conn.send_to_player(player, player_msg))
    if question_sends:
        await asyncio.gather(*question_sends)

    # Send question with correct answer to admin
    admin_msg = handler._round_messages.build_admin_question(
        game_state, question=question
    )
    # Also fan out to TV-dashboard connections so their question-view
    # populates with the same canonical-order payload. Without this the
    # dashboard's answer-grid stays empty and the v1.1.47 #151 answer-
    # distribution bars never attach. Admin-as-player still excluded.
    # The TV connects without a token (#604), so it gets the same payload
    # with the answer taken out — see strip_answer_for_dashboard.
    await handler._conn.broadcast_to_admins_and_dashboards(
        admin_msg, dashboard_message=strip_answer_for_dashboard(admin_msg)
    )

    # ONE question milestone from the dispatch point (#789): narration
    # (#281, with the canonical shuffled order so spoken letters match the
    # TV grid) plus the HA bus event (#366) fan out inside question_shown.
    handler.question_shown(
        question, game_state.round, game_state.total_rounds, shuffled_texts
    )

    # Cache players to avoid redundant calls
    players = game_state.get_players()

    # Notify players who got a power-up this round (parallel fan-out, #258).
    powerup_sends = []
    for player in players:
        powerup = game_state.get_player_powerup(player.name)
        if powerup and player.connected:
            powerup_sends.append(handler._conn.send_to_player(player, {
                "type": "powerup_assigned",
                "powerup_type": powerup.value,
            }))
    if powerup_sends:
        await asyncio.gather(*powerup_sends)

    # Broadcast game state with leaderboard so player sees rankings during game
    await handler._conn.broadcast(
        handler._round_messages.build_game_state_with_leaderboard(
            game_state, players=players
        )
    )

    # Start timer tick task
    handler._start_timer_tick(game_state)


def start_timer_tick(
    handler: QuizifyWebSocketHandler,
    game_state: QuizifyGameState,
) -> None:
    """Arm the normal round's countdown driver (#203/#413/#788).

    The cadence, the per-recipient coalescing, the two stop conditions and
    the auto-evaluate live in
    :class:`~custom_components.quizify.game.drivers.NormalRoundDriver`. This
    loop owns only the task and the wire messages it asks for.
    """
    handler._cancel_timer_tick()
    # ``TICK_INTERVAL`` is read here rather than defaulted inside the
    # driver so the module-level name stays the single knob a test (or a
    # future runtime setting) can turn.
    driver = NormalRoundDriver(
        handler._round_out, milestones=handler, tick_interval=TICK_INTERVAL
    )
    handler._timer_tick_task = asyncio.ensure_future(driver.run(game_state))
    handler._timer_tick_task.add_done_callback(handler._log_task_exception)


def cancel_timer_tick(handler: QuizifyWebSocketHandler) -> None:
    """Cancel the timer tick task — and the betting window with it.

    The two are the same thing from the caller's side: the round's
    background clock. Cancelling both here rather than at each of the
    eight ``_cancel_timer_tick`` call sites (reset, end game, skip,
    pause, disconnect, next question, …) is what guarantees no path can
    leave a window armed over a round that has already moved on (#656).
    """
    if handler._timer_tick_task is not None:
        handler._timer_tick_task.cancel()
        handler._timer_tick_task = None
    handler._cancel_wager_window()


def cancel_task_group(
    handler: QuizifyWebSocketHandler,
    group: tuple[tuple[str, str], ...],
) -> None:
    """Run every canceller in one of the registries above (#746).

    Cancellers are idempotent and safe on a task that was never started,
    so a group can always be run whole. That is the point: the caller
    says *which scope* it is tearing down, never *which tasks*.
    """
    for _attr, canceller in group:
        cancel: Callable[[], None] = getattr(handler, canceller)
        cancel()


def cancel_round_tasks(handler: QuizifyWebSocketHandler) -> None:
    """Tear down EVERY round-scoped task (#746).

    The one place that owns "the round is over": end game, reset,
    play again, start game, and the full cleanup all route through here
    rather than listing cancellers themselves. A sixth task added to
    ``_ROUND_SCOPED_TASKS`` is therefore stopped by all of them at once.

    Paths that deliberately stop only PART of a round — ``_advance_round``
    settling one detour, ``admin_action_pause`` freezing the clock while
    the round stays alive, ``_handle_admin_skip`` cutting a question short
    — keep calling the individual cancellers, with a comment saying why.
    A deliberate subset should read as one; only a *complete* teardown
    belongs here.
    """
    handler._cancel_task_group(handler._ROUND_SCOPED_TASKS)


def maybe_evaluate_after_dropout(
    handler: QuizifyWebSocketHandler,
    game_state: QuizifyGameState,
) -> bool:
    """Auto-evaluate the live round if a dropout left everyone submitted (#412).

    ``all_submitted()`` is normally only consulted inside
    submit_answer/submit_guess. When the last unanswered player disconnects
    (or is removed after the grace timeout) mid-question, that check never
    re-runs, so a room where everyone else already answered would sit idle
    until the timer expired. Re-test it here: during QUESTION_ACTIVE, if the
    registry now reports all active participants submitted, stop the
    countdown and evaluate. ``evaluate_round`` fires the
    ``round_evaluated`` state event, which drives the reveal broadcast (same
    as the timer-expiry and admin-skip paths); it is guarded against double
    evaluation, so racing with the tick loop is safe.

    Returns True when it triggered an evaluation.
    """
    if game_state.phase != GamePhase.QUESTION_ACTIVE:
        return False
    if not game_state.all_submitted():
        return False
    handler._cancel_timer_tick()
    game_state.evaluate_round()
    return True


def schedule_admin_pause(handler: QuizifyWebSocketHandler, admin_name: str) -> None:
    """Schedule a pause to fire ADMIN_REDIRECT_GRACE seconds from now.

    If admin reconnects (via session token or fresh join) before the
    timer expires, _cancel_admin_pause clears the task. Re-entrant:
    cancels any prior pending task before scheduling a new one, so a
    rapid disconnect-reconnect-disconnect doesn't stack tasks.
    """
    handler._cancel_admin_pause()

    async def pause_after_grace() -> None:
        try:
            await asyncio.sleep(handler.ADMIN_REDIRECT_GRACE)
            gs = handler._get_game_state()
            if gs is None:
                return
            # Final sanity checks before pausing — admin may have
            # reconnected via a path that doesn't cancel us, or the
            # game may have advanced past QUESTION_ACTIVE on its own.
            player = gs.get_player(admin_name)
            if player and player.connected:
                _LOGGER.debug(
                    "Admin %s reconnected within grace — skipping pause",
                    admin_name,
                )
                return
            if gs.phase != GamePhase.QUESTION_ACTIVE:
                return
            if not gs.pause(reason="admin_disconnected"):
                return
            handler._cancel_timer_tick()
            state = handler._snapshot(gs)
            state["type"] = "game_state"
            await handler._conn.broadcast(state)
            _LOGGER.info(
                "Paused game after admin %s failed to reconnect within %.1fs",
                admin_name,
                handler.ADMIN_REDIRECT_GRACE,
            )
        except asyncio.CancelledError:
            # Normal path: admin reconnected in time. Don't log.
            pass

    handler._admin_pause_task = asyncio.ensure_future(pause_after_grace())
    # Surface a crash in the deferred-pause loop (#362/#307) instead of
    # letting "Task exception was never retrieved" leak at GC time.
    handler._admin_pause_task.add_done_callback(handler._log_task_exception)


def cancel_admin_pause(handler: QuizifyWebSocketHandler) -> None:
    """Cancel any pending deferred admin-disconnect pause."""
    if handler._admin_pause_task and not handler._admin_pause_task.done():
        handler._admin_pause_task.cancel()
    handler._admin_pause_task = None
