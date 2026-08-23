"""One canonical player name per join (issue #603).

``PlayerRegistry.add_player`` sanitizes the name before storing it, but
``_handle_join`` used to keep working with the **raw** name afterwards: the
session token was issued under the raw name and ``get_player(name)`` looked the
raw name up, so for any name sanitization changes, both missed.

This was never an exotic input. ``sanitize_player_name`` strips Unicode category
``Cf``, which includes the zero-width joiner holding multi-codepoint emoji
together — family emoji, flag emoji, most of what a phone keyboard offers under
"people" — and it collapses repeated whitespace, so a stray double space does it
too. The names in ``CHANGED_BY_SANITIZE`` below were produced by running the
real function, not guessed.

Three consequences, and the third is the expensive one:

1. the ``joined`` frame goes out without colour and without the admin flag,
2. the host silently loses the crown, because the admin-as-player block is
   skipped when the lookup returns ``None``,
3. after a wifi blip the reconnect lookup misses too, the token is revoked, and
   because mid-game name-rejoin is deliberately closed, the score is unreachable
   until grace removal. To the player it looks like the game forgot them.

The fix canonicalizes once, in ``_handle_join``, before anything is keyed on the
name. All tests here were run against the unfixed code first.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT))

from custom_components.quizify.game.player_registry import (  # noqa: E402
    sanitize_player_name,
)
from custom_components.quizify.game.state import QuizifyGameState  # noqa: E402
from custom_components.quizify.server.connection import (  # noqa: E402
    ConnectionManager,
)
from custom_components.quizify.server.websocket import (  # noqa: E402
    QuizifyWebSocketHandler,
)

# Real-world names the sanitizer rewrites. Kept as data so every test below
# runs against all of them: a fix that handles the emoji but not the double
# space is not a fix.
CHANGED_BY_SANITIZE = [
    "Anna 👩‍👦",   # family emoji — zero-width joiner
    "Lea 🏳️‍🌈",   # flag emoji — same
    "x²",                # NFKC folds the superscript
    "Ben  Klaus",        # collapsed double space
]


class _FakeRuntime:
    def __init__(self, tmp_path: Path) -> None:
        self.data_dir = tmp_path

    def create_task(self, coro):
        return asyncio.ensure_future(coro)

    async def run_in_executor(self, func, *args):
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, func, *args)


def _ws(closed: bool = False) -> MagicMock:
    ws = MagicMock()
    ws.closed = closed
    ws.send_json = AsyncMock()
    return ws


@pytest.fixture
def game(tmp_path: Path) -> QuizifyGameState:
    return QuizifyGameState(runtime=_FakeRuntime(tmp_path), entry_id="test")


@pytest.fixture
def handler(game: QuizifyGameState) -> QuizifyWebSocketHandler:
    runtime = _FakeRuntime(game._runtime.data_dir)  # type: ignore[attr-defined]
    h = QuizifyWebSocketHandler(runtime=runtime, game_state_provider=lambda: game)
    h._conn = ConnectionManager(runtime, lambda: game)
    h._conn.broadcast = AsyncMock()
    h._conn.send = AsyncMock()
    h._conn.send_error = AsyncMock()
    h._REACTION_FLUSH_WINDOW = 0.02
    return h


def test_the_sample_names_really_are_rewritten() -> None:
    """Guards the premise of every test below.

    If a future sanitizer stopped touching these names, the tests would still
    pass while testing nothing at all. This one fails instead.
    """
    for raw in CHANGED_BY_SANITIZE:
        assert sanitize_player_name(raw) != raw, raw


@pytest.mark.parametrize("raw", CHANGED_BY_SANITIZE)
@pytest.mark.asyncio
async def test_the_player_is_findable_under_the_name_that_was_stored(
    handler: QuizifyWebSocketHandler, game: QuizifyGameState, raw: str
) -> None:
    """The lookup the join itself performs has to hit."""
    await handler._handle_join(_ws(), {"name": raw}, game)

    canonical = sanitize_player_name(raw)
    assert game.get_player(canonical) is not None
    assert [p.name for p in game.get_players()] == [canonical]


@pytest.mark.parametrize("raw", CHANGED_BY_SANITIZE)
@pytest.mark.asyncio
async def test_the_session_token_resolves_to_the_stored_player(
    handler: QuizifyWebSocketHandler, game: QuizifyGameState, raw: str
) -> None:
    """The token is what a reconnect after a wifi blip trades in.

    Issued under the raw name, it resolved to a player that does not exist, the
    token got revoked and the score became unreachable mid-game.
    """
    await handler._handle_join(_ws(), {"name": raw}, game)

    canonical = sanitize_player_name(raw)
    token = next(
        (t for t, (n, _) in handler._conn._session_tokens.items() if n == canonical),
        None,
    )
    assert token is not None, "no token was issued under the stored name"
    assert game.get_player(handler._conn.get_player_for_token(token)) is not None


@pytest.mark.parametrize("raw", CHANGED_BY_SANITIZE)
@pytest.mark.asyncio
async def test_the_host_keeps_the_crown(
    handler: QuizifyWebSocketHandler, game: QuizifyGameState, raw: str
) -> None:
    """The admin-as-player grant runs off the same lookup.

    A host joining with a family emoji in their name silently ended up without
    the crown and could not advance rounds from the player tab.
    """
    await handler._handle_join(_ws(), {"name": raw, "is_admin": True}, game)

    player = game.get_player(sanitize_player_name(raw))
    assert player is not None
    assert player.is_admin is True


@pytest.mark.asyncio
async def test_an_idempotent_rejoin_is_not_treated_as_a_second_player(
    handler: QuizifyWebSocketHandler, game: QuizifyGameState
) -> None:
    """Rejoining under the same raw name must reclaim, not spawn "Name 2".

    The duplicate-self-join guard and the auto-rename loop both compare against
    the stored name. Feeding them the raw one made an ordinary lobby refresh
    look like a different player to one check and like a taken name to the
    other.
    """
    raw = "Anna 👩‍👦"
    ws = _ws()
    await handler._handle_join(ws, {"name": raw}, game)
    await handler._handle_join(ws, {"name": raw}, game)

    names = [p.name for p in game.get_players()]
    assert names == [sanitize_player_name(raw)]
