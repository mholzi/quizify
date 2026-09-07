"""One small fan-out object per driver protocol (#881).

#788 moved the game loops out of ``QuizifyWebSocketHandler`` and gave each
driver a narrow ``Broadcaster`` contract to talk through. The object actually
handed to every driver was still the handler itself, so the contract existed
only as a type annotation: a driver held ~150 methods, and a test that wanted
to drive one mode against a fake had to re-stub whichever handler methods that
mode happened to touch.

This module holds the other half of #788. Each class here implements exactly
one of the protocols in
:mod:`custom_components.quizify.game.drivers.protocols`, owns the frame
literals for its mode, and knows nothing except a
:class:`~custom_components.quizify.server.connection.ConnectionManager` and the
:class:`~custom_components.quizify.server.round_message_builder.RoundMessageBuilder`.
The handler now *holds* these instead of *being* them.

Two protocol methods are not fan-out at all — ``resume_normal_question`` (the
hot seat's escape hatch when nobody bids) and ``close_wager_window`` (arm the
timers, then emit the question). They drive the round rather than describing
it, so they stay on the handler and are injected here as callbacks; the driver
still sees one object satisfying one protocol.

Behaviour-preserving: every payload below is the byte-for-byte dict the handler
used to build, sent to the same recipients over the same connection-manager
call. The comments that explained *why* a frame is shaped the way it is moved
with it — they are the reason the shape must not drift.

Connection managers are resolved through a provider rather than captured once,
because a large part of the suite swaps ``handler._conn`` after construction
(``handler._conn = ConnectionManager(...)``, or a fake). A broadcaster
constructed directly in a test can pass the manager itself; the handler passes
``lambda: self._conn`` so a swapped manager is picked up on the next send.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any

from custom_components.quizify.const import WAGER_WINDOW_DURATION
from custom_components.quizify.game.hot_seat import stake_of as hot_seat_stake
from custom_components.quizify.game.team import (
    ANSWER_CHANGE_LOCK_SECONDS as LIGHTNING_ANSWER_LOCK_SECONDS,
)
from custom_components.quizify.server.serializers import serialize_leaderboard

if TYPE_CHECKING:
    from aiohttp import web

    from custom_components.quizify.game.state import QuizifyGameState, TeamAnswerAck
    from custom_components.quizify.server.connection import ConnectionManager
    from custom_components.quizify.server.round_message_builder import (
        RoundMessageBuilder,
    )

__all__ = [
    "HotSeatBroadcaster",
    "LightningBroadcaster",
    "RoundBroadcaster",
    "WagerBroadcaster",
]


class _Broadcaster:
    """Shared plumbing: how to reach the wire, and how to build round frames."""

    def __init__(
        self,
        conn: ConnectionManager | Callable[[], ConnectionManager],
        messages: RoundMessageBuilder,
    ) -> None:
        self._conn_source = conn
        self._messages = messages

    @property
    def _conn(self) -> ConnectionManager:
        """The connection manager to send over, resolved per call."""
        source = self._conn_source
        if callable(source):
            return source()
        return source


# ---------------------------------------------------------------------------
# Lightning Round (#42/#201/#285/#552)
# ---------------------------------------------------------------------------


class LightningBroadcaster(_Broadcaster):
    """The Lightning Round's fan-out points.

    Satisfies
    :class:`custom_components.quizify.game.drivers.protocols.LightningBroadcaster`
    — plus the two frames the *message handlers* (not the driver) need, which
    live here for the same reason: they are lightning frames, and one object
    per mode is easier to keep honest than six frames spread over a 5k-line
    file.
    """

    async def send_lightning_splash(self, game_state: QuizifyGameState) -> None:
        """Fan out the intro-splash payload (rules preview) to all clients."""
        lr = game_state.lightning
        if lr is None:
            return
        await self._conn.broadcast({
            "type": "lightning_splash",
            "num_questions": lr.num_questions,
            "seconds_per_question": lr.seconds_per_question,
        })

    async def send_lightning_question(
        self, game_state: QuizifyGameState, lr: Any
    ) -> None:
        """Send the current lightning question per-player (own shuffle) and
        to admin/dashboard (canonical order)."""
        q = lr.current_question
        if q is None:
            return
        # Fan out the per-player lightning question in parallel (#258).
        lightning_sends = []
        for player in game_state.get_players():
            if not player.connected:
                continue
            lightning_sends.append(self._conn.send_to_player(player, {
                "type": "lightning_question",
                "question_text": q.question,
                "answers": lr.shuffled_answers_for(player.name),
                "index": lr.index,
                "num_questions": lr.num_questions,
                "seconds": lr.seconds_per_question,
                "category": q.category,
                "image_url": q.image_url,
            }))
        if lightning_sends:
            await asyncio.gather(*lightning_sends)
        await self._conn.broadcast_to_admins_and_dashboards({
            "type": "lightning_question",
            "question_text": q.question,
            "answers": [a.text for a in q.answers],
            "index": lr.index,
            "num_questions": lr.num_questions,
            "seconds": lr.seconds_per_question,
            "category": q.category,
            "image_url": q.image_url,
        })

    async def send_lightning_tick(
        self, game_state: QuizifyGameState, lr: Any
    ) -> None:
        """Push the shared lightning countdown."""
        remaining = round(lr.time_remaining(), 1)
        await self._conn.broadcast({
            "type": "lightning_tick",
            "remaining": remaining,
            "index": lr.index,
        })

    async def send_lightning_recap(self, game_state: QuizifyGameState) -> None:
        """Push the end-of-mode recap."""
        lr = game_state.lightning
        if lr is None:
            return
        await self._conn.broadcast({
            "type": "lightning_recap",
            "recap": lr.build_recap(),
        })

    async def send_lightning_answer_result(
        self, ws: web.WebSocketResponse, lr: Any, player_name: str, *, correct: bool
    ) -> None:
        """Lightweight ack: lock the player's buttons + show right/wrong."""
        await self._conn.send(ws, {
            "type": "lightning_answer_result",
            "correct": correct,
            "index": lr.index,
            "score": lr.score_for(player_name),
        })

    async def send_lightning_team_answer(
        self, game_state: QuizifyGameState, lr: Any, setter: str
    ) -> None:
        """Show the team's standing lightning answer on every member's phone.

        Mirrors the normal round's ``team_answer`` (#365): the index is
        remapped per member, because each phone shuffles the answers for
        itself — one number sent to the whole team would highlight the wrong
        row for everybody but the person who tapped.
        """
        standing = lr.standing_answer(setter)
        if standing is None or standing.answer_index is None:
            return
        members = lr.members_of(setter)
        for name in members:
            member = game_state.get_player(name)
            if member is None or not member.connected:
                continue
            order = lr.ensure_shuffle(name)
            try:
                shown_index = order.index(standing.answer_index)
            except ValueError:
                continue
            await self._conn.send_to_player(member, {
                "type": "lightning_team_answer",
                "index": lr.index,
                "answer_index": shown_index,
                "set_by": setter,
                "members": list(members),
                "lock_seconds": LIGHTNING_ANSWER_LOCK_SECONDS,
            })


