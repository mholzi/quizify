"""The house plays along during the three detours too (#708).

The auction, the betting window and the lightning round are the loudest
moments of an evening, and they were the only ones the house sat out. The
light recipes stopped at five phases while the game has eleven, so the room
simply froze on whatever colour the previous phase had left it; the narrator
had no line for any of the three; and no ``quizify_*`` event fired, so a host's
own blueprints could not react either.

The fix walks the same backbone the normal round walks — the ``MilestoneSink``
each mode driver already holds (#788) — rather than opening a second path.
Which is why most of what these tests assert is *reuse*: the chair's question
is a ``question_shown``, its settlement a ``reveal``, and both light recipes
are the ones an ordinary round uses. Only three beats are new, one per mode,
for the one moment each mode has that a normal round does not.

And all of it stays behind the house masters. A host who never turned the
house on must hear nothing, see nothing and get nothing on the bus — the last
class in this file is that promise, driven through the real consumers rather
than through recorders.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from custom_components.quizify import lights as lights_mod  # noqa: E402
from custom_components.quizify.game.drivers import (  # noqa: E402
    LightningDriver,
    WagerWindowDriver,
)
from custom_components.quizify.game.state import (  # noqa: E402
    GamePhase,
    QuizifyGameState,
)
from custom_components.quizify.game_events import (  # noqa: E402
    EVENT_HOT_SEAT_STARTED,
    EVENT_LIGHTNING_STARTED,
    EVENT_WAGER_OPEN,
    QuizifyEventEmitter,
)
from custom_components.quizify.lights import QuizifyPartyLights  # noqa: E402
from custom_components.quizify.server import websocket as ws_mod  # noqa: E402
from custom_components.quizify.server.connection import (  # noqa: E402
    ConnectionManager,
)
from custom_components.quizify.server.websocket import (  # noqa: E402
    QuizifyWebSocketHandler,
)
from custom_components.quizify.tts import QuizifyTTSAnnouncer  # noqa: E402

# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class _FakeRuntime:
    def __init__(self, tmp_path: Path) -> None:
        self.data_dir = tmp_path

    def create_task(self, coro):  # noqa: ANN001, ANN202
        return asyncio.ensure_future(coro)

    async def run_in_executor(self, func, *args):  # noqa: ANN001, ANN202
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, func, *args)


class _FakeBus:
    def __init__(self) -> None:
        self.fired: list[tuple[str, dict]] = []

    def async_fire(self, event_type, data):  # noqa: ANN001
        self.fired.append((event_type, dict(data)))

    def async_listen(self, event_type, cb):  # noqa: ANN001, ARG002
        return lambda: None


class _FakeHass:
    def __init__(self) -> None:
        self.bus = _FakeBus()

    def async_create_task(self, coro):  # noqa: ANN001, ANN202
        return asyncio.ensure_future(coro)


class _Announcer:
    """Records what the quizmaster was asked to say.

    Only the real method names are declared: the handler looks a consumer's
    method up by name and swallows the ``AttributeError``, so a beat calling
    the wrong one would record nothing and fail its assertion here rather than
    going quiet in a living room.
    """

    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple]] = []

    def announce_hot_seat_started(self, *args) -> None:  # noqa: ANN002
        self.calls.append(("announce_hot_seat_started", args))

    def announce_wager_open(self, *args) -> None:  # noqa: ANN002
        self.calls.append(("announce_wager_open", args))

    def announce_lightning_started(self, *args) -> None:  # noqa: ANN002
        self.calls.append(("announce_lightning_started", args))

    def announce_question(self, *args) -> None:  # noqa: ANN002
        self.calls.append(("announce_question", args))

    def announce_countdown(self, *args) -> None:  # noqa: ANN002
        self.calls.append(("announce_countdown", args))

    def announce_reveal(self, *args) -> None:  # noqa: ANN002
        self.calls.append(("announce_reveal", args))


class _Emitter:
    """Records what reached the Home Assistant bus."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple]] = []

    def notify_hot_seat_started(self, *args) -> None:  # noqa: ANN002
        self.calls.append(("notify_hot_seat_started", args))

    def notify_wager_open(self, *args) -> None:  # noqa: ANN002
        self.calls.append(("notify_wager_open", args))

    def notify_lightning_started(self, *args) -> None:  # noqa: ANN002
        self.calls.append(("notify_lightning_started", args))

    def notify_question_shown(self, *args) -> None:  # noqa: ANN002
        self.calls.append(("notify_question_shown", args))

    def notify_time_running_out(self, *args) -> None:  # noqa: ANN002
        self.calls.append(("notify_time_running_out", args))

    def notify_answer_revealed(self, *args) -> None:  # noqa: ANN002
        self.calls.append(("notify_answer_revealed", args))


