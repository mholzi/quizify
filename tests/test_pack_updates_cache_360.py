"""Tests for the cached pack-update-check endpoint (#360).

``pack_update_check_view`` backs ``GET /api/quizify/packs/updates`` — an
unauthenticated endpoint. Before #360 every request performed an uncached
aiohttp fetch of GitHub's raw ``versions.json`` plus a full pack re-load in the
executor, so any client could loop it to make the HA host hammer GitHub and burn
loop/executor time. The fix caches the upstream manifest in-memory for
``_UPSTREAM_CACHE_TTL_SECONDS`` and skips the pack reload once the bank is
already loaded, while keeping the response shape identical and falling back
gracefully when the fetch fails.
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


class _FakeBank:
    """Minimal QuestionBank double tracking reload calls."""

    def __init__(self, versions: dict, loaded: bool = True) -> None:
        self._versions = versions
        self._loaded = loaded
        self.load_calls = 0

    @property
    def is_loaded(self) -> bool:
        return self._loaded

    def load_all_categories(self) -> dict:
        self.load_calls += 1
        self._loaded = True
        return {}

    def get_pack_versions(self) -> dict:
        return {slug: dict(meta) for slug, meta in self._versions.items()}


class _FakeRuntime:
    def __init__(self) -> None:
        self.exec_calls = 0

    async def run_in_executor(self, fn, *args):  # noqa: ANN001, ANN002
        self.exec_calls += 1
        return fn(*args)


class _FakeRequest:
    def __init__(self, ctx) -> None:  # noqa: ANN001
        self.app = {APP_CTX_KEY: ctx}
        self.query: dict[str, str] = {}
        self.headers: dict[str, str] = {}


def _make_ctx(bank: _FakeBank) -> SimpleNamespace:
    return SimpleNamespace(
        game=SimpleNamespace(question_bank=bank),
        runtime=_FakeRuntime(),
    )


def _call(ctx) -> dict:  # noqa: ANN001
    resp = asyncio.run(views.pack_update_check_view(_FakeRequest(ctx)))  # type: ignore[arg-type]
    return json.loads(resp.body)


@pytest.fixture(autouse=True)
def _reset_cache():
    """Each test starts (and ends) with an empty upstream cache."""
    views._upstream_versions_cache = None
    yield
    views._upstream_versions_cache = None


def _install_fake_fetch(monkeypatch, payload, counter) -> None:  # noqa: ANN001
    async def _fake_fetch(*_args):  # noqa: ANN002 — accepts the runtime arg (#456)
        counter["n"] += 1
        return payload

    monkeypatch.setattr(views, "_fetch_upstream_versions", _fake_fetch)


def test_second_call_within_ttl_does_not_refetch(monkeypatch) -> None:
    """A repeat call inside the TTL window reuses the cached manifest."""
    counter = {"n": 0}
    _install_fake_fetch(monkeypatch, {"animals": "1.2.0"}, counter)
    bank = _FakeBank({"animals": {"version": "1.0.0", "name": "Animals"}})
    ctx = _make_ctx(bank)

    first = _call(ctx)
    second = _call(ctx)

    assert counter["n"] == 1  # only one GitHub fetch for two requests
    assert first == second
    # Response shape preserved + update detected.
    assert first["upstream_available"] is True
    assert first["upstream"] == {"animals": "1.2.0"}
    assert first["updates"] == [
        {
            "slug": "animals",
            "name": "Animals",
            "installed_version": "1.0.0",
            "upstream_version": "1.2.0",
        }
    ]
    assert first["installed"] == {"animals": {"version": "1.0.0", "name": "Animals"}}


def test_ttl_expiry_refetches(monkeypatch) -> None:
    """Once the cached entry ages past the TTL, the next call refetches."""
    counter = {"n": 0}
    _install_fake_fetch(monkeypatch, {"animals": "1.2.0"}, counter)
    bank = _FakeBank({"animals": {"version": "1.0.0", "name": "Animals"}})
    ctx = _make_ctx(bank)

    _call(ctx)
    assert counter["n"] == 1

    # Simulate the clock advancing past the TTL by ageing the cached timestamp.
    ts, payload = views._upstream_versions_cache
    views._upstream_versions_cache = (
        ts - views._UPSTREAM_CACHE_TTL_SECONDS - 1,
        payload,
    )

    _call(ctx)
    assert counter["n"] == 2


def test_failed_fetch_falls_back_and_is_not_cached(monkeypatch) -> None:
    """A failed fetch returns None gracefully and does not poison the cache."""
    counter = {"n": 0}
    _install_fake_fetch(monkeypatch, None, counter)
    bank = _FakeBank({"animals": {"version": "1.0.0", "name": "Animals"}})
    ctx = _make_ctx(bank)

    out = _call(ctx)
    assert out["upstream"] is None
    assert out["upstream_available"] is False
    assert out["updates"] == []
    assert out["installed"] == {"animals": {"version": "1.0.0", "name": "Animals"}}
    # Nothing cached → a subsequent successful fetch is attempted (not skipped).
    assert views._upstream_versions_cache is None

    _install_fake_fetch(monkeypatch, {"animals": "1.2.0"}, counter)
    out2 = _call(ctx)
    assert out2["upstream_available"] is True
    assert views._upstream_versions_cache is not None


def test_already_loaded_bank_skips_executor_reload(monkeypatch) -> None:
    """When packs are already loaded, the endpoint skips the executor reload."""
    counter = {"n": 0}
    _install_fake_fetch(monkeypatch, {"animals": "1.0.0"}, counter)
    bank = _FakeBank(
        {"animals": {"version": "1.0.0", "name": "Animals"}}, loaded=True
    )
    ctx = _make_ctx(bank)

    _call(ctx)

    assert bank.load_calls == 0
    assert ctx.runtime.exec_calls == 0


def test_unloaded_bank_loads_once_via_executor(monkeypatch) -> None:
    """A cold bank is loaded through the executor exactly once."""
    counter = {"n": 0}
    _install_fake_fetch(monkeypatch, {"animals": "1.0.0"}, counter)
    bank = _FakeBank(
        {"animals": {"version": "1.0.0", "name": "Animals"}}, loaded=False
    )
    ctx = _make_ctx(bank)

    _call(ctx)

    assert bank.load_calls == 1
    assert ctx.runtime.exec_calls == 1
