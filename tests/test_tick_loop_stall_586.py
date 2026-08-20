"""Regression tests for the frozen countdown reported in #586.

The countdown loop had two break conditions and a gap between them:

- ``all_timers_expired(connected)`` needs at least one live timer, so it
  returns False when the connected players have none.
- the wall-clock fallback only applied when *nobody* was connected (#255).

A round with connected players who hold no timer therefore matched neither
condition: the loop spun forever, the client counted its local clock down to
zero and sat there, and the admin's Skip/Pause/End were the only way out.

The fallback now keys on "no live timers" instead of "no connected players",
which covers the #255 case and this one with a single condition.
"""

from __future__ import annotations

import asyncio
import sys
import time
from pathlib import Path
from unittest.mock import MagicMock

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT))

from custom_components.quizify.game.phase_controller import (  # noqa: E402
    PhaseController,
)
from custom_components.quizify.game.state import QuizifyGameState  # noqa: E402


def _fake_ws() -> MagicMock:
    ws = MagicMock()
    ws.closed = False
    return ws


class _Runtime:
    """Minimal runtime: runs scheduled coroutines on the live loop."""

    def __init__(self, tmp_path: Path) -> None:
        self.data_dir = tmp_path

    async def run_in_executor(self, func, *args):  # noqa: ANN001, ANN002
        return func(*args)

    def create_task(self, coro):  # noqa: ANN001
        return asyncio.ensure_future(coro)


def _break_conditions(gs: QuizifyGameState, connected: list[str]) -> bool:
    """Mirror the tick loop's two break conditions (server/websocket.py).

    Kept as a tiny helper so the test asserts the *decision* the loop makes
    rather than driving the whole websocket handler.
    """
    if connected and gs.all_timers_expired(connected):
        return True
    return not gs.has_live_timers(connected) and gs.round_wall_clock_expired()


class TestHasLiveTimers:
    def test_false_when_connected_players_hold_no_timer(self) -> None:
        pc = PhaseController(players_fn=lambda: [])
        pc.begin_round(round_duration=10.0)
        # begin_round created timers for the (empty) player list, so a
        # connected player who never got one is unknown to the controller.
        assert pc.has_live_timers(["Alice"]) is False
        # And this is precisely why all_timers_expired can't end the round.
        assert pc.all_timers_expired(["Alice"]) is False

    def test_true_once_the_player_has_a_timer(self) -> None:
        pc = PhaseController(players_fn=lambda: ["Alice"])
        pc.begin_round(round_duration=10.0)
        assert pc.has_live_timers(["Alice"]) is True

    def test_empty_list_has_no_live_timers(self) -> None:
        pc = PhaseController(players_fn=lambda: ["Alice"])
        pc.begin_round(round_duration=10.0)
        assert pc.has_live_timers([]) is False


class TestTickLoopBreaksOnStall:
    def test_connected_without_timer_used_to_hang_forever(
        self, tmp_path: Path
    ) -> None:
        """The #586 state: a connected player, no timer, wall-clock elapsed."""
        gs = QuizifyGameState(runtime=_Runtime(tmp_path), entry_id="t")
        gs.add_player("Alice", _fake_ws())
        gs.start_game(language="de", num_rounds=3)
        gs.start_next_question()

        # Drop every per-player timer while the player stays connected — the
        # state neither break condition could resolve before.
        gs._phase_controller.clear_timers()
        connected = ["Alice"]
        assert gs.all_timers_expired(connected) is False
        assert gs.has_live_timers(connected) is False

        # Before the wall-clock runs out the loop must keep going: a round
        # that is merely mid-flight is not allowed to end early.
        assert _break_conditions(gs, connected) is False

        gs._phase_controller.round_start_time = time.monotonic() - 9999.0
        assert gs.round_wall_clock_expired() is True
        assert _break_conditions(gs, connected) is True

    def test_all_disconnected_still_breaks(self, tmp_path: Path) -> None:
        """The original #255 case keeps working through the same condition."""
        gs = QuizifyGameState(runtime=_Runtime(tmp_path), entry_id="t")
        gs.add_player("Alice", _fake_ws())
        gs.start_game(language="de", num_rounds=3)
        gs.start_next_question()

        gs._phase_controller.round_start_time = time.monotonic() - 9999.0
        assert _break_conditions(gs, []) is True

    def test_live_timers_do_not_end_the_round_early(self, tmp_path: Path) -> None:
        """A running timer must outrank an elapsed wall-clock.

        The wall-clock can read expired while a player still has time left —
        a late joiner's timer outlives the shared clock. The fallback must not
        cut that round short, which is why it requires *no* live timers.
        """
        gs = QuizifyGameState(runtime=_Runtime(tmp_path), entry_id="t")
        gs.add_player("Alice", _fake_ws())
        gs.start_game(language="de", num_rounds=3)
        gs.start_next_question()

        gs._phase_controller.round_start_time = time.monotonic() - 9999.0
        assert gs.round_wall_clock_expired() is True
        assert gs.has_live_timers(["Alice"]) is True
        # Alice's own timer has not expired, so the round runs on.
        assert gs.all_timers_expired(["Alice"]) is False
        assert _break_conditions(gs, ["Alice"]) is False
