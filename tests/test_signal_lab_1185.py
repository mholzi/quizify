"""The dev-only signal lab (#1185, section 5).

``dev_server.py --signals`` runs the real house consumers against a recording
fake Home Assistant. These tests drive the lab's endpoints with aiohttp's test
client: off without the flag, and with it a reveal with a streak lands in the
timeline as one bundled sentence plus the two bus events, in order.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO / "scripts"))
sys.path.insert(0, str(_REPO))

import dev_server  # noqa: E402

from custom_components.quizify.server.context import APP_CTX_KEY  # noqa: E402


def _app(tmp_path: Path, *flags: str):
    args = dev_server.parse_args(["--data-dir", str(tmp_path), *flags])
    app = dev_server.build_app(data_dir=tmp_path)
    dev_server.signal_lab.install(app, args, app[APP_CTX_KEY])
    return app


@pytest.mark.asyncio
async def test_the_lab_is_off_without_the_flag(aiohttp_client, tmp_path: Path) -> None:
    client = await aiohttp_client(_app(tmp_path))
    resp = await client.get("/quizify/dev/signals")
    assert resp.status == 404


@pytest.mark.asyncio
async def test_a_streak_reveal_shows_up_bundled_and_in_order(aiohttp_client, tmp_path: Path) -> None:
    client = await aiohttp_client(_app(tmp_path, "--signals"))
    page = await client.get("/quizify/dev/signals")
    assert page.status == 200 and "Signal lab" in await page.text()

    await client.post("/quizify/dev/signals/fire", json={"moment": "reveal:3"})
    rows = await (await client.get("/quizify/dev/signals/timeline")).json()
    names = [r["name"] for r in rows]
    speech = [r for r in rows if r["name"] == "tts.speak"]
    assert len(speech) == 1 and "Lena hat jetzt 3 in Folge." in speech[0]["data"]["message"]
    events = [n for n in names if n.startswith("quizify_")]
    assert events == ["quizify_answer_revealed", "quizify_streak_milestone"]
    # One speaker, one voice: no sting under the sentence.
    assert "media_player.play_media" not in names

    await client.post("/quizify/dev/signals/clear")
    assert await (await client.get("/quizify/dev/signals/timeline")).json() == []


@pytest.mark.asyncio
async def test_a_raw_frame_needs_a_type(aiohttp_client, tmp_path: Path) -> None:
    client = await aiohttp_client(_app(tmp_path, "--signals"))
    bad = await client.post("/quizify/dev/signals/fire", json={"frame": {"x": 1}})
    assert bad.status == 400
    ok = await client.post("/quizify/dev/signals/fire", json={"frame": {"type": "powerup_applied"}})
    assert ok.status == 200
