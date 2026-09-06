"""Performance / wasted-work tests for issue #304.

Pins the behaviour of three local perf fixes so a future edit can't silently
regress them:

  1. ``QuizifyGameState._fire_broadcast`` must NOT build a full
     ``serialize_state_snapshot()`` for named events (``round_evaluated`` /
     ``game_ended``) — those route to handlers that build their own messages,
     so the snapshot was always discarded. The broadcast payload now carries
     only ``{"event": ...}``.
  2. Visual reactions are coalesced into ~150ms windows and de-duplicated per
     ``(player, emoji)``, so a player mashing one emoji collapses to a single
     broadcast instead of one outbound fan-out per inbound message.
  3. The reaction-flush task is cancelled by ``cleanup_game_tasks``.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT))

from custom_components.quizify.game.state import (  # noqa: E402
    GamePhase,
    QuizifyGameState,
)
from custom_components.quizify.server import serializers  # noqa: E402
from custom_components.quizify.server.websocket import (  # noqa: E402
    QuizifyWebSocketHandler,
)



class _Runtime:
    """Minimal runtime: runs scheduled coroutines on the live loop."""

    def __init__(self, tmp_path: Path) -> None:
        self.data_dir = tmp_path

    async def run_in_executor(self, func, *args):  # noqa: ANN001, ANN002
        return func(*args)

    def create_task(self, coro):  # noqa: ANN001
        return asyncio.ensure_future(coro)


def _fake_ws() -> MagicMock:
    ws = MagicMock()
    ws.closed = False
    return ws


# ---------- #304 item 2: discarded snapshot ----------


class TestFireBroadcastSkipsSnapshot:
    @pytest.mark.asyncio
    async def test_named_event_payload_carries_only_event(
        self, tmp_path: Path
    ) -> None:
        """A named state event must broadcast just ``{"event": ...}`` — no
        full snapshot built only to be discarded by the event handler."""
        gs = QuizifyGameState(runtime=_Runtime(tmp_path), entry_id="t")

        captured: list[dict] = []

        async def _broadcast(payload: dict) -> None:
            captured.append(payload)

        gs.set_broadcast_callback(_broadcast)
        gs.add_player("Alice", _fake_ws())
        gs.add_player("Bob", _fake_ws())
        gs.start_game(language="de", num_rounds=2)
        gs.start_next_question()

        gs.end_game()
        await asyncio.sleep(0)
        await asyncio.sleep(0)

        assert captured, "expected a game_ended broadcast"
        payload = captured[-1]
        assert payload["event"] == "game_ended"
        # The whole point: only the event marker, none of the heavy snapshot
        # keys (podium, players, leaderboard, type, ...).
        assert set(payload.keys()) == {"event"}

    @pytest.mark.asyncio
    async def test_state_snapshot_not_built_by_fire_broadcast(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """``_fire_broadcast`` must not build a state snapshot for a named
        event (it's the expensive call we eliminated).

        The builder moved out of the domain object in #747, so the counter now
        wraps the module function rather than a method — same claim, one layer
        further out: nothing on the ``_fire_broadcast`` path may reach it.
        """
        gs = QuizifyGameState(runtime=_Runtime(tmp_path), entry_id="t")

        async def _noop(payload: dict) -> None:
            return None

        gs.set_broadcast_callback(_noop)

        calls = {"n": 0}
        original = serializers.serialize_state_snapshot

        def _counting(game_state):  # noqa: ANN001, ANN202
            calls["n"] += 1
            return original(game_state)

        monkeypatch.setattr(serializers, "serialize_state_snapshot", _counting)
        gs._fire_broadcast("round_evaluated")
        await asyncio.sleep(0)
        assert calls["n"] == 0


# ---------- #304 item 3: reaction coalescing ----------


def _handler(tmp_path: Path, game: QuizifyGameState) -> QuizifyWebSocketHandler:
    h = QuizifyWebSocketHandler(
        runtime=_Runtime(tmp_path), game_state_provider=lambda: game
    )
    return h


class TestReactionCoalescing:
    @pytest.mark.asyncio
    async def test_repeated_same_reaction_collapses_to_one_broadcast(
        self, tmp_path: Path
    ) -> None:
        """20 identical reactions from one player inside a window produce a
        single broadcast, not 20."""
        gs = QuizifyGameState(runtime=_Runtime(tmp_path), entry_id="t")
        ws = _fake_ws()
        gs.add_player("Alice", ws)
        gs.add_player("Bob", _fake_ws())
        gs.start_game(language="de", num_rounds=2)
        gs.start_next_question()

        h = _handler(tmp_path, gs)
        broadcasts: list[dict] = []

        async def _capture(msg: dict) -> None:
            broadcasts.append(msg)

        h._conn.broadcast = _capture  # type: ignore[assignment]

        for _ in range(20):
            await h._handle_reaction(ws, {"emoji": "🎉"}, gs)

        # Buffered, not yet flushed.
        assert broadcasts == []
        # Wait past the flush window.
        await asyncio.sleep(h._REACTION_FLUSH_WINDOW + 0.05)

        reactions = [m for m in broadcasts if m.get("type") == "reaction"]
        assert len(reactions) == 1
        assert reactions[0] == {
            "type": "reaction",
            "emoji": "🎉",
            "player_name": "Alice",
        }

    @pytest.mark.asyncio
    async def test_distinct_reactions_all_flushed(self, tmp_path: Path) -> None:
        """Distinct (player, emoji) pairs each get their own broadcast."""
        gs = QuizifyGameState(runtime=_Runtime(tmp_path), entry_id="t")
        a, b = _fake_ws(), _fake_ws()
        gs.add_player("Alice", a)
        gs.add_player("Bob", b)
        gs.start_game(language="de", num_rounds=2)
        gs.start_next_question()

        h = _handler(tmp_path, gs)
        broadcasts: list[dict] = []
        h._conn.broadcast = lambda m: broadcasts.append(m) or asyncio.sleep(0)  # type: ignore[assignment,return-value]

        await h._handle_reaction(a, {"emoji": "🎉"}, gs)
        await h._handle_reaction(b, {"emoji": "👏"}, gs)
        await h._handle_reaction(a, {"emoji": "🎉"}, gs)  # dup of first

        await asyncio.sleep(h._REACTION_FLUSH_WINDOW + 0.05)

        reactions = [m for m in broadcasts if m.get("type") == "reaction"]
        assert len(reactions) == 2
        keys = {(m["player_name"], m["emoji"]) for m in reactions}
        assert keys == {("Alice", "🎉"), ("Bob", "👏")}

    @pytest.mark.asyncio
    async def test_cleanup_cancels_flush_task(self, tmp_path: Path) -> None:
        """``cleanup_game_tasks`` cancels a pending flush and clears the
        buffer."""
        gs = QuizifyGameState(runtime=_Runtime(tmp_path), entry_id="t")
        ws = _fake_ws()
        gs.add_player("Alice", ws)
        gs.start_game(language="de", num_rounds=2)
        gs.start_next_question()

        h = _handler(tmp_path, gs)
        h._conn.broadcast = lambda m: asyncio.sleep(0)  # type: ignore[assignment]

        await h._handle_reaction(ws, {"emoji": "🎉"}, gs)
        assert h._reaction_flush_task is not None
        assert h._reaction_buffer

        await h.cleanup_game_tasks()
        assert h._reaction_flush_task is None
        assert not h._reaction_buffer

    @pytest.mark.asyncio
    async def test_reaction_bonus_still_awarded_during_reveal(
        self, tmp_path: Path
    ) -> None:
        """Coalescing the *visual* reaction must not break the reveal bonus
        path — a reaction during ANSWER_REVEAL still tips +1 to a correct
        answerer."""
        gs = QuizifyGameState(runtime=_Runtime(tmp_path), entry_id="t")
        a, b = _fake_ws(), _fake_ws()
        gs.add_player("Alice", a)
        gs.add_player("Bob", b)
        gs.start_game(language="de", num_rounds=2)
        gs.start_next_question()

        # Bob answers correctly; force into reveal.
        q = gs.get_current_question()
        correct_idx = next(i for i, ans in enumerate(q.answers) if ans.correct)
        gs.submit_answer("Bob", correct_idx)
        gs.evaluate_round()
        assert gs.phase == GamePhase.ANSWER_REVEAL

        h = _handler(tmp_path, gs)
        sent: list[dict] = []
        h._conn.broadcast = lambda m: sent.append(m) or asyncio.sleep(0)  # type: ignore[assignment,return-value]

        bob_before = gs.get_player_by_ws(b).score
        await h._handle_reaction(a, {"emoji": "🎉"}, gs)

        # The point award still fires synchronously inside _handle_reaction.
        assert gs.get_player_by_ws(b).score == bob_before + 1
        # The reaction_bonus BROADCAST is now coalesced into the ~150ms flush
        # window (#416) rather than emitted inline — drain the flush task.
        if h._reaction_flush_task is not None:
            await h._reaction_flush_task
        assert any(m.get("type") == "reaction_bonus" for m in sent)
