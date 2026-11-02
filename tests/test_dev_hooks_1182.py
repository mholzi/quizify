"""The dev server's state hooks do what they say, and only when asked (#1182).

``scripts/dev_hooks.py`` exists so every state in the states review can be
put on screen on purpose: a failing endpoint, a broken picture, a dropped
socket, a restart in the middle of a game. These run the real dev-server app
(``scripts/dev_server.build_app``) in-process.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from aiohttp.test_utils import TestClient, TestServer

_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO / "scripts"))

import dev_server  # noqa: E402


async def _client(tmp_path: Path, *argv: str) -> TestClient:
    args = dev_server.parse_args(["--data-dir", str(tmp_path), *argv])
    app = dev_server.build_app(data_dir=tmp_path)
    dev_server.dev_hooks.install(app, args)
    client = TestClient(TestServer(app))
    await client.start_server()
    return client


@pytest.mark.asyncio
async def test_no_flag_no_hook(tmp_path: Path) -> None:
    client = await _client(tmp_path)
    try:
        r = await client.post("/api/quizify/dev/restart")
        assert r.status in (404, 405), "dev endpoints must not exist without --dev"
        r = await client.get("/quizify/static/img/packs/nature/puffin.webp")
        assert r.status == 200
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_fail_answers_with_the_forced_status(tmp_path: Path) -> None:
    client = await _client(
        tmp_path,
        "--fail", "/api/quizify/analytics/data=500",
        "--fail", "/api/quizify/flags=json-error",
    )
    try:
        r = await client.get("/api/quizify/analytics/data?period=30d")
        assert r.status == 500
        assert (await r.json())["error"].startswith("dev:")
        r = await client.get("/api/quizify/flags")
        assert r.status == 200
        assert "not json" in await r.text()
        r = await client.get("/api/quizify/packs")
        assert r.status == 200, "only matching paths fail"
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_broken_images_404_every_pack_picture(tmp_path: Path) -> None:
    client = await _client(tmp_path, "--broken-images")
    try:
        r = await client.get("/quizify/static/img/packs/nature/puffin.webp")
        assert r.status == 404
        r = await client.get("/quizify/static/img/icon-256.png")
        assert r.status == 200, "the app's own icons stay"
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_drop_sockets_closes_and_blocks_the_role(tmp_path: Path) -> None:
    client = await _client(tmp_path, "--dev")
    try:
        ws = await client.ws_connect("/api/quizify/ws?role=dashboard")
        r = await client.post("/api/quizify/dev/drop-sockets?role=dashboard&for=30")
        assert (await r.json())["dropped"] == 1
        msg = await ws.receive()
        assert ws.closed or msg.type.name in ("CLOSE", "CLOSING", "CLOSED")
        r = await client.get("/api/quizify/ws?role=dashboard")
        assert r.status == 503
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_restart_reads_back_the_running_game_marker(tmp_path: Path) -> None:
    client = await _client(tmp_path, "--dev")
    try:
        handler = client.app[dev_server.APP_CTX_KEY].ws_handler
        await handler.interrupted_game.load()
        await handler.interrupted_game.sync("QUESTION_ACTIVE")
        r = await client.post("/api/quizify/dev/restart")
        body = await r.json()
        assert body["was_running"] is True
        assert body["notice"]["reason"] == "restart"
        assert handler._conn.reconnect_failure_reason("any-old-token") == "restart"
    finally:
        await client.close()
