"""WebSocket handler for Quizify real-time communication."""

from __future__ import annotations

import asyncio
import contextlib
import ipaddress
import logging
import math
import random
from typing import TYPE_CHECKING, Any

from aiohttp import WSMsgType, web

from custom_components.quizify.const import (
    ERR_ADMIN_REQUIRED,
    ERR_ALREADY_JOINED,
    ERR_ALREADY_SUBMITTED,
    ERR_FROZEN,
    ERR_GAME_ALREADY_STARTED,
    ERR_GAME_ENDED,
    ERR_GAME_FULL,
    ERR_GAME_NOT_STARTED,
    ERR_INVALID_ACTION,
    ERR_JOIN_RATE_LIMITED,
    ERR_NAME_INVALID,
    ERR_NAME_TAKEN,
    ERR_NO_QUESTIONS_REMAINING,
    ERR_NOT_IN_GAME,
    ERR_ROUND_EXPIRED,
    ERR_TEAM_CLOSED,
    LOBBY_DISCONNECT_GRACE_PERIOD,
    MAX_PLAYERS,
    WAGER_WINDOW_DURATION,
)
from custom_components.quizify.game.highlights import compute_superlatives
from custom_components.quizify.game.hot_seat import stake_of as hot_seat_stake
from custom_components.quizify.game.phase_controller import TICK_INTERVAL
from custom_components.quizify.game.player_registry import sanitize_player_name
from custom_components.quizify.game.powerups import (
    FREEZE_DURATION,
    PowerUpEffect,
    PowerUpType,
)
from custom_components.quizify.game.state import (
    AnswerResult,
    GamePhase,
    QuizifyGameState,
    TeamAnswerAck,
)
from custom_components.quizify.game.team import (
    ANSWER_CHANGE_LOCK_SECONDS as LIGHTNING_ANSWER_LOCK_SECONDS,
)
from custom_components.quizify.server.broadcast_dispatcher import BroadcastDispatcher
from custom_components.quizify.server.connection import ConnectionManager
from custom_components.quizify.server.rate_limit import SlidingWindowLimiter
from custom_components.quizify.server.round_message_builder import RoundMessageBuilder
from custom_components.quizify.server.serializers import (
    serialize_answer_progress,
    serialize_finale,
    serialize_leaderboard,
    serialize_player_list,
    snapshot_house_entities,
    snapshot_tts_entities,
    strip_answer_for_dashboard,
)

if TYPE_CHECKING:
    from collections.abc import Callable

    from ..analytics import PlayerStanding
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

# Admin messages that require WS-level admin (a real ?role=admin tab) rather
# than the admin-as-player relaxation ``_is_authorized_admin`` allows. These
# two reach Home Assistant service calls with host-supplied entity ids (#724).
_WS_ADMIN_ONLY = frozenset({MSG_CONFIGURE_TTS, MSG_CONFIGURE_HOUSE})
MSG_CREATE_TEAM = "create_team"
MSG_JOIN_TEAM = "join_team"
MSG_LEAVE_TEAM = "leave_team"


def _in_domain(entity_id: str, domain: str) -> bool:
    """True when ``entity_id`` names an entity of ``domain``.

    The house config arrives as untyped client JSON and its ids are handed
    straight to ``light.turn_on`` / ``scene.turn_on`` / ``media_player.play_media``.
    A domain check is the cheap half of #724: even an authorized host should not
    be able to aim the party lights at ``lock.front_door`` by editing the frame.
    """
    return entity_id.startswith(f"{domain}.")


def _entities_in_domain(entity_ids: list[str], domain: str, field: str) -> list[str]:
    """Keep only the ids of ``domain``, logging what was dropped (#724)."""
    kept = [e for e in entity_ids if _in_domain(e, domain)]
    dropped = [e for e in entity_ids if not _in_domain(e, domain)]
    if dropped:
        _LOGGER.warning(
            "House config: ignored %d %s entry/entries outside the %s domain: %s",
            len(dropped),
            field,
            domain,
            ", ".join(dropped),
        )
    return kept


def _entity_in_domain(entity_id: str, domain: str, field: str) -> str:
    """Keep ``entity_id`` only if it is of ``domain``, else "" (#724)."""
    if not entity_id or _in_domain(entity_id, domain):
        return entity_id
    _LOGGER.warning(
        "House config: ignored %s %r - not a %s entity",
        field,
        entity_id,
        domain,
    )
    return ""


