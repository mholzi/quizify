"""Regression tests for issue #812 (code quality).

Three handlers in ``server/websocket.py`` — the join branch, the estimate-guess
branch and the multiple-choice branch — each declared their own local
``error_messages`` dict mapping ``ERR_*`` codes to English fallback text. The
copies had already drifted: one carried ``ERR_INVALID_ACTION`` and the other did
not, and both said "Time is up" where ``www/i18n/en.json`` — the text the client
actually shows off the code — says "Time expired".

#729 was exactly that failure mode (a code missing from one of the copies), and
its regression test located "the join map" as the *first* ``error_messages = {``
in the file — correct only as long as ``_handle_join`` happened to be defined
before the two submit handlers.

There is now one module-level ``ERROR_FALLBACK_TEXT`` next to the codes it
names, and ``ConnectionManager.send_error`` fills the message from it whenever
the caller passes none.
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

import custom_components.quizify.const as const  # noqa: E402
from custom_components.quizify.const import (  # noqa: E402
    ERR_ROUND_EXPIRED,
    ERROR_FALLBACK_TEXT,
)
from custom_components.quizify.server.connection import ConnectionManager  # noqa: E402

_COMPONENT = _REPO_ROOT / "custom_components" / "quizify"
_EN_JSON = _COMPONENT / "www" / "i18n" / "en.json"

#: Codes that name a wire error. ``ERR_SUBMIT_*`` lives in the HTTP
#: pack-submission flow, which answers with its own JSON bodies rather than a
#: WebSocket error frame, so it is deliberately not part of this map.
_WIRE_ERROR_NAMES = sorted(
    name
    for name in dir(const)
    if name.startswith("ERR_") and not name.startswith("ERR_SUBMIT_")
)


def _ws() -> MagicMock:
    ws = MagicMock()
    ws.closed = False
    ws.send_str = AsyncMock()
    ws.send_json = AsyncMock()
    return ws


class _Runtime:
    def __init__(self, data_dir: Path) -> None:
        self.data_dir = data_dir

    def create_task(self, coro):  # noqa: ANN001, ANN202
        return asyncio.ensure_future(coro)

    async def run_in_executor(self, func, *args):  # noqa: ANN001, ANN002
        return func(*args)


def _conn(tmp_path: Path) -> ConnectionManager:
    return ConnectionManager(_Runtime(tmp_path), lambda: None)


# ---------------------------------------------------------------------------
# One map, covering every code
# ---------------------------------------------------------------------------


def test_every_wire_error_code_has_fallback_text() -> None:
    """A code with no entry reaches the phone as the bare code (#729)."""
    missing = [
        name
        for name in _WIRE_ERROR_NAMES
        if getattr(const, name) not in ERROR_FALLBACK_TEXT
    ]
    assert not missing, (
        f"these error codes have no entry in ERROR_FALLBACK_TEXT: {missing}"
    )


def test_no_entry_is_empty() -> None:
    for code, text in ERROR_FALLBACK_TEXT.items():
        assert text.strip(), f"ERROR_FALLBACK_TEXT[{code!r}] is blank"


def test_the_map_names_no_code_that_does_not_exist() -> None:
    known = {getattr(const, name) for name in _WIRE_ERROR_NAMES}
    assert set(ERROR_FALLBACK_TEXT) <= known


def test_the_wording_agrees_with_the_english_bundle() -> None:
    """The drift the issue is about: "Time is up" vs en.json's "Time expired".

    Only codes the bundle actually carries are compared — ``FROZEN`` and
    ``TEAM_LOCKED`` have no ``errors.*`` key of their own.
    """
    bundle = json.loads(_EN_JSON.read_text(encoding="utf-8")).get("errors", {})
    drift = {
        code: (text, bundle[code])
        for code, text in ERROR_FALLBACK_TEXT.items()
        if code in bundle and bundle[code] != text
    }
    assert not drift, (
        "ERROR_FALLBACK_TEXT disagrees with www/i18n/en.json — the server "
        f"fallback and the string the client shows must match: {drift}"
    )


def test_the_expired_round_says_what_the_client_says() -> None:
    """The exact divergence named in the issue."""
    assert ERROR_FALLBACK_TEXT[ERR_ROUND_EXPIRED] == "Time expired"


# ---------------------------------------------------------------------------
# send_error defaults from it
# ---------------------------------------------------------------------------


def test_send_error_fills_the_message_from_the_map(tmp_path: Path) -> None:
    conn = _conn(tmp_path)
    ws = _ws()
    sent: list[dict] = []

    async def _send(target, payload) -> None:  # noqa: ANN001
        sent.append(payload)

    conn.send = _send  # type: ignore[assignment]
    asyncio.run(conn.send_error(ws, ERR_ROUND_EXPIRED))

    assert sent == [
        {"type": "error", "code": ERR_ROUND_EXPIRED, "message": "Time expired"}
    ]


def test_an_explicit_message_still_wins(tmp_path: Path) -> None:
    """Call sites that know more than the code keep saying it."""
    conn = _conn(tmp_path)
    sent: list[dict] = []

    async def _send(target, payload) -> None:  # noqa: ANN001
        sent.append(payload)

    conn.send = _send  # type: ignore[assignment]
    asyncio.run(conn.send_error(_ws(), ERR_ROUND_EXPIRED, "That team is closed"))

    assert sent[0]["message"] == "That team is closed"


def test_an_unknown_code_falls_back_to_itself(tmp_path: Path) -> None:
    """What the old local maps did with ``.get(err, err)``."""
    conn = _conn(tmp_path)
    sent: list[dict] = []

    async def _send(target, payload) -> None:  # noqa: ANN001
        sent.append(payload)

    conn.send = _send  # type: ignore[assignment]
    asyncio.run(conn.send_error(_ws(), "SOMETHING_NEW"))

    assert sent[0]["message"] == "SOMETHING_NEW"


# ---------------------------------------------------------------------------
# The copies are gone
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "module", ["server/websocket.py", "server/connection.py"]
)
def test_no_module_keeps_a_private_fallback_map(module: str) -> None:
    source = (_COMPONENT / module).read_text(encoding="utf-8")
    assert "error_messages = {" not in source, (
        f"{module} declares a local error-message map again — the fallback "
        "text belongs in const.ERROR_FALLBACK_TEXT (#812)"
    )
