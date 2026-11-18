"""Auto-advance after the answer reveal (#1249).

The host can let the game move on by itself: Off (default, today's
behaviour), 30, 60 or 90 seconds after each reveal. The server owns the
clock — it arms the pause when the reveal goes out, tells every client the
seconds that remain, and starts the next question when they run out. The
host's early tap cancels the pending advance; ending, resetting or leaving
the reveal any other way must never be followed by a second advance.
"""

from __future__ import annotations

import asyncio
import json
import re
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT))

from custom_components.quizify.const import coerce_auto_advance  # noqa: E402
from custom_components.quizify.game.state import (  # noqa: E402
    GamePhase,
    QuizifyGameState,
)
from custom_components.quizify.server import round_flow  # noqa: E402
from custom_components.quizify.server.connection import (  # noqa: E402
    ConnectionManager,
)
from custom_components.quizify.server.preset_store import _validate  # noqa: E402
from custom_components.quizify.server.serializers import (  # noqa: E402
    serialize_state_snapshot,
)
from custom_components.quizify.server.start_settings import (  # noqa: E402
    _preset_to_start_settings,
)
from custom_components.quizify.server.websocket import (  # noqa: E402
    QuizifyWebSocketHandler,
)

_WWW = _REPO_ROOT / "custom_components" / "quizify" / "www"


class _Runtime:
    def __init__(self, tmp_path: Path) -> None:
        self.data_dir = tmp_path

    async def run_in_executor(self, func, *args):  # noqa: ANN001, ANN002
        return func(*args)

    def create_task(self, coro):  # noqa: ANN001
        return asyncio.ensure_future(coro)


def _ws() -> MagicMock:
    ws = MagicMock()
    ws.closed = False
    ws.send_json = AsyncMock()
    ws.close = AsyncMock()
    return ws


def _game(tmp_path: Path, *, auto_advance: int, rounds: int = 5) -> QuizifyGameState:
    gs = QuizifyGameState(runtime=_Runtime(tmp_path), entry_id="test")
    gs.add_player("A")
    gs.start_game(
        num_rounds=rounds,
        lightning_enabled=False,
        hot_seat_enabled=False,
        wager_enabled=False,
        auto_advance=auto_advance,
    )
    gs.start_next_question()
    assert gs.phase == GamePhase.QUESTION_ACTIVE
    return gs


def _handler(gs: QuizifyGameState, tmp_path: Path) -> QuizifyWebSocketHandler:
    runtime = _Runtime(tmp_path)
    h = QuizifyWebSocketHandler(runtime=runtime, game_state_provider=lambda: gs)
    h._conn = ConnectionManager(runtime, lambda: gs)
    h._conn.broadcast = AsyncMock()
    h._conn.broadcast_to_admins_and_dashboards = AsyncMock()
    h._conn.send = AsyncMock()
    h._conn.send_error = AsyncMock()
    h.START_REDIRECT_GRACE = 0.0
    return h


@pytest.fixture
def gate(monkeypatch: pytest.MonkeyPatch) -> asyncio.Event:
    """Hold the auto-advance pause until the test opens the gate.

    Only the pause's own sleep (the armed seconds, 30 and up) waits on the
    gate; every other sleep in the code under test runs as a plain yield so
    nothing else is slowed down or blocked.
    """
    real_sleep = asyncio.sleep
    opened = asyncio.Event()

    async def _sleep(delay: float, *args, **kwargs):  # noqa: ANN002, ANN003
        if delay >= 30:
            await opened.wait()
        await real_sleep(0)

    monkeypatch.setattr(round_flow.asyncio, "sleep", _sleep)
    return opened


async def _settle() -> None:
    for _ in range(20):
        await asyncio.sleep(0)


async def _reveal(handler: QuizifyWebSocketHandler, gs: QuizifyGameState) -> None:
    """Evaluate the round the way the timer does and run the reveal fan-out."""
    gs.evaluate_round()
    assert gs.phase == GamePhase.ANSWER_REVEAL
    await handler._dispatch_round_evaluated()


def _summary_frames(handler: QuizifyWebSocketHandler) -> list[dict]:
    return [
        c.args[0]
        for c in handler._conn.broadcast.await_args_list
        if c.args and isinstance(c.args[0], dict)
        and c.args[0].get("type") == "round_summary"
    ]


# ---------------------------------------------------------------------------
# The setting
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (None, 0), (0, 0), (False, 0), (True, 0), ("off", 0), (45, 0),
        (30, 30), (60, 60), (90, 90), ("60", 60),
    ],
)
def test_coerce_auto_advance(raw, expected) -> None:  # noqa: ANN001
    assert coerce_auto_advance(raw) == expected


