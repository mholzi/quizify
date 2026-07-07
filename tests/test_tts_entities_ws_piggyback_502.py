"""Tests for the TTS-entities WebSocket piggyback (#502).

The admin narration dropdowns (#281) used to populate only from the separate
``/api/quizify/tts-entities`` HTTP endpoint, which #356 put behind the admin
session token. That fetch fires at page-init, *before* the token arrives over
the WebSocket, so it 401s and the dropdowns fall back to "None found"; #501's
one-shot refetch was a fragile band-aid for the race.

Fix (#502): the admin-connect frame carries the TTS-engine + media-player lists
directly, over the already-authenticated admin socket, so the dropdowns never
depend on the token-gated HTTP fetch. ``snapshot_tts_entities`` is the shared
builder used by both the WS handler and the HTTP endpoint.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT))

from custom_components.quizify.game.state import QuizifyGameState  # noqa: E402
from custom_components.quizify.server import WS_PATH  # noqa: E402
from custom_components.quizify.server.connection import ConnectionManager  # noqa: E402
from custom_components.quizify.server.serializers import (  # noqa: E402
    snapshot_tts_entities,
)
from custom_components.quizify.server.websocket import (  # noqa: E402
    QuizifyWebSocketHandler,
)

# --------------------------------------------------------------------------
# snapshot_tts_entities (pure helper)
# --------------------------------------------------------------------------


class _FakeState:
    def __init__(self, entity_id: str, friendly_name: str | None = None) -> None:
        self.entity_id = entity_id
        self.attributes = {}
        if friendly_name is not None:
            self.attributes["friendly_name"] = friendly_name


class _FakeStates:
    def __init__(self, by_domain: dict[str, list[_FakeState]]) -> None:
        self._by_domain = by_domain

    def async_all(self, domain: str) -> list[_FakeState]:
        return list(self._by_domain.get(domain, []))


class _FakeHass:
    def __init__(self, by_domain: dict[str, list[_FakeState]]) -> None:
        self.states = _FakeStates(by_domain)


def test_snapshot_none_hass_returns_empty_lists() -> None:
    """Standalone dev server (no hass) → empty lists, no crash."""
    assert snapshot_tts_entities(None) == {"tts": [], "media_players": []}


def test_snapshot_sorts_and_falls_back_to_entity_id() -> None:
    hass = _FakeHass(
        {
            "tts": [_FakeState("tts.b", "Bravo"), _FakeState("tts.a", "alpha")],
            "media_player": [_FakeState("media_player.bare")],
        }
    )
    out = snapshot_tts_entities(hass)
    # Case-insensitive sort by friendly_name.
    assert [e["friendly_name"] for e in out["tts"]] == ["alpha", "Bravo"]
    # Missing friendly_name falls back to entity_id.
    assert out["media_players"] == [
        {"entity_id": "media_player.bare", "friendly_name": "media_player.bare"}
    ]


# --------------------------------------------------------------------------
# admin-connect frame carries the lists (integration over a live WS)
# --------------------------------------------------------------------------


class _FakeRuntime:
    def __init__(self, tmp_path: Path, hass=None) -> None:  # noqa: ANN001
        self.data_dir = tmp_path
        # Mirrors HARuntime.hass — the WS handler reads it via getattr, so a
        # missing attribute (standalone) degrades to empty lists.
        if hass is not None:
            self.hass = hass

    def create_task(self, coro):  # noqa: ANN001, ANN202
        return asyncio.ensure_future(coro)

    async def run_in_executor(self, func, *args):  # noqa: ANN001, ANN002
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, func, *args)


def _make_handler(tmp_path: Path, hass=None) -> QuizifyWebSocketHandler:  # noqa: ANN001
    runtime = _FakeRuntime(tmp_path, hass=hass)
    game = QuizifyGameState(runtime=runtime, entry_id="test")
    h = QuizifyWebSocketHandler(runtime=runtime, game_state_provider=lambda: game)
    h._conn = ConnectionManager(runtime, lambda: game)
    return h


async def _make_client(handler: QuizifyWebSocketHandler) -> TestClient:
    app = web.Application()
    app.router.add_get(WS_PATH, handler.handle)
    client = TestClient(TestServer(app))
    await client.start_server()
    return client


async def _first_game_state(ws) -> dict:  # noqa: ANN001
    """Return the first ``game_state`` frame after an ``admin_connect`` message.

    ``_handle_admin_connect`` (which carries the entity lists) fires only in
    response to an ``admin_connect`` frame from a ?role=admin socket — the admin
    page sends it right after connect. Each receive is bounded by a timeout so a
    regression can never hang the whole suite.
    """
    await ws.send_json({"type": "admin_connect"})
    for _ in range(100):
        msg = await asyncio.wait_for(ws.receive_json(), timeout=5)
        if msg.get("type") == "game_state":
            return msg
    raise AssertionError("no game_state frame received")


@pytest.mark.asyncio
async def test_admin_connect_frame_includes_entity_lists(tmp_path: Path) -> None:
    """A freshly-bootstrapped admin receives tts_entities + media_players on the
    admin-connect frame — no separate token-gated HTTP fetch needed."""
    hass = _FakeHass(
        {
            "tts": [
                _FakeState("tts.cloud", "Home Assistant Cloud"),
                _FakeState("tts.google", "Google Translate"),
            ],
            "media_player": [_FakeState("media_player.wohnzimmer", "Wohnzimmer")],
        }
    )
    handler = _make_handler(tmp_path, hass=hass)
    client = await _make_client(handler)
    try:
        # No token yet → the first ?role=admin connection wins bootstrap and is
        # granted admin, so _handle_admin_connect fires.
        ws = await client.ws_connect(WS_PATH + "?role=admin")
        msg = await _first_game_state(ws)

        assert "admin_session_token" in msg  # still authenticated as before
        assert [e["friendly_name"] for e in msg["tts_entities"]] == [
            "Google Translate",
            "Home Assistant Cloud",
        ]
        assert msg["media_players"] == [
            {"entity_id": "media_player.wohnzimmer", "friendly_name": "Wohnzimmer"}
        ]
        await ws.close()
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_admin_connect_frame_empty_lists_without_hass(tmp_path: Path) -> None:
    """Standalone runtime (no hass) → the frame still carries the keys, empty."""
    handler = _make_handler(tmp_path, hass=None)
    client = await _make_client(handler)
    try:
        ws = await client.ws_connect(WS_PATH + "?role=admin")
        msg = await _first_game_state(ws)
        assert msg["tts_entities"] == []
        assert msg["media_players"] == []
        await ws.close()
    finally:
        await client.close()
