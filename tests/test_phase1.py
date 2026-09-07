"""Tests for Phase 1 hardening ported from Beatify.

Covers:
- PlayerSession.is_active (ghost-WS detection)
- PlayerRegistry stale-WS rejoin (browser-reload race)
- PlayerRegistry.all_submitted uses is_active
- PlayerRegistry.get_average_score uses rounds_played
- QuizifyGameState.leader property
- State callbacks fire on phase changes
"""

from __future__ import annotations

import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT))

from custom_components.quizify.game.player import PlayerSession  # noqa: E402
from custom_components.quizify.game.player_registry import PlayerRegistry  # noqa: E402
from custom_components.quizify.game.state import QuizifyGameState  # noqa: E402


class _FakeRuntime:
    def __init__(self, tmp_path: Path) -> None:
        self.data_dir = tmp_path


# ---------- is_active ----------


class TestIsActive:
    """Since #882 liveness is the ``connected`` flag and nothing else.

    The transport used to be re-read here (``not ws.closed``), which is what
    put ``aiohttp`` inside the game model. The ghost case that check existed
    for is now handled one layer up, in
    ``QuizifyWebSocketHandler._reap_closed_connections`` — see
    ``TestStaleConnectionRejoin`` below.
    """

    def test_connected_player_is_active(self) -> None:
        p = PlayerSession(name="A")
        assert p.is_active is True

    def test_disconnected_is_not_active(self) -> None:
        p = PlayerSession(name="A", connected=False)
        assert p.is_active is False

    def test_a_player_needs_no_transport_to_exist(self) -> None:
        """The whole point of #882: pure game state, no socket in sight."""
        p = PlayerSession(name="A")
        assert p.connection_id is None
        assert p.is_active is True


# ---------- Stale-connection rejoin ----------


class TestStaleConnectionRejoin:
    def test_rejoin_when_the_old_connection_is_dead_succeeds(self) -> None:
        reg = PlayerRegistry()
        ok, err = reg.add_player("Alice", "conn-1", "LOBBY", reg.get_average_score)
        assert ok and err is None

        # The browser-reload race: the server layer has spotted that
        # ``conn-1`` is closed and reports it as reclaimable, but
        # ``_handle_disconnect`` has not run, so the slot still looks taken.
        reg.players["Alice"].connected = False
        ok2, err2 = reg.add_player(
            "Alice", "conn-2", "LOBBY", reg.get_average_score, reclaim_by_name=True
        )
        assert ok2 is True
        assert err2 is None
        assert reg.players["Alice"].connection_id == "conn-2"
        assert reg.get_player_by_connection("conn-2") is reg.players["Alice"]
        # The old handle no longer resolves to anyone.
        assert reg.get_player_by_connection("conn-1") is None

    def test_a_reclaim_is_allowed_mid_game_a_plain_rejoin_is_not(self) -> None:
        """The asymmetry #448/#646 depends on, preserved verbatim.

        A slot whose transport just died may be reclaimed by name in ANY
        phase — it is the user's own ghost. A slot that disconnected cleanly
        may only be reclaimed by name in the LOBBY; mid-game it needs the
        session token, or a stranger could inherit a score by typing a name.
        """
        reg = PlayerRegistry()
        reg.add_player("Alice", "conn-1", "LOBBY", reg.get_average_score)
        reg.players["Alice"].connected = False

        ok, err = reg.add_player(
            "Alice", "conn-2", "QUESTION_ACTIVE", reg.get_average_score
        )
        assert ok is False
        assert err is not None  # ERR_NAME_TAKEN

        ok2, _ = reg.add_player(
            "Alice",
            "conn-3",
            "QUESTION_ACTIVE",
            reg.get_average_score,
            reclaim_by_name=True,
        )
        assert ok2 is True

    def test_rejoin_while_the_old_connection_lives_rejects(self) -> None:
        reg = PlayerRegistry()
        reg.add_player("Bob", "conn-1", "LOBBY", reg.get_average_score)

        # Still connected → genuine dual-tab attempt; name should be taken.
        ok, err = reg.add_player("Bob", "conn-2", "LOBBY", reg.get_average_score)
        assert ok is False
        assert err is not None  # ERR_NAME_TAKEN


# ---------- all_submitted uses is_active ----------


