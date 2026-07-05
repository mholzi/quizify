"""Tests for the #256 submission hardening:

  1. The X-Quizify-Secret header is sent on the worker POST when a secret is
     configured, and omitted when it is not (open-proxy close, back-compat).
  2. The PackSubmissionStore per-file asyncio.Lock serialises the
     load-modify-save in get_with_reconcile()/add() so a submit landing
     mid-reconcile can't drop records.

No HA, no real network: the worker POST is intercepted with a fake aiohttp
ClientSession so we can capture the headers the integration sends.
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from typing import Any

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT))

from custom_components.quizify.server import pack_submission as ps  # noqa: E402
from custom_components.quizify.server.pack_submission import (  # noqa: E402
    PackSubmissionStore,
    submit_pack_view,
)


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class _FakeRuntime:
    def __init__(self, data_dir: Path) -> None:
        self.data_dir = data_dir

    async def run_in_executor(self, func, *args) -> Any:
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, func, *args)

    def get_client_session(self) -> "_CapturingSession":
        # submit_pack_view now pulls a shared session off the runtime (#456)
        # instead of building an aiohttp.ClientSession; hand back the capturing
        # double so the header assertions still work.
        return _CapturingSession()


class _FakeCtx:
    def __init__(
        self,
        data_dir: Path,
        submit_url: str | None = None,
        submit_secret: str | None = None,
    ) -> None:
        self.runtime = _FakeRuntime(data_dir)
        self.community_submit_url = submit_url
        self.community_submit_secret = submit_secret


class _FakeRequest:
    """Just enough of aiohttp.web.Request for submit_pack_view."""

    def __init__(self, ctx: _FakeCtx, body: dict) -> None:
        self.app = {ps.APP_CTX_KEY: ctx}
        self.remote = "1.2.3.4"
        self._body = body

    async def json(self) -> dict:
        return self._body


class _FakeResponse:
    def __init__(self) -> None:
        self.status = 200

    async def json(self, content_type=None) -> dict:
        return {"issue_number": 42, "issue_url": "https://github.com/mholzi/quizify/issues/42"}

    async def __aenter__(self) -> "_FakeResponse":
        return self

    async def __aexit__(self, *exc) -> None:
        return None


class _CapturingSession:
    """Captures the headers passed to .post() so we can assert on them."""

    captured_headers: Any = "<unset>"

    def __init__(self, *args, **kwargs) -> None:
        pass

    async def __aenter__(self) -> "_CapturingSession":
        return self

    async def __aexit__(self, *exc) -> None:
        return None

    def post(self, url, json=None, headers=None, timeout=None):  # noqa: A002
        type(self).captured_headers = headers
        return _FakeResponse()


def _good_pack() -> dict:
    return {
        "name": "Secret Test Pack",
        "language": "en",
        "questions": [
            {
                "id": "q1",
                "question": "Which planet is the Red Planet?",
                "answers": [
                    {"text": "Mars", "correct": True},
                    {"text": "Venus", "correct": False},
                    {"text": "Jupiter", "correct": False},
                ],
            }
        ],
    }


# ---------------------------------------------------------------------------
# 1. Secret-header wiring
# ---------------------------------------------------------------------------


def _run_submit(ctx: _FakeCtx, monkeypatch: pytest.MonkeyPatch) -> Any:
    """Drive submit_pack_view with the worker POST stubbed; return captured headers."""
    _CapturingSession.captured_headers = "<unset>"
    # Reset the module-level rate-limit buckets so repeated submits in the
    # suite (same fake client IP) never trip the limiter.
    ps._rate_limiter._buckets.clear()
    request = _FakeRequest(ctx, {"pack": _good_pack()})

    async def _go() -> None:
        resp = await submit_pack_view(request)  # type: ignore[arg-type]
        assert resp.status == 200

    asyncio.run(_go())
    return _CapturingSession.captured_headers


def test_secret_header_sent_when_configured(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ctx = _FakeCtx(
        tmp_path,
        submit_url="https://worker.example/submit",
        submit_secret="s3cr3t-token",
    )
    headers = _run_submit(ctx, monkeypatch)
    assert headers == {"X-Quizify-Secret": "s3cr3t-token"}


def test_secret_header_absent_when_not_configured(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ctx = _FakeCtx(
        tmp_path,
        submit_url="https://worker.example/submit",
        submit_secret=None,
    )
    headers = _run_submit(ctx, monkeypatch)
    # No secret → headers omitted entirely (None), preserving today's behaviour.
    assert headers is None


def test_secret_header_absent_when_blank(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ctx = _FakeCtx(
        tmp_path,
        submit_url="https://worker.example/submit",
        submit_secret="   ",
    )
    headers = _run_submit(ctx, monkeypatch)
    assert headers is None


def test_submit_persists_record(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Sanity: a successful submit still records the submission (so the store
    path that the lock now wraps is exercised end-to-end)."""
    ctx = _FakeCtx(
        tmp_path,
        submit_url="https://worker.example/submit",
        submit_secret="abc",
    )
    _run_submit(ctx, monkeypatch)
    records = json.loads((tmp_path / "pack_submissions.json").read_text())
    assert len(records["submissions"]) == 1
    assert records["submissions"][0]["issue_number"] == 42


# ---------------------------------------------------------------------------
# 2. Store load-modify-save lock
# ---------------------------------------------------------------------------


def test_concurrent_adds_keep_all_records(tmp_path: Path) -> None:
    """Without the lock, two adds that both load the same empty snapshot would
    each persist a single-record file and one record would be lost. With the
    lock they serialise and both survive."""

    async def _run() -> dict:
        ctx = _FakeCtx(tmp_path)
        store = PackSubmissionStore(ctx)
        records = [
            {"id": str(i), "name": f"pack {i}", "issue_number": i, "status": "pending"}
            for i in range(10)
        ]
        await asyncio.gather(*(store.add(r) for r in records))
        # _load() reads the persisted records WITHOUT the GitHub reconcile fetch
        # (get_with_reconcile would hit the network); the lock is what's under test.
        return store._load()

    data = asyncio.run(_run())
    ids = {s["id"] for s in data["submissions"]}
    assert ids == {str(i) for i in range(10)}, "the lock must not drop any record"


def test_two_stores_share_one_lock(tmp_path: Path) -> None:
    """The lock is keyed on the records-file path, so per-request store
    instances pointing at the same file share it (otherwise it wouldn't
    serialise anything)."""

    async def _run() -> bool:
        ctx = _FakeCtx(tmp_path)
        a = PackSubmissionStore(ctx)
        b = PackSubmissionStore(ctx)
        return a._lock is b._lock

    assert asyncio.run(_run()) is True


def test_add_during_reconcile_not_lost(tmp_path: Path) -> None:
    """An add() racing a get_with_reconcile() (no pending items, so reconcile
    just stamps last_poll and persists) must not be clobbered."""

    async def _run() -> dict:
        ctx = _FakeCtx(tmp_path)
        store = PackSubmissionStore(ctx)
        # Seed one accepted record so reconcile has nothing to fetch.
        await store.add({"id": "seed", "issue_number": 1, "status": "accepted"})
        await asyncio.gather(
            store.get_with_reconcile(),
            store.add({"id": "racing", "issue_number": 2, "status": "pending"}),
        )
        return await store.get_with_reconcile()

    data = asyncio.run(_run())
    ids = {s["id"] for s in data["submissions"]}
    assert {"seed", "racing"} <= ids