class _SilentWagerOut:
    """A wager broadcaster that closes nothing — the beat is what is on test."""

    def __init__(self) -> None:
        self.closed = 0

    async def close_wager_window(self, game_state) -> None:  # noqa: ANN001, ARG002
        self.closed += 1


def _names(recorder) -> list[str]:  # noqa: ANN001
    return [name for name, _args in recorder.calls]


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def game(tmp_path: Path) -> QuizifyGameState:
    st = QuizifyGameState(runtime=_FakeRuntime(tmp_path), entry_id="test")
    for name in ("Anna", "Ben", "Cem", "Dana"):
        st.add_player(name)
    st.start_game(num_rounds=6, language="en", hot_seat_seed=7, lightning_seed=7)
    return st


@pytest.fixture
def handler(game: QuizifyGameState, tmp_path: Path) -> QuizifyWebSocketHandler:
    h = QuizifyWebSocketHandler(
        runtime=_FakeRuntime(tmp_path), game_state_provider=lambda: game
    )
    h._conn = ConnectionManager(_FakeRuntime(tmp_path), lambda: game)
    h._conn.broadcast = AsyncMock()
    h._conn.broadcast_to_admins_and_dashboards = AsyncMock()
    h._conn.send = AsyncMock()
    h._conn.send_to_player = AsyncMock()
    # Four seconds of television; the hold is not what is under test.
    h.HOT_SEAT_REVEAL_HOLD = 0.0
    return h


async def _run_one_auction(
    handler: QuizifyWebSocketHandler, game: QuizifyGameState
) -> None:
    """Open an auction, place one bid, and let the driver run to settlement."""
    game.phase = GamePhase.ANSWER_REVEAL
    # A bid is a share of what the bidder holds, so a room on zero has nothing
    # to bid with and the auction correctly finds no winner.
    for score, player in enumerate(game.get_players(), start=1):
        player.score = score * 20
    assert game.start_hot_seat_auction() is True, "the auction refused to open"
    hs = game.hot_seat
    assert hs is not None
    hs.auction_seconds = 0.05
    hs.answer_seconds = 0.3
    hs.start_auction_clock()
    assert hs.record_bid("Anna", 50) is True

    handler._start_hot_seat_loop(game)
    for _ in range(200):
        await asyncio.sleep(0.01)
        if game.phase == GamePhase.HOT_SEAT_REVEAL:
            break
    handler._cancel_hot_seat_loop()
    assert game.phase == GamePhase.HOT_SEAT_REVEAL, (
        "the auction never reached settlement — the test setup drifted"
    )


# ---------------------------------------------------------------------------
# 1. The auction
# ---------------------------------------------------------------------------