# ---------------------------------------------------------------------------
# Hot Seat (#616/#804/#833)
# ---------------------------------------------------------------------------


class HotSeatBroadcaster(_Broadcaster):
    """The Hot Seat's fan-out points.

    Satisfies
    :class:`custom_components.quizify.game.drivers.protocols.HotSeatBroadcaster`.
    ``resume_normal_question`` is the one member that is not a broadcast — the
    mode's escape hatch when nobody bids — so it is injected as a callback and
    only forwarded here.
    """

    def __init__(
        self,
        conn: ConnectionManager | Callable[[], ConnectionManager],
        messages: RoundMessageBuilder,
        *,
        resume_normal_question: Callable[[QuizifyGameState], Awaitable[None]],
    ) -> None:
        super().__init__(conn, messages)
        self._resume_normal_question = resume_normal_question

    async def send_hot_seat_auction(
        self, game_state: QuizifyGameState, hs: Any
    ) -> None:
        """Open the auction: the room's clock, then each phone's own purse."""
        await self._conn.broadcast({
            "type": "hot_seat_auction",
            "seconds": hs.auction_seconds,
            "players": len(hs.scores),
            # #698: the television's round indicator is interpolated from
            # these two. Without them the auction kept the previous round's
            # number and the question that follows printed the literal string
            # "undefined" for the whole answer window.
            "round_num": game_state.round,
            "total_rounds": game_state.total_rounds,
        })
        # Each player needs their own number: a percentage is only meaningful
        # next to the points it costs *them*. In team mode that is the team's
        # score (#804) — ``player.score`` there is the shadow value #669 gated
        # the mode off for, and a slider priced against it costs nothing.
        sends = []
        for player in game_state.get_players():
            if not player.connected:
                continue
            sends.append(self._conn.send_to_player(player, {
                "type": "hot_seat_auction_you",
                "score": hs.scores.get(hs.entrant_for(player.name), 0),
                "seconds": hs.auction_seconds,
            }))
        if sends:
            await asyncio.gather(*sends, return_exceptions=True)

    async def send_hot_seat_bid_accepted(
        self, ws: web.WebSocketResponse, hs: Any, player_name: str, *, pct: int
    ) -> None:
        """Confirm one sealed bid to its bidder, then tell the room the count."""
        await self._conn.send(ws, {
            "type": "hot_seat_bid_accepted",
            "bid": pct,
            "points": hot_seat_stake(
                hs.scores.get(hs.entrant_for(player_name), 0), pct
            ),
        })
        # Blind auction: the room learns how many have bid, never how much.
        await self._conn.broadcast({
            "type": "hot_seat_bid_count",
            "count": len(hs.bids),
            "total": len(hs.scores),
        })

    async def send_hot_seat_bet_accepted(
        self,
        ws: web.WebSocketResponse,
        hs: Any,
        player_name: str,
        *,
        side: Any,
        pct: int,
    ) -> None:
        """Confirm a spectator's stake on the seat holder."""
        await self._conn.send(ws, {
            "type": "hot_seat_bet_accepted",
            "side": side,
            "bet": pct,
            "points": hot_seat_stake(
                hs.scores.get(hs.entrant_for(player_name), 0), pct
            ),
        })

    async def send_hot_seat_answer_accepted(
        self, ws: web.WebSocketResponse
    ) -> None:
        """Acknowledge the seat holder's single answer."""
        await self._conn.send(ws, {"type": "hot_seat_answer_accepted"})

    async def send_hot_seat_tick(self, stage: str, remaining: int) -> None:
        """Push the countdown for ``"auction"`` or ``"question"``."""
        await self._conn.broadcast({
            "type": "hot_seat_tick",
            "phase": stage,
            "remaining": remaining,
        })

    async def send_hot_seat_no_bids(self) -> None:
        """Tell the room nobody wanted the chair."""
        await self._conn.broadcast({"type": "hot_seat_no_bids"})

    async def send_hot_seat_awarded(
        self, game_state: QuizifyGameState, hs: Any
    ) -> None:
        """Break the seal: who paid what, and who is sitting down."""
        await self._conn.broadcast({
            "type": "hot_seat_awarded",
            # The PERSON taking the chair. Outside team mode that is also the
            # entrant, so this field keeps its old meaning for every client;
            # ``entrant`` names who pays (#804).
            "winner": hs.seat_holder,
            "entrant": hs.winner_name,
            "pct": hs.winning_pct,
            "stake": hs.winning_stake,
            "bids": hs.reveal(),
        })

    async def send_hot_seat_question(
        self, game_state: QuizifyGameState, hs: Any
    ) -> None:
        """Send the question: shuffled to the seat holder, canonical to the room.

        The spectators get the question text and the betting controls but no
        answer buttons — they are not answering it, they are staking on
        whoever is.
        """
        q = hs.question
        if q is None:
            return
        payload = {
            "type": "hot_seat_question",
            "question": q.question,
            "difficulty": q.difficulty,
            # #698: see the auction broadcast — the TV interpolates both.
            "round_num": game_state.round,
            "total_rounds": game_state.total_rounds,
            "image_url": getattr(q, "image_url", "") or "",
            "seconds": hs.answer_seconds,
            "winner": hs.seat_holder,
            "entrant": hs.winner_name,
        }
        sends = []
        for player in game_state.get_players():
            if not player.connected:
                continue
            if player.name == hs.seat_holder:
                sends.append(self._conn.send_to_player(player, {
                    **payload,
                    "answers": hs.shuffled_answers(),
                    "you_are_seated": True,
                }))
            else:
                sends.append(self._conn.send_to_player(player, {
                    **payload,
                    "answers": [],
                    "you_are_seated": False,
                    # A teammate of the seat holder may not bet: they stake the
                    # purse the chair already staked (#804). Told here so the
                    # phone shows why instead of a slider nothing accepts.
                    "you_are_seat_team": hs.is_on_seat_team(player.name),
                    "score": hs.scores.get(hs.entrant_for(player.name), 0),
                }))
        if sends:
            await asyncio.gather(*sends, return_exceptions=True)
        # The room watches the same board the seat holder does, so the TV gets
        # the *shuffled* order rather than the canonical one — which also keeps
        # #521 shut, where JSON order put the correct tile first in half the
        # packs. Admins additionally get the correct index; dashboards take no
        # token (#604) and must not learn it before the reveal.
        tv_payload = {**payload, "answers": hs.shuffled_answers()}
        await self._conn.broadcast_to_admins_and_dashboards(
            {**tv_payload, "correct_index": hs.correct_index},
            dashboard_message=tv_payload,
        )

    async def send_hot_seat_result(
        self, game_state: QuizifyGameState, hs: Any
    ) -> None:
        """Push the settlement."""
        await self._conn.broadcast({
            "type": "hot_seat_result",
            "round_num": game_state.round,
            "total_rounds": game_state.total_rounds,
            **hs.summary(),
            # The rows the room can see (#804): teams in team mode, players
            # otherwise. Keyed by ``player.name`` this reported the shadow
            # scores the settlement no longer writes to.
            "scores": {
                participant.name: participant.score
                for participant in game_state.get_ranked_participants()
            },
            # #833: the standings AFTER the settlement, in the shape every
            # board already renders. ``scores`` above is a name→number map —
            # no rank, no entrant id, nothing a leaderboard row is built from —
            # so the one screen the whole room reads had no way to repaint and
            # kept showing the player who had just lost everything in first
            # place. ``finish_hot_seat`` has already applied the deltas by the
            # time this runs, so these are the real numbers.
            "leaderboard": serialize_leaderboard(
                game_state.get_ranked_participants()
            ),
        })

    async def resume_normal_question(self, game_state: QuizifyGameState) -> None:
        """Leave the detour and start an ordinary question instead."""
        await self._resume_normal_question(game_state)


