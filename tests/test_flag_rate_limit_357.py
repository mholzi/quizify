"""Regression tests for issue #357 (P2 security).

The unauthenticated ``POST /api/quizify/flag-question`` had no rate limit and
only an is-non-empty check on ``question_id``. A flood could pin the event loop
on executor disk writes, and a single oversized ``question_id`` could append a
~1 MB line (the 256 KB size-trim runs *before* the append, so it persisted).

The endpoint now: rate-limits per client IP (5/60s, mirroring pack-submit) and
caps ``question_id`` to 64 chars before persisting.
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT))

from custom_components.quizify.server import views  # noqa: E402
from custom_components.quizify.server.context import APP_CTX_KEY  # noqa: E402
from custom_components.quizify.server.flag_store import FILENAME  # noqa: E402


class _Runtime:
    def __init__(self, data_dir: Path) -> None:
        self.data_dir = data_dir

    async def run_in_executor(self, func, *args):  # noqa: ANN001, ANN002
        return func(*args)


class _Req:
    def __init__(self, ctx, remote: str, body: dict) -> None:  # noqa: ANN001
        self.app = {APP_CTX_KEY: ctx}
        self.remote = remote
        self._body = body

    async def json(self) -> dict:
        return self._body


def _ctx(tmp_path: Path):
    return SimpleNamespace(runtime=_Runtime(tmp_path))


async def _post(ctx, remote: str, body: dict):  # noqa: ANN001
    return await views.flag_question_view(_Req(ctx, remote, body))


def test_valid_flag_persists(tmp_path: Path) -> None:
    ip = "203.0.113.1"
    views._flag_rate_limiter.forget(ip)
    resp = asyncio.run(_post(_ctx(tmp_path), ip, {"question_id": "geo_037"}))
    assert resp.status == 200
    lines = (tmp_path / FILENAME).read_text("utf-8").splitlines()
    assert json.loads(lines[0])["question_id"] == "geo_037"


def test_sixth_post_from_same_ip_is_rate_limited(tmp_path: Path) -> None:
    ip = "203.0.113.2"
    views._flag_rate_limiter.forget(ip)
    ctx = _ctx(tmp_path)

    async def run() -> list[int]:
        out = []
        for i in range(6):
            r = await _post(ctx, ip, {"question_id": f"q{i}"})
            out.append(r.status)
        return out

    statuses = asyncio.run(run())
    assert statuses[:5] == [200, 200, 200, 200, 200]
    assert statuses[5] == 429


def test_rate_limit_is_per_ip(tmp_path: Path) -> None:
    a, b = "203.0.113.3", "203.0.113.4"
    views._flag_rate_limiter.forget(a)
    views._flag_rate_limiter.forget(b)
    ctx = _ctx(tmp_path)

    async def run() -> tuple[int, int]:
        # Exhaust IP a.
        for i in range(5):
            await _post(ctx, a, {"question_id": f"a{i}"})
        blocked = (await _post(ctx, a, {"question_id": "a5"})).status
        # A different IP is unaffected.
        allowed = (await _post(ctx, b, {"question_id": "b0"})).status
        return blocked, allowed

    blocked, allowed = asyncio.run(run())
    assert blocked == 429
    assert allowed == 200


def test_question_id_is_capped_at_64_chars(tmp_path: Path) -> None:
    ip = "203.0.113.5"
    views._flag_rate_limiter.forget(ip)
    resp = asyncio.run(
        _post(_ctx(tmp_path), ip, {"question_id": "x" * 5000})
    )
    assert resp.status == 200
    entry = json.loads((tmp_path / FILENAME).read_text("utf-8").splitlines()[0])
    assert entry["question_id"] == "x" * 64


def test_over_limit_does_not_write(tmp_path: Path) -> None:
    ip = "203.0.113.6"
    views._flag_rate_limiter.forget(ip)
    ctx = _ctx(tmp_path)

    async def run() -> None:
        for i in range(5):
            await _post(ctx, ip, {"question_id": f"q{i}"})
        await _post(ctx, ip, {"question_id": "OVERFLOW"})

    asyncio.run(run())
    body = (tmp_path / FILENAME).read_text("utf-8")
    assert "OVERFLOW" not in body  # the rejected POST wrote nothing
