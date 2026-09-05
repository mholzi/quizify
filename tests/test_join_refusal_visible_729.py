"""A refused join must return the button and name the reason (#729).

``handleJoinClick`` sets ``state.playerName`` *before* the join goes out —
deliberately, because the ``onOpen`` handler auto-sends the join off that name
when the socket was not open at click time, and ``player-utils`` gates its
whole reconnect loop on it. The join-error reset in ``handleError``, however,
was guarded by ``if (!state.playerName && els.joinBtn)``. By the time any
refusal arrived the name was always set, so the guard never fired once: the
guest was left with a disabled button reading "Joining…", no reason, and no
way back.

The fix does not move the assignment. It stops the refusal path *inferring*
"we are joining" from the absence of a name and has ``handleJoinClick`` say so
outright (``state.joinPending``), then makes the refusal genuinely visible:
button re-enabled, reason persisted in the inline validation message, and the
refused name released so the reconnect loop does not quietly re-send it.

The second half is wording. Every reason the server can refuse a join for
needs a sentence a guest can act on, in all three languages. "Name already
taken" is a label, not an instruction — it does not tell the guest to add a
number and tap Join again. These tests pin both halves, plus the two refusals
that had no code of their own at all (the duplicate self-join and the per-IP
join flood, both shipped as a bare ``INVALID_ACTION``) and the one that had a
code but no wire message (``GAME_ENDED``, which fell through to the generic
"Failed to join").
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
    ERR_GAME_ENDED,
    ERR_INVALID_ACTION,
)

#: Spelled out rather than imported from const: these are wire values the
#: phone matches on (``t('join.refused.<CODE>')``), so the test has to break
#: if the string changes, not follow it.
ERR_ALREADY_JOINED = "ALREADY_JOINED"
ERR_JOIN_RATE_LIMITED = "JOIN_RATE_LIMITED"
from custom_components.quizify.game.state import QuizifyGameState  # noqa: E402
from custom_components.quizify.server.connection import ConnectionManager  # noqa: E402
from custom_components.quizify.server.websocket import (  # noqa: E402
    QuizifyWebSocketHandler,
)

_WWW = _REPO_ROOT / "custom_components" / "quizify" / "www"
I18N_DIR = _WWW / "i18n"
PLAYER_CORE = _WWW / "js" / "player-core.js"
PLAYER_BUNDLE = _WWW / "js" / "player.bundle.js"
_COMPONENT = _REPO_ROOT / "custom_components" / "quizify"
REGISTRY_PY = _COMPONENT / "game" / "player_registry.py"
WEBSOCKET_PY = _COMPONENT / "server" / "websocket.py"

LANGUAGES = ("de", "en", "es")

#: Every way a join can be refused, as the phone sees it. The first six come
#: off the wire; NO_CONNECTION is the one the client has to invent, because
#: the per-IP connection cap answers the upgrade with an HTTP 429 and no
#: WebSocket is ever opened to carry an error frame.
JOIN_REFUSAL_CODES = (
    "NAME_TAKEN",
    "NAME_INVALID",
    "GAME_FULL",
    "GAME_ENDED",
    "ALREADY_JOINED",
    "JOIN_RATE_LIMITED",
    "INVALID_ACTION",
    "NO_CONNECTION",
    "UNKNOWN",
)

#: Refusals that make the held name worthless. Leaving it set keeps the
#: reconnect loop (gated on ``state.playerName``) re-sending the very name the
#: server just refused.
CODES_THAT_RELEASE_THE_NAME = (
    "NAME_TAKEN",
    "NAME_INVALID",
    "GAME_FULL",
    "GAME_ENDED",
)


# ---------------------------------------------------------------------------
# Harness (mirrors tests/test_admin_refusal_visible_586.py)
# ---------------------------------------------------------------------------


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


def _function_body(source: str, name: str) -> str:
    """Return the text of ``function <name>(`` up to the next top-level function."""
    start = source.index(f"function {name}(")
    rest = source[start + 1 :]
    end = rest.find("\n    function ")
    return rest if end == -1 else rest[:end]


# ---------------------------------------------------------------------------
# The bug itself: the reset must not be inferred from the absent name
# ---------------------------------------------------------------------------


def test_the_refusal_branch_knows_a_join_is_in_flight() -> None:
    """``handleError`` must key the join reset on an explicit pending flag.

    Keyed on ``!state.playerName`` it is dead code, because
    ``handleJoinClick`` filled the name in before the join was sent.
    """
    body = _function_body(PLAYER_CORE.read_text(encoding="utf-8"), "handleError")
    assert "state.joinPending" in body, (
        "handleError still infers 'we are joining' from state.playerName — "
        "the branch is dead for every refusal (#729)"
    )


def test_clicking_join_declares_the_join_pending() -> None:
    """Nothing sets the flag, nothing resets the button."""
    body = _function_body(PLAYER_CORE.read_text(encoding="utf-8"), "handleJoinClick")
    assert "beginJoinPending()" in body, (
        "handleJoinClick does not mark the join as pending, so a refusal has "
        "no way to tell that the button belongs to it (#729)"
    )


def test_a_successful_join_clears_the_pending_flag() -> None:
    """Otherwise the answer timeout fires ten seconds into a joined game."""
    source = PLAYER_CORE.read_text(encoding="utf-8")
    joined = source.index("case 'joined':")
    assert "endJoinPending()" in source[joined : joined + 400], (
        "the 'joined' branch never clears joinPending — the join answer "
        "timeout would fire on a player who is already in the game"
    )


def test_the_refused_name_is_released() -> None:
    """A held-but-refused name keeps the reconnect loop re-sending it."""
    source = PLAYER_CORE.read_text(encoding="utf-8")
    body = _function_body(source, "showJoinRefusal")
    assert "state.playerName = null" in body, (
        "a refused join keeps state.playerName, so player-utils keeps "
        "reconnecting and re-sending the refused name (#729)"
    )
    listed = source[source.index("JOIN_REFUSALS_CLEARING_NAME") :][:400]
    for code in CODES_THAT_RELEASE_THE_NAME:
        assert code in listed, f"{code} does not release the refused name"


def test_the_reason_survives_the_toast() -> None:
    """A three-second toast is not a message; the inline text is."""
    body = _function_body(PLAYER_CORE.read_text(encoding="utf-8"), "showJoinRefusal")
    assert "name-validation-msg" in body, (
        "the refusal is only toasted — it fades in three seconds and the "
        "guest is back to a bare button (#426, #729)"
    )
    assert "els.joinBtn.disabled = false" in body, (
        "the refusal never re-enables the join button — this is the stuck "
        "'Joining…' the issue reports"
    )


def test_the_shipped_bundle_carries_the_fix() -> None:
    """player.html loads the bundle, not the modules (the #625 trap)."""
    bundle = PLAYER_BUNDLE.read_text(encoding="utf-8")
    assert "joinPending" in bundle and "join.refused." in bundle, (
        "player.bundle.js is stale — run: python3 scripts/build_bundle.py"
    )


# ---------------------------------------------------------------------------
# Wording: every refusal, every language, something a guest can act on
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("lang", LANGUAGES)
@pytest.mark.parametrize("code", JOIN_REFUSAL_CODES)
def test_every_refusal_has_wording_a_guest_can_act_on(lang: str, code: str) -> None:
    data = json.loads((I18N_DIR / f"{lang}.json").read_text(encoding="utf-8"))
    text = data.get("join", {}).get("refused", {}).get(code, "")
    assert text.strip(), f"{lang}.json has no join.refused.{code}"

    label = data.get("errors", {}).get(code, "")
    assert text != label, (
        f"{lang}.json reuses the terse errors.{code} label for the join "
        "refusal — a label names the problem, it does not tell the guest "
        "what to do next"
    )
    # A sentence, not a label: the wording has to carry a next step.
    assert len(text.split()) >= 8, (
        f"join.refused.{code} in {lang}.json is too short to say what the "
        f"guest should do: {text!r}"
    )


def test_the_three_languages_stay_in_key_parity() -> None:
    keys = {
        lang: set(
            json.loads((I18N_DIR / f"{lang}.json").read_text(encoding="utf-8"))["join"][
                "refused"
            ]
        )
        for lang in LANGUAGES
    }
    assert keys["de"] == keys["en"] == keys["es"], (
        f"join.refused key sets drifted: {keys}"
    )
    assert set(JOIN_REFUSAL_CODES) <= keys["en"]


# ---------------------------------------------------------------------------
# Server: no refusal may reach the phone unnamed
# ---------------------------------------------------------------------------


def test_every_registry_refusal_is_named_on_the_wire() -> None:
    """The join handler's fallback map must cover all of ``add_player``.

    ``ERR_GAME_ENDED`` was missing, so a guest scanning the QR code of a
    finished game got the bare "Failed to join".
    """
    registry = REGISTRY_PY.read_text(encoding="utf-8")
    add_player = registry[registry.index("def add_player(") : registry.index(
        "def get_player("
    )]
    refusals = set(re.findall(r"return False, (ERR_\w+)", add_player))
    assert refusals, "could not find any refusal in PlayerRegistry.add_player"

    handler = WEBSOCKET_PY.read_text(encoding="utf-8")
    error_map = handler[handler.index("error_messages = {") :][:600]
    for code in sorted(refusals):
        assert code in error_map, (
            f"{code} can refuse a join but has no message in the join "
            "handler's fallback map — the guest gets 'Failed to join'"
        )


@pytest.mark.asyncio
async def test_a_finished_game_says_it_is_finished(
    game: QuizifyGameState, tmp_path: Path
) -> None:
    handler, sent = _handler(game, tmp_path)
    game.add_player = lambda name, ws: (False, ERR_GAME_ENDED)  # type: ignore[assignment]

    ws = _ws()
    handler._conn.add_connection(ws, is_admin=False, is_dashboard=False)
    await handler._handle_join(ws, {"name": "Guest"}, game)

    assert sent, "the join was refused without telling the phone anything"
    assert sent[-1]["code"] == ERR_GAME_ENDED
    assert sent[-1]["message"] != "Failed to join", (
        "a finished game still answers with the generic fallback (#729)"
    )


@pytest.mark.asyncio
async def test_a_duplicate_self_join_has_its_own_code(
    game: QuizifyGameState, tmp_path: Path
) -> None:
    """Shipped as INVALID_ACTION, which localizes to "Invalid action".

    The guest is already in the game on this device and has nothing to
    retype; the join form has to be able to say exactly that.
    """
    handler, sent = _handler(game, tmp_path)
    ws = _ws()
    handler._conn.add_connection(ws, is_admin=False, is_dashboard=False)
    game.add_player("Guest", ws)

    await handler._handle_join(ws, {"name": "Someone Else"}, game)

    assert sent, "the duplicate self-join was refused silently"
    assert sent[-1]["code"] == ERR_ALREADY_JOINED, (
        "the duplicate self-join still answers INVALID_ACTION — the phone "
        "cannot tell it apart from a malformed frame"
    )


def test_the_join_flood_has_its_own_code() -> None:
    """A whole room behind one NAT can trip the limiter (#361, #701).

    Told "Invalid action", nobody waits — they hammer the button, which is
    exactly what keeps the window full.
    """
    source = WEBSOCKET_PY.read_text(encoding="utf-8")
    branch = source[source.index("Per-IP join rate limit exceeded") :][:400]
    assert ERR_JOIN_RATE_LIMITED in branch, (
        "the join-flood refusal is still a bare INVALID_ACTION"
    )
    assert ERR_INVALID_ACTION not in branch