class TestTheAuction:
    @pytest.mark.asyncio
    async def test_the_narrator_calls_the_auction_result(
        self, handler: QuizifyWebSocketHandler, game: QuizifyGameState
    ) -> None:
        """The sealed bid becomes public at exactly one moment. Say it."""
        announcer = _Announcer()
        handler.set_tts_announcer(announcer)  # type: ignore[arg-type]

        await _run_one_auction(handler, game)

        said = [c for c in announcer.calls if c[0] == "announce_hot_seat_started"]
        assert said, "the room was never told who bought the chair — #708"
        (_name, args) = said[0]
        seat_holder, stake = args
        assert seat_holder == "Anna"
        # Anna held 20 points and staked 50% of them.
        assert stake == 10

    @pytest.mark.asyncio
    async def test_the_auction_result_reaches_the_event_bus(
        self, handler: QuizifyWebSocketHandler, game: QuizifyGameState
    ) -> None:
        emitter = _Emitter()
        handler.set_event_emitter(emitter)  # type: ignore[arg-type]

        await _run_one_auction(handler, game)

        assert "notify_hot_seat_started" in _names(emitter), (
            "no quizify_hot_seat_started fired for the chair — #708"
        )

    @pytest.mark.asyncio
    async def test_the_payload_names_the_seat_and_what_it_cost(
        self, handler: QuizifyWebSocketHandler, game: QuizifyGameState
    ) -> None:
        """Through the REAL emitter, so the bus payload itself is pinned."""
        hass = _FakeHass()
        handler.set_event_emitter(
            QuizifyEventEmitter(hass=hass, game_state=game, enabled=True)
        )

        await _run_one_auction(handler, game)

        fired = [d for (t, d) in hass.bus.fired if t == EVENT_HOT_SEAT_STARTED]
        assert fired, "quizify_hot_seat_started never reached the bus — #708"
        payload = fired[0]
        assert payload["player_name"] == "Anna"
        assert payload["entrant_name"] == "Anna"
        assert payload["bid_pct"] == 50
        assert payload["stake"] == 10
        assert payload["bidder_count"] == 1

    @pytest.mark.asyncio
    async def test_a_bidless_auction_announces_nothing(
        self, handler: QuizifyWebSocketHandler, game: QuizifyGameState
    ) -> None:
        """A chair nobody wanted is a round that never happened (#616).

        The beat sits at the award and not at the opening precisely so a
        blueprint is never handed a mode it then has to unwind.
        """
        announcer = _Announcer()
        emitter = _Emitter()
        handler.set_tts_announcer(announcer)  # type: ignore[arg-type]
        handler.set_event_emitter(emitter)  # type: ignore[arg-type]
        handler._emit_question = AsyncMock()  # the fallback question

        game.phase = GamePhase.ANSWER_REVEAL
        for score, player in enumerate(game.get_players(), start=1):
            player.score = score * 20
        assert game.start_hot_seat_auction() is True
        hs = game.hot_seat
        assert hs is not None
        hs.auction_seconds = 0.05
        hs.start_auction_clock()
        # No bids at all.

        handler._start_hot_seat_loop(game)
        for _ in range(100):
            await asyncio.sleep(0.01)
            if game.phase != GamePhase.HOT_SEAT_AUCTION:
                break
        handler._cancel_hot_seat_loop()

        assert "announce_hot_seat_started" not in _names(announcer)
        assert "notify_hot_seat_started" not in _names(emitter)


# ---------------------------------------------------------------------------
# 2. The betting window
# ---------------------------------------------------------------------------


class TestTheBettingWindow:
    @pytest.mark.asyncio
    async def test_the_window_opens_the_house_before_it_waits(
        self, game: QuizifyGameState, tmp_path: Path
    ) -> None:
        """The beat is "bets are open", so it lands as the window appears."""
        announcer = _Announcer()
        emitter = _Emitter()
        handler = QuizifyWebSocketHandler(
            runtime=_FakeRuntime(tmp_path), game_state_provider=lambda: game
        )
        handler.set_tts_announcer(announcer)  # type: ignore[arg-type]
        handler.set_event_emitter(emitter)  # type: ignore[arg-type]

        out = _SilentWagerOut()
        driver = WagerWindowDriver(out, duration=30.0, milestones=handler)
        task = asyncio.ensure_future(driver.run(game))
        await asyncio.sleep(0.02)
        task.cancel()

        assert "announce_wager_open" in _names(announcer), (
            "the betting window opened in silence — #708"
        )
        assert "notify_wager_open" in _names(emitter), (
            "no quizify_wager_open reached the bus — #708"
        )
        assert out.closed == 0, "the window closed before its deadline"

    @pytest.mark.asyncio
    async def test_the_bus_payload_carries_the_deadline(
        self, game: QuizifyGameState, tmp_path: Path
    ) -> None:
        """An automation has no screen; the countdown has to travel."""
        hass = _FakeHass()
        handler = QuizifyWebSocketHandler(
            runtime=_FakeRuntime(tmp_path), game_state_provider=lambda: game
        )
        handler.set_event_emitter(
            QuizifyEventEmitter(hass=hass, game_state=game, enabled=True)
        )

        driver = WagerWindowDriver(
            _SilentWagerOut(), duration=17.5, milestones=handler
        )
        task = asyncio.ensure_future(driver.run(game))
        await asyncio.sleep(0.02)
        task.cancel()

        fired = [d for (t, d) in hass.bus.fired if t == EVENT_WAGER_OPEN]
        assert fired, "quizify_wager_open never reached the bus — #708"
        assert fired[0]["seconds"] == 17.5
        assert fired[0]["total_rounds"] == game.total_rounds
        # The question is withheld while the bets are placed — so is its text.
        assert "question" not in fired[0]
        assert "category" not in fired[0]

    @pytest.mark.asyncio
    async def test_the_handler_wires_the_window_to_itself(
        self, monkeypatch, game: QuizifyGameState, tmp_path: Path
    ) -> None:
        """The window used to be the one driver with no sink at all."""
        seen: dict = {}

        class _Recording:
            def __init__(self, out, *, duration, milestones=None) -> None:  # noqa: ANN001
                seen["milestones"] = milestones

            def run(self, game_state):  # noqa: ANN001, ANN202
                async def _noop() -> None:
                    return

                return _noop()

        monkeypatch.setattr(ws_mod, "WagerWindowDriver", _Recording)
        handler = QuizifyWebSocketHandler(
            runtime=_FakeRuntime(tmp_path), game_state_provider=lambda: game
        )
        handler._start_wager_window(game)
        handler._cancel_wager_window()

        assert seen["milestones"] is handler


