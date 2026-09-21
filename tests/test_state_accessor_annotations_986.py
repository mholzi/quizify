"""The state accessors carry their return types (#986).

Five accessors on ``QuizifyGameState`` returned without an annotation:
``lightning``, ``hot_seat``, ``get_player_timer``, ``resolve_tick`` and
``get_player_powerup``. ``pyproject.toml`` sets ``check_untyped_defs = false``,
so an unannotated def is not merely unchecked inside — its *return* is ``Any``
at every call site too. Every ``game_state.hot_seat.…`` chain in
``server/websocket.py`` and ``server/serializers.py`` was therefore invisible
to the mypy gate: a misspelled attribute on either object type-checked clean
and reached a living room.

The first test pins all five to the class they actually return, resolved
against the real classes rather than compared as text, so a plausible-looking
but wrong annotation fails here too. The second widens that to the whole class:
``QuizifyGameState`` is the object the server layer reads through, and an
unannotated accessor on it is an ``Any`` hole in the gate wherever it is used.

The last test is the one call-site fix the annotations surfaced. With
``hot_seat`` typed, mypy saw that ``_handle_hot_seat_bet`` passed
``data.get("side")`` — which is ``None`` when the client omits the field —
into ``HotSeatRound.record_bet(name, side: str, pct)``. The round already
refused that (``side not in BET_SIDES``), so the fix only moves the refusal
in front of the call; this test pins that the wire behaviour did not move
with it.
"""

from __future__ import annotations

import inspect
import sys
import typing
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT))

from custom_components.quizify.game import state as state_module  # noqa: E402
from custom_components.quizify.game.hot_seat import (  # noqa: E402
    BET_WILL,
    HotSeatRound,
)
from custom_components.quizify.game.lightning import LightningRound  # noqa: E402
from custom_components.quizify.game.phase_controller import (  # noqa: E402
    GamePhase,
    TickResolution,
)
from custom_components.quizify.game.powerups import PowerUpType  # noqa: E402
from custom_components.quizify.game.state import QuizifyGameState  # noqa: E402
from custom_components.quizify.game.timer import QuestionTimer  # noqa: E402
from custom_components.quizify.server.connection import (  # noqa: E402
    ConnectionManager,
)
from custom_components.quizify.server.websocket import (  # noqa: E402
    QuizifyWebSocketHandler,
)

#: ``state.py`` imports these two under ``TYPE_CHECKING`` only, so the string
#: annotations cannot be resolved from the module globals alone.
_LOCALNS = {"HotSeatRound": HotSeatRound, "LightningRound": LightningRound}

#: The five from the issue, each with the type it really returns — read off
#: the backing field (``self._lightning: LightningRound | None``) or the
#: delegate's own signature (``PhaseController.get_timer``,
#: ``PhaseController.resolve_tick``, ``PowerUpManager.get_powerup``).
EXPECTED_RETURNS = {
    "lightning": LightningRound | None,
    "hot_seat": HotSeatRound | None,
    "get_player_timer": QuestionTimer | None,
    "resolve_tick": TickResolution,
    "get_player_powerup": PowerUpType | None,
}


def _underlying(name: str):
    """The plain function behind an attribute, unwrapping ``property``."""
    attr = inspect.getattr_static(QuizifyGameState, name)
    return attr.fget if isinstance(attr, property) else attr


# ---------------------------------------------------------------------------
# The annotations themselves
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("name", sorted(EXPECTED_RETURNS))
def test_accessor_returns_its_real_type(name: str) -> None:
    fn = _underlying(name)
    assert "return" in fn.__annotations__, (
        f"QuizifyGameState.{name} returns unannotated — every call site reads "
        "its result as Any, which is the hole #986 closed"
    )
    hints = typing.get_type_hints(
        fn, globalns=vars(state_module), localns=_LOCALNS
    )
    assert hints["return"] == EXPECTED_RETURNS[name]


