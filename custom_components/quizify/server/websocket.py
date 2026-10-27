"""WebSocket handler for Quizify real-time communication."""

from __future__ import annotations

import asyncio
import ipaddress
import logging
from functools import cached_property
from typing import TYPE_CHECKING, Any

from aiohttp import WSMsgType, web

from custom_components.quizify.const import (
    ERR_ADMIN_REQUIRED,
    ERR_GAME_NOT_STARTED,
    ERR_INVALID_ACTION,
    ERR_JOIN_RATE_LIMITED,
    LOBBY_DISCONNECT_GRACE_PERIOD,
    MAX_PLAYERS,
)
from custom_components.quizify.game.state import (
    GamePhase,
    QuizifyGameState,
    RoundSummary,
)
from custom_components.quizify.server import (
    admin_auth,
    admin_claim,
    answer_handlers,
    batched_updates,
    game_broadcasts,
    game_commands,
    hot_seat_handlers,
    join_handlers,
    lightning_handlers,
    round_flow,
    settings_handlers,
    wager_handlers,
)
from custom_components.quizify.server.broadcast_dispatcher import BroadcastDispatcher
from custom_components.quizify.server.broadcasters import (
    HotSeatBroadcaster,
    LightningBroadcaster,
    RoundBroadcaster,
    WagerBroadcaster,
)
from custom_components.quizify.server.connection import ConnectionManager
from custom_components.quizify.server.origin import reject_cross_origin
from custom_components.quizify.server.rate_limit import SlidingWindowLimiter
from custom_components.quizify.server.round_message_builder import RoundMessageBuilder
from custom_components.quizify.server.serializers import (
    serialize_state_snapshot,
)
from custom_components.quizify.server.start_settings import (
    _START_GAME_FIELDS,
    _coerce_toggle,  # noqa: F401 - re-exported, tests import it from here
    _overrides_to_start_settings,
    _preset_to_start_settings,
)

if TYPE_CHECKING:
    from collections.abc import Callable

    from ..analytics import PlayerStanding
    from ..game.player import PlayerSession
    from ..game.questions import Question
    from ..game_events import QuizifyEventEmitter
    from ..lights import QuizifyPartyLights
    from ..runtime import Runtime
    from ..sound_effects import QuizifySoundEffects
    from ..tts import QuizifyTTSAnnouncer