# ---------------------------------------------------------------------------
# Normal round (#203/#365/#413)
# ---------------------------------------------------------------------------


class RoundBroadcaster(_Broadcaster):
    """The normal round's fan-out points.

    Satisfies
    :class:`custom_components.quizify.game.drivers.protocols.RoundBroadcaster`,
    and additionally owns the round's other frames — the summary and the two
    team-mode ones — which are fan-out with no rules attached.
    """

    async def send_timer_tick(
        self,
        game_state: QuizifyGameState,
        remaining_by_player: dict[str, float],
        dashboard_remaining: float | None,
    ) -> None:
        """Turn one driver tick into ``timer_tick`` frames.

        The driver has already dropped every recipient whose displayed second
        did not change (#413) and every disconnected player, so this only has
        to address what is left. Built as one fan-out and delivered in parallel
        (#258) so a single stalled client can't delay the room; the dashboard
        copy is pre-serialized ONCE and goes out over the broadcast string path
        (admin-as-player is already excluded there).
        """
        sends = []
        if remaining_by_player:
            by_name = {p.name: p for p in game_state.get_players()}
            for name, remaining in remaining_by_player.items():
                player = by_name.get(name)
                if player is None:
                    continue
                sends.append(self._conn.send_to_player(player, {
                    "type": "timer_tick",
                    "remaining": round(remaining, 1),
                }))
        if dashboard_remaining is not None:
            sends.append(
                self._conn.broadcast_to_admins_and_dashboards({
                    "type": "timer_tick",
                    "remaining": round(dashboard_remaining, 1),
                })
            )
        if sends:
            await asyncio.gather(*sends)

    async def send_round_summary(self, game_state: QuizifyGameState) -> None:
        """Broadcast round summary to all clients.

        Payload assembly (correct-index resolution, the per-player answer
        table, the summary serialization) lives in the RoundMessageBuilder
        (#189); this keeps ownership of the broadcast. ``None`` means there is
        no round summary yet — same no-op as before.
        """
        summary_msg = self._messages.build_round_summary(game_state)
        if summary_msg is None:
            return
        await self._conn.broadcast(summary_msg)

    async def send_teams_update(self, game_state: QuizifyGameState) -> None:
        """Tell the room who is playing with whom.

        Sent uncoalesced, unlike the roster: opening a team has to appear on
        the other phones *now*, because the next thing that happens is someone
        looking for it in the list. It is also what makes a join land on the
        founder's screen — without it she cannot tell whether it worked.
        """
        await self._conn.broadcast({
            "type": "teams_update",
            "teams": game_state.team_registry.to_list(),
        })

    async def send_team_answer(
        self,
        game_state: QuizifyGameState,
        ack: TeamAnswerAck,
        *,
        setter: str,
    ) -> None:
        """Show the standing answer on every member's phone (#365).

        The index is remapped per member: every player sees the answers in
        their own shuffled order (#253), so sending one number to the whole
        team would put the dots on the wrong row for everyone but the setter.
        """
        team = game_state.team_registry.get(ack.team_id)
        if team is None:
            return
        for name in team.members:
            member = game_state.get_player(name)
            if member is None or not member.connected:
                continue
            shuffle = game_state.get_player_shuffle(name)
            try:
                shown_index = shuffle.index(ack.answer_index)
            except ValueError:
                # No shuffle stored for this member yet (they joined between
                # the question start and this tap). Their client re-reads the
                # answer from the next projected snapshot.
                continue
            await self._conn.send_to_player(member, {
                "type": "team_answer",
                "team_id": ack.team_id,
                "answer_index": shown_index,
                "set_by": setter,
                # The lock belongs to the team, not to the person who tapped:
                # every member's buttons go quiet for the same two seconds,
                # which is what stops the tap war rather than slowing one side.
                "lock_seconds": ack.lock_seconds,
                "members": list(team.members),
            })