def test_start_game_stores_the_setting_and_the_rematch_keeps_it(tmp_path) -> None:  # noqa: ANN001
    gs = _game(tmp_path, auto_advance=60)
    assert gs.auto_advance_seconds == 60
    assert gs.last_settings["auto_advance"] == 60


def test_default_is_off(tmp_path) -> None:  # noqa: ANN001
    gs = QuizifyGameState(runtime=_Runtime(tmp_path), entry_id="test")
    gs.add_player("A")
    gs.start_game(num_rounds=3, lightning_enabled=False)
    assert gs.auto_advance_seconds == 0


def test_preset_and_service_layers_carry_the_setting() -> None:
    assert _preset_to_start_settings({"auto_advance": 90})["auto_advance"] == 90
    # Off is a real value and must not be dropped or read as a bool.
    assert _preset_to_start_settings({"auto_advance": 0})["auto_advance"] == 0
    assert _validate({"name": "Kids", "auto_advance": 60})["auto_advance"] == 60
    assert _validate({"name": "Odd", "auto_advance": 45})["auto_advance"] == 0
    assert "auto_advance" not in _validate({"name": "Old"})


async def test_start_game_message_reads_auto_advance(tmp_path) -> None:  # noqa: ANN001
    gs = QuizifyGameState(runtime=_Runtime(tmp_path), entry_id="test")
    gs.add_player("A")
    handler = _handler(gs, tmp_path)
    await handler._handle_start_game(
        _ws(), {"num_rounds": 3, "lightning_enabled": False, "auto_advance": 30}, gs
    )
    assert gs.auto_advance_seconds == 30
    handler._cancel_round_tasks()


# ---------------------------------------------------------------------------
# The server-side clock
# ---------------------------------------------------------------------------


async def test_reveal_broadcasts_the_countdown_and_advances(tmp_path, gate) -> None:  # noqa: ANN001
    gs = _game(tmp_path, auto_advance=30)
    handler = _handler(gs, tmp_path)
    await _reveal(handler, gs)

    frames = _summary_frames(handler)
    assert frames, "no round_summary broadcast"
    aa = frames[-1]["auto_advance"]
    assert aa["seconds"] == 30
    assert 29 <= aa["remaining"] <= 30
    assert aa["final"] is False
    # The cached summary (#414) must not carry a live countdown.
    assert "auto_advance" not in gs.get_cached_round_summary_msg((gs.game_id, 1))

    # A screen that reconnects mid-pause gets the same countdown.
    assert serialize_state_snapshot(gs)["auto_advance"]["seconds"] == 30

    gate.set()
    await _settle()
    assert gs.round == 2
    assert gs.phase == GamePhase.QUESTION_ACTIVE
    assert gs.auto_advance_status() is None
    handler._cancel_round_tasks()


async def test_off_never_advances(tmp_path, gate) -> None:  # noqa: ANN001
    gs = _game(tmp_path, auto_advance=0)
    handler = _handler(gs, tmp_path)
    await _reveal(handler, gs)

    assert handler._auto_advance_task is None
    assert "auto_advance" not in _summary_frames(handler)[-1]
    assert serialize_state_snapshot(gs)["auto_advance"] is None
    gate.set()
    await _settle()
    assert gs.round == 1
    assert gs.phase == GamePhase.ANSWER_REVEAL


async def test_host_early_tap_cancels_the_pending_advance(tmp_path, gate) -> None:  # noqa: ANN001
    gs = _game(tmp_path, auto_advance=60)
    handler = _handler(gs, tmp_path)
    await _reveal(handler, gs)
    pending = handler._auto_advance_task
    assert pending is not None

    await handler._handle_next_question(_ws(), gs)
    assert gs.round == 2
    await _settle()
    assert pending.cancelled()

    gate.set()
    await _settle()
    assert gs.round == 2, "the pause advanced a second time after the host's tap"
    assert gs.phase == GamePhase.QUESTION_ACTIVE
    handler._cancel_round_tasks()


async def test_service_next_round_also_cancels_it(tmp_path, gate) -> None:  # noqa: ANN001
    gs = _game(tmp_path, auto_advance=30)
    handler = _handler(gs, tmp_path)
    await _reveal(handler, gs)
    await handler.admin_action_next_round(gs)
    gate.set()
    await _settle()
    assert gs.round == 2
    handler._cancel_round_tasks()


async def test_end_game_during_the_pause_does_not_advance(tmp_path, gate) -> None:  # noqa: ANN001
    gs = _game(tmp_path, auto_advance=30)
    handler = _handler(gs, tmp_path)
    await _reveal(handler, gs)
    await handler.admin_action_end_game(gs)
    assert handler._auto_advance_task is None
    gate.set()
    await _settle()
    assert gs.phase == GamePhase.FINALE