_LOGGER = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Client → server message-type constants (#363)
# ---------------------------------------------------------------------------
# Single source of truth for the wire strings the dispatch table keys on, so
# a typo is a NameError at import time instead of a silently-dead branch.
# ``admin_connect`` and ``reset_game`` live OUTSIDE the dispatch table (special
# authorization paths) but are named here for completeness.
# ``server/protocol.py::CLIENT_MESSAGE_TYPES`` declares the same set for the
# client side; ``tests/test_protocol.py`` compares it against ``_DISPATCH``
# below, so the two cannot drift apart unnoticed (#749).
MSG_ADMIN_CONNECT = "admin_connect"
MSG_RESET_GAME = "reset_game"
MSG_JOIN = "join"
MSG_RECONNECT = "reconnect"
MSG_GET_STATE = "get_state"
MSG_SUBMIT_ANSWER = "submit_answer"
MSG_SUBMIT_WAGER = "submit_wager"
MSG_REACTION = "reaction"
MSG_USE_POWERUP = "use_powerup"
MSG_LIGHTNING_ANSWER = "lightning_answer"
MSG_HOT_SEAT_BID = "hot_seat_bid"
MSG_HOT_SEAT_BET = "hot_seat_bet"
MSG_HOT_SEAT_ANSWER = "hot_seat_answer"
MSG_START_GAME = "start_game"
MSG_NEXT_QUESTION = "next_question"
MSG_NEXT_ROUND = "next_round"
MSG_ADMIN_SKIP = "admin_skip"
MSG_END_GAME = "end_game"
MSG_PLAY_AGAIN = "play_again"
MSG_PAUSE_GAME = "pause_game"
MSG_RESUME_GAME = "resume_game"
MSG_KICK_PLAYER = "kick_player"
MSG_CONFIGURE_TTS = "configure_tts"
MSG_CONFIGURE_HOUSE = "configure_house"
MSG_SET_LANGUAGE = "set_language"
MSG_SET_DIFFICULTY = "set_difficulty"

# Admin messages that require WS-level admin (a real ?role=admin tab) rather
# than the admin-as-player relaxation ``_is_authorized_admin`` allows. These
# two reach Home Assistant service calls with host-supplied entity ids (#724).
_WS_ADMIN_ONLY = frozenset({MSG_CONFIGURE_TTS, MSG_CONFIGURE_HOUSE})
MSG_CREATE_TEAM = "create_team"
MSG_JOIN_TEAM = "join_team"
MSG_LEAVE_TEAM = "leave_team"


class QuizifyWebSocketHandler:
    """Handle WebSocket connections for Quizify."""

    HEARTBEAT_INTERVAL = 30

    # Per-IP WebSocket connection cap (#361). The existing flood guard is
    # per-connection (keyed on id(ws)), so opening N sockets bypasses it
    # entirely. This caps the number of *concurrent* sockets a single
    # ``request.remote`` may hold. It is deliberately GENEROUS: in the common
    # Quizify deployment every player is a distinct phone on the same wifi
    # hitting HA directly, so each device has its own LAN IP and one device
    # never legitimately needs anywhere near this many sockets (a page reload
    # or reconnect briefly overlaps two, no more). The cap only bites a single
    # host opening a flood of sockets. (If HA sits behind a reverse proxy that
    # collapses every client to one source IP without X-Forwarded-For, raise
    # this — but that is the uncommon setup for a local party game.) Refused
    # connections get an HTTP 429 before the WebSocket is upgraded.
    MAX_CONNECTIONS_PER_IP = 15

    # Largest WebSocket frame we will buffer, let alone parse (#786). Without
    # it aiohttp's 4 MiB default applies to every unauthenticated player and
    # dashboard socket, and the flood guard below counts *frames*, not bytes:
    # 15 frames/s x 4 MiB is 60 MiB of JSON per second per socket, each
    # ``msg.json()`` of which runs synchronously on Home Assistant's shared
    # event loop. Real Quizify frames are a few hundred bytes; the largest
    # legitimate one is ``start_game`` with its tts/house blocks, comfortably
    # under a kilobyte. aiohttp closes an oversized frame with 1009 before the
    # payload is buffered or parsed.
    MAX_MSG_SIZE = 16 * 1024

    # Loopback exception to the cap above (#701). Over Nabu Casa every remote
    # client reaches HA through snitun on 127.0.0.1, and HA's forwarded-header
    # middleware deliberately ignores X-Forwarded-For for cloud requests — so
    # every phone, the television and the admin share one source IP and the
    # generous-looking 15 became "the room is full at thirteen players". A
    # loopback address is never a stranger's flood (it is either the cloud
    # tunnel or something already running on this host), so the cap there only
    # needs to bound resources: a full room, plus the reload overlap of every
    # phone at once, plus the television and the admin.
    MAX_CONNECTIONS_PER_LOOPBACK = MAX_PLAYERS * 2 + 5

    # Admin-as-player redirect grace: when the admin clicks "Spiel starten"
    # from /quizify/admin, admin.js navigates the tab to /quizify/player so
    # the admin can answer questions. That navigation closes the admin's
    # player WS for ~1-2 seconds before a new player WS reconnects with the
    # session token. Without this grace period, _handle_disconnect would
    # immediately pause the game with reason "admin_disconnected" — every
    # other player's screen would flash "Lost connection to the host" for
    # the duration of the redirect. With the grace, the redirect completes
    # quietly and the game keeps running.
    #
    # 4 seconds covers: page load + i18n init + WS connect + reconnect
    # round-trip, with margin. If admin genuinely disconnects (closed tab,
    # lost wifi), the pause still fires after 4s — only the spurious
    # redirect-driven flash is suppressed.
    ADMIN_REDIRECT_GRACE = 4.0

    # Grace before round 1's timer starts after a game/restart. The admin-as-
    # player flow redirects the admin tab from /quizify/admin to /quizify/player
    # after start_game; that navigation + WS reconnect + i18n init costs
    # ~1.5-2.5s, during which the server timer is already ticking. Without this
    # buffer the admin lands on round 1 with ~25s left on a 30s timer. Applied
    # at both start_game (L3) and finale→new-game (L73) — identical reason.
    START_REDIRECT_GRACE = 2.5

    # Lightning round (#42/#201): the intro splash ("Bolt Burst") shows on the
    # host/TV + every phone before question 1. LIGHTNING_SPLASH_GRACE lets
    # clients swap from the splash to the question view before the first clock
    # starts ticking, so the countdown the players see matches the server window.
    LIGHTNING_SPLASH_GRACE = 1.0

    # Auto Lightning Round (#285): with the host-manual "Start" tap retired,
    # the intro splash holds for this long on its own before question 1 so
    # players get the "Lightning Round incoming!" beat to read it, then the
    # loop auto-dismisses the splash and begins.
    AUTO_LIGHTNING_SPLASH_HOLD = 3.0

    # Hot Seat auction (#616): the sealed bids land together on the TV.
    # This hold is the beat between the reveal and the question — without
    # it the room never sees who paid what before the chair starts
    # answering, which is the whole moment the auction buys.
    HOT_SEAT_REVEAL_HOLD = 4.0

    # ------------------------------------------------------------------
    # Task ownership registry (#746)
    # ------------------------------------------------------------------
    # Five round-scoped tasks used to be cancelled by hand at ~20 call sites,
    # and the resulting matrix was asymmetric: reset/play_again/start_game/
    # cleanup cancelled four, _advance_round two, and end_game exactly one —
    # so a lightning question was still scored after the finale had been
    # broadcast. That was the fifth repeat of one shape (#362, #407, #656,
    # #671): a new task is added, and one teardown path is forgotten.
    #
    # These two tuples are the single source of truth for "who owns what".
    # ``_cancel_round_tasks()`` walks the first, ``cleanup_game_tasks()``
    # walks both, and every teardown path goes through one of those two
    # instead of listing cancellers itself. A task added to __init__ but to
    # neither tuple fails ``tests/test_round_task_teardown_746.py`` — the
    # registry is checked against the annotated ``asyncio.Task | None``
    # attributes, so "forgot a call site" is no longer expressible.
    #
    # Round-scoped: anything that drives, times, or interrupts THE CURRENT
    # ROUND. It must not survive a round ending, a game ending, or a reset.
    _ROUND_SCOPED_TASKS: tuple[tuple[str, str], ...] = (
        ("_timer_tick_task", "_cancel_timer_tick"),
        ("_wager_window_task", "_cancel_wager_window"),
        ("_lightning_task", "_cancel_lightning_loop"),
        ("_hot_seat_task", "_cancel_hot_seat_loop"),
        ("_admin_pause_task", "_cancel_admin_pause"),
    )

    # Deliberately NOT round-scoped: broadcast coalescers. They batch frames
    # over a few hundred milliseconds and are keyed to nothing in the round,
    # so a round boundary is not a reason to drop a pending flush. Only the
    # full ``cleanup_game_tasks()`` (integration unload / dev-server shutdown)
    # stops them.
    #
    # The host-presence announcement (#848) belongs here for the same reason:
    # whether anybody is hosting the room is a property of the connections,
    # not of the round, and a round boundary is the last moment to drop the
    # frame that arms the guests' escape hatch.
    _CONNECTION_SCOPED_TASKS: tuple[tuple[str, str], ...] = (
        ("_reaction_flush_task", "_cancel_reaction_flush"),
        ("_roster_flush_task", "_cancel_roster_flush"),
        ("_progress_flush_task", "_cancel_progress_flush"),
        ("_host_presence_task", "_cancel_host_presence"),
    )

    def __init__(
        self,
        runtime: Runtime,
        game_state_provider: Callable[[], QuizifyGameState | None],
    ) -> None:
        """Initialize handler."""
        self._runtime = runtime
        self._get_game_state = game_state_provider
        self._conn = ConnectionManager(runtime, game_state_provider)
        # Public alias for the connection manager so collaborators (e.g.
        # __init__.py setup/teardown) use a contract-bearing accessor instead
        # of reaching into the private ``_conn`` attribute. Backed by
        # ``_conn`` so test fixtures that assign ``handler._conn`` still work.
        self._timer_tick_task: asyncio.Task | None = None
        # Closes the final round's betting window at its deadline (#656).
        # Cancelled together with the tick task, so every path that stops a
        # round (reset, end, skip, next question) stops this too.
        self._wager_window_task: asyncio.Task | None = None
        # Deferred-pause task scheduled by _handle_disconnect when the
        # admin-as-player WS closes mid-question. Cancelled by reconnect
        # or join when admin comes back within ADMIN_REDIRECT_GRACE.
        self._admin_pause_task: asyncio.Task | None = None
        # Last value of the host-presence flag actually put on the wire (#842),
        # so the announcer broadcasts transitions rather than every socket
        # event. None until the first announcement.
        self._host_presence_sent: bool | None = None
        # The departure announcement (#848). It cannot be awaited where it is
        # raised — see ``_announce_host_presence_soon`` — so it runs as its own
        # task and is held here for the same reason every other fire-and-forget
        # task in this file is: so it is not garbage-collected mid-flight.
        self._host_presence_task: asyncio.Task | None = None
        # Drives the fast lightning-round loop (issue #42). Distinct from
        # the normal per-question tick task so the two modes can't fight.
        self._lightning_task: asyncio.Task | None = None
        self._hot_seat_task: asyncio.Task | None = None
        # Optional TTS announcer. Set by __init__.py / dev_server after
        # construction so the handler doesn't have to know about HA
        # services. Calling announce_milestone on None is the no-op path.
        self._tts_announcer: QuizifyTTSAnnouncer | None = None
        # Optional HA event-bus emitter (#366). Set by __init__.py after
        # construction so the handler stays HA-agnostic. Firing on None is the
        # no-op path (standalone dev server).
        self._event_emitter: QuizifyEventEmitter | None = None
        # Optional "House Plays Along" consumers (#494 Phase 4). Set by
        # __init__.py after construction, like the announcer/emitter above.
        # The handler never *drives* these (party lights and SFX react to the
        # game state / bus events on their own) — it only holds them so the
        # admin panel's ``configure_house`` message can push runtime overrides
        # onto them. None on the standalone dev server → configure is a no-op.
        self._party_lights: QuizifyPartyLights | None = None
        self._sound_effects: QuizifySoundEffects | None = None
        # Per-connection message flood guard (#169). Keyed on id(ws); the
        # entry is dropped by _forget_rate_limit() on disconnect so the
        # backing dict is bounded by the number of *live* connections.
        self._rate_limiter = SlidingWindowLimiter(
            max_requests=15,  # max messages per window
            window=1.0,  # seconds
            clock=lambda: asyncio.get_event_loop().time(),
        )
        # Per-IP concurrent-connection counter (#361). Incremented after a
        # successful ws.prepare(), decremented in handle()'s finally on
        # disconnect; a count that drops to zero is popped so the dict stays
        # bounded by the number of *currently connected* source IPs.
        self._ip_connections: dict[str, int] = {}
        # Per-IP join-attempt flood guard (#361). Distinct from the
        # per-connection message limiter above: a client that opens several
        # sockets could otherwise fire a burst of joins, one per socket. The
        # window is generous so a full room of players behind one NAT (each
        # joining once, plus the odd reconnect) is never blocked; it only
        # trips on an automated join flood.
        self._join_limiter = SlidingWindowLimiter(
            max_requests=30,  # max join attempts per window, per IP
            window=60.0,  # seconds
            clock=lambda: asyncio.get_event_loop().time(),
        )
        # The same key collapse hits the join guard over Nabu Casa (#701):
        # 30 attempts a minute is a whole room's worth of joins plus barely
        # any reconnects. Loopback therefore gets its own, room-sized budget.
        self._loopback_join_limiter = SlidingWindowLimiter(
            max_requests=MAX_PLAYERS * 5,
            window=60.0,
            clock=lambda: asyncio.get_event_loop().time(),
        )
        # Routes named state events (round_evaluated / game_ended) to the
        # matching broadcast, falling back to a full-state push (#184).
        self._broadcast_dispatcher = BroadcastDispatcher(
            handlers={
                "round_evaluated": self._dispatch_round_evaluated,
                "game_ended": self._dispatch_game_ended,
                "analytics_recorded": self._dispatch_analytics_followups,
            },
            default=self._dispatch_full_state,
        )
        # Assembles the per-round question + round-summary payloads (#189).
        # The handler keeps ownership of sending and shuffle mutation; the
        # builder produces the exact message dicts to hand to the connection
        # manager. Behaviour-preserving — identical wire shapes.
        self._round_messages = RoundMessageBuilder()
        # Visual-reaction coalescing (#304). Floating-reaction broadcasts are
        # best-effort eye-candy, but at the 20-player cap a reaction-mashing
        # room (20×15/s inbound, each fanned out to 20 sockets) generated
        # ~6600 outbound frames/s. Reactions are buffered here and flushed once
        # per ~150ms window, de-duplicated per (player, emoji), so the same
        # player hammering one emoji collapses to a single float per window
        # while distinct reactions still get through. Wire shape is unchanged —
        # one ``reaction`` message per distinct buffered reaction.
        self._reaction_buffer: dict[tuple[str, str], None] = {}
        self._reaction_flush_task: asyncio.Task | None = None
        # Reveal reaction-BONUS coalescing (#416). Each distinct reactor's first
        # reveal reaction used to broadcast its own ``reaction_bonus`` carrying a
        # FULL serialized leaderboard (up to P×P frames in a reaction-mashing
        # room). The point awards stay synchronous (scores + per-round caps must
        # settle immediately), but the broadcast is deferred into the same
        # ~150ms flush window and collapsed to ONE ``reaction_bonus`` with a
        # single leaderboard reflecting the whole batch. Ordered dicts so the
        # union of reactors/recipients keeps first-seen order.
        self._reaction_bonus_from: dict[str, None] = {}
        self._reaction_bonus_to: dict[str, None] = {}
        # Roster-broadcast coalescing (#453). Every join / reconnect / kick /
        # disconnect / grace-removal used to fan a FULL serialize_player_list
        # roster out to every socket immediately. A room-wide wifi blip (all P
        # players reconnecting at once) turned that into O(P²) frames. The
        # roster is now marked dirty and a single ``player_joined`` /
        # ``player_left`` message carrying the CURRENT list is broadcast once
        # per flush window — same wire shape, one frame per window instead of
        # one per event. ``_roster_last_type`` records the direction of the
        # last event in the window so the client still gets the right
        # join/leave animation; the ``players`` list is always authoritative,
        # re-serialized from live game state at flush time (correctness: the
        # final roster is always sent).
        self._roster_dirty: bool = False
        self._roster_last_type: str = "player_joined"
        self._roster_flush_task: asyncio.Task | None = None
        # #619: answer-progress rides its own coalescing window, same shape as
        # the roster one (#453). A broadcast per accepted answer would be the
        # O(N²) fan-out that #453 removed, re-introduced on a hotter path — a
        # room of eight taps eight times per round, not once per game.
        self._progress_dirty = False
        self._progress_flush_task: asyncio.Task | None = None

    # ------------------------------------------------------------------
    # The four fan-out objects the drivers actually talk to (#881)
    # ------------------------------------------------------------------
    # #788 gave each driver a narrow ``Broadcaster`` protocol and then handed
    # it ``self``, so the contract was an annotation and nothing more: a driver
    # held every method on this class, and a mode could not be exercised
    # against a small fake. Each protocol now has one small implementation in
    # ``server/broadcasters.py`` that owns its mode's frames; the handler holds
    # them rather than being them.
    #
    # Built on first use rather than in ``__init__`` so a handler assembled
    # with ``__new__`` — which several tests do, to exercise one message
    # handler against a stub connection — gets them from the same wiring the
    # real one uses instead of a hand-rolled copy.
    #
    # ``lambda: self._conn`` rather than ``self._conn``: much of the suite
    # replaces the connection manager after construction, and a reference
    # captured once would keep sending into the discarded one.

    @cached_property
    def _lightning_out(self) -> LightningBroadcaster:
        """The Lightning Round's fan-out (#42/#201)."""
        return LightningBroadcaster(lambda: self._conn, self._round_messages)

    @cached_property
    def _hot_seat_out(self) -> HotSeatBroadcaster:
        """The Hot Seat's fan-out (#616/#804)."""
        return HotSeatBroadcaster(
            lambda: self._conn,
            self._round_messages,
            resume_normal_question=self._continue_normal_question,
        )

    @cached_property
    def _round_out(self) -> RoundBroadcaster:
        """The normal round's fan-out (#203/#365/#413)."""
        return RoundBroadcaster(lambda: self._conn, self._round_messages)

    @cached_property
    def _wager_out(self) -> WagerBroadcaster:
        """The betting window's fan-out (#656)."""
        return WagerBroadcaster(
            lambda: self._conn,
            self._round_messages,
            close=self._close_wager_window,
        )

    # Coalescing window for visual reactions (#304), seconds.
    _REACTION_FLUSH_WINDOW = 0.15

    @property
    def conn(self) -> ConnectionManager:
        """The connection manager owned by this handler (public accessor)."""
        return self._conn

    def _check_rate_limit(self, ws: web.WebSocketResponse) -> bool:
        """Record a message for ``ws`` and report whether it is within the
        per-connection rate limit.

        Delegates to the shared :class:`SlidingWindowLimiter`: old timestamps
        are pruned on every call so each connection's list stays small; the
        connection's entry is dropped by :meth:`_forget_rate_limit` from
        ``handle()``'s ``finally`` on disconnect, so the backing dict is
        bounded by the number of *live* connections (≤ MAX_PLAYERS +
        admin/dashboard), never unbounded (#169). A message that exceeds the
        limit is NOT recorded.
        """
        return self._rate_limiter.check(id(ws))

    def _forget_rate_limit(self, ws: web.WebSocketResponse) -> None:
        """Drop a connection's rate-limit state (called on disconnect)."""
        self._rate_limiter.forget(id(ws))

    async def _grant_admin(
        self, role: str | None, admin_token: str | None, request: web.Request
    ) -> bool:
        """Delegate: see :func:`admin_auth.grant_admin` (#1124)."""
        return await admin_auth.grant_admin(self, role, admin_token, request)

    @staticmethod
    def _is_loopback(remote: str) -> bool:
        """True when ``remote`` is a loopback address (#701).

        Nabu Casa hands cloud connections to HA over 127.0.0.1, so this is
        the shape every remote player arrives in. A hostname or anything
        unparseable is treated as a normal remote address.
        """
        try:
            return ipaddress.ip_address(remote.split("%", 1)[0]).is_loopback
        except ValueError:
            return False

    def _connection_cap(self, remote: str) -> int:
        """Concurrent-socket cap that applies to ``remote`` (#361, #701)."""
        if self._is_loopback(remote):
            return self.MAX_CONNECTIONS_PER_LOOPBACK
        return self.MAX_CONNECTIONS_PER_IP

    def _join_limiter_for(self, remote: str) -> SlidingWindowLimiter:
        """Join-flood limiter that applies to ``remote`` (#361, #701)."""
        if self._is_loopback(remote):
            return self._loopback_join_limiter
        return self._join_limiter

    async def handle(self, request: web.Request) -> web.StreamResponse:
        """Handle WebSocket connection."""
        remote = request.remote
        # Per-IP connection cap (#361): refuse BEFORE upgrading the socket so
        # a flood of sockets from one host can't exhaust resources. Checked
        # before prepare() so we answer with a plain HTTP 429. Loopback gets
        # the room-sized cap instead (#701) — over Nabu Casa it carries every
        # player at once rather than a single host.
        if (
            remote is not None
            and self._ip_connections.get(remote, 0) >= self._connection_cap(remote)
        ):
            _LOGGER.warning(
                "Refusing WebSocket from %s: per-IP connection cap (%d) reached",
                remote,
                self._connection_cap(remote),
            )
            return web.Response(
                status=429, text="Too many connections from this address"
            )

        # Same-origin gate (#785): browsers do NOT apply CORS to a WebSocket
        # handshake, so without this any page open in a player's (or the
        # host's) browser could open this socket from their own address —
        # inheriting the per-IP allowances and, inside the admin-bootstrap
        # window, the admin role and its session token. Checked before
        # prepare() so a cross-site handshake is answered with a plain HTTP
        # 403 and never upgraded. A request with no Origin still passes: the
        # dev server, the tests and non-browser clients send none, and a
        # browser cannot be made to omit it.
        if reject_cross_origin(request, self._runtime, "WebSocket handshake"):
            return web.Response(status=403, text="Cross-origin request refused")

        ws = web.WebSocketResponse(
            heartbeat=self.HEARTBEAT_INTERVAL, max_msg_size=self.MAX_MSG_SIZE
        )
        await ws.prepare(request)
        if remote is not None:
            self._ip_connections[remote] = self._ip_connections.get(remote, 0) + 1
        # Everything after the per-IP increment runs inside the try so the
        # ``finally`` below always releases the slot (#361). An exception
        # during the handshake (admin grant, registration, host announcement)
        # used to skip the release and burn one slot of the cap for good. The
        # cleanup steps all tolerate a socket that was never registered.
        try:
            role = request.query.get("role")
            is_dashboard = role == "dashboard"
            # #359: prefer the token from the ``X-Quizify-Token`` header (kept out
            # of aiohttp/reverse-proxy access logs and browser history); fall back
            # to the deprecated ``?token=`` query param for backward compat. A
            # browser WS handshake can't set headers, so the admin frontend also
            # sends the token in a first-message ``admin_auth`` frame handled in
            # the message loop below.
            admin_token = request.headers.get("X-Quizify-Token") or request.query.get(
                "token"
            )

            is_admin = await self._grant_admin(role, admin_token, request)

            self._conn.add_connection(ws, is_admin=is_admin, is_dashboard=is_dashboard)

            _LOGGER.debug(
                "WebSocket connected (admin=%s), total: %d",
                is_admin,
                len(self._conn.connections),
            )

            # #842: a host arriving is the one half of the signal the room was
            # never sent. Announced here rather than in ``admin_connect`` because
            # the socket is already counted by ``has_admin_connections()``, which
            # is what the flag reports.
            if is_admin:
                await self._announce_host_presence()

            async for msg in ws:
                if msg.type == WSMsgType.TEXT:
                    # Rate limiting: record timestamp BEFORE processing so the
                    # first message is also counted (#21 in logical review).
                    if not self._check_rate_limit(ws):
                        _LOGGER.warning("Rate limit exceeded for WebSocket %s", id(ws))
                        await self._conn.send_error(
                            ws, ERR_INVALID_ACTION, "Rate limit exceeded"
                        )
                        continue
                    # Parse JSON separately so we can distinguish parse errors
                    # from handler errors (#20 in logical review).
                    try:
                        data = msg.json()
                    except (ValueError, RecursionError):
                        # RecursionError (#786): a deeply nested frame blows
                        # the interpreter's stack inside ``json.loads``. It is
                        # not a ValueError, so it used to escape the read loop
                        # and tear the connection down with a traceback
                        # instead of the structured error every other
                        # malformed frame gets.
                        await self._conn.send_error(
                            ws, ERR_INVALID_ACTION, "Malformed message (invalid JSON)"
                        )
                        continue
                    # #410: ``msg.json()`` happily parses any valid JSON value —
                    # ``[1,2]``, ``"x"``, ``5`` — but the ``admin_auth`` and
                    # ``join`` guards below call ``data.get(...)``, which raises
                    # AttributeError on a non-dict OUTSIDE the try/except around
                    # ``_handle_message``, tearing down the connection with a
                    # traceback. Reject anything that isn't a JSON object here.
                    if not isinstance(data, dict):
                        await self._conn.send_error(
                            ws, ERR_INVALID_ACTION, "Malformed message"
                        )
                        continue
                    # First-message admin auth (#359): the token now travels in
                    # this frame instead of the URL. Validate + upgrade the
                    # connection to admin before any other traffic. FAIL SOFT —
                    # a bad/absent token never closes the socket; it just stays
                    # a plain player, so the host can never lock itself out.
                    if data.get("type") == "admin_auth":
                        if not is_admin and await self._grant_admin(
                            role, data.get("token"), request
                        ):
                            is_admin = True
                            self._conn.add_connection(
                                ws, is_admin=True, is_dashboard=is_dashboard
                            )
                            _LOGGER.info(
                                "Admin authenticated via admin_auth frame (ip=%s)",
                                remote,
                            )
                            # #359 upgrades this socket to admin after the
                            # fact, so the room only learns a host is here now.
                            await self._announce_host_presence()
                        continue
                    # Per-IP join flood guard (#361): keyed on the source IP so
                    # opening extra sockets can't multiply join attempts. The
                    # window is generous, so a full room behind one NAT (each
                    # player joining once, plus the odd reconnect) never trips.
                    if (
                        data.get("type") == "join"
                        and remote is not None
                        and not self._join_limiter_for(remote).check(remote)
                    ):
                        _LOGGER.warning(
                            "Per-IP join rate limit exceeded for %s", remote
                        )
                        await self._conn.send_error(
                            ws, ERR_JOIN_RATE_LIMITED, "Too many join attempts"
                        )
                        continue
                    try:
                        await self._handle_message(ws, data, is_admin)
                    except Exception:  # noqa: BLE001
                        _LOGGER.exception("Failed to handle WebSocket message")
                        await self._conn.send_error(
                            ws, ERR_INVALID_ACTION, "Server error processing message"
                        )
                elif msg.type == WSMsgType.ERROR:
                    _LOGGER.error("WebSocket error: %s", ws.exception())
        finally:
            # #293: capture the admin-connection flag BEFORE remove_connection
            # discards this ws from _admin_connections. The disconnect handler
            # needs it to decide whether to start the admin-session grace
            # timeout; reading is_admin_connection(ws) AFTER removal always
            # returned False, so schedule_admin_timeout never fired (and
            # cancel_admin_disconnect was equally dead).
            was_admin = self._conn.is_admin_connection(ws)
            self._conn.remove_connection(ws)
            self._forget_rate_limit(ws)
            # #361: release this connection's slot in the per-IP counter; drop
            # the key entirely when it hits zero so the dict stays bounded by
            # the number of *currently connected* source IPs.
            if remote is not None:
                remaining = self._ip_connections.get(remote, 0) - 1
                if remaining > 0:
                    self._ip_connections[remote] = remaining
                else:
                    self._ip_connections.pop(remote, None)
            await self._handle_disconnect(ws, was_admin=was_admin)
            # Only now: ``_handle_disconnect`` still needs the id to find the
            # player sitting on this socket (#882).
            self._conn.forget_connection(ws)
            _LOGGER.debug(
                "WebSocket disconnected, total: %d", len(self._conn.connections)
            )

        return ws

    # ------------------------------------------------------------------
    # Message dispatch
    # ------------------------------------------------------------------

    async def _handle_message(
        self, ws: web.WebSocketResponse, data: dict, is_admin: bool
    ) -> None:
        """Route incoming WebSocket message."""
        msg_type = data.get("type")
        game_state = self._get_game_state()

        if not game_state:
            await self._conn.send_error(ws, ERR_GAME_NOT_STARTED, "No active game")
            return

        # ``admin_connect`` and ``reset_game`` use a different / special
        # authorization path than the boolean ``admin_required`` table below,
        # so they are handled out-of-band first.
        if msg_type == MSG_ADMIN_CONNECT:
            # WS-level admin only (not the player-as-admin ``_is_authorized_admin``
            # relaxation) — an admin_connect must come from a real ?role=admin tab.
            if not is_admin:
                await self._conn.send_error(ws, ERR_INVALID_ACTION, "Admin only")
                return
            await self._handle_admin_connect(ws, game_state)
            return

        if msg_type == MSG_RESET_GAME:
            # Reset is the recovery escape-hatch (#207). Besides the normal
            # admin check, allow it whenever NO connected admin currently
            # holds the crown: in that orphaned-crown state (the legitimate
            # host lost its admin slot to the #209 name-disambiguation race)
            # the host has no other way back to a clean lobby. Reset is safe
            # and idempotent — it can only return the game to its initial
            # state, never escalate privilege — so this cannot be abused.
            if not self._is_reset_authorized(ws, is_admin, game_state):
                await self._conn.send_error(
                    ws, ERR_ADMIN_REQUIRED, "This connection is not the host"
                )
                return
            await self._handle_reset_game(ws, game_state)
            return

        # msg_type comes from untyped client JSON and may be None (no "type"
        # field) — dict.get tolerates that and routes it to the unknown-type
        # branch below, so the arg-type mismatch is intentional.
        entry = self._DISPATCH.get(msg_type)  # type: ignore[arg-type]
        if entry is None:
            _LOGGER.warning("Unknown message type: %s", msg_type)
            await self._conn.send_error(ws, ERR_INVALID_ACTION, "Unknown message type")
            return

        handler, admin_required = entry

        # #724: two admin messages need WS-level admin, not the
        # admin-as-player relaxation. ``configure_house`` and ``configure_tts``
        # forward host-supplied entity ids into light.turn_on, scene.turn_on,
        # media_player.play_media and tts.speak - Home Assistant service calls
        # against the whole house. The ``is_admin: true`` join claim (#208) was
        # accepted as "claim the single admin slot of the quiz"; it was never
        # meant to reach HA services, and a host who runs the game from the
        # ?role=admin tab leaves that player slot free all evening for any
        # guest to take. Same bar as ``admin_connect`` above.
        # Same bar, same reason, different carrier: the ``tts`` and ``house``
        # blocks ride the start payload straight into ``_apply_tts_config`` /
        # ``_apply_house_config``. A player-admin may still start the game (that
        # IS the #208 flow) — the room's lights, speakers and scenes just stay on
        # whatever the host configured. Stripped here rather than in the handler
        # so the authoritative ``is_admin`` decides, not a second lookup.
        if msg_type == MSG_START_GAME and not is_admin:
            if data.get("tts") or data.get("house"):
                _LOGGER.warning(
                    "Dropped the tts/house blocks of start_game from a "
                    "connection without the WS admin role (#724)"
                )
            data = {k: v for k, v in data.items() if k not in ("tts", "house")}

        if msg_type in _WS_ADMIN_ONLY and not is_admin:
            _LOGGER.warning(
                "Refused %s from a connection without the WS admin role (#724)",
                msg_type,
            )
            await self._conn.send_error(
                ws, ERR_ADMIN_REQUIRED, "This connection is not the host"
            )
            return

        if admin_required and not self._is_authorized_admin(ws, is_admin, game_state):
            # Centralized admin guard — same error code/message as the legacy
            # per-type checks. ``_is_authorized_admin`` accepts either WS-level
            # admin (admin tab via ?role=admin) OR a player whose session has
            # is_admin=True (admin-as-player flow). Without that relaxation the
            # admin-as-player flow could never advance LOBBY → QUESTION_ACTIVE.
            _LOGGER.warning(
                "Refused admin command %s from a connection without the admin "
                "role — the client is told, not just ignored (#586)",
                msg_type,
            )
            await self._conn.send_error(
                ws, ERR_ADMIN_REQUIRED, "This connection is not the host"
            )
            return

        # Every handler in ``_DISPATCH`` is normalized to the uniform
        # ``(self, ws, data, game_state)`` signature (arity mismatches are
        # absorbed by the adapter lambdas), so the guarded dispatch is a single
        # call regardless of what the underlying handler needs.
        await handler(self, ws, data, game_state)

    # ------------------------------------------------------------------
    # Transport ↔ player bridge (#882)
    # ------------------------------------------------------------------

    def _player_for_ws(
        self, game_state: QuizifyGameState, ws: web.WebSocketResponse
    ) -> PlayerSession | None:
        """Return the player sitting on *ws*, or None.

        The game layer is keyed by an opaque ``connection_id``; translating a
        socket into one is the server layer's job and this is the only place
        that does it for lookups.
        """
        return game_state.get_player_by_connection(self._conn.connection_id(ws))

    def _reap_closed_connections(
        self, game_state: QuizifyGameState
    ) -> set[str]:
        """Mark players whose socket is known-dead as disconnected.

        Replaces the ``ws.closed`` read that used to sit inside
        ``PlayerSession.is_active`` and ``PlayerRegistry.add_player`` (#882).
        The situation is real and unchanged: a phone reloads, its new socket
        opens and sends ``join`` before aiohttp has run the old socket's
        ``finally`` — so the old slot still says ``connected = True`` while
        its transport is already shut. Left alone, that ghost blocks
        all-submitted early reveal for the whole room and makes the user's own
        name look taken.

        Only ids the manager still tracks AND reports closed are reaped: an
        unknown id is unknown, not dead, so a player built without a transport
        (tests, snapshots) is never touched.

        Returns the names it just took down, which is what tells
        ``_handle_join`` that a name-collision is the caller's own ghost
        rather than a live stranger.
        """
        reaped: set[str] = set()
        for player in game_state.get_players():
            if not player.connected:
                continue
            if self._conn.is_connection_dead(player.connection_id):
                player.connected = False
                reaped.add(player.name)
                _LOGGER.info(
                    "Reaped stale connection for %s (socket closed without a "
                    "disconnect)",
                    player.name,
                )
        return reaped

    async def _handle_get_state(
        self, ws: web.WebSocketResponse, data: dict, game_state: QuizifyGameState
    ) -> None:
        """Handle a ``get_state`` request (#286)."""
        state_msg = self._snapshot(game_state)
        # Project into the requesting PLAYER's frame (#286): the raw
        # snapshot carries canonical answer order, but ``submit_answer``
        # maps the tapped index through the player's OWN shuffle — sending
        # canonical order here mis-scores ~2/3 of taps after a mid-round
        # reconnect (the client auto-sends get_state on every join). Pure
        # admin/dashboard sockets (no player session) keep canonical order.
        player = self._player_for_ws(game_state, ws)
        if player is not None:
            state_msg = self._round_messages.project_snapshot_for_player(
                game_state, snapshot=state_msg, player=player
            )
        state_msg["type"] = "game_state"
        await self._conn.send(ws, state_msg)

    # ------------------------------------------------------------------
    # Admin connect
    # ------------------------------------------------------------------

    async def _handle_admin_connect(
        self, ws: web.WebSocketResponse, game_state: QuizifyGameState
    ) -> None:
        """Delegate: see :func:`admin_auth.handle_admin_connect` (#1124)."""
        await admin_auth.handle_admin_connect(self, ws, game_state)

    # ------------------------------------------------------------------
    # Player join
    # ------------------------------------------------------------------

    def _resolve_admin_claim(
        self,
        game_state: QuizifyGameState,
        player_obj: PlayerSession,
        name: str,
        data: dict,
    ) -> None:
        """Delegate to :func:`admin_claim.resolve_admin_claim` (#1124)."""
        admin_claim.resolve_admin_claim(self, game_state, player_obj, name, data)

    async def _handle_join(
        self, ws: web.WebSocketResponse, data: dict, game_state: QuizifyGameState
    ) -> None:
        """Delegate: see :func:`join_handlers.handle_join` (#1124)."""
        await join_handlers.handle_join(self, ws, data, game_state)

    # ------------------------------------------------------------------
    # Player reconnect (session-based)
    # ------------------------------------------------------------------

    async def _handle_reconnect(
        self, ws: web.WebSocketResponse, data: dict, game_state: QuizifyGameState
    ) -> None:
        """Delegate: see :func:`join_handlers.handle_reconnect` (#1124)."""
        await join_handlers.handle_reconnect(self, ws, data, game_state)

    # ------------------------------------------------------------------
    # Teams (#365)
    # ------------------------------------------------------------------

    async def _handle_create_team(
        self, ws: web.WebSocketResponse, data: dict, game_state: QuizifyGameState
    ) -> None:
        """Delegate: see :func:`join_handlers.handle_create_team` (#1124)."""
        await join_handlers.handle_create_team(self, ws, data, game_state)

    async def _handle_join_team(
        self, ws: web.WebSocketResponse, data: dict, game_state: QuizifyGameState
    ) -> None:
        """Delegate: see :func:`join_handlers.handle_join_team` (#1124)."""
        await join_handlers.handle_join_team(self, ws, data, game_state)

    async def _handle_leave_team(
        self, ws: web.WebSocketResponse, data: dict, game_state: QuizifyGameState
    ) -> None:
        """Delegate: see :func:`join_handlers.handle_leave_team` (#1124)."""
        await join_handlers.handle_leave_team(self, ws, data, game_state)

    # ------------------------------------------------------------------
    # Submit answer
    # ------------------------------------------------------------------

    async def _handle_submit_answer(
        self, ws: web.WebSocketResponse, data: dict, game_state: QuizifyGameState
    ) -> None:
        """Delegate: see :func:`answer_handlers.handle_submit_answer` (#1124)."""
        await answer_handlers.handle_submit_answer(self, ws, data, game_state)

    # ------------------------------------------------------------------
    # Power-ups
    # ------------------------------------------------------------------

    # ------------------------------------------------------------------
    # Reactions (gameplay idea #11)
    # ------------------------------------------------------------------

    # Max reaction-bonus points a single correct answerer can collect per
    # round. Without a cap, a 6-player room could pile 5 reactors × 1 pt =
    # +5, which dwarfs the base 10-point scoring. Cap at 3 so reactions
    # feel like a meaningful "tip your hat" without breaking the scoring.
    _REACTION_BONUS_CAP_PER_ROUND = 3

    async def _handle_reaction(
        self, ws: web.WebSocketResponse, data: dict, game_state: QuizifyGameState
    ) -> None:
        """Delegate: see :func:`answer_handlers.handle_reaction` (#1124)."""
        await answer_handlers.handle_reaction(self, ws, data, game_state)

    def _enqueue_reaction(self, player_name: str, emoji: str) -> None:
        """Delegate: see :func:`batched_updates.enqueue_reaction` (#1124)."""
        batched_updates.enqueue_reaction(self, player_name, emoji)

    def _ensure_reaction_flush(self) -> None:
        """Delegate: see :func:`batched_updates.ensure_reaction_flush` (#1124)."""
        batched_updates.ensure_reaction_flush(self)

    async def _flush_reactions_after_window(self) -> None:
        """Delegate to :func:`batched_updates.flush_reactions_after_window`."""
        await batched_updates.flush_reactions_after_window(self)

    def _cancel_reaction_flush(self) -> None:
        """Delegate: see :func:`batched_updates.cancel_reaction_flush` (#1124)."""
        batched_updates.cancel_reaction_flush(self)

    def _mark_roster_dirty(self, event_type: str) -> None:
        """Delegate: see :func:`batched_updates.mark_roster_dirty` (#1124)."""
        batched_updates.mark_roster_dirty(self, event_type)

    def _ensure_roster_flush(self) -> None:
        """Delegate: see :func:`batched_updates.ensure_roster_flush` (#1124)."""
        batched_updates.ensure_roster_flush(self)

    async def _flush_roster_after_window(self) -> None:
        """Delegate: see :func:`batched_updates.flush_roster_after_window` (#1124)."""
        await batched_updates.flush_roster_after_window(self)

    def _mark_progress_dirty(self) -> None:
        """Delegate: see :func:`batched_updates.mark_progress_dirty` (#1124)."""
        batched_updates.mark_progress_dirty(self)

    async def _flush_progress_after_window(self) -> None:
        """Delegate: see :func:`batched_updates.flush_progress_after_window` (#1124)."""
        await batched_updates.flush_progress_after_window(self)

    def _cancel_progress_flush(self) -> None:
        """Delegate: see :func:`batched_updates.cancel_progress_flush` (#1124)."""
        batched_updates.cancel_progress_flush(self)

    async def _send_head_to_head(self, game_state: QuizifyGameState) -> None:
        """Delegate: see :func:`batched_updates.send_head_to_head` (#1124)."""
        await batched_updates.send_head_to_head(self, game_state)

    async def _dispatch_end_head_to_head(self) -> None:
        """Delegate: see :func:`batched_updates.dispatch_end_head_to_head` (#1124)."""
        await batched_updates.dispatch_end_head_to_head(self)

    async def _broadcast_head_to_head(
        self, game_state: QuizifyGameState, *, at: str
    ) -> None:
        """Delegate: see :func:`batched_updates.broadcast_head_to_head` (#1124)."""
        await batched_updates.broadcast_head_to_head(self, game_state, at=at)

    def _cancel_roster_flush(self) -> None:
        """Delegate: see :func:`batched_updates.cancel_roster_flush` (#1124)."""
        batched_updates.cancel_roster_flush(self)

    # ------------------------------------------------------------------
    # Wager (gameplay idea #3 — Jeopardy-style final round)
    # ------------------------------------------------------------------

    async def _handle_submit_wager(
        self, ws: web.WebSocketResponse, data: dict, game_state: QuizifyGameState
    ) -> None:
        """Delegate: see :func:`wager_handlers.handle_submit_wager` (#1116)."""
        await wager_handlers.handle_submit_wager(self, ws, data, game_state)

    async def _handle_use_powerup(
        self, ws: web.WebSocketResponse, data: dict, game_state: QuizifyGameState
    ) -> None:
        """Delegate: see :func:`answer_handlers.handle_use_powerup` (#1124)."""
        await answer_handlers.handle_use_powerup(self, ws, data, game_state)

    # ------------------------------------------------------------------
    # Game commands: start/next/skip/end/pause/resume/play-again/reset/kick
    # live in game_commands.py (#1124); these are the delegates.
    # ------------------------------------------------------------------

    async def _handle_start_game(
        self, ws: web.WebSocketResponse, data: dict, game_state: QuizifyGameState
    ) -> None:
        """Delegate: see :func:`game_commands.handle_start_game` (#1124)."""
        await game_commands.handle_start_game(self, ws, data, game_state)

    async def _after_start_game(
        self,
        game_state: QuizifyGameState,
        tts_config: dict | None = None,
        house_config: dict | None = None,
    ) -> None:
        """Delegate: see :func:`game_commands.after_start_game` (#1124)."""
        await game_commands.after_start_game(self, game_state, tts_config, house_config)

    async def admin_action_start_game(
        self,
        game_state: QuizifyGameState,
        preset: dict[str, Any] | None = None,
        overrides: dict[str, Any] | None = None,
    ) -> None:
        """Delegate: see :func:`game_commands.admin_action_start_game` (#1124)."""
        await game_commands.admin_action_start_game(self, game_state, preset, overrides)

    @staticmethod
    def resolve_start_settings(
        game_state: QuizifyGameState,
        preset: dict[str, Any] | None = None,
        overrides: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Resolve the ``start_game`` kwargs for a socket-less start (#744).

        Four layers, each beating the one before it:

        1. ``start_game``'s own defaults — nothing is passed for a field no
           layer below mentions, so the game-state defaults keep owning them;
        2. the host's **last-used settings** (``game_state.last_settings``, the
           same snapshot the one-tap rematch replays). This is what makes "Hey
           Nabu, start the quiz" replay the evening the host actually set up
           rather than the factory one;
        3. a **saved preset**, matched by name in the service layer;
        4. the **explicit service fields**.

        Kept static and pure so the layering can be unit-tested without a
        handler, a socket or a Home Assistant instance.
        """
        settings: dict[str, Any] = dict(game_state.last_settings or {})
        # #889: a preset carries the language it was built for, because its
        # packs do. Older presets have no such field and leave the language to
        # layer 2/4, which is where it lived before.
        if preset:
            settings.update(_preset_to_start_settings(preset))
        if overrides:
            settings.update(_overrides_to_start_settings(overrides))
        # ``last_settings`` is written by start_game itself and can carry keys a
        # future version adds; filter to what start_game actually accepts so a
        # stale snapshot can never raise TypeError on the host's voice command.
        return {k: v for k, v in settings.items() if k in _START_GAME_FIELDS}

    async def _handle_next_question(
        self, ws: web.WebSocketResponse, game_state: QuizifyGameState
    ) -> None:
        """Delegate: see :func:`game_commands.handle_next_question` (#1124)."""
        await game_commands.handle_next_question(self, ws, game_state)

    async def _advance_round(self, game_state: QuizifyGameState) -> str | None:
        """Delegate: see :func:`game_commands.advance_round` (#1124)."""
        return await game_commands.advance_round(self, game_state)

    async def admin_action_next_round(self, game_state: QuizifyGameState) -> None:
        """Delegate: see :func:`game_commands.admin_action_next_round` (#1124)."""
        await game_commands.admin_action_next_round(self, game_state)

    async def _handle_admin_skip(
        self, ws: web.WebSocketResponse, game_state: QuizifyGameState
    ) -> None:
        """Delegate: see :func:`game_commands.handle_admin_skip` (#1124)."""
        await game_commands.handle_admin_skip(self, ws, game_state)

    async def _handle_end_game(
        self, ws: web.WebSocketResponse, game_state: QuizifyGameState
    ) -> None:
        """Delegate: see :func:`game_commands.handle_end_game` (#1124)."""
        await game_commands.handle_end_game(self, ws, game_state)

    async def admin_action_end_game(self, game_state: QuizifyGameState) -> None:
        """Delegate: see :func:`game_commands.admin_action_end_game` (#1124)."""
        await game_commands.admin_action_end_game(self, game_state)

    async def _handle_pause_game(
        self, ws: web.WebSocketResponse, game_state: QuizifyGameState
    ) -> None:
        """Delegate: see :func:`game_commands.handle_pause_game` (#1124)."""
        await game_commands.handle_pause_game(self, ws, game_state)

    async def admin_action_pause(self, game_state: QuizifyGameState) -> bool:
        """Delegate: see :func:`game_commands.admin_action_pause` (#1124)."""
        return await game_commands.admin_action_pause(self, game_state)

    async def _handle_resume_game(
        self, ws: web.WebSocketResponse, game_state: QuizifyGameState
    ) -> None:
        """Delegate: see :func:`game_commands.handle_resume_game` (#1124)."""
        await game_commands.handle_resume_game(self, ws, game_state)

    async def admin_action_resume(self, game_state: QuizifyGameState) -> bool:
        """Delegate: see :func:`game_commands.admin_action_resume` (#1124)."""
        return await game_commands.admin_action_resume(self, game_state)

    async def _handle_play_again(
        self, ws: web.WebSocketResponse, game_state: QuizifyGameState
    ) -> None:
        """Delegate: see :func:`game_commands.handle_play_again` (#1124)."""
        await game_commands.handle_play_again(self, ws, game_state)

    async def _handle_reset_game(
        self, ws: web.WebSocketResponse, game_state: QuizifyGameState
    ) -> None:
        """Delegate: see :func:`game_commands.handle_reset_game` (#1124)."""
        await game_commands.handle_reset_game(self, ws, game_state)

    async def _handle_kick_player(
        self, ws: web.WebSocketResponse, data: dict, game_state: QuizifyGameState
    ) -> None:
        """Delegate: see :func:`game_commands.handle_kick_player` (#1124)."""
        await game_commands.handle_kick_player(self, ws, data, game_state)

    # ------------------------------------------------------------------
    # Lightning Round (issue #42)
    # ------------------------------------------------------------------
    #
    # A self-contained fast mode: 5 questions, fixed 15s each, auto-advance
    # on timeout OR when all connected players have answered, NO reveal
    # between questions, flat points per correct, power-ups disabled. The
    # mode's rules live in game/lightning.py; here we just drive the loop
    # and fan out the question/recap payloads over the existing connection
    # layer.

    async def _start_auto_lightning(self, game_state: QuizifyGameState) -> None:
        """Delegate: see :func:`round_flow.start_auto_lightning` (#1124)."""
        await round_flow.start_auto_lightning(self, game_state)

    async def _continue_normal_question(self, game_state: QuizifyGameState) -> None:
        """Delegate: see :func:`round_flow.continue_normal_question` (#1124)."""
        await round_flow.continue_normal_question(self, game_state)

    async def _handle_lightning_answer(
        self, ws: web.WebSocketResponse, data: dict, game_state: QuizifyGameState
    ) -> None:
        """Delegate: see :func:`lightning_handlers.handle_lightning_answer` (#1124)."""
        await lightning_handlers.handle_lightning_answer(self, ws, data, game_state)

    def _start_lightning_loop(
        self,
        game_state: QuizifyGameState,
        *,
        auto_dismiss_splash: bool = False,
    ) -> None:
        """Delegate: see :func:`round_flow.start_lightning_loop` (#1124)."""
        round_flow.start_lightning_loop(
            self, game_state, auto_dismiss_splash=auto_dismiss_splash
        )

    def _cancel_lightning_loop(self) -> None:
        """Delegate: see :func:`round_flow.cancel_lightning_loop` (#1124)."""
        round_flow.cancel_lightning_loop(self)

    # ------------------------------------------------------------------
    # Hot Seat auction (issue #616)
    # ------------------------------------------------------------------

    def _cancel_hot_seat_loop(self) -> None:
        """Delegate: see :func:`round_flow.cancel_hot_seat_loop` (#1124)."""
        round_flow.cancel_hot_seat_loop(self)

    async def _start_hot_seat(self, game_state: QuizifyGameState) -> bool:
        """Delegate: see :func:`round_flow.start_hot_seat` (#1124)."""
        return await round_flow.start_hot_seat(self, game_state)

    def _start_hot_seat_loop(self, game_state: QuizifyGameState) -> None:
        """Delegate: see :func:`round_flow.start_hot_seat_loop` (#1124)."""
        round_flow.start_hot_seat_loop(self, game_state)

    async def _broadcast_state_projected(
        self,
        game_state: QuizifyGameState,
        *,
        extra: dict[str, Any] | None = None,
    ) -> None:
        """Delegate: see :func:`round_flow.broadcast_state_projected` (#1124)."""
        await round_flow.broadcast_state_projected(self, game_state, extra=extra)

    # ------------------------------------------------------------------
    # Question flow
    # ------------------------------------------------------------------

    async def _start_next_question(self, game_state: QuizifyGameState) -> None:
        """Delegate: see :func:`round_flow.start_next_question` (#1124)."""
        await round_flow.start_next_question(self, game_state)

    async def _deliver_question(
        self, game_state: QuizifyGameState, question: Any
    ) -> None:
        """Delegate: see :func:`round_flow.deliver_question` (#1124)."""
        await round_flow.deliver_question(self, game_state, question)

    async def _open_wager_window(
        self, game_state: QuizifyGameState, question: Any
    ) -> None:
        """Delegate: see :func:`round_flow.open_wager_window` (#1124)."""
        await round_flow.open_wager_window(self, game_state, question)

    def _start_wager_window(self, game_state: QuizifyGameState) -> None:
        """Delegate: see :func:`round_flow.start_wager_window` (#1124)."""
        round_flow.start_wager_window(self, game_state)

    def _cancel_wager_window(self) -> None:
        """Delegate: see :func:`round_flow.cancel_wager_window` (#1124)."""
        round_flow.cancel_wager_window(self)

    async def _close_wager_window(self, game_state: QuizifyGameState) -> None:
        """Delegate: see :func:`round_flow.close_wager_window` (#1124)."""
        await round_flow.close_wager_window(self, game_state)

    async def _emit_question(
        self, game_state: QuizifyGameState, question: Any
    ) -> None:
        """Delegate: see :func:`round_flow.emit_question` (#1124)."""
        await round_flow.emit_question(self, game_state, question)

    # ------------------------------------------------------------------
    # Timer ticks
    # ------------------------------------------------------------------

    def _start_timer_tick(self, game_state: QuizifyGameState) -> None:
        """Delegate: see :func:`round_flow.start_timer_tick` (#1124)."""
        round_flow.start_timer_tick(self, game_state)

    def _cancel_timer_tick(self) -> None:
        """Delegate: see :func:`round_flow.cancel_timer_tick` (#1124)."""
        round_flow.cancel_timer_tick(self)

    def _cancel_task_group(self, group: tuple[tuple[str, str], ...]) -> None:
        """Delegate: see :func:`round_flow.cancel_task_group` (#1124)."""
        round_flow.cancel_task_group(self, group)

    def _cancel_round_tasks(self) -> None:
        """Delegate: see :func:`round_flow.cancel_round_tasks` (#1124)."""
        round_flow.cancel_round_tasks(self)

    @staticmethod
    def _log_task_exception(task: asyncio.Task) -> None:
        """Done-callback that surfaces a fire-and-forget task's exception (#307).

        ensure_future/create_task tasks whose exception is never retrieved
        otherwise raise "Task exception was never retrieved" at GC time (or get
        swallowed entirely). Reuses the analytics.py logging-done-callback
        pattern so a crashed background loop is at least loud in the log.
        """
        if task.cancelled():
            return
        if (exc := task.exception()) is not None:
            _LOGGER.error("Unhandled exception in background task: %s", exc)

    # ------------------------------------------------------------------
    # Round summary broadcast
    # ------------------------------------------------------------------

    # ------------------------------------------------------------------
    # Disconnect handling
    # ------------------------------------------------------------------

    def _maybe_evaluate_after_dropout(self, game_state: QuizifyGameState) -> bool:
        """Delegate: see :func:`round_flow.maybe_evaluate_after_dropout` (#1124)."""
        return round_flow.maybe_evaluate_after_dropout(self, game_state)

    async def _handle_disconnect(
        self, ws: web.WebSocketResponse, was_admin: bool | None = None
    ) -> None:
        """Handle WebSocket disconnection.

        ``was_admin`` is the admin-connection flag captured by ``handle()``
        BEFORE ``remove_connection`` (#293). When omitted (legacy/test callers),
        fall back to querying the connection manager — only correct if the ws
        hasn't been removed yet.
        """
        game_state = self._get_game_state()
        if not game_state:
            return

        # Handle admin disconnect — keep game alive for grace period.
        # Strictly gated on real admin connection (was: OR clause allowed
        # dashboard disconnects to trigger the timeout, see #2 in review).
        is_admin_ws = (
            was_admin
            if was_admin is not None
            else self._conn.is_admin_connection(ws)
        )
        if is_admin_ws and not self._conn.has_admin_connections():
            _LOGGER.info(
                "Admin disconnected, keeping game alive for %ds",
                self._conn.ADMIN_SESSION_GRACE,
            )
            self._conn.schedule_admin_timeout()
            # #842: the other half. An admin-only host closing the tab is
            # otherwise silent — no player row to mark, no roster frame, and
            # the grace pause refuses every phase but QUESTION_ACTIVE — so
            # this is the only thing that ever tells the phones. The announcer
            # re-reads ``_host_connected``, so a host who is also a connected
            # player keeps the flag true.
            #
            # #848: scheduled, not awaited. This runs in a request task aiohttp
            # has already cancelled, so awaiting the broadcast here sends
            # nothing at all — see ``_announce_host_presence_soon``.
            self._announce_host_presence_soon()

        player = self._player_for_ws(game_state, ws)
        if not player:
            return

        player.connected = False
        _LOGGER.info("Player disconnected: %s", player.name)

        # #842: in the #208 flow the crown lives on a player row, so this is
        # the host leaving even though no admin socket was involved.
        # #848: scheduled for the same reason as the branch above, and with a
        # second benefit — everything below this line (the dropout
        # re-evaluation, the deferred pause, the roster broadcast, the removal
        # timer) used to sit behind an ``await`` in a cancelled task.
        if player.is_admin:
            self._announce_host_presence_soon()

        # #412: if the last unanswered player just dropped mid-question and
        # everyone still in the room has already submitted, nothing else would
        # re-check ``all_submitted()`` — the room would wait out the full timer.
        # Evaluate now (same path submit_answer/timer-expiry use) so the reveal
        # fires immediately. Done BEFORE the admin-pause scheduling below so a
        # completed round doesn't also arm a spurious pause.
        if self._maybe_evaluate_after_dropout(game_state):
            _LOGGER.info(
                "Round auto-evaluated after last unanswered player %s dropped",
                player.name,
            )

        # Host-disconnect graceful recovery: if the admin-as-player tab
        # drops mid-question, pause the game instead of letting the timer
        # run out while everyone wonders what happened. The admin's
        # PLAYER_SESSION_GRACE (60s by default) gives them time to reload
        # or switch networks; if they don't come back, the game can be
        # resumed by another path (their row stays until the lobby, #1093).
        #
        # BUT: the most common admin disconnect is the intentional
        # admin.html → player.html redirect that fires when admin clicks
        # "Spiel starten" (see admin.js::redirectToPlayer). The new
        # player WS reconnects via session token within 1-2s. Pausing
        # immediately would flash "Lost connection to the host" on every
        # other player's screen for the duration of the redirect — what
        # users perceive as the game "not reliably starting". Defer the
        # pause by ADMIN_REDIRECT_GRACE seconds; if admin reconnects in
        # time the pause never fires.
        if (
            player.is_admin
            and game_state.phase == GamePhase.QUESTION_ACTIVE
        ):
            self._schedule_admin_pause(player.name)

        # Broadcast updated player list — coalesced (#453).
        self._mark_roster_dirty("player_left")

        # Schedule removal after grace period
        grace = (
            LOBBY_DISCONNECT_GRACE_PERIOD
            if game_state.phase == GamePhase.LOBBY
            else self._conn.PLAYER_SESSION_GRACE
        )

        async def remove_after_timeout(name: str, timeout: float) -> None:
            # #1093: only the lobby removes. Mid-game a dropped player keeps
            # their row, score and session tokens — a guest whose phone locked
            # for just over a minute used to vanish from the leaderboard and
            # come back as a fresh player on the room average. The
            # disconnected row is harmless meanwhile: ``all_submitted()`` and
            # every fan-out skip it. Re-checked each grace period, so a
            # departed guest still leaves once the room is back in the lobby.
            while True:
                await asyncio.sleep(timeout)
                gs = self._get_game_state()
                if gs is None:
                    return
                p = gs.get_player(name)
                if p is None or p.connected:
                    return
                if gs.phase == GamePhase.LOBBY:
                    break
            gs.remove_player(name)
            # Clean up session tokens for this player
            self._conn.clear_player_tokens(name)
            # Coalesced roster broadcast (#453).
            self._mark_roster_dirty("player_left")
            _LOGGER.info("Removed disconnected player after grace period: %s", name)

        self._conn.schedule_player_removal(player.name, grace, remove_after_timeout)

    # ------------------------------------------------------------------
    # Admin-redirect pause: defer the QUESTION_ACTIVE pause that would
    # otherwise fire instantly when admin's WS closes (typically the
    # intentional /quizify/admin → /quizify/player redirect after Start).
    # ------------------------------------------------------------------

    def _schedule_admin_pause(self, admin_name: str) -> None:
        """Delegate: see :func:`round_flow.schedule_admin_pause` (#1124)."""
        round_flow.schedule_admin_pause(self, admin_name)

    def _cancel_admin_pause(self) -> None:
        """Delegate: see :func:`round_flow.cancel_admin_pause` (#1124)."""
        round_flow.cancel_admin_pause(self)

    # ------------------------------------------------------------------
    # Admin auth helper
    # ------------------------------------------------------------------

    def _is_authorized_admin(
        self, ws: web.WebSocketResponse, is_admin: bool, game_state: QuizifyGameState
    ) -> bool:
        """Delegate: see :func:`admin_auth.is_authorized_admin` (#1124)."""
        return admin_auth.is_authorized_admin(self, ws, is_admin, game_state)

    def _is_reset_authorized(
        self, ws: web.WebSocketResponse, is_admin: bool, game_state: QuizifyGameState
    ) -> bool:
        """Delegate: see :func:`admin_auth.is_reset_authorized` (#1124)."""
        return admin_auth.is_reset_authorized(self, ws, is_admin, game_state)

    def _host_connected(self, game_state: QuizifyGameState) -> bool:
        """Whether anybody is hosting this room right now (#842).

        The exact condition the escape hatch turns on, named once and then
        broadcast, because the phones could not work it out for themselves.

        Two ways to be hosting, and #726 is the reason both are here: a live
        ``?role=admin`` socket IS a connected admin even when it holds no
        player slot, while ``get_admin()`` only ever looks at the player
        registry — so a host who runs the evening from /quizify/admin without
        joining leaves it ``None`` all night, and reading that alone as
        "nobody is hosting" handed every guest a working ``reset_game``
        mid-game.

        The mirror image is #834: the phone had only the roster to read, the
        same registry that is empty in that flow, so the #299/#803 escape hatch
        armed on every guest phone while the host sat looking at the same
        recap. Nothing on the wire distinguished a host at the admin page from
        one whose tab had been closed for twenty minutes — an admin-only host
        broadcasts nothing when they arrive or leave, and the grace pause
        refuses every phase but QUESTION_ACTIVE. This is that missing signal.

        ``not _host_connected(...)`` is exactly "any client may reset", so the
        flag the phone reads and the rule the server enforces cannot drift.
        """
        if self._conn.has_admin_connections():
            return True
        admin = game_state.get_admin()
        return admin is not None and admin.connected

    def _snapshot(self, game_state: QuizifyGameState) -> dict:
        """``serialize_state_snapshot`` plus the host-presence flag (#842).

        The snapshot is the frame a client is handed on join, on reconnect and
        on ``get_state``, so it is where a phone that was not listening when
        the host arrived or left picks the answer up. The live
        ``host_presence`` broadcast carries every change after that.

        Kept here rather than in the serializer because the answer is a
        property of the *connections*, not of the game: ``serializers.py`` has
        no connection manager, and forty tests build snapshots without one.
        """
        state = serialize_state_snapshot(game_state)
        state["host_connected"] = self._host_connected(game_state)
        return state

    async def _announce_host_presence(self) -> None:
        """Delegate: see :func:`game_broadcasts.announce_host_presence` (#1124)."""
        await game_broadcasts.announce_host_presence(self)

    def _announce_host_presence_soon(self) -> None:
        """Delegate: see :func:`game_broadcasts.announce_host_presence_soon` (#1124)."""
        game_broadcasts.announce_host_presence_soon(self)

    def _cancel_host_presence(self) -> None:
        """Delegate: see :func:`game_broadcasts.cancel_host_presence` (#1124)."""
        game_broadcasts.cancel_host_presence(self)

    async def _dispatch_analytics_followups(self) -> None:
        """Delegate to :func:`game_broadcasts.dispatch_analytics_followups`."""
        await game_broadcasts.dispatch_analytics_followups(self)

    async def _dispatch_evening_tally(self) -> None:
        """Delegate: see :func:`game_broadcasts.dispatch_evening_tally` (#1124)."""
        await game_broadcasts.dispatch_evening_tally(self)

    async def _dispatch_all_time_standings(self) -> None:
        """Delegate: see :func:`game_broadcasts.dispatch_all_time_standings` (#1124)."""
        await game_broadcasts.dispatch_all_time_standings(self)

    def _all_time_standing(self, name: str) -> PlayerStanding | None:
        """Delegate: see :func:`game_broadcasts.all_time_standing` (#1124)."""
        return game_broadcasts.all_time_standing(self, name)

    def set_tts_announcer(
        self, announcer: QuizifyTTSAnnouncer | None
    ) -> None:
        """Wire (or rewire) the optional TTS announcer.

        Public entry point used by ``__init__.py`` at setup and on every
        options reload, so the wiring no longer pokes the private
        ``_tts_announcer`` attribute across the module boundary (#364).
        ``None`` clears it, restoring the no-op announcement path.
        """
        self._tts_announcer = announcer

    def _apply_tts_config(self, tts: dict[str, Any]) -> None:
        """Delegate to :func:`settings_handlers.apply_tts_config` (#1124)."""
        settings_handlers.apply_tts_config(self, tts)

    async def _handle_configure_tts(
        self, ws: web.WebSocketResponse, data: dict, game_state: QuizifyGameState
    ) -> None:
        """Delegate to :func:`settings_handlers.handle_configure_tts`."""
        await settings_handlers.handle_configure_tts(self, ws, data, game_state)

    # ------------------------------------------------------------------
    # "House Plays Along" runtime config (#494 Phase 4)
    # ------------------------------------------------------------------

    def set_party_lights(self, lights: QuizifyPartyLights | None) -> None:
        """Wire (or rewire) the optional party-light choreography (#494 P4).

        Public entry point used by ``__init__.py`` at setup and on every options
        reload, mirroring :meth:`set_tts_announcer` / :meth:`set_event_emitter`.
        The handler does not drive the lights (they react to the game state and
        the ``quizify_*`` bus events themselves) — it holds the reference purely
        so ``configure_house`` can push the panel's runtime overrides onto it.
        ``None`` clears it, restoring the no-op path (standalone dev server).
        """
        self._party_lights = lights

    def set_sound_effects(self, effects: QuizifySoundEffects | None) -> None:
        """Wire (or rewire) the optional room-SFX player (#494 P4).

        Same contract as :meth:`set_party_lights`: held only so the admin
        panel's ``configure_house`` overrides reach it. ``None`` clears it.
        """
        self._sound_effects = effects

    def _apply_house_config(self, house: dict[str, Any]) -> None:
        """Delegate to :func:`settings_handlers.apply_house_config` (#1124)."""
        settings_handlers.apply_house_config(self, house)

    async def _handle_configure_house(
        self, ws: web.WebSocketResponse, data: dict, game_state: QuizifyGameState
    ) -> None:
        """Delegate to :func:`settings_handlers.handle_configure_house`."""
        await settings_handlers.handle_configure_house(self, ws, data, game_state)

    async def _handle_set_language(
        self, ws: web.WebSocketResponse, data: dict, game_state: QuizifyGameState
    ) -> None:
        """Delegate to :func:`settings_handlers.handle_set_language`."""
        await settings_handlers.handle_set_language(self, ws, data, game_state)

    async def _handle_set_difficulty(
        self, ws: web.WebSocketResponse, data: dict, game_state: QuizifyGameState
    ) -> None:
        """Delegate to :func:`settings_handlers.handle_set_difficulty`."""
        await settings_handlers.handle_set_difficulty(self, ws, data, game_state)

    # ------------------------------------------------------------------
    # The house beats (#789/#788)
    # ------------------------------------------------------------------
    #
    # Nine game moments the house reacts to. Each one is ONE method here, and
    # each method is the whole fan-out: which consumers hear this beat, and
    # what they are asked to do about it. Nothing sits between a dispatch point
    # and a consumer any more.
    #
    # Six of the nine are the beats of an ordinary round. The other three are
    # the openings of the detour modes (#708) — and only the openings, because
    # every other moment inside a detour is one of the six: the chair answers a
    # question and gets a reveal, so it reports ``question_shown`` and
    # ``reveal`` rather than a mode-shaped copy of them.
    #
    # This used to be three layers deep and three names wide for the same
    # event: a tick arrived as ``time_running_out``, became ``_notify_
    # countdown``, and ended in ``_notify_tts_countdown`` plus ``_notify_house_
    # time_running_out``. Two of the six beats skipped the middle layer, so the
    # layer read as an accident rather than a contract.
    #
    # The six PUBLIC methods below are :class:`~custom_components.quizify.
    # game.drivers.protocols.MilestoneSink` — the contract a mode driver holds,
    # which is what lets every mode walk the house path (#708) instead of only
    # the normal round. The three private ones are the beats no driver reports;
    # the underscore is the entire difference between them.
    #
    # The narrator is deliberately NOT wired up as a subscriber of
    # ``QuizifyEventEmitter``, which is the shape the lights and the SFX use.
    # The emitter's fires are gated on the house master (``CONF_HOUSE_EVENTS_
    # ENABLED``, off out of the box), while narration has its own independent
    # master in the TTS panel — routing speech through the bus would silence the
    # quizmaster on every install that has narration on and house events off.

    def _fire(self, consumer: object | None, method: str, *args: object) -> None:
        """Deliver one beat to one consumer, or do nothing (#886).

        The ten hand-written forwarders this replaces were the same eight lines
        each: read the consumer, return if it is None, call it inside a
        ``try``, log the exception. Both guards are load-bearing. ``None`` is
        the standalone dev server and the HA install with no TTS entity, so it
        must stay a silent no-op rather than an Optional threaded through every
        dispatch point; and a broken TTS entity is not a reason to stall a game
        loop, which is why the exception is logged and swallowed here — the
        :class:`MilestoneSink` contract says these never raise.
        """
        if consumer is None:
            return
        try:
            getattr(consumer, method)(*args)
        except Exception:  # noqa: BLE001
            _LOGGER.exception("House beat %s raised", method)

    # -- MilestoneSink (game/drivers/protocols.py) ----------------------

    def question_shown(
        self,
        question: Question,
        round_no: int,
        total_rounds: int,
        options: list[str] | None = None,
    ) -> None:
        """A question just went out to the room (#789).

        The announcer gets the shuffled option texts, so the spoken letters
        match the TV grid; the bus event deliberately gets only the round and
        the question type, never the text or the answers, which would leak the
        question to automations before the players see it.
        """
        self._fire(
            self._tts_announcer,
            "announce_question",
            question,
            round_no,
            total_rounds,
            options,
        )
        self._fire(
            self._event_emitter,
            "notify_question_shown",
            question,
            round_no,
            total_rounds,
        )

    def time_running_out(self, seconds_remaining: float) -> None:
        """The answer window is running down (#789).

        Called on every timer tick. Each consumer keeps its own once-per-round
        guard and its own threshold — the spoken warning at 10s, the light
        pulse at 5s — so this stays a plain fan-out.
        """
        self._fire(self._tts_announcer, "announce_countdown", seconds_remaining)
        self._fire(
            self._event_emitter, "notify_time_running_out", seconds_remaining
        )

    def reveal(
        self, game_state: QuizifyGameState, summary: RoundSummary | None = None
    ) -> None:
        """The answer is out and the round has been scored (#789).

        ``summary`` overrides the game's round summary — the Hot Seat passes
        its chair's question, which is not the round summary (#1092).
        """
        self._fire(self._tts_announcer, "announce_reveal", game_state, summary)
        self._fire(
            self._event_emitter, "notify_answer_revealed", game_state, summary
        )

    # -- MilestoneSink, the detours (#708) ------------------------------
    #
    # One beat per mode, for the one moment each mode has that an ordinary
    # round does not. Everything else a detour does already arrives above:
    # the chair's question through :meth:`question_shown`, its clock through
    # :meth:`time_running_out`, its settlement through :meth:`reveal`.

    def hot_seat_started(self, hot_seat: Any) -> None:
        """The chair has been sold (#616/#708).

        The narrator gets the person and the points — the sealed bid becomes
        public at this exact moment, so this line is the room's first word on
        it. The bus event carries the percentage and the field size as well,
        which speech would only clutter.
        """
        self._fire(
            self._tts_announcer,
            "announce_hot_seat_started",
            getattr(hot_seat, "seat_holder", "") or "",
            getattr(hot_seat, "winning_stake", 0),
        )
        self._fire(self._event_emitter, "notify_hot_seat_started", hot_seat)

    def wager_open(self, game_state: QuizifyGameState, seconds: float) -> None:
        """The betting window is open (#656/#708).

        The spoken line takes no arguments: it doubles as the "final round"
        call, and reading the deadline out would be narrating a countdown the
        players already have on screen. The bus event gets the deadline,
        because an automation has no screen.
        """
        self._fire(self._tts_announcer, "announce_wager_open")
        self._fire(
            self._event_emitter, "notify_wager_open", game_state, seconds
        )

    def lightning_started(
        self, game_state: QuizifyGameState, lightning: Any
    ) -> None:
        """The fast round is starting (#42/#708)."""
        self._fire(self._tts_announcer, "announce_lightning_started")
        self._fire(
            self._event_emitter,
            "notify_lightning_started",
            game_state,
            lightning,
        )

    # -- Beats no driver reports ----------------------------------------

    def _streak_milestone(self, player_name: str, streak: int, bonus: int) -> None:
        """A player hit a streak milestone (#789).

        Not on the sink because a milestone is scored by the answer path, not
        by a mode's control loop; a driver has no occasion to report one.
        """
        self._fire(self._tts_announcer, "announce_milestone", player_name, streak)
        self._fire(
            self._event_emitter,
            "notify_streak_milestone",
            player_name,
            streak,
            bonus,
        )

    def _player_joined(self, player_name: str, is_admin: bool) -> None:
        """Somebody walked into the lobby (#281).

        Narration only: there is no ``quizify_player_joined`` bus event, and
        the host's own admin-as-player tab is skipped inside the announcer so
        the room never hears the host announce themselves.
        """
        self._fire(self._tts_announcer, "announce_join", player_name, is_admin)

    def _game_ended(self, game_state: QuizifyGameState) -> None:
        """The finale is on screen (#366).

        Bus only: the announcer says its piece in :meth:`reveal` for the final
        round, and the finale itself has no spoken line.
        """
        self._fire(self._event_emitter, "notify_game_ended", game_state)

    def set_event_emitter(
        self, emitter: QuizifyEventEmitter | None
    ) -> None:
        """Wire (or rewire) the optional HA event emitter (#366).

        Public entry point used by ``__init__.py`` at setup and on every options
        reload, mirroring :meth:`set_tts_announcer`. ``None`` clears it, restoring
        the no-op path.
        """
        self._event_emitter = emitter

    # ------------------------------------------------------------------
    # Finale broadcast helper
    # ------------------------------------------------------------------

    async def _broadcast_finale(self, game_state: QuizifyGameState) -> None:
        """Delegate: see :func:`game_broadcasts.broadcast_finale` (#1124)."""
        await game_broadcasts.broadcast_finale(self, game_state)

    # ------------------------------------------------------------------
    # Broadcast callback for game state
    # ------------------------------------------------------------------

    async def broadcast_state(self, payload: dict[str, Any] | None = None) -> None:
        """Broadcast callback — called by game state on auto-events.

        Routing lives in the BroadcastDispatcher (#184); the per-event message
        building stays here. Behaviour is unchanged: each handler re-fetches the
        current game state and no-ops if there isn't one.
        """
        await self._broadcast_dispatcher.dispatch(payload)

    async def _dispatch_round_evaluated(self) -> None:
        """Handler for the ``round_evaluated`` state event."""
        game_state = self._get_game_state()
        if game_state:
            await self._round_out.send_round_summary(game_state)
            # ONE reveal milestone (#789), after the summary broadcast: the
            # combined spoken utterance (#281) and the bus event carrying the
            # correct answer + how many got it (#366) fan out inside
            # reveal(), both off the same round summary.
            self.reveal(game_state)

    async def _dispatch_game_ended(self) -> None:
        """Handler for the ``game_ended`` state event."""
        game_state = self._get_game_state()
        if game_state:
            await self._broadcast_finale(game_state)
            # Fire the HA bus event with the final leaderboard (#366), after
            # the finale broadcast so entity state is already settled.
            self._game_ended(game_state)

    async def _dispatch_full_state(self) -> None:
        """Default handler: broadcast a full game-state snapshot."""
        game_state = self._get_game_state()
        if game_state:
            state = self._snapshot(game_state)
            state["type"] = "game_state"
            await self._conn.broadcast(state)

    async def cleanup_game_tasks(self) -> None:
        """Cancel all pending tasks — both registries (#746).

        The widest teardown there is (integration unload, dev-server
        shutdown), so it takes the round-scoped tasks *and* the broadcast
        coalescers that a round boundary deliberately leaves alone.
        """
        self._cancel_round_tasks()
        self._cancel_task_group(self._CONNECTION_SCOPED_TASKS)
        await self._conn.cleanup()
        _LOGGER.debug("Cleaned up all pending game tasks")

    # ------------------------------------------------------------------
    # Static message-dispatch table (#363)
    # ------------------------------------------------------------------
    # Built ONCE at class-definition time — NOT rebuilt per inbound message.
    # Maps message-type → (handler, admin_required). Every handler is adapted
    # to the uniform ``(self, ws, data, game_state)`` signature so the
    # centralized guard in ``_handle_message`` invokes them identically,
    # regardless of whether the underlying ``_handle_*`` method also needs
    # ``data``. ``admin_connect`` / ``reset_game`` are intentionally absent —
    # they take special authorization paths handled before this table.
    _DISPATCH: dict[
        str,
        tuple[
            Callable[
                [QuizifyWebSocketHandler, web.WebSocketResponse, dict,
                 QuizifyGameState],
                Any,
            ],
            bool,
        ],
    ] = {
        # --- non-admin (player) message types ---
        MSG_JOIN: (
            lambda self, ws, data, gs: self._handle_join(ws, data, gs),
            False,
        ),
        MSG_SUBMIT_ANSWER: (
            lambda self, ws, data, gs: self._handle_submit_answer(ws, data, gs),
            False,
        ),
        MSG_USE_POWERUP: (
            lambda self, ws, data, gs: self._handle_use_powerup(ws, data, gs),
            False,
        ),
        MSG_LIGHTNING_ANSWER: (
            lambda self, ws, data, gs: self._handle_lightning_answer(ws, data, gs),
            False,
        ),
        MSG_RECONNECT: (
            lambda self, ws, data, gs: self._handle_reconnect(ws, data, gs),
            False,
        ),
        MSG_GET_STATE: (
            lambda self, ws, data, gs: self._handle_get_state(ws, data, gs),
            False,
        ),
        MSG_REACTION: (
            lambda self, ws, data, gs: self._handle_reaction(ws, data, gs),
            False,
        ),
        MSG_SUBMIT_WAGER: (
            lambda self, ws, data, gs: self._handle_submit_wager(ws, data, gs),
            False,
        ),
        MSG_HOT_SEAT_BID: (
            lambda self, ws, data, gs: hot_seat_handlers.handle_hot_seat_bid(
                self, ws, data, gs
            ),
            False,
        ),
        MSG_HOT_SEAT_BET: (
            lambda self, ws, data, gs: hot_seat_handlers.handle_hot_seat_bet(
                self, ws, data, gs
            ),
            False,
        ),
        MSG_HOT_SEAT_ANSWER: (
            lambda self, ws, data, gs: hot_seat_handlers.handle_hot_seat_answer(
                self, ws, data, gs
            ),
            False,
        ),
        # --- admin-required message types ---
        MSG_START_GAME: (
            lambda self, ws, data, gs: self._handle_start_game(ws, data, gs),
            True,
        ),
        MSG_NEXT_QUESTION: (
            lambda self, ws, data, gs: self._handle_next_question(ws, gs),
            True,
        ),
        MSG_NEXT_ROUND: (
            lambda self, ws, data, gs: self._handle_next_question(ws, gs),
            True,
        ),
        MSG_ADMIN_SKIP: (
            lambda self, ws, data, gs: self._handle_admin_skip(ws, gs),
            True,
        ),
        MSG_END_GAME: (
            lambda self, ws, data, gs: self._handle_end_game(ws, gs),
            True,
        ),
        MSG_PLAY_AGAIN: (
            lambda self, ws, data, gs: self._handle_play_again(ws, gs),
            True,
        ),
        MSG_PAUSE_GAME: (
            lambda self, ws, data, gs: self._handle_pause_game(ws, gs),
            True,
        ),
        MSG_RESUME_GAME: (
            lambda self, ws, data, gs: self._handle_resume_game(ws, gs),
            True,
        ),
        MSG_KICK_PLAYER: (
            lambda self, ws, data, gs: self._handle_kick_player(ws, data, gs),
            True,
        ),
        MSG_CONFIGURE_TTS: (
            lambda self, ws, data, gs: self._handle_configure_tts(ws, data, gs),
            True,
        ),
        MSG_CONFIGURE_HOUSE: (
            lambda self, ws, data, gs: self._handle_configure_house(ws, data, gs),
            True,
        ),
        MSG_SET_LANGUAGE: (
            lambda self, ws, data, gs: self._handle_set_language(ws, data, gs),
            True,
        ),
        MSG_SET_DIFFICULTY: (
            lambda self, ws, data, gs: self._handle_set_difficulty(ws, data, gs),
            True,
        ),
        # --- teams (#365): player messages, refused outside the lobby ---
        MSG_CREATE_TEAM: (
            lambda self, ws, data, gs: self._handle_create_team(ws, data, gs),
            False,
        ),
        MSG_JOIN_TEAM: (
            lambda self, ws, data, gs: self._handle_join_team(ws, data, gs),
            False,
        ),
        MSG_LEAVE_TEAM: (
            lambda self, ws, data, gs: self._handle_leave_team(ws, data, gs),
            False,
        ),
    }