# ---------------------------------------------------------------------------
# 3. The lightning round
# ---------------------------------------------------------------------------


class TestTheLightningRound:
    @pytest.mark.asyncio
    async def test_the_fast_round_announces_itself_once(
        self, handler: QuizifyWebSocketHandler, game: QuizifyGameState
    ) -> None:
        """One line for the mode, and no line per question.

        ``announce_question`` reads a question aloud; five of those inside
        seventy-five seconds would talk over the very mode they accompany.
        """
        announcer = _Announcer()
        emitter = _Emitter()
        handler.set_tts_announcer(announcer)  # type: ignore[arg-type]
        handler.set_event_emitter(emitter)  # type: ignore[arg-type]

        assert game.start_lightning_round() is True
        handler._start_lightning_loop(game)
        await asyncio.sleep(0.05)
        handler._cancel_lightning_loop()

        assert _names(announcer) == ["announce_lightning_started"], (
            "the lightning round was neither announced, nor left unnarrated "
            "as designed — #708"
        )
        assert _names(emitter) == ["notify_lightning_started"]

    @pytest.mark.asyncio
    async def test_the_bus_payload_describes_the_mode(
        self, handler: QuizifyWebSocketHandler, game: QuizifyGameState
    ) -> None:
        hass = _FakeHass()
        handler.set_event_emitter(
            QuizifyEventEmitter(hass=hass, game_state=game, enabled=True)
        )

        assert game.start_lightning_round() is True
        lr = game.lightning
        assert lr is not None
        handler._start_lightning_loop(game)
        await asyncio.sleep(0.05)
        handler._cancel_lightning_loop()

        fired = [d for (t, d) in hass.bus.fired if t == EVENT_LIGHTNING_STARTED]
        assert fired, "quizify_lightning_started never reached the bus — #708"
        assert fired[0]["question_count"] == lr.num_questions
        assert fired[0]["seconds_per_question"] == lr.seconds_per_question
        assert fired[0]["player_count"] == 4

    @pytest.mark.asyncio
    async def test_the_auto_flow_sounds_the_same_as_a_host_tap(
        self, game: QuizifyGameState, tmp_path: Path
    ) -> None:
        """#285 dismisses the splash itself; the room must not notice."""
        announcer = _Announcer()
        handler = QuizifyWebSocketHandler(
            runtime=_FakeRuntime(tmp_path), game_state_provider=lambda: game
        )
        handler._conn = ConnectionManager(_FakeRuntime(tmp_path), lambda: game)
        handler._conn.broadcast = AsyncMock()
        handler._conn.broadcast_to_admins_and_dashboards = AsyncMock()
        handler._conn.send_to_player = AsyncMock()
        handler.set_tts_announcer(announcer)  # type: ignore[arg-type]

        assert game.start_lightning_round() is True
        driver = LightningDriver(
            handler._lightning_out, milestones=handler, splash_hold=30.0
        )
        task = asyncio.ensure_future(
            driver.run(game, auto_dismiss_splash=True)
        )
        await asyncio.sleep(0.02)
        task.cancel()

        assert _names(announcer) == ["announce_lightning_started"]