def test_no_accessor_on_the_state_returns_unannotated() -> None:
    """The class the whole server layer reads through stays fully annotated.

    Closing five holes is worth little if the sixth opens next week. Every
    method and property on ``QuizifyGameState`` carries a return type as of
    #986, so this can be an exact assertion rather than a budget.
    """
    missing = sorted(
        name
        for name in dir(QuizifyGameState)
        if inspect.isfunction(_underlying(name))
        and "return" not in _underlying(name).__annotations__
    )
    assert missing == [], (
        f"unannotated return(s) on QuizifyGameState: {missing} — mypy reads "
        "each one as Any wherever it is called"
    )


# ---------------------------------------------------------------------------
# The call site the annotations surfaced
# ---------------------------------------------------------------------------


class _Runtime:
    def __init__(self, tmp_path: Path) -> None:
        self.data_dir = tmp_path


def _ws() -> MagicMock:
    ws = MagicMock()
    ws.closed = False
    ws.send_json = AsyncMock()
    ws.close = AsyncMock()
    return ws


@pytest.fixture
def seated(tmp_path: Path):
    """A live Hot Seat question: Anna holds the chair, Ben may stake on it."""
    game = QuizifyGameState(runtime=_Runtime(tmp_path), entry_id="test")
    handler = QuizifyWebSocketHandler(
        runtime=_Runtime(tmp_path), game_state_provider=lambda: game
    )
    handler._conn = ConnectionManager(_Runtime(tmp_path), lambda: game)
    handler._get_game_state = lambda: game  # type: ignore[assignment]

    errors: list[dict] = []

    async def _send_error(ws, code, message) -> None:
        errors.append({"code": code, "message": message})

    async def _noop_broadcast(message: dict) -> None:
        return None

    async def _safe_send(ws, message: dict) -> None:
        return None

    handler._conn.send_error = _send_error  # type: ignore[assignment]
    handler._conn.broadcast = _noop_broadcast  # type: ignore[assignment]
    handler._conn._safe_send = _safe_send  # type: ignore[assignment]

    sockets = {}
    for name in ("Anna", "Ben", "Mira"):
        ws = _ws()
        handler._conn.add_connection(ws, is_admin=False, is_dashboard=False)
        # The game layer is keyed by the opaque connection id the server layer
        # mints, not by the socket — that is what ``_player_for_ws`` looks up.
        game.add_player(name, handler._conn.connection_id(ws))
        sockets[name] = ws

    game.start_game(
        category="picture-round-en", difficulty="easy", num_rounds=5, language="en"
    )
    for player in game.get_players():
        player.score = 40
    game.phase = GamePhase.ANSWER_REVEAL
    assert game.start_hot_seat_auction()
    game.hot_seat.record_bid("Anna", 50)
    assert game.close_hot_seat_auction() == "Anna"
    assert game.phase == GamePhase.HOT_SEAT

    return game, handler, sockets, errors


@pytest.mark.asyncio
async def test_a_bet_with_no_side_is_refused(seated) -> None:
    """The message the typing hole was hiding: ``side`` absent from the frame.

    It reached ``record_bet`` as ``None`` where a ``str`` was declared. The
    refusal is unchanged — same code, same message — it just happens before
    the call now.
    """
    game, handler, sockets, errors = seated

    await handler._handle_message(
        sockets["Ben"], {"type": "hot_seat_bet", "bet": 50}, is_admin=False
    )

    assert errors, "a bet with no side was accepted"
    assert errors[-1]["message"] == "Bet not accepted"
    assert game.hot_seat.bets == {}


@pytest.mark.asyncio
async def test_a_bet_with_a_real_side_still_lands(seated) -> None:
    """The positive control: the guard must not swallow a well-formed bet."""
    game, handler, sockets, errors = seated

    await handler._handle_message(
        sockets["Ben"],
        {"type": "hot_seat_bet", "side": BET_WILL, "bet": 50},
        is_admin=False,
    )

    assert errors == []
    assert game.hot_seat.bets["Ben"].side == BET_WILL