def _coerce_toggle(value: Any, *, default: bool) -> bool:
    """Coerce a wire toggle value to a bool (#285).

    Front-end checkboxes serialize as a JSON ``bool``, but be defensive about
    string forms ("false"/"0"/"off"/"no") and a missing key so a malformed
    payload can't accidentally arm/disarm a setting against the default.
    """
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        return value.strip().lower() not in ("false", "0", "off", "no", "")
    return default


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
    _CONNECTION_SCOPED_TASKS: tuple[tuple[str, str], ...] = (
        ("_reaction_flush_task", "_cancel_reaction_flush"),
        ("_roster_flush_task", "_cancel_roster_flush"),
        ("_progress_flush_task", "_cancel_progress_flush"),
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
        """Evaluate the admin-grant rules for a ``role=admin`` connection.

        Token-source-agnostic (#359): the token may arrive via the
        ``X-Quizify-Token`` header, the deprecated ``?token=`` query param, or
        a first-message ``admin_auth`` frame. FAIL SOFT — a bad/absent token
        never rejects the socket, it only withholds the admin grant, so the
        host can never lock itself out.
        """
        if role != "admin":
            return False
        # Ensure the persisted token is loaded before evaluating rules.
        # async_load_admin_token() is idempotent and cheap after first call.
        await self._conn.async_load_admin_token()
        if admin_token and self._conn.validate_admin_token(admin_token):
            _LOGGER.info("Admin authenticated with valid session token")
            return True
        if await self._conn.try_bootstrap_admin():
            # Bootstrap: no token has ever been issued on this HA instance.
            # try_bootstrap_admin() grants + persists the token atomically
            # under a lock, so exactly one of two racing first-connections
            # wins (#168). The loser gets player role only.
            _LOGGER.warning(
                "ADMIN BOOTSTRAP: granting admin to first connection "
                "(ip=%s). Future restarts will require the persisted "
                "token. If this was NOT you, reset the integration.",
                request.remote,
            )
            return True
        if admin_token:
            # A token was presented but failed validation — this is the
            # interesting signal (real intrusion attempt or stale token).
            _LOGGER.warning(
                "Admin connection attempt with INVALID token rejected (ip=%s)",
                request.remote,
            )
        else:
            # No token presented and one is already on disk — the most common
            # cause is a fresh browser tab on the home LAN, not an attack.
            _LOGGER.debug(
                "Admin connection attempt without token (ip=%s)",
                request.remote,
            )
        return False

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

        ws = web.WebSocketResponse(heartbeat=self.HEARTBEAT_INTERVAL)
        await ws.prepare(request)
        if remote is not None:
            self._ip_connections[remote] = self._ip_connections.get(remote, 0) + 1

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

        try:
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
                    except ValueError:
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

    async def _handle_get_state(
        self, ws: web.WebSocketResponse, data: dict, game_state: QuizifyGameState
    ) -> None:
        """Handle a ``get_state`` request (#286)."""
        state_msg = game_state.get_state_snapshot()
        # Project into the requesting PLAYER's frame (#286): the raw
        # snapshot carries canonical answer order, but ``submit_answer``
        # maps the tapped index through the player's OWN shuffle — sending
        # canonical order here mis-scores ~2/3 of taps after a mid-round
        # reconnect (the client auto-sends get_state on every join). Pure
        # admin/dashboard sockets (no player session) keep canonical order.
        player = game_state.get_player_by_ws(ws)
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
        """Send full state to admin on connect."""
        # Cancel admin disconnect task if reconnecting
        if self._conn.has_pending_admin_disconnect():
            self._conn.cancel_admin_disconnect()
            _LOGGER.info("Admin reconnected, cancelled disconnect timeout")

        admin_token = self._conn.get_or_create_admin_token()

        state = game_state.get_state_snapshot()
        state["type"] = "game_state"
        state["join_url"] = "/quizify/player"
        state["admin_session_token"] = admin_token
        # Ride the TTS-engine + media-player lists for the narration dropdowns
        # (#281) on this already-authenticated admin frame, so the panel never
        # depends on the separate admin-token-gated /api/quizify/tts-entities
        # fetch racing the token's arrival (#356/#501). ``hass`` is None on the
        # standalone dev server → empty lists → the dropdowns' "configure in HA"
        # fallback, exactly like the HTTP endpoint.
        hass = getattr(self._runtime, "hass", None)
        entities = snapshot_tts_entities(hass)
        state["tts_entities"] = entities["tts"]
        state["media_players"] = entities["media_players"]
        # Same trick for the "House Plays Along" panel's entity pickers (#494
        # Phase 4): lights + media players + scenes ride this authenticated
        # frame, so the panel never races the admin token against the parallel
        # token-gated /api/quizify/house-entities fetch. Sent as ONE nested dict
        # (not three top-level keys) to keep the frame's namespace tidy — the
        # panel reads state.house_entities.{lights,media_players,scenes}.
        state["house_entities"] = snapshot_house_entities(hass)
        await self._conn.send(ws, state)

    # ------------------------------------------------------------------
    # Player join
    # ------------------------------------------------------------------

    async def _handle_join(
        self, ws: web.WebSocketResponse, data: dict, game_state: QuizifyGameState
    ) -> None:
        """Handle player join."""
        # Canonicalize ONCE, here, before anything is keyed on the name (#603).
        # The registry sanitizes on store; using the raw name afterwards meant
        # the session token was issued under a name the registry did not have
        # and every later `get_player(name)` missed. That silently cost the
        # host the crown and, after a wifi blip, made the player's score
        # unreachable — for any name holding a zero-width joiner, i.e. most
        # multi-codepoint emoji, and for a stray double space.
        name = sanitize_player_name(data.get("name", ""))

        if not name:
            await self._conn.send_error(ws, ERR_NAME_INVALID, "Name is required")
            return

        # Defense in depth against a second self-join (#244, cousin of #207).
        #
        # The admin "Join as Player" flow registers the host as a player on
        # the SAME admin WebSocket. If the host taps "Join as Player" again,
        # the client opens the modal afresh and (with a different typed name)
        # would otherwise create a duplicate/ghost player from the one admin
        # session. The client now hides the button after the first join, but
        # a crafted message must not slip past either: if THIS connection
        # already holds a connected player under a different name, reject the
        # duplicate join. A re-join under the SAME name is idempotent and
        # handled by the reconnect path in PlayerRegistry.add_player, so it is
        # explicitly allowed here (no-op rejoin / lobby refresh).
        existing = game_state.get_player_by_ws(ws)
        if (
            existing is not None
            and existing.connected
            and existing.name.lower() != name.lower()
        ):
            _LOGGER.warning(
                "Duplicate self-join rejected: connection already holds "
                "player %s, refusing second join as %s",
                existing.name,
                name,
            )
            await self._conn.send_error(
                ws,
                ERR_ALREADY_JOINED,
                "Already joined as a player",
            )
            return

        # Auto-append number if name is taken
        #
        # #448: gate on ``is_active`` (connected AND ws open), not the raw
        # ``connected`` flag. After a reload the old slot can linger with
        # ``connected = True`` but a CLOSED WebSocket — the "stale connected
        # flag, old WS closed" case that PlayerRegistry.add_player treats as a
        # legitimate rejoin/reclaim. Renaming to "Name 2" here (before
        # add_player ever sees the original name) made that reclaim branch
        # unreachable, spawning a duplicate ghost with score 0. Falling through
        # on a stale slot lets add_player reclaim the original name; genuinely
        # live duplicates (ws still open) still get the "Name 2" suffix.
        #
        # ``existing.ws is not ws`` (#603): once the name is canonicalized, an
        # idempotent rejoin from the SAME connection — a lobby refresh, the
        # admin's redirect from /quizify/admin to /quizify/player — matches an
        # active slot that IS this connection. Renaming it to "Name 2" would
        # spawn a score-0 duplicate of the player who is already sitting there.
        # Before canonicalization this never surfaced, because the raw name
        # differed from the stored one and the duplicate-self-join guard above
        # rejected the rejoin outright instead. Both behaviours were wrong; a
        # rejoin under the same name from the same socket is a no-op reclaim.
        original_name = name
        counter = 2
        while (
            (existing := game_state.get_player(name))
            and existing.is_active
            and existing.ws is not ws
        ):
            name = f"{original_name} {counter}"
            counter += 1

        success, error_code = game_state.add_player(name, ws)

        if success:
            # Cancel pending removal on reconnect
            self._conn.cancel_pending_removal(name)

            # Generate session token for reconnect
            session_token = self._conn.create_session_token(name)

            # Admin-as-player: trust `is_admin: true` in the join message.
            #
            # This is the Beatify pattern (which has shipped without bugs
            # for years). Earlier Quizify releases tried to cryptographically
            # validate an admin token threaded through the player's join
            # message; that pattern created a brittle state machine where
            # browser sessionStorage and server token storage could drift
            # apart with no in-product recovery (8 betas worth of bugs).
            #
            # Trust trade-off: a malicious client on the LAN could send
            # `is_admin: true` and become admin. Mitigations:
            #   - The user's home LAN is generally trusted.
            #   - Nabu Casa already requires HA auth to reach the
            #     integration through its tunnel.
            #   - "First admin claims it" still applies; only one admin
            #     slot exists per game.
            # The persisted admin token is still validated for the pure
            # admin-dashboard WebSocket connect (`?role=admin&token=...`),
            # which is the higher-stakes path. Player joins are simpler.
            # See DESIGN.md for the full rationale.
            player_obj = game_state.get_player(name)
            if player_obj and data.get("is_admin"):
                # Single-admin invariant (#208): exactly one player may hold
                # the crown per game. Only grant admin if no *other* player is
                # already admin. A re-claim by the same name (e.g. the admin's
                # redirect from /quizify/admin to /quizify/player re-joining
                # under the same name) is idempotent and still granted; a
                # claim by a *different* player while an admin exists is
                # rejected — the original admin keeps the crown. Rejecting
                # rather than taking over is the safer behaviour: it stops any
                # LAN client from seizing control mid-game by sending
                # `is_admin: true`.
                if game_state.has_other_admin(name):
                    _LOGGER.warning(
                        "Admin claim rejected for %s: a different player "
                        "already holds the single admin slot",
                        name,
                    )
                else:
                    # Crown-recovery (#207 regression of #209): if a *stale*
                    # (disconnected) admin slot still lingers under a different
                    # name — the host's old /admin slot during the
                    # /admin -> /player redirect — demote it before crowning the
                    # re-joining host. has_other_admin() no longer blocks on a
                    # disconnected admin, so without this demotion two players
                    # would briefly carry is_admin and break the #208 invariant.
                    #
                    # #358: transferring the crown from a stale admin to a
                    # DIFFERENT name is the takeover vector. During the host's
                    # reload / wifi blip the real admin is momentarily
                    # disconnected (so has_other_admin() is False), and any LAN
                    # client sending `is_admin: true` under a new name would
                    # otherwise demote the host and seize control mid-game.
                    # Require proof: a valid admin session token in the join.
                    # Fail SOFT — no error, just no crown — so we don't
                    # reintroduce the brittle token state machine DESIGN.md
                    # warns about; the legit host still recovers via the
                    # token-based reconnect path, which preserves is_admin on
                    # their own slot. A first claim (no admin yet) and a
                    # same-name reclaim stay token-free (the documented Beatify
                    # trust trade-off).
                    stale_admin = game_state.get_admin()
                    if stale_admin is not None and stale_admin.name != name:
                        claim_token = data.get("admin_token")
                        if claim_token and self._conn.validate_admin_token(
                            claim_token
                        ):
                            stale_admin.is_admin = False
                            player_obj.is_admin = True
                            _LOGGER.info(
                                "Crown transferred from stale admin %s to %s",
                                stale_admin.name,
                                name,
                            )
                        else:
                            _LOGGER.warning(
                                "Crown transfer denied for %s: no valid admin "
                                "token; stale admin %s keeps the crown",
                                name,
                                stale_admin.name,
                            )
                    else:
                        player_obj.is_admin = True
            elif player_obj and player_obj.is_admin:
                # #389 (P2 security): silent crown inheritance via a LOBBY
                # name-rejoin. In LOBBY a disconnected player's slot can be
                # reclaimed by simply re-typing the same name, with NO session
                # token (PlayerRegistry.add_player, the ``phase_value ==
                # "LOBBY"`` reconnect branch). If that slot was the host's, the
                # reclaimer INHERITS is_admin — and because this join carries no
                # ``is_admin: true`` claim, the #358 crown-gating block above
                # never runs to vet it. So an attacker who just types the host's
                # exact name in the plain player-join form, while the host is
                # briefly disconnected in the lobby, silently seizes the crown
                # with no token.
                #
                # (Reaching this branch means the join did NOT claim admin, yet
                # the resulting slot holds is_admin — a freshly added player
                # starts non-admin, so the crown can only have been inherited
                # from the reclaimed slot.)
                #
                # Strip the inherited crown unless the joiner proves ownership
                # with a valid admin session token — same proof and FAIL-SOFT
                # posture as #358 (never reject the join). The legit host's
                # admin-as-player tab always re-sends ``is_admin: true`` on join
                # (see player-core.js), so it takes the #358 path above and is
                # unaffected here; a token holder still keeps the crown even on
                # this path.
                claim_token = data.get("admin_token")
                if not (
                    claim_token
                    and self._conn.validate_admin_token(claim_token)
                ):
                    _LOGGER.warning(
                        "Inherited admin crown stripped for %s: LOBBY "
                        "name-rejoin without a valid admin token (#389)",
                        name,
                    )
                    player_obj.is_admin = False

            # If a lightning round is mid-flight, register the late joiner so
            # they can score from the next question on (issue #42).
            if game_state.phase == GamePhase.LIGHTNING and game_state.lightning:
                game_state.lightning.add_player(name)

            # Cancel any deferred admin-disconnect pause: the admin's
            # redirect from /quizify/admin to /quizify/player took the
            # fresh-join path (no session token) instead of the
            # reconnect path. Same desired outcome — game keeps running.
            # Also clear the admin-disconnect grace armed by the closed admin
            # WS: the host is right here as a player, so nothing is "away".
            # (It no longer wipes the token on expiry — #725 — but leaving it
            # armed would keep has_pending_admin_disconnect() lying.)
            if player_obj and player_obj.is_admin:
                self._cancel_admin_pause()
                self._conn.cancel_admin_disconnect()

            # Send join confirmation with session token and assigned color
            powerup = game_state.get_player_powerup(name)
            await self._conn.send(ws, {
                "type": "joined",
                "player_id": name,
                "powerup": powerup.value if powerup else None,
                "session_token": session_token,
                "color": player_obj.color if player_obj else "",
                "is_admin": player_obj.is_admin if player_obj else False,
                # All-time standing for THIS player only (#371, variant A).
                # Rides the join frame rather than the roster broadcast: it
                # is per-player data, it never changes mid-lobby, and putting
                # it in the coalesced roster frame would ship everyone's
                # history to every phone. ``None`` for a first-timer.
                "all_time": self._all_time_standing(name),
            })

            # Send current state to the joining player. Project the
            # player-agnostic snapshot into THIS player's frame (#253): own
            # shuffle order for the answer buttons, own timer, flat reveal —
            # otherwise a mid-round joiner mis-scores their taps and sees an
            # empty reveal.
            state = game_state.get_state_snapshot()
            if player_obj is not None:
                state = self._round_messages.project_snapshot_for_player(
                    game_state, snapshot=state, player=player_obj
                )
            state["type"] = "game_state"
            await self._conn.send(ws, state)

            # Broadcast player list to everyone — coalesced (#453).
            self._mark_roster_dirty("player_joined")

            # Narrate the join (#281). The host's own admin-as-player tab is
            # skipped inside the announcer so the room doesn't hear the host
            # announce themselves. Pre-game lobby joins narrate because the
            # admin pushes the TTS config on connect via ``configure_tts``.
            self._notify_tts_join(
                name, bool(player_obj.is_admin) if player_obj else False
            )
        else:
            # English i18n-fallback strings only — the client localizes off
            # the structured ``code`` via ``t('join.refused.<CODE>')`` and only
            # falls back to this ``message`` if the key is missing
            # (player-core.js).
            #
            # #729: every code ``add_player`` can return must appear here.
            # ``ERR_GAME_ENDED`` did not, so a guest who scanned a QR code from
            # a finished game got the bare "Failed to join" — no hint that the
            # game was over and the host had to start a new one.
            error_messages = {
                ERR_NAME_TAKEN: "Name already taken",
                ERR_NAME_INVALID: "Please enter a name",
                ERR_GAME_FULL: "Game is full",
                ERR_GAME_ENDED: "This game has already finished",
            }
            await self._conn.send_error(
                ws, error_code or ERR_INVALID_ACTION,
                error_messages.get(error_code or "", "Failed to join"),
            )

    # ------------------------------------------------------------------
    # Player reconnect (session-based)
    # ------------------------------------------------------------------

    async def _handle_reconnect(
        self, ws: web.WebSocketResponse, data: dict, game_state: QuizifyGameState
    ) -> None:
        """Handle player reconnect with session token."""
        token = data.get("session_token", "")
        name = self._conn.get_player_for_token(token)

        if not name:
            # Token not found — treat as unknown, client should show join form
            await self._conn.send(ws, {"type": "reconnect_failed"})
            return

        player = game_state.get_player(name)
        if not player:
            # Player was fully removed — token stale
            self._conn.revoke_token(token)
            await self._conn.send(ws, {"type": "reconnect_failed"})
            return

        # Restore player connection
        player.ws = ws
        player.connected = True
        self._conn.cancel_pending_removal(name)

        # If this is the admin returning from the intentional
        # /quizify/admin → /quizify/player redirect after Start,
        # cancel the deferred pause scheduled by _handle_disconnect.
        # Without this, the pause would fire ~4s after the redirect
        # completes — the user would see the question briefly, then
        # the paused-view, defeating the whole grace-period fix.
        # Also clear the admin-disconnect grace (#351) so the returning host
        # isn't still counted as away. Since #725 the grace no longer wipes
        # the persisted token, only reports "no admin socket attached".
        if player.is_admin:
            self._cancel_admin_pause()
            self._conn.cancel_admin_disconnect()

        _LOGGER.info("Player session-reconnected: %s", name)

        # If we paused on admin disconnect and this is the admin coming
        # back, auto-resume the game so players don't sit on the paused
        # screen wondering. Resume only when WE caused the pause —
        # leave admin-initiated pauses alone.
        if (
            player.is_admin
            and game_state.phase == GamePhase.PAUSED
            and game_state.get_pause_reason() == "admin_disconnected"
            and game_state.resume()
        ):
            self._start_timer_tick(game_state)
            # Broadcast the resumed state to EVERY player (#287). Without
            # this, the other players stay frozen on the "Host disconnected"
            # paused-view (player-core.js only leaves it on a game_state
            # message) while their timers tick down → scored as timeouts.
            # Per-player PROJECTED — same mechanism as the manual-resume
            # fix (#286) so answer buttons keep the right shuffle order.
            await self._broadcast_state_projected(game_state)
            _LOGGER.info("Auto-resumed after admin reconnect")

        # Generate a fresh token and revoke old one
        new_token = self._conn.rotate_session_token(token, name)

        # Send reconnect success with new token
        powerup = game_state.get_player_powerup(name)
        await self._conn.send(ws, {
            "type": "reconnected",
            "player_id": name,
            "session_token": new_token,
            "powerup": powerup.value if powerup else None,
            # Same per-player standing as the join frame (#371) — a player who
            # reloads their phone in the lobby must not lose the line.
            "all_time": self._all_time_standing(name),
        })

        # Send full game state, projected into THIS player's frame (#253):
        # the reconnect snapshot otherwise carries canonical answer order
        # (mis-scoring taps) and a nested round_summary the reveal can't read.
        state = game_state.get_state_snapshot()
        state = self._round_messages.project_snapshot_for_player(
            game_state, snapshot=state, player=player
        )
        state["type"] = "game_state"
        await self._conn.send(ws, state)

        # Broadcast updated player list — coalesced (#453).
        self._mark_roster_dirty("player_joined")

    # ------------------------------------------------------------------
    # Teams (#365)
    # ------------------------------------------------------------------

    async def _broadcast_teams(self, game_state: QuizifyGameState) -> None:
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

    async def _handle_create_team(
        self, ws: web.WebSocketResponse, data: dict, game_state: QuizifyGameState
    ) -> None:
        """Open a team and put the requesting player in it (lobby only)."""
        player = game_state.get_player_by_ws(ws)
        if not player:
            await self._conn.send_error(ws, ERR_NOT_IN_GAME, "Not in game")
            return

        team = game_state.create_team(str(data.get("name", ""))[:24], player.name)
        if team is None:
            # Teams are fixed once the game starts — a latecomer plays alone.
            await self._conn.send_error(
                ws, ERR_TEAM_CLOSED, "Teams are set for this game"
            )
            return

        await self._conn.send(ws, {"type": "team_joined", "team": team})
        await self._broadcast_teams(game_state)

    async def _handle_join_team(
        self, ws: web.WebSocketResponse, data: dict, game_state: QuizifyGameState
    ) -> None:
        """Join an existing team (lobby only)."""
        player = game_state.get_player_by_ws(ws)
        if not player:
            await self._conn.send_error(ws, ERR_NOT_IN_GAME, "Not in game")
            return

        team = game_state.join_team(str(data.get("team_id", "")), player.name)
        if team is None:
            # Either the game has started or the team dissolved while the
            # player was tapping it — the same answer either way: it is gone.
            await self._conn.send_error(
                ws, ERR_TEAM_CLOSED, "That team is no longer open"
            )
            return

        await self._conn.send(ws, {"type": "team_joined", "team": team})
        await self._broadcast_teams(game_state)

    async def _handle_leave_team(
        self, ws: web.WebSocketResponse, data: dict, game_state: QuizifyGameState
    ) -> None:
        """Leave the current team (lobby only). The last one out dissolves it."""
        player = game_state.get_player_by_ws(ws)
        if not player:
            await self._conn.send_error(ws, ERR_NOT_IN_GAME, "Not in game")
            return

        if not game_state.leave_team(player.name):
            await self._conn.send_error(
                ws, ERR_TEAM_CLOSED, "Teams are set for this game"
            )
            return

        await self._conn.send(ws, {"type": "team_left"})
        await self._broadcast_teams(game_state)

    async def _broadcast_team_answer(
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
            if member is None or member.ws is None or not member.connected:
                continue
            shuffle = game_state.get_player_shuffle(name)
            try:
                shown_index = shuffle.index(ack.answer_index)
            except ValueError:
                # No shuffle stored for this member yet (they joined between
                # the question start and this tap). Their client re-reads the
                # answer from the next projected snapshot.
                continue
            await self._conn.send(member.ws, {
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

    # ------------------------------------------------------------------
    # Submit answer
    # ------------------------------------------------------------------

    async def _handle_submit_answer(
        self, ws: web.WebSocketResponse, data: dict, game_state: QuizifyGameState
    ) -> None:
        """Handle answer submission from player."""
        player = game_state.get_player_by_ws(ws)
        if not player:
            await self._conn.send_error(ws, ERR_NOT_IN_GAME, "Not in game")
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
                await self._conn.send_error(ws, ERR_INVALID_ACTION, "Invalid guess")
                return
            err = game_state.submit_guess(player.name, float(guess_raw))
            if err is None:
                await self._conn.send(ws, {"type": "guess_accepted"})
            else:
                error_messages = {
                    ERR_ALREADY_SUBMITTED: "Already answered",
                    ERR_ROUND_EXPIRED: "Time is up",
                    ERR_FROZEN: "Frozen — wait for the freeze to end",
                    ERR_NOT_IN_GAME: "Not in the game",
                    ERR_GAME_NOT_STARTED: "No active game",
                    ERR_INVALID_ACTION: "Invalid action",
                }
                await self._conn.send_error(ws, err, error_messages.get(err, err))
            return

        shuffled_index = data.get("answer_index")
        if shuffled_index is None or not isinstance(shuffled_index, int):
            await self._conn.send_error(ws, ERR_INVALID_ACTION, "Invalid answer index")
            return

        # Map shuffled index back to original index — use the player's
        # own shuffle (anti-cheat per-player), falling back to canonical.
        player_shuffle = game_state.get_player_shuffle(player.name)
        if 0 <= shuffled_index < len(player_shuffle):
            original_index = player_shuffle[shuffled_index]
        else:
            await self._conn.send_error(
                ws, ERR_INVALID_ACTION, "Answer index out of range"
            )
            return

        result = game_state.submit_answer(player.name, original_index)

        if isinstance(result, TeamAnswerAck):
            # Team mode (#365): the tap set the team's answer, it did not score
            # anything. Every member — the setter included — gets the standing
            # answer in their own answer order, so the dots appear on the same
            # question for all of them.
            await self._broadcast_team_answer(game_state, result, setter=player.name)
            self._mark_progress_dirty()
            return

        if isinstance(result, AnswerResult):
            # #619: the room can see who it is waiting for. Coalesced, so a
            # simultaneous tap-storm is one frame, not one per tap.
            self._mark_progress_dirty()
            await self._conn.send(ws, {
                "type": "answer_result",
                "correct": result.correct,
                "points_earned": result.points_earned,
                "speed_bonus": result.speed_bonus,
                "streak_bonus": result.streak_bonus,
                "difficulty_multiplier": result.difficulty_multiplier,
                "new_streak": result.new_streak,
                "new_total": result.new_total,
                "milestone_bonus": result.milestone_bonus,
                "milestone_streak": result.milestone_streak,
            })
            # A milestone is announced out loud and on the HA bus — not over
            # the socket (#750). There used to be a ``streak_milestone``
            # broadcast here "so the TV/admin view can flash", but no surface
            # ever grew that flash, and the phone toast has always been driven
            # off ``new_streak`` in the answer_result above. A message with no
            # reader is worse than no message: it reads like a contract.
            if result.milestone_bonus:
                # Speak it if TTS is configured. Cheap to look up; the
                # announcer no-ops if no TTS entity is set.
                self._notify_tts_milestone(player.name, result.milestone_streak)
                # Fire the HA bus event so the host can automate on a streak
                # (#366).
                self._notify_house_milestone(
                    player.name, result.milestone_streak, result.milestone_bonus
                )
            # NB: round-summary broadcast is fired exclusively by
            # state._fire_broadcast("round_evaluated") \u2192 broadcast_state().
            # Do NOT broadcast here \u2014 that would double-fire when the timer
            # path races with all-submitted (#3 in logical review).
        elif isinstance(result, str):
            # English i18n-fallback strings only (client localizes off ``code``).
            error_messages = {
                ERR_ALREADY_SUBMITTED: "Already answered",
                ERR_ROUND_EXPIRED: "Time is up",
                ERR_FROZEN: "Frozen — wait for the freeze to end",
                ERR_NOT_IN_GAME: "Not in the game",
                ERR_GAME_NOT_STARTED: "No active game",
            }
            await self._conn.send_error(
                ws, result, error_messages.get(result, result)
            )

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
        """Broadcast an emoji reaction. If the reaction comes in during
        ANSWER_REVEAL, also award a +1 bonus to each correct answerer
        from that round — cheap "audience appreciation" mechanic. The
        reactor themselves can only grant one such bonus per round
        (tracked on PlayerSession.reaction_bonuses_given), and each
        correct answerer caps at _REACTION_BONUS_CAP_PER_ROUND incoming
        bonuses so a 6-player room can't pile 5 free points on the
        leader every reveal."""
        reactor = game_state.get_player_by_ws(ws)
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
        self._enqueue_reaction(reactor.name, emoji)

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

        # Award +1 to each player who answered correctly this round,
        # respecting the per-round incoming cap.
        bonus_recipients: list[str] = []
        for result in summary.results:
            if not result.correct:
                continue
            recipient = game_state.get_player(result.player_id)
            if not recipient or recipient.name == reactor.name:
                continue  # can't tip your own hat
            # Per-round inbound counter (separate from `reaction_bonuses_given`
            # which tracks OUTGOING bonuses). Encapsulated on PlayerSession so
            # it is reset per game in reset_for_new_game() (#167) and the cap
            # logic lives with the state it guards (#364).
            if not recipient.add_reaction_bonus(
                round_num, self._REACTION_BONUS_CAP_PER_ROUND
            ):
                continue  # recipient already at the per-round cap
            bonus_recipients.append(recipient.name)

        if bonus_recipients:
            # #449: the bonus above mutated recipient scores, but the #414
            # round-summary memo (built for the reveal broadcast) still holds
            # the pre-bonus leaderboard. Invalidate it so any join/reconnect/
            # get_state during this same ANSWER_REVEAL re-serializes the fresh,
            # post-bonus leaderboard instead of serving the stale cache.
            invalidate = getattr(game_state, "invalidate_round_summary_msg", None)
            if invalidate is not None:
                invalidate()
            # #416: defer + coalesce the leaderboard broadcast into the shared
            # ~150ms flush window instead of emitting a full-leaderboard frame
            # per reactor right here. The points are already awarded above; the
            # flush builds one ``reaction_bonus`` reflecting the whole batch.
            self._reaction_bonus_from[reactor.name] = None
            for recipient_name in bonus_recipients:
                self._reaction_bonus_to[recipient_name] = None
            # The visual reaction above already armed the flush task, but arm it
            # defensively so a bonus can never sit undrained.
            self._ensure_reaction_flush()

    def _enqueue_reaction(self, player_name: str, emoji: str) -> None:
        """Buffer a visual reaction and ensure a flush task is pending (#304).

        Reactions are keyed on ``(player_name, emoji)`` so a player mashing the
        same emoji within a flush window collapses to a single broadcast; a
        ``dict`` preserves insertion order so distinct reactions flush in the
        order they first arrived. The flush task is (re)started lazily and runs
        exactly one window before draining.
        """
        self._reaction_buffer[(player_name, emoji)] = None
        self._ensure_reaction_flush()

    def _ensure_reaction_flush(self) -> None:
        """(Re)arm the coalescing flush task if one isn't already pending.

        Shared by the visual-reaction buffer (#304) and the reveal
        reaction-bonus buffer (#416) so either kind of buffered event guarantees
        a flush within one window.
        """
        if self._reaction_flush_task is None or self._reaction_flush_task.done():
            self._reaction_flush_task = self._runtime.create_task(
                self._flush_reactions_after_window()
            )

    async def _flush_reactions_after_window(self) -> None:
        """Wait one coalescing window, then broadcast the buffered reactions.

        One ``reaction`` message per distinct buffered ``(player, emoji)`` —
        same wire shape the client already renders, just batched so a burst of
        spam produces a handful of frames instead of one per inbound message.
        """
        try:
            await asyncio.sleep(self._REACTION_FLUSH_WINDOW)
        except asyncio.CancelledError:
            # Cancelled on cleanup — drop whatever was buffered (best-effort
            # eye-candy, nothing to persist) and re-raise so the task ends.
            self._reaction_buffer.clear()
            self._reaction_bonus_from.clear()
            self._reaction_bonus_to.clear()
            raise
        buffered = list(self._reaction_buffer)
        self._reaction_buffer.clear()
        # Collect every broadcast for this window, then fan them all out in a
        # single gather (#416) instead of awaiting them one at a time.
        broadcasts = [
            self._conn.broadcast({
                "type": "reaction",
                "emoji": emoji,
                "player_name": player_name,
            })
            for player_name, emoji in buffered
        ]

        # #416: collapse the reveal reaction-bonus events buffered this window
        # into ONE ``reaction_bonus``. The leaderboard is serialized once, now —
        # after every point award in the window has settled — so it reflects the
        # whole batch. ``from_player`` keeps the existing single-name wire field
        # (first reactor) for the client toast; ``from_players`` carries the full
        # set for completeness.
        if self._reaction_bonus_to:
            from_players = list(self._reaction_bonus_from)
            to_players = list(self._reaction_bonus_to)
            self._reaction_bonus_from.clear()
            self._reaction_bonus_to.clear()
            gs = self._get_game_state()
            if gs is not None and from_players:
                broadcasts.append(self._conn.broadcast({
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
        if self._reaction_buffer or self._reaction_bonus_to:
            self._reaction_flush_task = self._runtime.create_task(
                self._flush_reactions_after_window()
            )

    def _cancel_reaction_flush(self) -> None:
        """Cancel any pending reaction-flush task (called on cleanup)."""
        if self._reaction_flush_task is not None:
            self._reaction_flush_task.cancel()
            self._reaction_flush_task = None
        self._reaction_buffer.clear()
        self._reaction_bonus_from.clear()
        self._reaction_bonus_to.clear()

    def _mark_roster_dirty(self, event_type: str) -> None:
        """Flag the roster as changed and (re)arm the coalescing flush (#453).

        ``event_type`` is ``"player_joined"`` or ``"player_left"`` and only
        determines the wire ``type`` of the coalesced message (the join/leave
        animation the client plays); the ``players`` list itself is always the
        live roster serialized at flush time. Mixed joins+leaves in one window
        collapse to a single frame typed by the LAST event.
        """
        self._roster_dirty = True
        self._roster_last_type = event_type
        self._ensure_roster_flush()

    def _ensure_roster_flush(self) -> None:
        """(Re)arm the roster-coalescing flush task if none is pending."""
        if self._roster_flush_task is None or self._roster_flush_task.done():
            # Stored on self (no GC risk), matching the timer-tick / lightning /
            # admin-pause fire-and-forget pattern in this handler.
            self._roster_flush_task = asyncio.ensure_future(
                self._flush_roster_after_window()
            )
            self._roster_flush_task.add_done_callback(self._log_task_exception)

    async def _flush_roster_after_window(self) -> None:
        """Wait one window, then broadcast ONE roster frame (#453).

        Mirrors ``_flush_reactions_after_window``: coalesces a burst of roster
        changes into a single ``player_joined`` / ``player_left`` carrying the
        current player list. Re-arms itself if more changes landed during the
        broadcast so the final roster always reaches every client.
        """
        try:
            await asyncio.sleep(self._REACTION_FLUSH_WINDOW)
        except asyncio.CancelledError:
            self._roster_dirty = False
            raise

        if not self._roster_dirty:
            return

        event_type = self._roster_last_type
        self._roster_dirty = False
        gs = self._get_game_state()
        if gs is not None:
            await self._conn.broadcast({
                "type": event_type,
                "players": serialize_player_list(gs.get_players()),
                # A player leaving also leaves their team, and the last one out
                # dissolves it (#365) — so the roster frame carries the teams
                # too, or the lobby keeps showing a team nobody is in.
                "teams": gs.team_registry.to_list(),
            })
            await self._send_head_to_head(gs)

        # A roster change that landed DURING the broadcast set _roster_dirty
        # again; _ensure_roster_flush won't start a new task while this one is
        # running, so re-arm here to drain the tail (mirrors #354).
        if self._roster_dirty:
            self._roster_flush_task = asyncio.ensure_future(
                self._flush_roster_after_window()
            )
            self._roster_flush_task.add_done_callback(self._log_task_exception)

    def _mark_progress_dirty(self) -> None:
        """Flag answer-progress as changed and (re)arm its coalescing flush."""
        self._progress_dirty = True
        if self._progress_flush_task is None or self._progress_flush_task.done():
            self._progress_flush_task = asyncio.ensure_future(
                self._flush_progress_after_window()
            )
            self._progress_flush_task.add_done_callback(self._log_task_exception)

    async def _flush_progress_after_window(self) -> None:
        """Wait one window, then broadcast ONE answer-progress frame (#619).

        Mirrors ``_flush_roster_after_window`` down to the tail re-arm: taps
        that land during the broadcast set the flag again, and no new task can
        start while this one runs.

        The list is serialized at flush time, not at tap time, so the frame
        always carries the room as it is when it goes out rather than as it was
        when the first tap of the window arrived.
        """
        try:
            await asyncio.sleep(self._REACTION_FLUSH_WINDOW)
        except asyncio.CancelledError:
            self._progress_dirty = False
            raise

        if not self._progress_dirty:
            return

        self._progress_dirty = False
        gs = self._get_game_state()
        if gs is not None:
            await self._conn.broadcast(serialize_answer_progress(gs.get_players()))

        if self._progress_dirty:
            self._progress_flush_task = asyncio.ensure_future(
                self._flush_progress_after_window()
            )
            self._progress_flush_task.add_done_callback(self._log_task_exception)

    def _cancel_progress_flush(self) -> None:
        """Cancel any pending answer-progress flush (called on cleanup)."""
        if self._progress_flush_task is not None:
            self._progress_flush_task.cancel()
            self._progress_flush_task = None
        self._progress_dirty = False

    async def _send_head_to_head(self, game_state: QuizifyGameState) -> None:
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
        await self._broadcast_head_to_head(game_state, at="lobby")

    async def _dispatch_end_head_to_head(self) -> None:
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
        game_state = self._get_game_state()
        if game_state is None or game_state.phase != GamePhase.FINALE:
            return
        await self._broadcast_head_to_head(game_state, at="finale")

    async def _broadcast_head_to_head(
        self, game_state: QuizifyGameState, *, at: str
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
        duel = analytics.get_head_to_head(
            [p.name for p in game_state.get_players()]
        )
        if duel is None:
            # Fewer than two present, or no pair has met twice. Silence beats a
            # "1-0" that reads like a record and is a coincidence.
            return
        await self._conn.broadcast_to_admins_and_dashboards(
            {"type": "head_to_head", "at": at, **duel}
        )

    def _cancel_roster_flush(self) -> None:
        """Cancel any pending roster-flush task (called on cleanup)."""
        if self._roster_flush_task is not None:
            self._roster_flush_task.cancel()
            self._roster_flush_task = None
        self._roster_dirty = False

    # ------------------------------------------------------------------
    # Wager (gameplay idea #3 — Jeopardy-style final round)
    # ------------------------------------------------------------------

    async def _handle_submit_wager(
        self, ws: web.WebSocketResponse, data: dict, game_state: QuizifyGameState
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
        player = game_state.get_player_by_ws(ws)
        if not player:
            await self._conn.send_error(ws, ERR_NOT_IN_GAME, "Not in game")
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
            await self._conn.send_error(
                ws, ERR_INVALID_ACTION, "Wager not available on estimate rounds"
            )
            return

        if game_state.phase != GamePhase.WAGER_ACTIVE:
            return  # silent: betting window not open (or already closed)

        # A wager only makes sense *before* the answer is locked in — the wager
        # stakes points on getting that answer right. Once the player has
        # submitted, accepting a new wager silently mutated the stake on an
        # already-locked answer (and the client never learned it was a no-op).
        # Reject explicitly so the player isn't misled. (#255.)
        if player.submitted:
            await self._conn.send_error(
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
            await self._conn.send_error(ws, ERR_INVALID_ACTION, "Invalid wager")
            return
        if not 0 <= wager_int <= 100:
            await self._conn.send_error(ws, ERR_INVALID_ACTION, "Wager must be 0-100")
            return

        player.wager = wager_int
        await self._conn.send(ws, {
            "type": "wager_accepted",
            "wager": wager_int,
        })
        # Host/TV tally — how many bets are in, never how big they are (#656).
        await self._conn.broadcast_to_admins_and_dashboards(
            self._round_messages.build_wager_progress(game_state)
        )
        # Everyone in: close early rather than making a decided table sit out
        # the rest of the deadline.
        if not game_state.players_missing_wager():
            await self._close_wager_window(game_state)

    async def _handle_use_powerup(
        self, ws: web.WebSocketResponse, data: dict, game_state: QuizifyGameState
    ) -> None:
        """Handle power-up usage."""
        player = game_state.get_player_by_ws(ws)
        if not player:
            await self._conn.send_error(ws, ERR_NOT_IN_GAME, "Not in game")
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

                await self._conn.send(ws, {
                    "type": "powerup_applied",
                    "powerup_type": "joker",
                    "source_player": result.source_player,
                    "joker_remove_index": shuffled_remove_idx,
                })
                # Public broadcast w/o joker_remove_index — opponents get a
                # "Player X used Joker" toast (the removed-answer index is
                # per-shuffle so it stays in the private send above).
                await self._conn.broadcast({
                    "type": "powerup_applied",
                    "powerup_type": "joker",
                    "source_player": result.source_player,
                })
            elif result.type == PowerUpType.STEAL:
                effect_data["stolen_points"] = result.stolen_points
                await self._conn.broadcast(effect_data)
            else:
                # FREEZE: tell the target how long the lockout lasts so the
                # client can show a countdown overlay (#300). Server is the
                # authority — it rejects submits via ERR_FROZEN regardless of
                # what the client does with this.
                if result.type == PowerUpType.FREEZE:
                    effect_data["freeze_duration"] = FREEZE_DURATION
                await self._conn.broadcast(effect_data)
        elif isinstance(result, str):
            await self._conn.send_error(ws, result, "Power-up not available")

    # ------------------------------------------------------------------
    # Admin: start game
    # ------------------------------------------------------------------

    async def _handle_start_game(
        self, ws: web.WebSocketResponse, data: dict, game_state: QuizifyGameState
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
        self._cancel_round_tasks()

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
            await self._conn.send_error(ws, code, str(err))
            return

        # Socket-independent continuation (TTS config, grace, first question).
        # Shared with the HA ``quizify.start_game`` service so the WS path and
        # the service path drive one implementation (#367).
        await self._after_start_game(
            game_state,
            data.get("tts") or {},
            house_config=data.get("house"),
        )

    async def _after_start_game(
        self,
        game_state: QuizifyGameState,
        tts_config: dict,
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
        # wired (standalone dev server, HA without a TTS entity). The service
        # path passes an empty dict (no per-game overrides).
        self._apply_tts_config(tts_config)

        # Apply the "House Plays Along" settings the same way (#494 Phase 4) —
        # the admin panel rides them on the start_game payload under ``house``,
        # alongside ``tts``.
        #
        # Deliberately NOT symmetric with the TTS line above: an ABSENT block is
        # skipped rather than applied as an empty dict. ``_apply_house_config``
        # reads the master as ``bool(house.get("enabled"))`` (default off, like
        # TTS), so feeding it ``{}`` would silently disarm lights/SFX/events that
        # the host had switched on in the config-entry options. Two callers hit
        # that case: ``admin_action_start_game`` (the HA-service path, which has
        # no panel and passes nothing) and any admin client that omits the block.
        # Skipping leaves whatever is already in force — the config-entry options
        # plus whatever the lobby-time ``configure_house`` push installed — which
        # is the only non-destructive reading of "no per-game override".
        if house_config:
            self._apply_house_config(house_config)

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
        await asyncio.sleep(self.START_REDIRECT_GRACE)

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
        await self._start_next_question(game_state)

    async def admin_action_start_game(self, game_state: QuizifyGameState) -> None:
        """Start a game with default settings — HA-service entry point (#367).

        Callable without an admin WebSocket connection so hosts can start the
        quiz via Assist voice, a Zigbee remote or a dashboard button. Only valid
        from the LOBBY phase: unlike ``_handle_start_game`` (which force-resets a
        lingering game because the admin explicitly re-picked settings in the
        UI), the service refuses to nuke a game already in progress — a voice
        "start the quiz" mid-round should be a clear error, not a silent wipe.
        Raises ``ValueError`` (game already started / no questions) for the
        caller to translate; on success runs the same continuation as the admin
        path.
        """
        if game_state.phase != GamePhase.LOBBY:
            raise ValueError(ERR_GAME_ALREADY_STARTED)
        # Default settings: mixed packs, default difficulty, 10 rounds, HA
        # language default. start_game() re-raises ERR_GAME_ALREADY_STARTED /
        # ERR_NO_QUESTIONS_REMAINING which the service surfaces to the host.
        game_state.start_game()
        await self._after_start_game(game_state, {})

    # ------------------------------------------------------------------
    # Admin: next question
    # ------------------------------------------------------------------

    async def _handle_next_question(
        self, ws: web.WebSocketResponse, game_state: QuizifyGameState
    ) -> None:
        """Handle admin next_question command."""
        err = await self._advance_round(game_state)
        if err is not None:
            await self._conn.send_error(ws, err, "Cannot advance now")

    async def _advance_round(self, game_state: QuizifyGameState) -> str | None:
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
            self._cancel_lightning_loop()
            if game_state.resume_after_lightning():
                await self._start_next_question(game_state)
            return None

        # Hot Seat reveal (#616): the host's advance settles the detour and
        # returns to the round the auction interrupted.
        if game_state.phase == GamePhase.HOT_SEAT_REVEAL:
            # DELIBERATE SUBSET (#746), same reasoning as the branch above:
            # one detour settles, the game carries on.
            self._cancel_hot_seat_loop()
            if game_state.resume_after_hot_seat():
                await self._start_next_question(game_state)
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

        await self._start_next_question(game_state)
        return None

    async def admin_action_next_round(self, game_state: QuizifyGameState) -> None:
        """Advance to the next question — HA-service entry point (#367).

        Mirrors the admin "Next" button (``next_round`` → ``_handle_next_
        question``). Raises ``ValueError`` when the phase forbids advancing so
        the service can surface a clear ``ServiceValidationError`` instead of
        silently no-opping.
        """
        err = await self._advance_round(game_state)
        if err is not None:
            raise ValueError(err)

    async def _handle_admin_skip(
        self, ws: web.WebSocketResponse, game_state: QuizifyGameState
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
            await self._close_wager_window(game_state)
            return
        if game_state.phase == GamePhase.QUESTION_ACTIVE:
            # Stop the countdown and evaluate now. evaluate_round()'s
            # state-machine event (_fire_broadcast("round_evaluated")) drives
            # the summary broadcast, same as the timer-expiry auto-evaluate.
            # DELIBERATE SUBSET (#746): a skip cuts the QUESTION short, it
            # does not end the round or the game. Only the clock stops.
            self._cancel_timer_tick()
            game_state.evaluate_round()
            return
        await self._handle_next_question(ws, game_state)

    # ------------------------------------------------------------------
    # Admin: end game
    # ------------------------------------------------------------------

    async def _handle_end_game(
        self, ws: web.WebSocketResponse, game_state: QuizifyGameState
    ) -> None:
        """Handle admin end_game command."""
        await self.admin_action_end_game(game_state)

    async def admin_action_end_game(self, game_state: QuizifyGameState) -> None:
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
        self._cancel_round_tasks()
        # end_game() fires the ``game_ended`` state event, which the
        # BroadcastDispatcher routes to _broadcast_finale — that's the single
        # finale broadcast source. Calling _broadcast_finale here too would
        # double-broadcast the podium. (#255.) end_game() is idempotent, so a
        # repeated admin end-game is a harmless no-op.
        game_state.end_game()

    # ------------------------------------------------------------------
    # Admin: reset game
    # ------------------------------------------------------------------

    async def _handle_pause_game(
        self, ws: web.WebSocketResponse, game_state: QuizifyGameState
    ) -> None:
        """Pause the current question. No-op if not in QUESTION_ACTIVE."""
        # Silent no-op on a non-pausable phase — the admin UI can call this
        # anytime. The service path (admin_action_pause) instead surfaces the
        # False as a ServiceValidationError.
        await self.admin_action_pause(game_state)

    async def admin_action_pause(self, game_state: QuizifyGameState) -> bool:
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
        self._cancel_timer_tick()
        # pause_reason rides along in the snapshot itself since #703, so it
        # is identical here and on every reconnect.
        state = game_state.get_state_snapshot()
        state["type"] = "game_state"
        await self._conn.broadcast(state)
        return True

    async def _handle_resume_game(
        self, ws: web.WebSocketResponse, game_state: QuizifyGameState
    ) -> None:
        """Resume from PAUSED → restart timer ticks and broadcast state."""
        await self.admin_action_resume(game_state)

    async def admin_action_resume(self, game_state: QuizifyGameState) -> bool:
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
        await self._broadcast_state_projected(game_state)
        # Restart the per-player tick loop.
        self._start_timer_tick(game_state)
        return True

    async def _handle_play_again(
        self, ws: web.WebSocketResponse, game_state: QuizifyGameState
    ) -> None:
        """Restart with the previous game's settings — one-tap rematch.

        Cheaper than full reset_game + admin re-enters everything: keeps
        the existing players, just resets scores and starts the next game
        with the cached settings. If we don't have a snapshot yet (server
        never saw a start_game on this instance), fall back to reset.
        """
        if not game_state.last_settings:
            await self._handle_reset_game(ws, game_state)
            return
        settings = game_state.last_settings
        # #407 (follow-up to #362), via the #746 registry: nothing from the
        # finished game may run into the rematch — a stale pause task would
        # pause round 1, a lingering lightning loop would broadcast stale
        # frames over it.
        self._cancel_round_tasks()
        # Reset to LOBBY first so start_game's phase guard passes; keeps
        # players (reset_to_lobby leaves connected players in place).
        game_state.reset_to_lobby()
        try:
            game_state.start_game(**settings)
        except ValueError as err:
            await self._conn.send_error(ws, ERR_GAME_ALREADY_STARTED, str(err))
            return
        # Snapshot the freshly-minted game_id before the grace sleep (#352) —
        # same concurrency guard as _handle_start_game: a reset_game or a
        # second start/play_again from another admin socket during the grace
        # window re-arms state, and the stale continuation must not fire.
        started_game_id = game_state.game_id
        # Same redirect grace as start_game — admin tab is still on the finale
        # view and needs to redirect/reconnect before round 1's timer ticks.
        await asyncio.sleep(self.START_REDIRECT_GRACE)
        if game_state.game_id != started_game_id or game_state.phase != GamePhase.LOBBY:
            _LOGGER.info(
                "play_again grace continuation aborted: game changed during "
                "grace window (game_id %s -> %s, phase %s)",
                started_game_id,
                game_state.game_id,
                game_state.phase,
            )
            return
        await self._start_next_question(game_state)

    async def _handle_reset_game(
        self, ws: web.WebSocketResponse, game_state: QuizifyGameState
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
        self._cancel_round_tasks()
        # Cancel any pending admin-disconnect timer and stale player-removal
        # tasks from the finished game.
        await self._conn.cleanup()
        # Wipe player session tokens to prevent cross-game reuse.
        self._conn.clear_all_player_tokens()

        # Snapshot the live player WSes BEFORE clearing the registry — we
        # need them both to broadcast the reset signal and to close them
        # afterwards. The host who pressed reset is frequently an
        # admin-as-player (joined the lobby on the admin WS), so their own
        # socket is in this list. That's exactly why ordering matters here
        # (issue #207): if we close the sockets first, the broadcasts below
        # never reach the host (or any real player) and their UI stays
        # frozen on the now-stale lobby. So: clear state → broadcast the
        # reset to everyone still connected → only THEN close the sockets.
        stale_wses = [p.ws for p in game_state.get_players() if p.ws is not None]

        # Drop every player from the registry (full wipe — not just score reset).
        game_state.clear_all_players()
        game_state.reset_to_lobby()

        # Tell every currently-connected client to reset its view to the
        # initial screen (admin → setup, players → join). This MUST run
        # while the sockets are still open.
        await self._conn.broadcast({"type": "game_reset"})

        state = game_state.get_state_snapshot()
        state["type"] = "game_state"
        await self._conn.broadcast(state)

        # Now close the (snapshotted) player sockets so abandoned/stale
        # connections actually die. Real clients have already received the
        # reset above and will reconnect into the fresh lobby on their own.
        for pws in stale_wses:
            # Best-effort cleanup — a socket that's already gone is fine.
            with contextlib.suppress(Exception):
                await pws.close()

    # ------------------------------------------------------------------
    # Admin: kick player
    # ------------------------------------------------------------------

    async def _handle_kick_player(
        self, ws: web.WebSocketResponse, data: dict, game_state: QuizifyGameState
    ) -> None:
        """Remove a player from the lobby. LOBBY-only — kicking mid-game
        would orphan their score and surprise everyone watching the TV.
        Admins can always end the game first if they want a hard reset.
        """
        if game_state.phase != GamePhase.LOBBY:
            await self._conn.send_error(
                ws, ERR_INVALID_ACTION, "Players can only be kicked from the lobby"
            )
            return

        target_name = (data.get("player_name") or data.get("name") or "").strip()
        if not target_name:
            await self._conn.send_error(ws, ERR_INVALID_ACTION, "Missing player_name")
            return

        target = game_state.get_player(target_name)
        if not target:
            await self._conn.send_error(ws, ERR_INVALID_ACTION, "Player not found")
            return

        if target.is_admin:
            await self._conn.send_error(ws, ERR_INVALID_ACTION, "Cannot kick the admin")
            return

        # Tell the target first, then close. The message is what puts the
        # "you were removed" screen on their phone (#750) — the close alone
        # is indistinguishable from a flat battery or a dead wifi hop, and
        # for two years that is exactly what a removed guest saw. Order
        # matters: send before close, or the client never reads it. We don't
        # rely on the closed event reaching us — remove_player flushes state
        # immediately and the WS cleanup path is idempotent.
        target_ws = target.ws
        game_state.remove_player(target.name)
        self._conn.clear_player_tokens(target.name)

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
        self._mark_roster_dirty("player_left")

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
        """Fire the auto Lightning Round mid-game (#285).

        Replaces the retired host-manual ``start_lightning`` entry: there is
        no host tap. We detour out of the current round (``auto=True``
        remembers it), broadcast the intro splash, then the loop auto-advances
        out of the splash after a grace and runs the fast question loop. After
        the recap the host's normal advance resumes the main game.
        """
        # Stop any normal-round timer first — the two loops are mutually
        # exclusive.
        self._cancel_timer_tick()

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
            await self._continue_normal_question(game_state)
            return

        # Broadcast a phase-entry state so every client switches to the
        # lightning view, then the intro splash ("Bolt Burst", #201).
        state = game_state.get_state_snapshot()
        state["type"] = "game_state"
        await self._conn.broadcast(state)
        await self._broadcast_lightning_splash(game_state)

        # Auto-advance: no host tap. The loop itself dismisses the splash after
        # the grace and starts question 1.
        self._start_lightning_loop(game_state, auto_dismiss_splash=True)

    async def _continue_normal_question(self, game_state: QuizifyGameState) -> None:
        """Start the next normal question without re-checking the LR trigger.

        Used by the auto-LR fallback path (LR couldn't start) so we don't
        recurse back into should_trigger_lightning() — the flag is already
        burned, so this is just the plain question start.
        """
        question = game_state.start_next_question()
        if question is None:
            return
        await self._deliver_question(game_state, question)

    async def _broadcast_lightning_splash(
        self, game_state: QuizifyGameState
    ) -> None:
        """Fan out the intro-splash payload (rules preview) to all clients."""
        lr = game_state.lightning
        if lr is None:
            return
        await self._conn.broadcast({
            "type": "lightning_splash",
            "num_questions": lr.num_questions,
            "seconds_per_question": lr.seconds_per_question,
        })

    async def _handle_lightning_answer(
        self, ws: web.WebSocketResponse, data: dict, game_state: QuizifyGameState
    ) -> None:
        """Record a player's answer to the current lightning question."""
        if game_state.phase != GamePhase.LIGHTNING:
            return  # silent — window closed
        lr = game_state.lightning
        if lr is None:
            return
        player = game_state.get_player_by_ws(ws)
        if not player:
            await self._conn.send_error(ws, ERR_NOT_IN_GAME, "Not in game")
            return

        shuffled_index = data.get("answer_index")
        if not isinstance(shuffled_index, int):
            await self._conn.send_error(ws, ERR_INVALID_ACTION, "Invalid answer index")
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
            await self._broadcast_lightning_team_answer(game_state, lr, player.name)
            return

        # Lightweight ack: lock the player's buttons + show right/wrong.
        await self._conn.send(ws, {
            "type": "lightning_answer_result",
            "correct": bool(result),
            "index": lr.index,
            "score": lr.score_for(player.name),
        })

    async def _broadcast_lightning_team_answer(
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
            if member is None or member.ws is None or not member.connected:
                continue
            order = lr.ensure_shuffle(name)
            try:
                shown_index = order.index(standing.answer_index)
            except ValueError:
                continue
            await self._conn.send(member.ws, {
                "type": "lightning_team_answer",
                "index": lr.index,
                "answer_index": shown_index,
                "set_by": setter,
                "members": list(members),
                "lock_seconds": LIGHTNING_ANSWER_LOCK_SECONDS,
            })

    def _start_lightning_loop(
        self,
        game_state: QuizifyGameState,
        *,
        auto_dismiss_splash: bool = False,
    ) -> None:
        """Drive the fast lightning loop: broadcast question, wait for the
        fixed window or all-answered, advance with no reveal, repeat.

        With ``auto_dismiss_splash`` (the #285 auto flow) the loop first holds
        the intro splash for ``AUTO_LIGHTNING_SPLASH_HOLD`` seconds and then
        dismisses it itself — there is no host "Start" tap any more.
        """
        self._cancel_lightning_loop()

        async def loop() -> None:
            try:
                if auto_dismiss_splash:
                    # Hold the intro splash on its own, then advance out of it.
                    await asyncio.sleep(self.AUTO_LIGHTNING_SPLASH_HOLD)
                    if game_state.phase != GamePhase.LIGHTNING:
                        return
                    game_state.begin_lightning_questions()
                # Brief grace so clients swap from the intro splash (#201) to
                # the question view before the first clock starts ticking.
                await asyncio.sleep(self.LIGHTNING_SPLASH_GRACE)
                while game_state.phase == GamePhase.LIGHTNING:
                    lr = game_state.lightning
                    if lr is None:
                        break
                    await self._broadcast_lightning_question(game_state, lr)
                    # Re-arm the question clock now (after the broadcast) so the
                    # countdown the players see matches the server window.
                    lr.restart_clock()
                    # Wait out the fixed window, but cut short once every
                    # connected player has answered.
                    deadline = lr.seconds_per_question
                    waited = 0.0
                    step = 0.25
                    # The display shows whole seconds (1 Hz), so only push a
                    # tick when the ceil(remaining) actually changes — no point
                    # broadcasting at the 4 Hz poll cadence (#258).
                    last_shown = None
                    while waited < deadline:
                        shown = math.ceil(lr.time_remaining())
                        if shown != last_shown:
                            await self._broadcast_lightning_tick(game_state, lr)
                            last_shown = shown
                        connected = [
                            p.name for p in game_state.get_players() if p.connected
                        ]
                        if lr.all_connected_answered(connected):
                            break
                        await asyncio.sleep(step)
                        waited += step
                        if game_state.phase != GamePhase.LIGHTNING:
                            return
                    # #746, the re-check the hot-seat loop got in #671 and
                    # this one did not: the wait above only tests the phase
                    # AFTER a sleep, so the all-answered ``break`` leaves it
                    # untested. An end_game landing in that gap was still
                    # followed by ``lr.advance()`` — a lightning question
                    # scored, and a recap frame broadcast, after the finale.
                    if game_state.phase != GamePhase.LIGHTNING:
                        return
                    # No reveal — score silently and arm the next question.
                    has_more = lr.advance()
                    if not has_more:
                        game_state.finish_lightning_round()
                        await self._broadcast_lightning_recap(game_state)
                        return
            except asyncio.CancelledError:
                pass
            except Exception:  # noqa: BLE001
                # #307: an unexpected exception here used to kill the lightning
                # task silently, hanging the round on the current question with
                # a frozen clock. Log it so the failure surfaces.
                _LOGGER.exception("Lightning loop crashed")

        self._lightning_task = asyncio.ensure_future(loop())
        self._lightning_task.add_done_callback(self._log_task_exception)

    def _cancel_lightning_loop(self) -> None:
        if self._lightning_task is not None:
            self._lightning_task.cancel()
            self._lightning_task = None

    # ------------------------------------------------------------------
    # Hot Seat auction (issue #616)
    # ------------------------------------------------------------------

    def _cancel_hot_seat_loop(self) -> None:
        if self._hot_seat_task is not None:
            self._hot_seat_task.cancel()
            self._hot_seat_task = None

    async def _handle_hot_seat_bid(
        self, ws: web.WebSocketResponse, data: dict, game_state: QuizifyGameState
    ) -> None:
        """Accept one sealed bid for the chair (#616).

        The bid is a PERCENT of the bidder's own score, exactly like a finale
        wager — bidding absolute points would hand every auction to whoever is
        already ahead, which is the opposite of what this mode is for.
        """
        player = game_state.get_player_by_ws(ws)
        if not player:
            await self._conn.send_error(ws, ERR_NOT_IN_GAME, "Not in game")
            return
        if game_state.phase != GamePhase.HOT_SEAT_AUCTION:
            return  # silent: the window is shut
        hs = game_state.hot_seat
        if hs is None:
            return

        try:
            pct = int(data.get("bid"))  # type: ignore[arg-type]
        except (TypeError, ValueError):
            await self._conn.send_error(ws, ERR_INVALID_ACTION, "Invalid bid")
            return
        if not 0 <= pct <= 100:
            await self._conn.send_error(ws, ERR_INVALID_ACTION, "Bid must be 0-100")
            return
        if not hs.record_bid(player.name, pct):
            await self._conn.send_error(ws, ERR_INVALID_ACTION, "Bid already placed")
            return

        await self._conn.send(ws, {
            "type": "hot_seat_bid_accepted",
            "bid": pct,
            "points": hot_seat_stake(hs.scores.get(player.name, 0), pct),
        })
        # Blind auction: the room learns how many have bid, never how much.
        await self._conn.broadcast({
            "type": "hot_seat_bid_count",
            "count": len(hs.bids),
            "total": len(hs.scores),
        })

    async def _handle_hot_seat_bet(
        self, ws: web.WebSocketResponse, data: dict, game_state: QuizifyGameState
    ) -> None:
        """Accept a spectator's optional stake on the seat holder (#616)."""
        player = game_state.get_player_by_ws(ws)
        if not player:
            await self._conn.send_error(ws, ERR_NOT_IN_GAME, "Not in game")
            return
        if game_state.phase != GamePhase.HOT_SEAT:
            return  # silent: betting window shut
        hs = game_state.hot_seat
        if hs is None:
            return
        if player.name == hs.winner:
            # Refused rather than ignored: betting against yourself and then
            # answering wrongly on purpose turns a question you cannot answer
            # into a profit.
            await self._conn.send_error(
                ws, ERR_INVALID_ACTION, "The hot seat does not bet"
            )
            return

        side = data.get("side")
        try:
            pct = int(data.get("bet"))  # type: ignore[arg-type]
        except (TypeError, ValueError):
            await self._conn.send_error(ws, ERR_INVALID_ACTION, "Invalid bet")
            return
        if not 0 <= pct <= 100:
            await self._conn.send_error(ws, ERR_INVALID_ACTION, "Bet must be 0-100")
            return
        if not hs.record_bet(player.name, side, pct):
            await self._conn.send_error(ws, ERR_INVALID_ACTION, "Bet not accepted")
            return

        await self._conn.send(ws, {
            "type": "hot_seat_bet_accepted",
            "side": side,
            "bet": pct,
            "points": hot_seat_stake(hs.scores.get(player.name, 0), pct),
        })

    async def _handle_hot_seat_answer(
        self, ws: web.WebSocketResponse, data: dict, game_state: QuizifyGameState
    ) -> None:
        """Record the seat holder's single answer (#616)."""
        player = game_state.get_player_by_ws(ws)
        if not player:
            await self._conn.send_error(ws, ERR_NOT_IN_GAME, "Not in game")
            return
        if game_state.phase != GamePhase.HOT_SEAT:
            return
        hs = game_state.hot_seat
        if hs is None or player.name != hs.winner:
            return

        try:
            idx = int(data.get("answer"))  # type: ignore[arg-type]
        except (TypeError, ValueError):
            await self._conn.send_error(ws, ERR_INVALID_ACTION, "Invalid answer")
            return
        result = hs.record_answer(player.name, idx)
        if result is None:
            return
        await self._conn.send(ws, {"type": "hot_seat_answer_accepted"})

    async def _start_hot_seat(self, game_state: QuizifyGameState) -> bool:
        """Open the auction and drive it to the reveal. False means "skipped".

        A False here is ordinary — too few players, or every remaining
        question is spoken for by the main game (#544) — and the caller falls
        through to the normal round rather than ending it.
        """
        self._cancel_timer_tick()
        if not game_state.start_hot_seat_auction():
            _LOGGER.info("Hot seat auction skipped; continuing normal game")
            return False

        hs = game_state.hot_seat
        if hs is None:
            return False

        state = game_state.get_state_snapshot()
        state["type"] = "game_state"
        await self._conn.broadcast(state)
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
        # next to the points it costs *them*.
        sends = []
        for player in game_state.get_players():
            if not player.connected or player.ws is None:
                continue
            sends.append(self._conn.send(player.ws, {
                "type": "hot_seat_auction_you",
                "score": player.score,
                "seconds": hs.auction_seconds,
            }))
        if sends:
            await asyncio.gather(*sends, return_exceptions=True)

        self._start_hot_seat_loop(game_state)
        return True

    def _start_hot_seat_loop(self, game_state: QuizifyGameState) -> None:
        """Drive auction → reveal → question → settlement (#616)."""
        self._cancel_hot_seat_loop()

        async def loop() -> None:
            try:
                hs = game_state.hot_seat
                if hs is None:
                    return

                # --- bidding window -----------------------------------
                last_shown = None
                while not hs.is_expired():
                    if game_state.phase != GamePhase.HOT_SEAT_AUCTION:
                        return
                    shown = math.ceil(hs.time_remaining())
                    if shown != last_shown:
                        await self._conn.broadcast({
                            "type": "hot_seat_tick",
                            "phase": "auction",
                            "remaining": shown,
                        })
                        last_shown = shown
                    connected = [
                        p.name for p in game_state.get_players() if p.connected
                    ]
                    if hs.all_bid(connected):
                        break
                    await asyncio.sleep(0.25)

                # #671: the loop above only checks the phase INSIDE the wait.
                # Leaving it (expired, or everyone bid) and acting without a
                # re-check lets a reset that landed in the last poll interval
                # be followed by a ghost round in the fresh lobby.
                if game_state.phase != GamePhase.HOT_SEAT_AUCTION:
                    return

                winner = game_state.close_hot_seat_auction()
                if winner is None:
                    # Nobody wanted the chair. Not a failure — just a round
                    # that does not happen. Fall back to the normal question
                    # so the game keeps moving.
                    await self._conn.broadcast({"type": "hot_seat_no_bids"})
                    game_state.abort_hot_seat()
                    await self._continue_normal_question(game_state)
                    return

                # --- simultaneous reveal ------------------------------
                await self._conn.broadcast({
                    "type": "hot_seat_awarded",
                    "winner": winner,
                    "pct": hs.winning_pct,
                    "stake": hs.winning_stake,
                    "bids": hs.reveal(),
                })
                await asyncio.sleep(self.HOT_SEAT_REVEAL_HOLD)
                if game_state.phase != GamePhase.HOT_SEAT:
                    return

                # --- the question -------------------------------------
                await self._broadcast_hot_seat_question(game_state, hs)
                hs.start_answer_clock()
                last_shown = None
                while not hs.is_expired():
                    if game_state.phase != GamePhase.HOT_SEAT:
                        return
                    shown = math.ceil(hs.time_remaining())
                    if shown != last_shown:
                        await self._conn.broadcast({
                            "type": "hot_seat_tick",
                            "phase": "question",
                            "remaining": shown,
                        })
                        last_shown = shown
                    if hs.answered is not None:
                        break
                    await asyncio.sleep(0.25)

                # #671: same re-check as after the auction wait — a teardown
                # that lands in the last poll interval must not be followed by
                # a settlement against a game that no longer exists.
                if game_state.phase != GamePhase.HOT_SEAT:
                    return

                # Settle even when nothing was answered: the chair was bought
                # either way (#653). This is the one place the mode parts ways
                # with the finale's forgiving timeout.
                game_state.finish_hot_seat()
                await self._conn.broadcast({
                    "type": "hot_seat_result",
                    "round_num": game_state.round,
                    "total_rounds": game_state.total_rounds,
                    **hs.summary(),
                    "scores": {
                        p.name: p.score for p in game_state.get_players()
                    },
                })
            except asyncio.CancelledError:
                pass
            except Exception:  # noqa: BLE001
                _LOGGER.exception("Hot seat loop crashed")

        self._hot_seat_task = asyncio.ensure_future(loop())
        self._hot_seat_task.add_done_callback(self._log_task_exception)

    async def _broadcast_hot_seat_question(
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
            "winner": hs.winner,
        }
        sends = []
        for player in game_state.get_players():
            if not player.connected or player.ws is None:
                continue
            if player.name == hs.winner:
                sends.append(self._conn.send(player.ws, {
                    **payload,
                    "answers": hs.shuffled_answers(),
                    "you_are_seated": True,
                }))
            else:
                sends.append(self._conn.send(player.ws, {
                    **payload,
                    "answers": [],
                    "you_are_seated": False,
                    "score": player.score,
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

    async def _broadcast_lightning_question(
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
            lightning_sends.append(self._conn.send(player.ws, {
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

    async def _broadcast_lightning_tick(
        self, game_state: QuizifyGameState, lr: Any
    ) -> None:
        remaining = round(lr.time_remaining(), 1)
        await self._conn.broadcast({
            "type": "lightning_tick",
            "remaining": remaining,
            "index": lr.index,
        })

    async def _broadcast_lightning_recap(
        self, game_state: QuizifyGameState
    ) -> None:
        lr = game_state.lightning
        if lr is None:
            return
        await self._conn.broadcast({
            "type": "lightning_recap",
            "recap": lr.build_recap(),
        })

    async def _broadcast_state_projected(
        self,
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
        base = game_state.get_state_snapshot()

        # Per-player projected sends.
        player_ws: set[Any] = set()
        sends = []
        for player in game_state.get_players():
            if player.ws is None or not player.connected:
                continue
            player_ws.add(player.ws)
            msg = self._round_messages.project_snapshot_for_player(
                game_state, snapshot=base, player=player
            )
            msg["type"] = "game_state"
            if extra:
                msg.update(extra)
            sends.append(self._conn.send(player.ws, msg))

        # Raw snapshot for pure admin/dashboard sockets — but skip any socket
        # that is also a player (admin-as-player already got its projected
        # copy above, and must NOT see canonical order mid-question).
        raw = dict(base)
        raw["type"] = "game_state"
        if extra:
            raw.update(extra)
        for ws, _is_admin in self._conn.iter_admin_and_dashboard_ws():
            if ws.closed or ws in player_ws:
                continue
            sends.append(self._conn.send(ws, raw))

        if sends:
            await asyncio.gather(*sends)

    # ------------------------------------------------------------------
    # Question flow
    # ------------------------------------------------------------------

    async def _start_next_question(self, game_state: QuizifyGameState) -> None:
        """Start the next question: shuffle answers, broadcast, start timer ticks."""
        self._cancel_timer_tick()

        # Auto Lightning Round (#285): exactly once per game, when the game is
        # about to enter the pre-picked target round, detour into the fast
        # Lightning Round first. The normal round resumes after the recap (the
        # host's next advance lands on resume_after_lightning → ANSWER_REVEAL →
        # this same path, which by then sees _lightning_fired and proceeds).
        if game_state.should_trigger_lightning():
            await self._start_auto_lightning(game_state)
            return

        # Hot Seat auction (#616): the same detour shape, one round later or
        # earlier — never the same round, the draw excludes it. A failed start
        # (too few players, no spare question) falls through to the normal
        # question so the game can never wedge on a skipped bonus.
        if game_state.should_trigger_hot_seat() and await self._start_hot_seat(
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

        await self._deliver_question(game_state, question)

    async def _deliver_question(
        self, game_state: QuizifyGameState, question: Any
    ) -> None:
        """Send a freshly-started question — or open its betting window (#656).

        On an MC final ``start_next_question`` parks the state machine in
        WAGER_ACTIVE instead of QUESTION_ACTIVE: bets first, question after.
        Every path that starts a question goes through here, so none of them
        can emit a question into a phase that has no timers — which would
        exit the tick loop on its first iteration and hang the round.
        """
        if game_state.phase == GamePhase.WAGER_ACTIVE:
            await self._open_wager_window(game_state, question)
            return
        await self._emit_question(game_state, question)

    async def _open_wager_window(
        self, game_state: QuizifyGameState, question: Any
    ) -> None:
        """Announce the betting window and arm its deadline (#656).

        Sends every phone its own bank, gives the host/TV the lock-in tally,
        and starts the one task that guarantees the window ends — with or
        without the players.
        """
        players = game_state.get_players()
        sends = [
            self._conn.send(
                player.ws,
                self._round_messages.build_wager_window(
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
            self._round_messages.build_wager_progress(
                game_state, window_duration=WAGER_WINDOW_DURATION
            )
        )
        # Phase broadcast last: the phones already hold the window payload, so
        # a client driving off the phase alone (reconnect path) lands on a view
        # it can render rather than an empty one.
        await self._conn.broadcast(
            self._round_messages.build_game_state_with_leaderboard(
                game_state, players=players
            )
        )
        self._start_wager_window(game_state)

    def _start_wager_window(self, game_state: QuizifyGameState) -> None:
        """Arm the task that closes the betting window at the deadline (#656).

        The deadline is what keeps the final round from hanging on a player
        who never bets — an AFK phone, or a room that all walked out. It is
        the same class of hang as #586, so it gets an unconditional timer
        rather than a condition that depends on clients behaving.
        """
        self._cancel_wager_window()

        async def window() -> None:
            try:
                await asyncio.sleep(WAGER_WINDOW_DURATION)
            except asyncio.CancelledError:
                return
            try:
                await self._close_wager_window(game_state)
            except Exception:  # noqa: BLE001 — a stuck window strands the game
                _LOGGER.exception("Wager window failed to close")

        self._wager_window_task = asyncio.create_task(window())

    def _cancel_wager_window(self) -> None:
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
        task = self._wager_window_task
        self._wager_window_task = None
        if task is not None and not task.done() and task is not asyncio.current_task():
            task.cancel()

    async def _close_wager_window(self, game_state: QuizifyGameState) -> None:
        """Close the betting window: arm the timers, then send the question.

        ``arm_round_timers`` returns False when the window is already shut,
        which is what makes the two closing triggers safe to race — the last
        player locking in, and the deadline. Whoever arrives second finds the
        phase already flipped and returns without re-emitting the question.
        """
        self._cancel_wager_window()
        question = game_state.get_current_question()
        if question is None or not game_state.arm_round_timers():
            return
        await self._emit_question(game_state, question)

    async def _emit_question(
        self, game_state: QuizifyGameState, question: Any
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
        is_final = game_state.round == game_state.total_rounds
        # Build every per-player payload, then fan out in parallel (#258) so a
        # single slow client can't delay the question reaching the rest.
        question_sends = []
        for player in players_now:
            if not player.connected:
                continue
            player_msg = self._round_messages.build_player_question(
                game_state, question=question, player=player, is_final=is_final
            )
            question_sends.append(self._conn.send(player.ws, player_msg))
        if question_sends:
            await asyncio.gather(*question_sends)

        # Send question with correct answer to admin
        admin_msg = self._round_messages.build_admin_question(
            game_state, question=question
        )
        # Also fan out to TV-dashboard connections so their question-view
        # populates with the same canonical-order payload. Without this the
        # dashboard's answer-grid stays empty and the v1.1.47 #151 answer-
        # distribution bars never attach. Admin-as-player still excluded.
        # The TV connects without a token (#604), so it gets the same payload
        # with the answer taken out — see strip_answer_for_dashboard.
        await self._conn.broadcast_to_admins_and_dashboards(
            admin_msg, dashboard_message=strip_answer_for_dashboard(admin_msg)
        )

        # Narrate the question text (+ options) at round start (#281). The
        # canonical shuffled order matches the TV grid so spoken letters line
        # up. Guarded like the milestone hook so a bad config can't break the
        # question fan-out.
        self._notify_tts_question(
            question, game_state.round, game_state.total_rounds, shuffled_texts
        )
        # Fire the HA bus event (round + type only; no text/answers) so the host
        # can automate on each question start (#366).
        self._notify_house_question(
            question, game_state.round, game_state.total_rounds
        )

        # Cache players to avoid redundant calls
        players = game_state.get_players()

        # Notify players who got a power-up this round (parallel fan-out, #258).
        powerup_sends = []
        for player in players:
            powerup = game_state.get_player_powerup(player.name)
            if powerup and player.connected:
                powerup_sends.append(self._conn.send(player.ws, {
                    "type": "powerup_assigned",
                    "powerup_type": powerup.value,
                }))
        if powerup_sends:
            await asyncio.gather(*powerup_sends)

        # Broadcast game state with leaderboard so player sees rankings during game
        await self._conn.broadcast(
            self._round_messages.build_game_state_with_leaderboard(
                game_state, players=players
            )
        )

        # Start timer tick task
        self._start_timer_tick(game_state)

    # ------------------------------------------------------------------
    # Timer ticks
    # ------------------------------------------------------------------

    def _start_timer_tick(self, game_state: QuizifyGameState) -> None:
        """Start async task that broadcasts the countdown every tick.

        The *timing* — which players have a live timer, each one's
        authoritative remaining time (so time-boost / freeze power-ups are
        reflected, #4), the dashboard minimum and the all-expired stop
        condition — lives in the PhaseController (#203). This loop owns only
        the I/O: turning that timing into ``timer_tick`` wire messages for
        players, pure-admins and dashboards, the sleep cadence and the
        auto-evaluate when the round runs out.
        """
        self._cancel_timer_tick()

        # #413: the client renders ``Math.ceil(remaining)`` WHOLE seconds, but
        # the loop ticks every ~0.5s — so half the frames redraw the same
        # number and are pure waste (at the 20-player cap: 20 sockets × the
        # dead frame, every tick). Coalesce like the lightning loop already
        # does: remember the last displayed second per recipient and only emit
        # a ``timer_tick`` when that second actually changes. The sleep cadence
        # is unchanged, so the countdown accuracy and the auto-evaluate timing
        # are unaffected — only the redundant frames are dropped.
        last_shown_by_name: dict[str, int] = {}
        last_dashboard_shown: int | None = None

        async def tick_loop() -> None:
            nonlocal last_dashboard_shown
            try:
                while game_state.phase == GamePhase.QUESTION_ACTIVE:
                    players = game_state.get_players()
                    by_name = {p.name: p for p in players}
                    # Ask the timing unit for this tick's per-player remaining
                    # plus the shared dashboard minimum (one timer read each).
                    tick = game_state.resolve_tick([p.name for p in players])
                    # Build the full (ws, payload) fan-out, then deliver it in
                    # parallel via gather (#258). A single stalled client no
                    # longer delays the whole room — ConnectionManager.send
                    # swallows errors so a plain gather is safe.
                    sends = []
                    # Each connected player gets their authoritative remaining,
                    # but only when their displayed second changed (#413).
                    for name, remaining in tick.per_player:
                        p = by_name.get(name)
                        if p is None or not p.connected:
                            continue
                        shown = math.ceil(max(0.0, remaining))
                        if last_shown_by_name.get(name) == shown:
                            continue
                        last_shown_by_name[name] = shown
                        sends.append(self._conn.send(p.ws, {
                            "type": "timer_tick",
                            "remaining": round(remaining, 1),
                        }))
                    # Broadcast the minimum remaining to dashboards/admins so
                    # the TV view shows a consistent countdown — again only when
                    # its displayed second changes. Pre-serialized ONCE and fanned
                    # out via the broadcast string path (admin-as-player already
                    # excluded there) instead of a per-socket send_json (#413/#258).
                    min_remaining = tick.dashboard_remaining
                    # Spoken "time running out" warning (#281), once per round.
                    self._notify_tts_countdown(min_remaining)
                    # House "time running out" bus event (#280), once per round —
                    # drives the faster countdown light pulse.
                    self._notify_house_time_running_out(min_remaining)
                    dash_shown = math.ceil(max(0.0, min_remaining))
                    if dash_shown != last_dashboard_shown:
                        last_dashboard_shown = dash_shown
                        sends.append(
                            self._conn.broadcast_to_admins_and_dashboards({
                                "type": "timer_tick",
                                "remaining": round(min_remaining, 1),
                            })
                        )
                    if sends:
                        await asyncio.gather(*sends)
                    await asyncio.sleep(TICK_INTERVAL)

                    # Stop if everyone's timer hit zero and phase is still
                    # active. Re-read fresh timers (state can change during the
                    # sleep) for the CONNECTED players only; the all-expired
                    # decision itself lives in the PhaseController, which ignores
                    # players without a timer so a late-joining connected player
                    # (e.g. the admin's own /quizify/player tab) can't end the
                    # round before their per-player timer exists.
                    connected = [p.name for p in players if p.connected]
                    if connected and game_state.all_timers_expired(connected):
                        break
                    # Fallback: all_timers_expired needs at least one live
                    # timer to break on, so the loop hangs in every state where
                    # the connected players have none. That covers the original
                    # case — everyone disconnected mid-question (#255) — and
                    # the one it missed: connected players who never got a
                    # timer, where NEITHER condition could fire and the loop
                    # spun forever with the countdown frozen at 0 (#586).
                    # Keying the fallback on "no live timers" instead of "no
                    # connected players" covers both with one condition.
                    if not game_state.has_live_timers(
                        connected
                    ) and game_state.round_wall_clock_expired():
                        break

                # Timer expired globally
                if game_state.phase == GamePhase.QUESTION_ACTIVE:
                    # Auto-evaluate round. The state machine's
                    # _fire_broadcast("round_evaluated") handles the summary
                    # broadcast \u2014 do NOT broadcast here (#3 in review).
                    game_state.evaluate_round()
            except asyncio.CancelledError:
                pass
            except Exception:  # noqa: BLE001
                # #307: any other exception used to kill the tick task
                # silently, leaving the game frozen in QUESTION_ACTIVE with a
                # stuck countdown. Log it loudly so the failure is diagnosable
                # instead of presenting as a mysterious hang.
                _LOGGER.exception("Timer tick loop crashed")

        self._timer_tick_task = asyncio.ensure_future(tick_loop())
        self._timer_tick_task.add_done_callback(self._log_task_exception)

    def _cancel_timer_tick(self) -> None:
        """Cancel the timer tick task — and the betting window with it.

        The two are the same thing from the caller's side: the round's
        background clock. Cancelling both here rather than at each of the
        eight ``_cancel_timer_tick`` call sites (reset, end game, skip,
        pause, disconnect, next question, …) is what guarantees no path can
        leave a window armed over a round that has already moved on (#656).
        """
        if self._timer_tick_task is not None:
            self._timer_tick_task.cancel()
            self._timer_tick_task = None
        self._cancel_wager_window()

    def _cancel_task_group(self, group: tuple[tuple[str, str], ...]) -> None:
        """Run every canceller in one of the registries above (#746).

        Cancellers are idempotent and safe on a task that was never started,
        so a group can always be run whole. That is the point: the caller
        says *which scope* it is tearing down, never *which tasks*.
        """
        for _attr, canceller in group:
            cancel: Callable[[], None] = getattr(self, canceller)
            cancel()

    def _cancel_round_tasks(self) -> None:
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
        self._cancel_task_group(self._ROUND_SCOPED_TASKS)

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

    async def _broadcast_round_summary(self, game_state: QuizifyGameState) -> None:
        """Broadcast round summary to all clients.

        Payload assembly (correct-index resolution, the per-player answer
        table, the summary serialization) lives in the RoundMessageBuilder
        (#189); the handler keeps ownership of the broadcast. ``None`` means
        there is no round summary yet — same no-op as before.
        """
        summary_msg = self._round_messages.build_round_summary(game_state)
        if summary_msg is None:
            return
        await self._conn.broadcast(summary_msg)

    # ------------------------------------------------------------------
    # Disconnect handling
    # ------------------------------------------------------------------

    def _maybe_evaluate_after_dropout(self, game_state: QuizifyGameState) -> bool:
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
        self._cancel_timer_tick()
        game_state.evaluate_round()
        return True

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

        player = game_state.get_player_by_ws(ws)
        if not player:
            return

        player.connected = False
        _LOGGER.info("Player disconnected: %s", player.name)

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
        # or switch networks; if they don't come back, the existing
        # remove-after-timeout below cleans up and the game can be
        # resumed by another path.
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
            await asyncio.sleep(timeout)
            gs = self._get_game_state()
            if gs:
                p = gs.get_player(name)
                if p and not p.connected:
                    gs.remove_player(name)
                    # Clean up session tokens for this player
                    self._conn.clear_player_tokens(name)
                    # Coalesced roster broadcast (#453).
                    self._mark_roster_dirty("player_left")
                    _LOGGER.info(
                        "Removed disconnected player after grace period: %s", name
                    )
                    # #412: removing the last unanswered player can complete the
                    # round — re-check all-submitted and evaluate if so, same as
                    # the immediate-disconnect path above.
                    if self._maybe_evaluate_after_dropout(gs):
                        _LOGGER.info(
                            "Round auto-evaluated after removing last "
                            "unanswered player %s",
                            name,
                        )

        self._conn.schedule_player_removal(player.name, grace, remove_after_timeout)

    # ------------------------------------------------------------------
    # Admin-redirect pause: defer the QUESTION_ACTIVE pause that would
    # otherwise fire instantly when admin's WS closes (typically the
    # intentional /quizify/admin → /quizify/player redirect after Start).
    # ------------------------------------------------------------------

    def _schedule_admin_pause(self, admin_name: str) -> None:
        """Schedule a pause to fire ADMIN_REDIRECT_GRACE seconds from now.

        If admin reconnects (via session token or fresh join) before the
        timer expires, _cancel_admin_pause clears the task. Re-entrant:
        cancels any prior pending task before scheduling a new one, so a
        rapid disconnect-reconnect-disconnect doesn't stack tasks.
        """
        self._cancel_admin_pause()

        async def pause_after_grace() -> None:
            try:
                await asyncio.sleep(self.ADMIN_REDIRECT_GRACE)
                gs = self._get_game_state()
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
                self._cancel_timer_tick()
                state = gs.get_state_snapshot()
                state["type"] = "game_state"
                await self._conn.broadcast(state)
                _LOGGER.info(
                    "Paused game after admin %s failed to reconnect within %.1fs",
                    admin_name,
                    self.ADMIN_REDIRECT_GRACE,
                )
            except asyncio.CancelledError:
                # Normal path: admin reconnected in time. Don't log.
                pass

        self._admin_pause_task = asyncio.ensure_future(pause_after_grace())
        # Surface a crash in the deferred-pause loop (#362/#307) instead of
        # letting "Task exception was never retrieved" leak at GC time.
        self._admin_pause_task.add_done_callback(self._log_task_exception)

    def _cancel_admin_pause(self) -> None:
        """Cancel any pending deferred admin-disconnect pause."""
        if self._admin_pause_task and not self._admin_pause_task.done():
            self._admin_pause_task.cancel()
        self._admin_pause_task = None

    # ------------------------------------------------------------------
    # Admin auth helper
    # ------------------------------------------------------------------

    def _is_authorized_admin(
        self, ws: web.WebSocketResponse, is_admin: bool, game_state: QuizifyGameState
    ) -> bool:
        """Return True if the connection is authorized to perform admin actions."""
        player = game_state.get_player_by_ws(ws)
        return is_admin or bool(player and player.is_admin)

    def _is_reset_authorized(
        self, ws: web.WebSocketResponse, is_admin: bool, game_state: QuizifyGameState
    ) -> bool:
        """Authorize a reset_game request.

        Stricter-than-nothing escape hatch (#207): a normal authorized admin
        may always reset; additionally, ANY connected client may reset when
        no *connected* admin currently holds the crown. The latter recovers
        the legitimate host from the orphaned-crown state left by the #209
        single-admin name race, where otherwise every admin-only action is
        silently rejected. Reset only ever returns the game to its initial
        state, so granting it in the admin-less case cannot escalate control.
        """
        if self._is_authorized_admin(ws, is_admin, game_state):
            return True
        # #726: a live ``?role=admin`` socket IS a connected admin, even when it
        # holds no player slot. ``get_admin()`` below only ever looks at the
        # player registry, so a host who runs the game from /quizify/admin
        # without joining leaves it ``None`` for the whole evening — and the
        # escape hatch then reads as "nobody is hosting" while somebody is.
        # That handed every guest a working reset_game mid-game.
        if self._conn.has_admin_connections():
            return False
        admin = game_state.get_admin()
        return admin is None or not admin.connected

    async def _dispatch_analytics_followups(self) -> None:
        """Everything true only once the game is recorded (#624, #612, #613).

        One handler because one event: the season standing per player, the
        evening tally for the room and the end-screen duel all become correct
        at the same instant, and registering three handlers for the same event
        would make their order an accident of dict insertion.
        """
        await self._dispatch_all_time_standings()
        await self._dispatch_evening_tally()
        await self._dispatch_end_head_to_head()

    async def _dispatch_evening_tally(self) -> None:
        """Broadcast tonight's running score to the TV (#612).

        Rides the same ``analytics_recorded`` event as the season standing: the
        game that just finished is only part of the tally once it is written.

        Broadcast rather than per-player — unlike the season standing, this is
        the room's number, and the TV is the screen it belongs on.
        """
        gs = self._get_game_state()
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
        await self._conn.broadcast_to_admins_and_dashboards(
            {"type": "evening_tally", **tally}
        )

    async def _dispatch_all_time_standings(self) -> None:
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
        game_state = self._get_game_state()
        if game_state is None:
            return
        sends = []
        for player in game_state.get_players():
            if player.ws is None or player.ws.closed:
                continue
            standing = self._all_time_standing(player.name)
            if standing is None:
                continue
            sends.append(
                self._conn.send(
                    player.ws,
                    {"type": "all_time_update", "all_time": standing},
                )
            )
        if sends:
            await asyncio.gather(*sends)

    def _all_time_standing(self, name: str) -> PlayerStanding | None:
        """This player's own all-time placing for the lobby line (#371).

        FAIL-SOFT by construction: no game state, no wired analytics, or a
        name that never finished a game all return ``None``, and the client
        simply renders no line. A join must never fail over a decoration.
        """
        gs = self._get_game_state()
        if gs is None:
            return None
        analytics = gs.stats_service
        if analytics is None:
            return None
        return analytics.get_player_standing(name)

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
        """Push a flat TTS-settings dict onto the announcer (#281).

        Shared by ``start_game`` (config nested under ``data["tts"]``) and the
        lobby-time ``configure_tts`` message (config sent flat). No-op when no
        announcer is wired (standalone dev server, HA without a TTS entity);
        guarded so a malformed payload can't break the caller.
        """
        announcer = self._tts_announcer
        if announcer is None:
            return
        try:
            announcer.configure(
                enabled=bool(tts.get("enabled")),
                announce_question=bool(tts.get("announce_question", True)),
                announce_options=bool(tts.get("announce_options", True)),
                announce_reveal=bool(tts.get("announce_reveal", True)),
                announce_standings=bool(tts.get("announce_standings", True)),
                announce_join=bool(tts.get("announce_join", True)),
                announce_countdown=bool(tts.get("announce_countdown", True)),
                announce_milestone=bool(tts.get("announce_milestone", True)),
                # Per-game entity overrides from the admin dropdowns (#281).
                # Empty/missing → the announcer falls back to the config-entry
                # default entities.
                tts_entity=_entity_in_domain(
                    str(tts.get("tts_entity") or "").strip(), "tts", "tts_entity"
                )
                or None,
                media_player=_entity_in_domain(
                    str(tts.get("media_player") or "").strip(),
                    "media_player",
                    "media_player",
                )
                or None,
            )
        except Exception:  # noqa: BLE001
            _LOGGER.exception("TTS configure raised")

    async def _handle_configure_tts(
        self, ws: web.WebSocketResponse, data: dict, game_state: QuizifyGameState
    ) -> None:
        """Handle the admin ``configure_tts`` message (#281).

        Sent by admin.js on connect and whenever a narration toggle/entity
        changes, so the announcer is configured during the pre-game lobby —
        otherwise player-join narration (which fires before ``start_game``)
        would never speak. The payload is the flat TTS-settings object.
        """
        self._apply_tts_config(data or {})

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
        """Push the flat "House Plays Along" settings dict onto its consumers.

        Shared by ``start_game`` (config nested under ``data["house"]``) and the
        lobby-time ``configure_house`` message (config sent flat), exactly like
        :meth:`_apply_tts_config`. The panel persists one flat dict in
        localStorage and pushes it verbatim; "presets" are a frontend-only
        concept, so a ``preset`` key (if any) is simply ignored here — the
        backend only ever sees the resolved booleans.

        Fans out to all THREE consumers, because the panel presents one master
        switch over what are internally three independent subsystems:
          * party lights  — the 5 accent cues + the winner scene;
          * sound effects — the 4 one-shot cues;
          * event emitter — the ``quizify_*`` bus events the other two ride on.
        The master ``enabled`` is a runtime override of the
        ``CONF_HOUSE_EVENTS_ENABLED`` option and is handed to each of them, so
        flipping it off silences the whole feature in one frame.

        Defensive by design — the payload is untyped client JSON:
          * each child toggle defaults to ON (``bool(house.get(key, True))``),
            so a partial dict degrades to "master decides" rather than to a
            silently half-dead panel; only the master defaults OFF;
          * each ``configure()`` is individually guarded, so one bad entity id
            (e.g. a stale ``light.*`` the host has since removed) can at worst
            kill its own subsystem, never the other two — and never the frame,
            which would drop the admin's whole message.
        No-op per consumer when it isn't wired (standalone dev server).
        """
        enabled = bool(house.get("enabled"))

        def _toggle(key: str) -> bool:
            return bool(house.get(key, True))

        # Entity overrides. An empty selection ("" / []) means "no override" —
        # the consumer then falls back to its config-entry default
        # (CONF_PARTY_LIGHT_ENTITIES / CONF_MEDIA_PLAYER_ENTITY /
        # CONF_FINALE_SCENE). Coerced defensively: a non-list ``light_entities``
        # from a malformed payload must not reach the light service call.
        raw_light_entities = house.get("light_entities")
        light_entities = (
            [str(e) for e in raw_light_entities]
            if isinstance(raw_light_entities, list)
            else []
        )
        media_player = str(house.get("media_player") or "").strip()
        winner_scene_entity = str(house.get("winner_scene_entity") or "").strip()

        # Domain allowlist (#724). Each override is forwarded to exactly one
        # service - light.turn_on, scene.turn_on, media_player.play_media - so
        # an id from any other domain is never a legitimate value here. Dropped
        # rather than rejected: one bad picker entry must not disarm the whole
        # house block, which is the same defensive stance as the guards below.
        light_entities = _entities_in_domain(light_entities, "light", "light_entities")
        media_player = _entity_in_domain(media_player, "media_player", "media_player")
        winner_scene_entity = _entity_in_domain(
            winner_scene_entity, "scene", "winner_scene_entity"
        )

        lights = self._party_lights
        if lights is not None:
            try:
                lights.configure(
                    enabled=enabled,
                    light_question=_toggle("light_question"),
                    light_countdown=_toggle("light_countdown"),
                    light_reveal=_toggle("light_reveal"),
                    light_streak=_toggle("light_streak"),
                    light_winner=_toggle("light_winner"),
                    winner_scene=_toggle("winner_scene"),
                    light_entities=light_entities,
                    winner_scene_entity=winner_scene_entity,
                )
            except Exception:  # noqa: BLE001
                _LOGGER.exception("House party-lights configure raised")

        effects = self._sound_effects
        if effects is not None:
            try:
                effects.configure(
                    enabled=enabled,
                    sfx_correct=_toggle("sfx_correct"),
                    sfx_wrong=_toggle("sfx_wrong"),
                    sfx_streak=_toggle("sfx_streak"),
                    sfx_winner=_toggle("sfx_winner"),
                    media_player=media_player,
                )
            except Exception:  # noqa: BLE001
                _LOGGER.exception("House sound-effects configure raised")

        emitter = self._event_emitter
        if emitter is not None:
            try:
                # The emitter has no per-cue toggles of its own — the bus events
                # are the substrate the lights and SFX subscribe to, so it only
                # honours the master switch. Leaving it armed while the master is
                # off would keep spamming quizify_* events at the host's own
                # automations, which is exactly what the master promises to stop.
                emitter.configure(enabled=enabled)
            except Exception:  # noqa: BLE001
                _LOGGER.exception("House event-emitter configure raised")

    async def _handle_configure_house(
        self, ws: web.WebSocketResponse, data: dict, game_state: QuizifyGameState
    ) -> None:
        """Handle the admin ``configure_house`` message (#494 Phase 4).

        Sent by admin.js on connect (right after ``admin_connect``) and whenever
        a house toggle / entity picker changes, mirroring ``configure_tts``. The
        on-connect push is why this must work with no game in progress: the
        lobby-phase cues (player-join glow, lobby SFX) fire before ``start_game``
        ever lands, so waiting for the start payload would leave them
        misconfigured for the entire lobby. Nothing here touches ``game_state``
        — the applier only reconfigures long-lived consumers — so the lobby path
        is safe by construction. The payload is the flat house-settings object.
        """
        self._apply_house_config(data or {})

    def _notify_tts_milestone(self, player_name: str, streak: int) -> None:
        """Forward a milestone hit to the TTS announcer if one is wired.

        Kept as a no-op when ``_tts_announcer`` is None (standalone dev
        server, HA setup without TTS configured) so the handler doesn't
        have to thread an Optional everywhere.
        """
        announcer = self._tts_announcer
        if announcer is None:
            return
        try:
            announcer.announce_milestone(player_name, streak)
        except Exception:  # noqa: BLE001
            _LOGGER.exception("TTS milestone announcement raised")

    def _notify_tts_question(
        self,
        question: Any,
        round_no: int,
        total_rounds: int,
        options: list[str] | None = None,
    ) -> None:
        """Forward a question-start (+ shuffled options) to the TTS announcer
        if one is wired (#281).

        No-op when ``_tts_announcer`` is None (standalone dev server, HA setup
        without a TTS entity). Guarded so a bad announcement can't break the
        question fan-out.
        """
        announcer = self._tts_announcer
        if announcer is None:
            return
        try:
            announcer.announce_question(question, round_no, total_rounds, options)
        except Exception:  # noqa: BLE001
            _LOGGER.exception("TTS question announcement raised")

    def _notify_tts_join(self, player_name: str, is_admin: bool) -> None:
        """Forward a lobby join to the TTS announcer if one is wired (#281).

        No-op when ``_tts_announcer`` is None. Guarded like the milestone hook.
        """
        announcer = self._tts_announcer
        if announcer is None:
            return
        try:
            announcer.announce_join(player_name, is_admin)
        except Exception:  # noqa: BLE001
            _LOGGER.exception("TTS join announcement raised")

    def _notify_tts_countdown(self, seconds_remaining: float) -> None:
        """Forward the per-tick remaining time to the TTS announcer (#281).

        Called every timer tick; the announcer fires its one-shot "time
        running out" warning at most once per round. No-op when no announcer
        is wired. Guarded like the milestone hook.
        """
        announcer = self._tts_announcer
        if announcer is None:
            return
        try:
            announcer.announce_countdown(seconds_remaining)
        except Exception:  # noqa: BLE001
            _LOGGER.exception("TTS countdown announcement raised")

    def _notify_tts_reveal(self, game_state: QuizifyGameState) -> None:
        """Forward the reveal to the TTS announcer if one is wired (#281).

        No-op when ``_tts_announcer`` is None. Guarded like the milestone hook.
        """
        announcer = self._tts_announcer
        if announcer is None:
            return
        try:
            announcer.announce_reveal(game_state)
        except Exception:  # noqa: BLE001
            _LOGGER.exception("TTS reveal announcement raised")

    # ------------------------------------------------------------------
    # HA event-bus forwarders (#366) — thin, no-op-guarded siblings of the
    # _notify_tts_* hooks. The emitter fires quizify_* bus events so the host
    # can drive automations off game milestones.
    # ------------------------------------------------------------------

    def set_event_emitter(
        self, emitter: QuizifyEventEmitter | None
    ) -> None:
        """Wire (or rewire) the optional HA event emitter (#366).

        Public entry point used by ``__init__.py`` at setup and on every options
        reload, mirroring :meth:`set_tts_announcer`. ``None`` clears it, restoring
        the no-op path.
        """
        self._event_emitter = emitter

    def _notify_house_question(
        self, question: Any, round_no: int, total_rounds: int
    ) -> None:
        """Forward a question-start to the HA event emitter if one is wired.

        No-op when ``_event_emitter`` is None (standalone dev server). Guarded so
        a bad fire can't break the question fan-out.
        """
        emitter = self._event_emitter
        if emitter is None:
            return
        try:
            emitter.notify_question_shown(question, round_no, total_rounds)
        except Exception:  # noqa: BLE001
            _LOGGER.exception("House question event raised")

    def _notify_house_time_running_out(self, seconds_remaining: float) -> None:
        """Forward the per-tick remaining time to the HA event emitter (#280).

        Called every timer tick alongside :meth:`_notify_tts_countdown`; the
        emitter fires its one-shot ``quizify_time_running_out`` event at most
        once per round in the final seconds. No-op when ``_event_emitter`` is
        None (standalone dev server). Guarded like the question hook.
        """
        emitter = self._event_emitter
        if emitter is None:
            return
        try:
            emitter.notify_time_running_out(seconds_remaining)
        except Exception:  # noqa: BLE001
            _LOGGER.exception("House time-running-out event raised")

    def _notify_house_milestone(
        self, player_name: str, streak: int, bonus: int
    ) -> None:
        """Forward a streak milestone to the HA event emitter if one is wired.

        No-op when ``_event_emitter`` is None. Guarded like the question hook.
        """
        emitter = self._event_emitter
        if emitter is None:
            return
        try:
            emitter.notify_streak_milestone(player_name, streak, bonus)
        except Exception:  # noqa: BLE001
            _LOGGER.exception("House milestone event raised")

    def _notify_house_reveal(self, game_state: QuizifyGameState) -> None:
        """Forward the reveal to the HA event emitter if one is wired.

        No-op when ``_event_emitter`` is None. Guarded like the question hook.
        """
        emitter = self._event_emitter
        if emitter is None:
            return
        try:
            emitter.notify_answer_revealed(game_state)
        except Exception:  # noqa: BLE001
            _LOGGER.exception("House reveal event raised")

    def _notify_house_game_ended(self, game_state: QuizifyGameState) -> None:
        """Forward game end to the HA event emitter if one is wired.

        No-op when ``_event_emitter`` is None. Guarded like the question hook.
        """
        emitter = self._event_emitter
        if emitter is None:
            return
        try:
            emitter.notify_game_ended(game_state)
        except Exception:  # noqa: BLE001
            _LOGGER.exception("House game-ended event raised")

    # ------------------------------------------------------------------
    # Finale broadcast helper
    # ------------------------------------------------------------------

    async def _broadcast_finale(self, game_state: QuizifyGameState) -> None:
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
        # Pack labels for the shareable card (#369). ``categories`` is the
        # multi-select the host picked; ``category`` is the single-pick
        # fallback. Empty when the host played "mixed", and the card simply
        # omits the line rather than inventing a pack name.
        packs = list(getattr(game_state, "categories", None) or [])
        if not packs and getattr(game_state, "category", None):
            packs = [game_state.category]
        # Slug -> display name. The card is written to be pasted into a group
        # chat, and "picture-round-en" reads like a filename; the picker calls
        # the same pack "Picture Round". Unknown slugs (a pack removed
        # mid-game) fall back to the slug rather than vanishing from the line.
        try:
            _meta = game_state.question_bank.get_pack_versions()
            packs = [(_meta.get(slug) or {}).get("name") or slug for slug in packs]
        except (AttributeError, TypeError):  # pragma: no cover - defensive
            pass
        finale_msg = serialize_finale(
            podium, all_players, superlatives=awards, packs=packs
        )
        await self._conn.broadcast(finale_msg)

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
            await self._broadcast_round_summary(game_state)
            # Narrate the reveal (correct answer + who got it + standings) as
            # a single combined utterance (#281), after the summary broadcast.
            self._notify_tts_reveal(game_state)
            # Fire the HA bus event with the correct answer + how many got it
            # (#366), off the same round summary.
            self._notify_house_reveal(game_state)

    async def _dispatch_game_ended(self) -> None:
        """Handler for the ``game_ended`` state event."""
        game_state = self._get_game_state()
        if game_state:
            await self._broadcast_finale(game_state)
            # Fire the HA bus event with the final leaderboard (#366), after
            # the finale broadcast so entity state is already settled.
            self._notify_house_game_ended(game_state)

    async def _dispatch_full_state(self) -> None:
        """Default handler: broadcast a full game-state snapshot."""
        game_state = self._get_game_state()
        if game_state:
            state = game_state.get_state_snapshot()
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
            lambda self, ws, data, gs: self._handle_hot_seat_bid(ws, data, gs),
            False,
        ),
        MSG_HOT_SEAT_BET: (
            lambda self, ws, data, gs: self._handle_hot_seat_bet(ws, data, gs),
            False,
        ),
        MSG_HOT_SEAT_ANSWER: (
            lambda self, ws, data, gs: self._handle_hot_seat_answer(ws, data, gs),
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