# ---------------------------------------------------------------------------
# 4. The room follows every phase
# ---------------------------------------------------------------------------


class TestTheRoomFollowsEveryPhase:
    """``lights.py`` knew five phases; the game has eleven.

    ``_on_state_changed`` returns early on a missing recipe, so an unknown
    phase was not a dim room — it was the *previous* phase's colour, held for
    the whole detour.
    """

    @pytest.fixture
    def calls(self, monkeypatch):  # noqa: ANN201
        recorded: list[tuple[str, str, dict]] = []

        def _record(_hass, domain, service, data, _ctx):  # noqa: ANN001
            recorded.append((domain, service, dict(data)))

        monkeypatch.setattr(lights_mod, "fire_and_forget_service", _record)
        return recorded

    class _Game:
        def __init__(self) -> None:
            self.phase = GamePhase.LOBBY
            self.round = 3
            self.game_id = "g1"
            self.callbacks: list = []

        def register_state_callback(self, cb) -> None:  # noqa: ANN001
            self.callbacks.append(cb)

        def unregister_state_callback(self, cb) -> None:  # noqa: ANN001
            if cb in self.callbacks:
                self.callbacks.remove(cb)

    @pytest.mark.parametrize(
        "phase",
        [
            GamePhase.WAGER_ACTIVE,
            GamePhase.HOT_SEAT_AUCTION,
            GamePhase.HOT_SEAT,
            GamePhase.HOT_SEAT_REVEAL,
            GamePhase.LIGHTNING,
            GamePhase.LIGHTNING_RECAP,
        ],
    )
    def test_every_detour_phase_moves_the_room(self, calls, phase) -> None:  # noqa: ANN001
        game = self._Game()
        pl = QuizifyPartyLights(
            hass=_FakeHass(), game_state=game, entity_ids=["light.party"]
        )
        game.phase = phase
        pl._on_state_changed()

        assert calls, f"{phase.value} left the room on the last phase — #708"
        domain, service, data = calls[-1]
        assert (domain, service) == ("light", "turn_on")
        assert data["entity_id"] == ["light.party"]
        assert "rgb_color" in data and "brightness_pct" in data

    def test_the_chair_reuses_the_ordinary_question_and_reveal_looks(
        self,
    ) -> None:
        """A question is a question. Do not invent a third and fourth colour."""
        recipes = lights_mod._PHASE_LIGHT_RECIPES
        assert recipes[GamePhase.HOT_SEAT] == recipes[GamePhase.QUESTION_ACTIVE]
        assert (
            recipes[GamePhase.HOT_SEAT_REVEAL] == recipes[GamePhase.ANSWER_REVEAL]
        )
        assert (
            recipes[GamePhase.LIGHTNING_RECAP] == recipes[GamePhase.ANSWER_REVEAL]
        )

    def test_the_auction_drops_the_room_rather_than_pushing_it(self) -> None:
        """Sealed bids are the evening's one moment of private thinking."""
        auction = lights_mod._PHASE_LIGHT_RECIPES[GamePhase.HOT_SEAT_AUCTION]
        question = lights_mod._PHASE_LIGHT_RECIPES[GamePhase.QUESTION_ACTIVE]
        assert auction is not None and question is not None
        assert auction["brightness_pct"] < question["brightness_pct"]

    def test_every_phase_the_game_can_reach_has_an_answer(self) -> None:
        """The gap #708 is about, pinned as a rule instead of a list.

        ``PAUSED`` maps to ``None`` on purpose — "leave the room alone" is an
        answer. A phase that is simply absent from the table is not.
        """
        missing = [
            phase.value
            for phase in GamePhase
            if phase not in lights_mod._PHASE_LIGHT_RECIPES
        ]
        assert missing == [], f"phases with no light recipe: {missing}"


# ---------------------------------------------------------------------------
# 5. Default off
# ---------------------------------------------------------------------------