class TestAllSubmitted:
    def test_ghost_player_does_not_block_early_reveal(self) -> None:
        reg = PlayerRegistry()
        reg.add_player("Alice", "conn-a", "LOBBY", reg.get_average_score)
        reg.add_player("Bob", "conn-b", "LOBBY", reg.get_average_score)

        # Alice answers.
        reg.players["Alice"].submitted = True
        # Bob has not answered, and the server layer has reaped his dead
        # socket (see ``_reap_closed_connections``).
        reg.players["Bob"].connected = False

        assert reg.all_submitted() is True

    def test_late_joiner_does_not_block(self) -> None:
        reg = PlayerRegistry()
        reg.add_player("Alice", None, "LOBBY", reg.get_average_score)
        reg.players["Alice"].submitted = True

        # Late joiner — mid-round add.
        reg.add_player("Late", None, "QUESTION_ACTIVE", reg.get_average_score)
        assert reg.players["Late"].joined_late is True

        assert reg.all_submitted() is True


# ---------- get_average_score uses rounds_played ----------


class TestAverageScore:
    def test_excludes_players_without_completed_rounds(self) -> None:
        reg = PlayerRegistry()
        reg.add_player("A", None, "LOBBY", reg.get_average_score)
        reg.add_player("B", None, "LOBBY", reg.get_average_score)

        reg.players["A"].score = 100
        reg.players["A"].rounds_played = 2
        # B is a late joiner who hasn't played a round; should not count.
        reg.players["B"].score = 0
        reg.players["B"].rounds_played = 0

        # Old impl would average (100 + 0) / 2 = 50.
        # New impl averages over scored players only: 100 / 1 = 100.
        assert reg.get_average_score() == 100

    def test_returns_zero_when_no_one_has_played(self) -> None:
        reg = PlayerRegistry()
        reg.add_player("A", None, "LOBBY", reg.get_average_score)
        assert reg.get_average_score() == 0


# ---------- leader property ----------


class TestLeader:
    def test_leader_none_when_no_players(self, tmp_path: Path) -> None:
        gs = QuizifyGameState(runtime=_FakeRuntime(tmp_path), entry_id="t")
        assert gs.leader is None

    def test_leader_is_top_scorer(self, tmp_path: Path) -> None:
        gs = QuizifyGameState(runtime=_FakeRuntime(tmp_path), entry_id="t")
        gs.add_player("Alice")
        gs.add_player("Bob")
        gs.players["Alice"].score = 50
        gs.players["Bob"].score = 80
        assert gs.leader is not None
        assert gs.leader.name == "Bob"


# ---------- State callbacks (HA sensor push) ----------


class TestStateCallbacks:
    def test_callback_fires_on_add_player(self, tmp_path: Path) -> None:
        gs = QuizifyGameState(runtime=_FakeRuntime(tmp_path), entry_id="t")
        calls = []
        gs.register_state_callback(lambda: calls.append("tick"))
        gs.add_player("Alice")
        assert calls == ["tick"]

    def test_callback_fires_on_remove_player(self, tmp_path: Path) -> None:
        gs = QuizifyGameState(runtime=_FakeRuntime(tmp_path), entry_id="t")
        gs.add_player("Alice")
        calls = []
        gs.register_state_callback(lambda: calls.append("tick"))
        gs.remove_player("Alice")
        assert calls == ["tick"]

    def test_unregister_stops_callback(self, tmp_path: Path) -> None:
        gs = QuizifyGameState(runtime=_FakeRuntime(tmp_path), entry_id="t")
        calls: list[str] = []

        def cb() -> None:
            calls.append("x")

        gs.register_state_callback(cb)
        gs.unregister_state_callback(cb)
        gs.add_player("Alice")
        assert calls == []

    def test_failing_callback_does_not_break_others(self, tmp_path: Path) -> None:
        gs = QuizifyGameState(runtime=_FakeRuntime(tmp_path), entry_id="t")
        good_calls: list[str] = []

        def bad() -> None:
            raise RuntimeError("boom")

        gs.register_state_callback(bad)
        gs.register_state_callback(lambda: good_calls.append("ok"))
        # Should not raise, and the good callback should still fire.
        gs.add_player("Alice")
        assert good_calls == ["ok"]