async def test_a_stale_pause_never_advances_a_later_reveal(tmp_path, gate) -> None:  # noqa: ANN001
    """Even if the task survived (it should not), the round check stops it."""
    gs = _game(tmp_path, auto_advance=30)
    handler = _handler(gs, tmp_path)
    await _reveal(handler, gs)
    stale = handler._auto_advance_task
    handler._auto_advance_task = None  # dodge the cancel, keep it alive
    await handler._handle_next_question(_ws(), gs)
    assert gs.round == 2
    gs.evaluate_round()  # round 2's reveal, no pause armed for it here
    gate.set()
    await _settle()
    assert stale.done() and not stale.cancelled()
    assert gs.round == 2
    assert gs.phase == GamePhase.ANSWER_REVEAL


async def test_last_reveal_counts_down_into_the_final_results(tmp_path, gate) -> None:  # noqa: ANN001
    gs = _game(tmp_path, auto_advance=30, rounds=1)
    handler = _handler(gs, tmp_path)
    await _reveal(handler, gs)
    assert _summary_frames(handler)[-1]["auto_advance"]["final"] is True
    gate.set()
    await _settle()
    assert gs.phase == GamePhase.FINALE


async def test_empty_room_is_not_played_to_the_end(tmp_path, gate) -> None:  # noqa: ANN001
    gs = _game(tmp_path, auto_advance=30)
    handler = _handler(gs, tmp_path)
    await _reveal(handler, gs)
    gs.get_player("A").connected = False
    gate.set()
    await _settle()
    assert gs.round == 1
    assert gs.phase == GamePhase.ANSWER_REVEAL
    assert gs.auto_advance_status() is None


async def test_a_guest_cannot_advance_and_the_pause_keeps_running(  # noqa: ANN001
    tmp_path, gate
) -> None:
    """The countdown is no back door: a non-host next_question is refused."""
    gs = _game(tmp_path, auto_advance=30)
    handler = _handler(gs, tmp_path)
    await _reveal(handler, gs)
    await handler._handle_message(_ws(), {"type": "next_question"}, False)
    handler._conn.send_error.assert_awaited()
    assert gs.round == 1
    assert handler._auto_advance_task is not None
    assert not handler._auto_advance_task.done()
    gate.set()
    await _settle()
    assert gs.round == 2
    handler._cancel_round_tasks()


# ---------------------------------------------------------------------------
# The three surfaces
# ---------------------------------------------------------------------------


def _read(rel: str) -> str:
    return (_WWW / rel).read_text(encoding="utf-8")


def test_setup_step_ten_sits_before_the_room() -> None:
    html = _read("admin.html")
    i_wager = html.index('data-i18n="setup.eigene.q9"')
    i_adv = html.index('data-i18n="setup.eigene.qAdvance"')
    i_room = html.index('data-i18n="setup.room.title"')
    assert i_wager < i_adv < i_room
    num = r'class="num">{}</span><span data-i18n="{}"'
    assert re.search(num.format(10, "setup.eigene.qAdvance"), html)
    assert re.search(num.format(11, "setup.room.title"), html)
    # Off by default: the checkbox is not pre-checked, the chips are hidden.
    assert re.search(r'<input type="checkbox" id="auto-advance-toggle">', html)
    chips = re.search(r'<div[^>]*id="auto-advance-chips"[^>]*>', html).group(0)
    assert "hidden" in chips
    assert re.search(r'class="chip active" data-value="60"', html)


def test_start_payload_and_presets_carry_auto_advance() -> None:
    js = _read("js/admin.js")
    assert "auto_advance: autoAdvance," in js  # start_game payload
    assert "auto_advance: selectedAutoAdvance," in js  # saved preset + reload
    assert js.count("autoAdvance: 0,") == 4  # every built-in preset is Off


def test_tv_and_phone_have_the_ring() -> None:
    dash = _read("dashboard.html")
    assert 'id="auto-advance-ring"' in dash
    assert 'data-i18n="dashboard.nextQuestion"' in dash
    player = _read("player.html")
    assert re.search(r'id="header-auto-advance"[^>]*role="timer"', player)
    assert 'id="next-round-ring"' in player
    assert "createCountdownRing" in _read("js/render-shared.js")


@pytest.mark.parametrize("lang", ["en", "de", "es"])
def test_i18n_keys(lang: str) -> None:
    d = json.loads(_read(f"i18n/{lang}.json"))
    assert d["setup"]["eigene"]["qAdvance"]
    assert d["setup"]["eigene"]["advanceHint"]
    assert d["dashboard"]["nextQuestion"]
    assert "{seconds}" in d["reveal"]["autoAdvanceAria"]
