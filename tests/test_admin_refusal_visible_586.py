"""A refused admin command must not be invisible (#586).

The reporter of #586 saw Skip, Pause and Stop do nothing at all: no movement,
no message, nothing in the interface. The server was answering — it sent
``INVALID_ACTION`` with the message ``"Admin only"`` — but the admin page
suppresses exactly that code-and-message pair on purpose, because the
pre-authentication ``admin_connect`` handshake produces it on every load. A
filter written for handshake noise was swallowing every refused command with
it.

So the fix is not "show more errors". It is to stop the two cases from
looking identical on the wire: a refused command now answers
``ADMIN_REQUIRED``, which nothing suppresses, while the handshake keeps its
``INVALID_ACTION`` / "Admin only" pair and stays quiet.

These tests pin both halves. Without the first, a future refactor could route
the refusal back through the suppressed pair and the buttons would go silent
again — with every existing test still green, because they only ever asserted
that *an* error was sent.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT))

from custom_components.quizify.const import (  # noqa: E402
    ERR_ADMIN_REQUIRED,
    ERR_INVALID_ACTION,
)
from custom_components.quizify.game.state import QuizifyGameState  # noqa: E402
from custom_components.quizify.server import websocket as ws_mod  # noqa: E402
from custom_components.quizify.server.connection import ConnectionManager  # noqa: E402
from custom_components.quizify.server.websocket import (  # noqa: E402
    QuizifyWebSocketHandler,
)

ADMIN_JS = _REPO_ROOT / "custom_components" / "quizify" / "www" / "js" / "admin.js"
I18N_DIR = _REPO_ROOT / "custom_components" / "quizify" / "www" / "i18n"

#: The commands the reporter pressed.
DEAD_BUTTONS = (
    ws_mod.MSG_ADMIN_SKIP,
    ws_mod.MSG_PAUSE_GAME,
    ws_mod.MSG_END_GAME,
)


class _FakeRuntime:
    def __init__(self, tmp_path: Path) -> None:
        self.data_dir = tmp_path


def _ws() -> MagicMock:
    ws = MagicMock()
    ws.closed = False
    ws.send_json = AsyncMock()
    ws.close = AsyncMock()
    return ws


@pytest.fixture
def game(tmp_path: Path) -> QuizifyGameState:
    return QuizifyGameState(runtime=_FakeRuntime(tmp_path), entry_id="test")


def _handler(game: QuizifyGameState, tmp_path: Path):
    runtime = _FakeRuntime(tmp_path)
    handler = QuizifyWebSocketHandler(runtime=runtime, game_state_provider=lambda: game)
    handler._conn = ConnectionManager(runtime, lambda: game)
    handler._get_game_state = lambda: game  # type: ignore[assignment]
    sent: list[dict] = []

    async def _send_error(ws, code, message) -> None:
        sent.append({"code": code, "message": message})

    handler._conn.send_error = _send_error  # type: ignore[assignment]
    handler._conn.broadcast = AsyncMock()  # type: ignore[assignment]
    return handler, sent


@pytest.mark.asyncio
@pytest.mark.parametrize("msg_type", DEAD_BUTTONS)
async def test_refused_command_does_not_use_the_suppressed_pair(
    game: QuizifyGameState, tmp_path: Path, msg_type: str
) -> None:
    """The refusal must be distinguishable from handshake noise."""
    handler, sent = _handler(game, tmp_path)

    admin_ws = _ws()
    handler._conn.add_connection(admin_ws, is_admin=True, is_dashboard=False)
    game.add_player("Host", admin_ws)
    game.get_player("Host").is_admin = True

    guest_ws = _ws()
    handler._conn.add_connection(guest_ws, is_admin=False, is_dashboard=False)
    game.add_player("Guest", guest_ws)

    await handler._handle_message(guest_ws, {"type": msg_type}, is_admin=False)

    assert sent, f"{msg_type} was refused without telling the client anything"
    refusal = sent[-1]
    assert refusal["code"] == ERR_ADMIN_REQUIRED
    assert not (
        refusal["code"] == ERR_INVALID_ACTION and refusal["message"] == "Admin only"
    ), "the refusal is back on the pair the admin page suppresses — #586 again"


@pytest.mark.asyncio
async def test_admin_connect_handshake_keeps_the_quiet_pair(
    game: QuizifyGameState, tmp_path: Path
) -> None:
    """The other half: the pre-auth handshake must stay silent.

    If this one flipped to ADMIN_REQUIRED, every admin page would greet its
    host with a red toast on load, which is how a well-meant fix turns into a
    worse bug than the one it cured.
    """
    handler, sent = _handler(game, tmp_path)
    guest_ws = _ws()
    handler._conn.add_connection(guest_ws, is_admin=False, is_dashboard=False)

    await handler._handle_message(
        guest_ws, {"type": ws_mod.MSG_ADMIN_CONNECT}, is_admin=False
    )

    assert sent and sent[-1]["code"] == ERR_INVALID_ACTION
    assert sent[-1]["message"] == "Admin only"


def test_admin_page_suppresses_only_the_handshake_pair() -> None:
    """Asserted on the source: the filter must name both code and message.

    A filter on the code alone would swallow the refusal again the moment its
    message changed.
    """
    source = ADMIN_JS.read_text(encoding="utf-8")
    for match in re.finditer(r"msg\.code === 'INVALID_ACTION'", source):
        window = source[match.start() : match.start() + 120]
        assert "msg.message === 'Admin only'" in window, (
            "admin.js suppresses INVALID_ACTION without checking the message — "
            "a refused command would be swallowed with the handshake again"
        )
    # Comments are stripped first: the branch *explains* ADMIN_REQUIRED in
    # prose, and an assertion that cannot tell prose from code fails on its
    # own documentation.
    branch = source.split("case 'error':")[1][:900]
    code_only = "\n".join(
        line for line in branch.splitlines() if not line.strip().startswith("//")
    )
    assert "ADMIN_REQUIRED" not in code_only, (
        "the refusal code is being matched in the suppression branch"
    )


@pytest.mark.parametrize("lang", ("de", "en", "es"))
def test_every_language_can_explain_the_refusal(lang: str) -> None:
    """An untranslated code would show the raw English wire message."""
    data = json.loads((I18N_DIR / f"{lang}.json").read_text(encoding="utf-8"))
    text = data["errors"].get(ERR_ADMIN_REQUIRED, "")
    assert text.strip(), f"{lang}.json has no wording for {ERR_ADMIN_REQUIRED}"
