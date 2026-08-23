"""Issue #371 (variant A) — own all-time standing on the player lobby.

Variant A is the smallest of the four design variants: one line under the
hero, about the joining player only, no table and no lobby card. That shape
drives what these tests pin down:

  - the standing is ranked by **wins** (score only breaks ties), which is a
    DIFFERENT order from ``get_all_time_leaderboard()`` — that one stays
    score-first for the analytics dashboard, and must not drift;
  - it is per-player and rides the ``joined`` / ``reconnected`` frames, never
    the roster broadcast, so nobody's history is shipped to everyone's phone;
  - it is fail-soft: no analytics wired, or a name with no history, yields
    ``None`` and the client renders nothing (a first-timer must not read
    "1st of 1").
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT))

from custom_components.quizify.analytics import QuizifyAnalytics  # noqa: E402
from custom_components.quizify.game.state import QuizifyGameState  # noqa: E402
from custom_components.quizify.server.connection import (  # noqa: E402
    ConnectionManager,
)
from custom_components.quizify.server.websocket import (  # noqa: E402
    QuizifyWebSocketHandler,
)

WWW = _REPO_ROOT / "custom_components" / "quizify" / "www"


class _Runtime:
    """Minimal runtime satisfying analytics + game state."""

    def __init__(self, tmp_path: Path) -> None:
        self.data_dir = tmp_path

    async def run_in_executor(self, func, *args):  # noqa: ANN001, ANN002
        return func(*args)

    def create_task(self, coro):  # noqa: ANN001
        return asyncio.ensure_future(coro)


async def _analytics_with_games(tmp_path: Path) -> QuizifyAnalytics:
    """Three players, deliberately win-order != score-order.

    Papa: 2 wins, 300 points from 3 games
    Mama: 1 win, 500 points from 3 games  <- more points, fewer wins
    Kind: 0 wins, 90 points from 2 games
    """
    a = QuizifyAnalytics(_Runtime(tmp_path))
    await a.load()
    await a.record_game(
        game_id="g1", category="mixed", difficulty="medium", num_rounds=5,
        players={"Papa": 100, "Mama": 90, "Kind": 40},
        duration_seconds=100,
        player_details={},
    )
    await a.record_game(
        game_id="g2", category="mixed", difficulty="medium", num_rounds=5,
        players={"Papa": 100, "Mama": 10, "Kind": 50},
        duration_seconds=100,
        player_details={},
    )
    await a.record_game(
        game_id="g3", category="mixed", difficulty="medium", num_rounds=5,
        players={"Mama": 400, "Papa": 100},
        duration_seconds=100,
        player_details={},
    )
    return a


class TestStandingRanking:
    @pytest.mark.asyncio
    async def test_ranked_by_wins_not_by_score(self, tmp_path: Path) -> None:
        a = await _analytics_with_games(tmp_path)

        papa = a.get_player_standing("Papa")
        mama = a.get_player_standing("Mama")
        assert papa is not None and mama is not None
        # Papa has fewer points but more wins -> he is 1st on this surface.
        assert papa["wins"] == 2
        assert mama["total_score"] > papa["total_score"]
        assert papa["rank"] == 1
        assert mama["rank"] == 2
        assert papa["total_players"] == 3
        assert papa["games_played"] == 3

    @pytest.mark.asyncio
    async def test_dashboard_leaderboard_order_unchanged(
        self, tmp_path: Path
    ) -> None:
        # The analytics dashboard keeps its score-first order — the lobby
        # line must not have re-sorted the shared function.
        a = await _analytics_with_games(tmp_path)
        board = a.get_all_time_leaderboard()
        assert [r["name"] for r in board][:2] == ["Mama", "Papa"]

    @pytest.mark.asyncio
    async def test_ties_share_a_rank(self, tmp_path: Path) -> None:
        a = QuizifyAnalytics(_Runtime(tmp_path))
        await a.load()
        # Identical wins AND identical score for two players.
        await a.record_game(
            game_id="t1", category="mixed", difficulty="medium", num_rounds=5,
            players={"A": 100, "B": 50}, duration_seconds=60, player_details={},
        )
        await a.record_game(
            game_id="t2", category="mixed", difficulty="medium", num_rounds=5,
            players={"C": 100, "D": 50}, duration_seconds=60, player_details={},
        )
        a_st = a.get_player_standing("A")
        c_st = a.get_player_standing("C")
        assert a_st is not None and c_st is not None
        assert a_st["rank"] == c_st["rank"] == 1
        # ... and the next distinct pair does not become rank 2.
        b_st = a.get_player_standing("B")
        assert b_st is not None
        assert b_st["rank"] == 3

    @pytest.mark.asyncio
    async def test_unknown_name_and_empty_history_return_none(
        self, tmp_path: Path
    ) -> None:
        empty = QuizifyAnalytics(_Runtime(tmp_path))
        await empty.load()
        assert empty.get_player_standing("Papa") is None

        a = await _analytics_with_games(tmp_path / "sub")
        assert a.get_player_standing("Gast") is None


def _ws() -> MagicMock:
    ws = MagicMock()
    ws.closed = False
    ws.send_json = AsyncMock()
    return ws


def _sent_frames(conn_send: AsyncMock, msg_type: str) -> list[dict]:
    return [
        call.args[1]
        for call in conn_send.await_args_list
        if isinstance(call.args[1], dict) and call.args[1].get("type") == msg_type
    ]


@pytest.fixture
def game(tmp_path: Path) -> QuizifyGameState:
    return QuizifyGameState(runtime=_Runtime(tmp_path), entry_id="test")


@pytest.fixture
def handler(game: QuizifyGameState, tmp_path: Path) -> QuizifyWebSocketHandler:
    runtime = _Runtime(tmp_path)
    h = QuizifyWebSocketHandler(runtime=runtime, game_state_provider=lambda: game)
    h._conn = ConnectionManager(runtime, lambda: game)
    h._conn.broadcast = AsyncMock()
    h._conn.send_error = AsyncMock()
    h._conn.send = AsyncMock()
    return h


class TestJoinFrameCarriesStanding:
    @pytest.mark.asyncio
    async def test_joined_carries_own_standing(
        self,
        handler: QuizifyWebSocketHandler,
        game: QuizifyGameState,
        tmp_path: Path,
    ) -> None:
        game.set_stats_services(await _analytics_with_games(tmp_path), None)

        await handler._handle_join(_ws(), {"name": "Papa"}, game)

        joined = _sent_frames(handler._conn.send, "joined")
        assert len(joined) == 1
        standing = joined[0]["all_time"]
        assert standing is not None
        assert standing["rank"] == 1
        assert standing["wins"] == 2
        assert standing["games_played"] == 3

    @pytest.mark.asyncio
    async def test_first_timer_gets_none(
        self,
        handler: QuizifyWebSocketHandler,
        game: QuizifyGameState,
        tmp_path: Path,
    ) -> None:
        game.set_stats_services(await _analytics_with_games(tmp_path), None)

        await handler._handle_join(_ws(), {"name": "Gast"}, game)

        joined = _sent_frames(handler._conn.send, "joined")
        assert joined[0]["all_time"] is None

    @pytest.mark.asyncio
    async def test_join_works_without_analytics_wired(
        self, handler: QuizifyWebSocketHandler, game: QuizifyGameState
    ) -> None:
        # Dev server / fresh install: no analytics sink at all.
        assert game.stats_service is None

        await handler._handle_join(_ws(), {"name": "Papa"}, game)

        joined = _sent_frames(handler._conn.send, "joined")
        assert joined and joined[0]["all_time"] is None
        handler._conn.send_error.assert_not_called()

    @pytest.mark.asyncio
    async def test_reconnect_keeps_the_line(
        self,
        handler: QuizifyWebSocketHandler,
        game: QuizifyGameState,
        tmp_path: Path,
    ) -> None:
        game.set_stats_services(await _analytics_with_games(tmp_path), None)

        await handler._handle_join(_ws(), {"name": "Papa"}, game)
        token = _sent_frames(handler._conn.send, "joined")[0]["session_token"]

        await handler._handle_reconnect(_ws(), {"session_token": token}, game)

        reconnected = _sent_frames(handler._conn.send, "reconnected")
        assert reconnected, "reconnect did not send a reconnected frame"
        assert reconnected[0]["all_time"] is not None
        assert reconnected[0]["all_time"]["rank"] == 1

    @pytest.mark.asyncio
    async def test_roster_broadcast_stays_clean(
        self,
        handler: QuizifyWebSocketHandler,
        game: QuizifyGameState,
        tmp_path: Path,
    ) -> None:
        # The standing is per-player; it must not leak into the frame every
        # phone in the room receives.
        game.set_stats_services(await _analytics_with_games(tmp_path), None)
        await handler._handle_join(_ws(), {"name": "Papa"}, game)

        for call in handler._conn.broadcast.await_args_list:
            payload = call.args[0]
            assert "all_time" not in payload
            for entry in payload.get("players", []):
                assert "all_time" not in entry


class TestFrontendWiring:
    def test_player_html_has_the_line(self) -> None:
        html = (WWW / "player.html").read_text("utf-8")
        assert 'id="pl-alltime"' in html
        # Starts hidden — a first-timer must never see an empty placeholder.
        assert 'class="pl-allTime hidden"' in html

    def test_bundle_renders_and_core_calls_it(self) -> None:
        bundle = (WWW / "js" / "player.bundle.js").read_text("utf-8")
        # #624 widened the signature with an optional target element so the same
        # line can render on the end screen. The lobby call site is unchanged
        # and still passes one argument — that is what this guard is for.
        assert "function renderAllTime(standing" in bundle
        assert "renderAllTime: renderAllTime" in bundle
        assert "lobby.renderAllTime(msg.all_time)" in bundle
        # Zero-win phrasing exists rather than rendering "0 wins".
        assert "lobby.allTimeFirstWin" in bundle

    def test_i18n_keys_in_every_language(self) -> None:
        for lang in ("de", "en", "es"):
            data = json.loads((WWW / "i18n" / f"{lang}.json").read_text("utf-8"))
            lobby = data["lobby"]
            for key in ("allTime", "allTimeFirstWin"):
                assert key in lobby, f"{lang}.json missing lobby.{key}"
            assert "{rank}" in lobby["allTime"]
            assert "{total}" in lobby["allTime"]
            assert "{wins}" in lobby["allTime"]
            assert "{games}" in lobby["allTime"]
            # The no-win variant must not promise a win count.
            assert "{wins}" not in lobby["allTimeFirstWin"]

    def test_css_ships_the_class(self) -> None:
        css = (WWW / "css" / "styles.css").read_text("utf-8")
        assert ".pl-allTime" in css