class TestTheHouseStaysOffUntilItIsTurnedOn:
    """A host who never enabled the house must see and hear no change.

    Driven through the REAL consumers, because that is where the masters live:
    the emitter's ``CONF_HOUSE_EVENTS_ENABLED`` (off out of the box), the
    narrator's ``_enabled`` (off until ``configure``), and the lights' "is any
    entity configured at all".
    """

    @pytest.mark.asyncio
    async def test_the_bus_stays_quiet_with_house_events_off(
        self, handler: QuizifyWebSocketHandler, game: QuizifyGameState
    ) -> None:
        hass = _FakeHass()
        handler.set_event_emitter(
            QuizifyEventEmitter(hass=hass, game_state=game, enabled=False)
        )

        await _run_one_auction(handler, game)
        driver = WagerWindowDriver(
            _SilentWagerOut(), duration=30.0, milestones=handler
        )
        task = asyncio.ensure_future(driver.run(game))
        await asyncio.sleep(0.02)
        task.cancel()

        assert hass.bus.fired == [], (
            "the house fired on the bus without being switched on"
        )

    @pytest.mark.asyncio
    async def test_the_narrator_stays_quiet_until_configured(
        self, handler: QuizifyWebSocketHandler, game: QuizifyGameState
    ) -> None:
        spoken: list[str] = []
        announcer = QuizifyTTSAnnouncer(
            hass=_FakeHass(),
            game_state=game,
            tts_entity_id="tts.google",
            media_player_entity_id="media_player.kitchen",
        )
        announcer._speak = lambda message: spoken.append(message)  # type: ignore[method-assign]
        handler.set_tts_announcer(announcer)

        await _run_one_auction(handler, game)
        handler.wager_open(game, 20.0)
        handler.lightning_started(game, None)

        assert spoken == [], (
            "the quizmaster narrated a detour on an install with narration off"
        )

    @pytest.mark.asyncio
    async def test_the_narrator_speaks_once_the_master_is_on(
        self, handler: QuizifyWebSocketHandler, game: QuizifyGameState
    ) -> None:
        """The other half of the promise: switched on, it does play along."""
        spoken: list[str] = []
        announcer = QuizifyTTSAnnouncer(
            hass=_FakeHass(),
            game_state=game,
            tts_entity_id="tts.google",
            media_player_entity_id="media_player.kitchen",
        )
        announcer.configure(
            enabled=True,
            announce_question=True,
            announce_reveal=True,
            announce_standings=True,
        )
        announcer._speak = lambda message: spoken.append(message)  # type: ignore[method-assign]
        handler.set_tts_announcer(announcer)

        handler.hot_seat_started(_FakeSeat())
        handler.wager_open(game, 20.0)
        handler.lightning_started(game, None)

        assert spoken == [
            "The chair goes to Anna for 10 points.",
            "Final round. Place your bets!",
            "Lightning round! Speed decides now.",
        ]

    def test_an_unconfigured_room_never_touches_a_light(
        self, monkeypatch, tmp_path: Path
    ) -> None:
        recorded: list = []
        monkeypatch.setattr(
            lights_mod,
            "fire_and_forget_service",
            lambda *a, **k: recorded.append(a),
        )
        game = TestTheRoomFollowsEveryPhase._Game()
        pl = QuizifyPartyLights(hass=_FakeHass(), game_state=game, entity_ids=[])
        for phase in (
            GamePhase.WAGER_ACTIVE,
            GamePhase.HOT_SEAT_AUCTION,
            GamePhase.LIGHTNING,
        ):
            game.phase = phase
            pl._on_state_changed()
        assert recorded == []


class _FakeSeat:
    """A settled auction, as the narrator sees it."""

    seat_holder = "Anna"
    winner_name = "Anna"
    winning_pct = 50
    winning_stake = 10
    bids: dict = {"Anna": object()}


# ---------------------------------------------------------------------------
# 6. A broken consumer never strands a mode
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_broken_narrator_does_not_strand_the_betting_window(
    game: QuizifyGameState, tmp_path: Path
) -> None:
    """A dead TTS entity is not a reason to hang the final round."""

    class _Exploding:
        def announce_wager_open(self) -> None:
            raise RuntimeError("the TTS entity is gone")

    handler = QuizifyWebSocketHandler(
        runtime=_FakeRuntime(tmp_path), game_state_provider=lambda: game
    )
    handler.set_tts_announcer(_Exploding())  # type: ignore[arg-type]

    out = _SilentWagerOut()
    driver = WagerWindowDriver(out, duration=0.02, milestones=handler)
    await driver.run(game)

    assert out.closed == 1, "the window never closed — #708"
