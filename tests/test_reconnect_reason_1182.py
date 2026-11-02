"""Why a reconnect failed, and that a restart ended a game (#1182, L3/L4).

L3: a phone that slept through a reset (display off, the normal case) woke to
a bare ``{"type": "reconnect_failed"}`` and showed "we could not bring you
back into the game" — the guest thinks something is wrong with their phone.
Only a phone that was awake at the reset got ``game_reset`` and the right
sentence. ``reconnect_failed`` now carries ``reason``.

L4: an HA restart mid-game threw the game away and every screen came back to a
fresh lobby without a word. A marker on disk survives the restart and the
lobby snapshot carries ``interrupted_game`` until the next game starts.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT))

from custom_components.quizify.game.state import (  # noqa: E402
    GamePhase,
    QuizifyGameState,
)
from custom_components.quizify.runtime import StandaloneRuntime  # noqa: E402
from custom_components.quizify.server import connection as connection_mod  # noqa: E402
from custom_components.quizify.server.connection import ConnectionManager  # noqa: E402
from custom_components.quizify.server.interrupted_game import (  # noqa: E402
    InterruptedGameMarker,
)
from custom_components.quizify.server.websocket import (  # noqa: E402
    QuizifyWebSocketHandler,
)


class _FakeRuntime:
    def __init__(self, tmp_path: Path) -> None:
        self.data_dir = tmp_path


def _ws() -> MagicMock:
    ws = MagicMock()
    ws.closed = False
    ws.send_json = AsyncMock()
    return ws


@pytest.fixture
def game(tmp_path: Path) -> QuizifyGameState:
    return QuizifyGameState(runtime=_FakeRuntime(tmp_path), entry_id="test")


@pytest.fixture
def handler(game: QuizifyGameState, tmp_path: Path) -> QuizifyWebSocketHandler:
    runtime = _FakeRuntime(tmp_path)
    h = QuizifyWebSocketHandler(runtime=runtime, game_state_provider=lambda: game)
    h._conn = ConnectionManager(runtime, lambda: game)
    h._conn.broadcast = AsyncMock()
    return h


async def _reason(handler, game, token: str) -> str | None:
    ws = _ws()
    await handler._handle_reconnect(ws, {"session_token": token, "name": "Ben"}, game)
    frames = [c.args[0] for c in ws.send_json.await_args_list]
    failed = [f for f in frames if f.get("type") == "reconnect_failed"]
    assert len(failed) == 1, frames
    return failed[0].get("reason")


@pytest.mark.asyncio
async def test_a_token_retired_by_reset_says_reset(handler, game) -> None:
    token = handler._conn.create_session_token("Ben")
    handler._conn.clear_all_player_tokens()
    assert await _reason(handler, game, token) == "reset"


@pytest.mark.asyncio
async def test_a_token_this_process_never_issued_says_restart(handler, game) -> None:
    """Session tokens live in memory, so an unknown one predates the restart."""
    assert await _reason(handler, game, "issued-before-the-restart") == "restart"


@pytest.mark.asyncio
async def test_a_kicked_or_timed_out_player_says_removed(handler, game) -> None:
    token = handler._conn.create_session_token("Ben")
    handler._conn.clear_player_tokens("Ben")
    assert await _reason(handler, game, token) == "removed"


@pytest.mark.asyncio
async def test_a_token_whose_player_is_gone_says_removed(handler, game) -> None:
    token = handler._conn.create_session_token("Ben")  # never added to the game
    assert await _reason(handler, game, token) == "removed"
    # …and asking again still knows why, rather than guessing restart.
    assert await _reason(handler, game, token) == "removed"


@pytest.mark.asyncio
async def test_an_expired_token_says_expired(handler, game, monkeypatch) -> None:
    token = handler._conn.create_session_token("Ben")
    name, _issued = handler._conn._session_tokens[token]
    handler._conn._session_tokens[token] = (
        name,
        time.monotonic() - connection_mod._PLAYER_TOKEN_TTL - 1,
    )
    assert await _reason(handler, game, token) == "expired"


def test_the_retired_map_is_bounded(handler) -> None:
    for i in range(connection_mod._RETIRED_TOKEN_CAP + 50):
        handler._conn._retire(f"t{i}", "reset")
    assert len(handler._conn._retired_tokens) == connection_mod._RETIRED_TOKEN_CAP


def test_every_reason_is_declared() -> None:
    assert connection_mod.RECONNECT_REASONS == {"reset", "restart", "expired", "removed"}


# --- L4: the marker -------------------------------------------------------


@pytest.mark.asyncio
async def test_a_game_running_at_shutdown_leaves_a_notice(tmp_path: Path) -> None:
    runtime = StandaloneRuntime(data_dir=tmp_path)
    before = InterruptedGameMarker(runtime)
    await before.load()
    await before.sync(GamePhase.QUESTION_ACTIVE)
    assert (tmp_path / "game_running.json").is_file()

    # The process dies here. The next one reads what was left behind.
    after = InterruptedGameMarker(runtime)
    await after.load()
    assert after.notice is not None
    assert after.notice["reason"] == "restart"
    # Read once: a second restart in the lobby must not repeat it.
    assert not (tmp_path / "game_running.json").exists()

    # The next game clears it.
    await after.sync(GamePhase.QUESTION_ACTIVE)
    assert after.notice is None


@pytest.mark.asyncio
async def test_a_game_that_ended_leaves_nothing(tmp_path: Path) -> None:
    runtime = StandaloneRuntime(data_dir=tmp_path)
    m = InterruptedGameMarker(runtime)
    await m.load()
    await m.sync(GamePhase.QUESTION_ACTIVE)
    await m.sync(GamePhase.FINALE)
    assert not (tmp_path / "game_running.json").exists()
    again = InterruptedGameMarker(runtime)
    await again.load()
    assert again.notice is None


@pytest.mark.asyncio
async def test_an_unloaded_marker_never_touches_the_disk(tmp_path: Path) -> None:
    m = InterruptedGameMarker(_FakeRuntime(tmp_path))
    await m.sync(GamePhase.QUESTION_ACTIVE)
    assert list(tmp_path.iterdir()) == []


@pytest.mark.asyncio
async def test_the_lobby_snapshot_carries_the_notice(handler, game) -> None:
    assert handler._snapshot(game)["interrupted_game"] is None
    handler.interrupted_game._notice = {"reason": "restart", "at": 1}
    assert handler._snapshot(game)["interrupted_game"] == {"reason": "restart", "at": 1}


@pytest.mark.asyncio
async def test_a_reset_clears_the_notice(handler, game) -> None:
    handler.interrupted_game._notice = {"reason": "restart", "at": 1}
    await handler._handle_reset_game(_ws(), game)
    assert handler._snapshot(game)["interrupted_game"] is None


@pytest.mark.asyncio
async def test_a_reset_then_a_sleeping_phone_says_reset(handler, game) -> None:
    """The whole L3 path: the reset retires the token, the phone wakes later."""
    ok, _err = game.add_player("Ben", "cid")
    assert ok
    token = handler._conn.create_session_token("Ben")
    await handler._handle_reset_game(_ws(), game)
    assert await _reason(handler, game, token) == "reset"


# --- L3 on the phone: the reason picks the sentence --------------------------
#
# Runs the real ``reconnect_failed`` case through the harness of
# test_reset_join_hint_1018.py, with the reason on the frame.


def _phone(reason: str | None) -> dict:
    import json as _json
    import shutil
    import subprocess

    from tests import test_reset_join_hint_1018 as base

    if shutil.which("node") is None:
        pytest.skip("node not installed")
    frame = {"type": "reconnect_failed"}
    if reason is not None:
        frame["reason"] = reason
    template = base._SCRIPT.replace(
        "handleMessage({{ type: scenario }});",
        "handleMessage(" + _json.dumps(frame).replace("{", "{{").replace("}", "}}") + ");",
    )
    assert template != base._SCRIPT, "the 1018 harness changed shape"
    cases = base._slice(
        "            case 'game_reset':", "            case 'kicked':"
    ) + base._slice(
        "            case 'reconnect_failed':", "            case 'host_presence':"
    )
    script = template.format(
        stub=_json.dumps(str(base._STUB)),
        client_core=_json.dumps(str(base._CLIENT_CORE)),
        utils=_json.dumps(str(base._UTILS)),
        forget=base._slice("    function forgetIdentity() {", "\n    }\n") + "\n    }",
        helpers=base._slice(
            "    function lastPlayerName() {",
            "    // Put the join form back in a usable state",
            required=False,
        ),
        cases=cases,
        scenario=_json.dumps("reconnect_failed"),
    )
    out = subprocess.run(["node", "-e", script], capture_output=True, text=True, check=True)
    return _json.loads(out.stdout.strip().splitlines()[-1])


@pytest.mark.parametrize(
    ("reason", "key"),
    [
        ("reset", "join.resetHint"),
        ("restart", "states.restartPhone"),
        ("expired", "join.reconnectFailedHint"),
        ("removed", "join.reconnectFailedHint"),
        (None, "join.reconnectFailedHint"),
    ],
)
def test_the_phone_says_why(reason: str | None, key: str) -> None:
    result = _phone(reason)
    assert result["onJoinView"] is True
    assert result["hintKey"] == key
    assert result["nameValue"] == "Anna", "the name is handed back either way"