# ---------------------------------------------------------------------------
# Wager window (#656)
# ---------------------------------------------------------------------------


class WagerBroadcaster(_Broadcaster):
    """The betting window's fan-out.

    Satisfies
    :class:`custom_components.quizify.game.drivers.protocols.WagerBroadcaster`.
    Like the hot seat's escape hatch, ``close_wager_window`` is a round
    transition rather than a frame — it arms the timers and emits the question
    — so it is injected and forwarded.
    """

    def __init__(
        self,
        conn: ConnectionManager | Callable[[], ConnectionManager],
        messages: RoundMessageBuilder,
        *,
        close: Callable[[QuizifyGameState], Awaitable[None]],
    ) -> None:
        super().__init__(conn, messages)
        self._close = close

    async def send_wager_window(
        self, game_state: QuizifyGameState, question: Any
    ) -> None:
        """Announce the betting window.

        Sends every phone its own bank and gives the host/TV the lock-in
        tally. Arming the deadline is the handler's job — the task lives in
        the #746 registry, not here.
        """
        players = game_state.get_players()
        sends = [
            self._conn.send_to_player(
                player,
                self._messages.build_wager_window(
                    game_state,
                    question=question,
                    player=player,
                    window_duration=WAGER_WINDOW_DURATION,
                ),
            )
            for player in players
            if player.connected
        ]
        if sends:
            await asyncio.gather(*sends)

        await self._conn.broadcast_to_admins_and_dashboards(
            self._messages.build_wager_progress(
                game_state, window_duration=WAGER_WINDOW_DURATION
            )
        )
        # Phase broadcast last: the phones already hold the window payload, so
        # a client driving off the phase alone (reconnect path) lands on a view
        # it can render rather than an empty one.
        await self._conn.broadcast(
            self._messages.build_game_state_with_leaderboard(
                game_state, players=players
            )
        )

    async def close_wager_window(self, game_state: QuizifyGameState) -> None:
        """The deadline passed: arm the round timers and send the question."""
        await self._close(game_state)
