"""Regression tests for the ws/state bug+perf batch (#407,#410,#412,#413,
#414,#415,#416).

Each concern gets a focused test so a future edit can't silently regress:

  * #407 — an explicit start / play-again out of LOBBY cancels the lightning
    loop AND the deferred admin-pause, not just the timer tick.
  * #410 — a non-dict JSON frame (``[1,2]``, ``"x"``, ``5``) is rejected with a
    clean error instead of tearing down the connection with an AttributeError.
  * #412 — when the last unanswered player drops (or is removed after grace)
    mid-question and everyone else has submitted, the round evaluates now
    instead of waiting out the timer.
  * #413 — the normal-round tick loop coalesces frames to one per displayed
    second (``ceil(remaining)``), like the lightning loop.
  * #414 — ``build_round_summary`` is memoized per (game_id, round) and
    invalidated on the next round / reset.
  * #415 — ``serialize_finale`` builds the leaderboard once; ``_broadcast_finale``
    consumes the cached podium/superlatives instead of recomputing.
  * #416 — reveal reaction bonuses are coalesced into one ``reaction_bonus`` per
    flush window carrying a single leaderboard.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT))

from custom_components.quizify.game import scoring  # noqa: E402
from custom_components.quizify.game.state import (  # noqa: E402
    GamePhase,
    QuizifyGameState,
)
from custom_components.quizify.server import WS_PATH  # noqa: E402
from custom_components.quizify.server import websocket as ws_mod  # noqa: E402
from custom_components.quizify.server.round_message_builder import (  # noqa: E402
    RoundMessageBuilder,
)
from custom_components.quizify.server.serializers import serialize_finale  # noqa: E402
from custom_components.quizify.server.websocket import (  # noqa: E402
    QuizifyWebSocketHandler,
)


class _Runtime:
    def __init__(self, tmp_path: Path) -> None:
        self.data_dir = tmp_path

    async def run_in_executor(self, func, *args):  # noqa: ANN001, ANN002
        return func(*args)

    def create_task(self, coro):  # noqa: ANN001
        return asyncio.ensure_future(coro)


def _fake_ws() -> MagicMock:
    ws = MagicMock()
    ws.closed = False
    ws.send_json = AsyncMock()
    return ws


def _handler(tmp_path: Path, game: QuizifyGameState) -> QuizifyWebSocketHandler:
    return QuizifyWebSocketHandler(
        runtime=_Runtime(tmp_path), game_state_provider=lambda: game
    )


def _started_game(tmp_path: Path, names: list[str]) -> QuizifyGameState:
    gs = QuizifyGameState(runtime=_Runtime(tmp_path), entry_id="t")
    for n in names:
        gs.add_player(n, _fake_ws())
    gs.start_game(language="de", num_rounds=3, lightning_enabled=False)
    gs.start_next_question()
    return gs


# ---------------------------------------------------------------------------
# #407 — start / play-again cancels lightning loop + admin pause
# ---------------------------------------------------------------------------


class TestStartResetCancels407:
    @pytest.mark.asyncio
    async def test_start_game_out_of_lobby_cancels_lightning_and_admin_pause(
        self, tmp_path: Path
    ) -> None:
        gs = _started_game(tmp_path, ["Alice", "Bob"])
        assert gs.phase == GamePhase.QUESTION_ACTIVE

        h = _handler(tmp_path, gs)
        h.START_REDIRECT_GRACE = 0.0  # don't wait the redirect grace
        # Pre-arm the two tasks the old code left dangling.
        h._lightning_task = asyncio.ensure_future(asyncio.sleep(100))
        h._admin_pause_task = asyncio.ensure_future(asyncio.sleep(100))

        await h._handle_start_game(
            _fake_ws(),
            {"language": "de", "num_rounds": 3, "lightning_enabled": False},
            gs,
        )

        # Both must have been cancelled (the cancel helpers null them out).
        assert h._lightning_task is None
        assert h._admin_pause_task is None
        h._cancel_timer_tick()

    @pytest.mark.asyncio
    async def test_play_again_cancels_lightning_and_admin_pause(
        self, tmp_path: Path
    ) -> None:
        gs = _started_game(tmp_path, ["Alice", "Bob"])
        # last_settings is populated by start_game, so play_again won't fall
        # back to reset.
        assert gs.last_settings

        h = _handler(tmp_path, gs)
        h.START_REDIRECT_GRACE = 0.0
        h._lightning_task = asyncio.ensure_future(asyncio.sleep(100))
        h._admin_pause_task = asyncio.ensure_future(asyncio.sleep(100))

        await h._handle_play_again(_fake_ws(), gs)

        assert h._lightning_task is None
        assert h._admin_pause_task is None
        h._cancel_timer_tick()


# ---------------------------------------------------------------------------
# #410 — non-dict JSON frame is rejected, connection survives
# ---------------------------------------------------------------------------


class TestMalformedFrameGuard410:
    async def _make_client(self, handler: QuizifyWebSocketHandler) -> TestClient:
        app = web.Application()
        app.router.add_get(WS_PATH, handler.handle)
        client = TestClient(TestServer(app))
        await client.start_server()
        return client

    @pytest.mark.parametrize("payload", [[1, 2], "x", 5, True])
    @pytest.mark.asyncio
    async def test_non_dict_json_gets_error_not_teardown(
        self, tmp_path: Path, payload: object
    ) -> None:
        gs = QuizifyGameState(runtime=_Runtime(tmp_path), entry_id="t")
        handler = _handler(tmp_path, gs)
        client = await self._make_client(handler)
        try:
            ws = await client.ws_connect(WS_PATH)
            await ws.send_json(payload)
            err = await ws.receive_json(timeout=2)
            assert err["type"] == "error"
            # The socket must still be alive: a follow-up valid message gets a
            # real response instead of a closed connection.
            await ws.send_json({"type": "get_state"})
            follow = await ws.receive_json(timeout=2)
            assert follow["type"] in ("game_state", "error")
            await ws.close()
        finally:
            await client.close()


# ---------------------------------------------------------------------------
# #412 — last unanswered player dropping completes the round
# ---------------------------------------------------------------------------


class TestLastPlayerDropoutEval412:
    @pytest.mark.asyncio
    async def test_disconnect_of_last_unanswered_evaluates_round(
        self, tmp_path: Path
    ) -> None:
        gs = _started_game(tmp_path, ["Alice", "Bob"])
        bob = gs.get_player("Bob")
        q = gs.get_current_question()
        correct = next(i for i, a in enumerate(q.answers) if a.correct)
        gs.submit_answer("Bob", correct)
        assert gs.phase == GamePhase.QUESTION_ACTIVE  # Alice hasn't answered

        h = _handler(tmp_path, gs)
        h._conn.broadcast = AsyncMock()  # type: ignore[method-assign]

        alice_ws = gs.get_player("Alice").ws
        await h._handle_disconnect(alice_ws, was_admin=False)

        # Alice (the last unanswered) is gone → registry now reports all
        # submitted → round auto-evaluated into the reveal.
        assert gs.phase == GamePhase.ANSWER_REVEAL
        assert bob.score > 0
        await h._conn.cleanup()

    @pytest.mark.asyncio
    async def test_disconnect_when_others_unanswered_does_not_evaluate(
        self, tmp_path: Path
    ) -> None:
        gs = _started_game(tmp_path, ["Alice", "Bob"])
        # Nobody has answered. Alice dropping still leaves Bob unanswered, so
        # the round must NOT evaluate early.
        h = _handler(tmp_path, gs)
        h._conn.broadcast = AsyncMock()  # type: ignore[method-assign]

        alice_ws = gs.get_player("Alice").ws
        await h._handle_disconnect(alice_ws, was_admin=False)

        assert gs.phase == GamePhase.QUESTION_ACTIVE
        await h._conn.cleanup()


# ---------------------------------------------------------------------------
# #413 — tick loop coalesces to one frame per displayed second
# ---------------------------------------------------------------------------


class TestTickCoalescing413:
    @pytest.mark.asyncio
    async def test_no_duplicate_frames_within_one_displayed_second(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Fast poll cadence, long timer → many poll iterations but the displayed
        # second (ceil) never changes across the test window.
        monkeypatch.setattr(ws_mod, "TICK_INTERVAL", 0.02)
        gs = _started_game(tmp_path, ["Alice"])
        assert gs.phase == GamePhase.QUESTION_ACTIVE

        h = _handler(tmp_path, gs)
        player_sends: list[dict] = []
        admin_broadcasts: list[dict] = []

        async def _send(ws, msg):  # noqa: ANN001
            player_sends.append(msg)

        async def _admin_bcast(msg):  # noqa: ANN001
            admin_broadcasts.append(msg)

        h._conn.send = _send  # type: ignore[assignment]
        h._conn.broadcast_to_admins_and_dashboards = _admin_bcast  # type: ignore[assignment]

        h._start_timer_tick(gs)
        # ~7 poll iterations at 0.02s; the 30s-ish timer barely moves so
        # ceil(remaining) stays constant the whole time.
        await asyncio.sleep(0.15)
        h._cancel_timer_tick()

        tick_frames = [m for m in player_sends if m.get("type") == "timer_tick"]
        # Before the fix: one frame per poll (~7). After: exactly one, because
        # the displayed second never changed.
        assert len(tick_frames) == 1
        assert len(admin_broadcasts) == 1


# ---------------------------------------------------------------------------
# #414 — build_round_summary is memoized per round
# ---------------------------------------------------------------------------


class TestRoundSummaryMemo414:
    def _to_reveal(self, gs: QuizifyGameState) -> None:
        q = gs.get_current_question()
        correct = next(i for i, a in enumerate(q.answers) if a.correct)
        gs.submit_answer(gs.get_players()[0].name, correct)
        gs.evaluate_round()

    def test_memoized_within_round_and_invalidated_next_round(
        self, tmp_path: Path
    ) -> None:
        gs = _started_game(tmp_path, ["Alice", "Bob"])
        self._to_reveal(gs)
        assert gs.phase == GamePhase.ANSWER_REVEAL

        builder = RoundMessageBuilder()
        msg1 = builder.build_round_summary(gs)
        msg2 = builder.build_round_summary(gs)
        # Same cached object, not a fresh rebuild.
        assert msg1 is msg2
        key = (gs.game_id, gs.round)
        assert gs.get_cached_round_summary_msg(key) is msg1

        # Next round invalidates the cache.
        gs.start_next_question()
        assert gs.get_cached_round_summary_msg(key) is None

    def test_reset_to_lobby_clears_cache(self, tmp_path: Path) -> None:
        gs = _started_game(tmp_path, ["Alice", "Bob"])
        self._to_reveal(gs)
        key = (gs.game_id, gs.round)
        RoundMessageBuilder().build_round_summary(gs)
        assert gs.get_cached_round_summary_msg(key) is not None
        gs.reset_to_lobby()
        assert gs.get_cached_round_summary_msg(key) is None


# ---------------------------------------------------------------------------
# #415 — finale serialization dedup + cached-data consumption
# ---------------------------------------------------------------------------


class TestFinaleEfficiency415:
    def test_serialize_finale_reuses_one_leaderboard(self, tmp_path: Path) -> None:
        gs = _started_game(tmp_path, ["Alice", "Bob"])
        players = gs.get_players()
        podium = scoring.calculate_podium(players)
        msg = serialize_finale(podium, players)
        # Same list object powers both keys — serialized once.
        assert msg["leaderboard"] is msg["all_players"]

    @pytest.mark.asyncio
    async def test_broadcast_finale_consumes_cache_without_recompute(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        gs = _started_game(tmp_path, ["Alice", "Bob"])
        gs.end_game()  # populates _finale_podium / _finale_superlatives
        assert gs.get_finale_podium() is not None

        h = _handler(tmp_path, gs)
        captured: list[dict] = []
        h._conn.broadcast = lambda m: captured.append(m) or asyncio.sleep(0)  # type: ignore[assignment,return-value]

        # If _broadcast_finale recomputes instead of using the cache, these blow
        # up — proving the cache is consumed.
        def _boom(*_a, **_k):  # noqa: ANN002, ANN003
            raise AssertionError("podium recomputed despite cached finale data")

        monkeypatch.setattr(scoring, "calculate_podium", _boom)
        monkeypatch.setattr(ws_mod, "compute_superlatives", _boom)

        await h._broadcast_finale(gs)
        assert captured and captured[-1]["type"] == "finale"


# ---------------------------------------------------------------------------
# #416 — reaction bonuses coalesced into one flush frame
# ---------------------------------------------------------------------------


class TestReactionBonusCoalescing416:
    @pytest.mark.asyncio
    async def test_two_reactors_produce_one_bonus_frame(
        self, tmp_path: Path
    ) -> None:
        gs = _started_game(tmp_path, ["Bob", "Alice", "Carol"])
        q = gs.get_current_question()
        correct = next(i for i, a in enumerate(q.answers) if a.correct)
        gs.submit_answer("Bob", correct)
        gs.evaluate_round()
        assert gs.phase == GamePhase.ANSWER_REVEAL

        h = _handler(tmp_path, gs)
        sent: list[dict] = []
        h._conn.broadcast = lambda m: sent.append(m) or asyncio.sleep(0)  # type: ignore[assignment,return-value]

        bob_before = gs.get_player("Bob").score
        await h._handle_reaction(gs.get_player("Alice").ws, {"emoji": "🎉"}, gs)
        await h._handle_reaction(gs.get_player("Carol").ws, {"emoji": "👏"}, gs)

        # Points settle synchronously: Bob tipped +1 by each of two reactors.
        assert gs.get_player("Bob").score == bob_before + 2

        # Drain the flush window.
        if h._reaction_flush_task is not None:
            await h._reaction_flush_task

        bonus_frames = [m for m in sent if m.get("type") == "reaction_bonus"]
        assert len(bonus_frames) == 1
        frame = bonus_frames[0]
        assert frame["to_players"] == ["Bob"]
        assert set(frame["from_players"]) == {"Alice", "Carol"}
        assert frame["leaderboard"], "coalesced bonus must carry a leaderboard"
