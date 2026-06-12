"""Performance / resource-leak regression tests for issue #169.

The #169 code-review batch flagged three items. On inspection all three are
non-issues under the actual execution model (single-threaded asyncio, bounded
player count) — these tests lock in the invariants that make them safe, so a
future refactor can't silently reintroduce a real leak/race:

  1. The per-connection rate-limit map (the shared SlidingWindowLimiter's
     buckets) is bounded: each connection's entry is pruned of stale timestamps
     on every check and removed entirely on disconnect, so the dict never grows
     beyond the set of live connections.
  2. (tick-loop) — covered by the existing timer/game-state suite; this file
     only adds the bounded-map + cache invariants. The tick loop was tidied to
     resolve each player's timer once per tick (behaviour unchanged; 162 tests
     still pass).
  3. The module-global asset/manifest caches return consistent values and are
     keyed correctly (manifest by mtime, asset fingerprint by a TTL). The
     read-modify-write sections are synchronous (no `await`), so in
     single-threaded asyncio they cannot interleave — no lock needed.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT))

from custom_components.quizify.server import views  # noqa: E402
from custom_components.quizify.server import websocket as ws_mod  # noqa: E402
from custom_components.quizify.server.websocket import (  # noqa: E402
    QuizifyWebSocketHandler,
)


class _FakeRuntime:
    def __init__(self, tmp_path: Path) -> None:
        self.data_dir = tmp_path


class _FakeWS:
    """Stand-in for a WebSocketResponse — only its identity matters here."""


@pytest.fixture
def handler(tmp_path: Path) -> QuizifyWebSocketHandler:
    return QuizifyWebSocketHandler(
        runtime=_FakeRuntime(tmp_path),
        game_state_provider=lambda: None,
    )


def _freeze_clock(monkeypatch: pytest.MonkeyPatch) -> dict:
    """Make the rate limiter read a controllable clock instead of the loop."""
    clock = {"t": 0.0}
    monkeypatch.setattr(
        ws_mod.asyncio,
        "get_event_loop",
        lambda: SimpleNamespace(time=lambda: clock["t"]),
    )
    return clock


# ---------------------------------------------------------------------------
# 1. Rate-limit map stays bounded
# ---------------------------------------------------------------------------


class TestRateLimitBounded:
    def test_forget_removes_the_entry(
        self, handler: QuizifyWebSocketHandler, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """After a connection is forgotten (handle()'s finally), it leaves no
        residue in the rate limiter — so the dict is bounded by *live*
        connections, never unbounded (#169.1)."""
        _freeze_clock(monkeypatch)
        buckets = handler._rate_limiter._buckets
        ws = _FakeWS()
        assert handler._check_rate_limit(ws) is True
        assert id(ws) in buckets

        handler._forget_rate_limit(ws)
        assert id(ws) not in buckets
        assert buckets == {}

    def test_window_prunes_old_timestamps(
        self, handler: QuizifyWebSocketHandler, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A long-lived connection's timestamp list does not grow without
        bound — entries older than the window are dropped each call."""
        clock = _freeze_clock(monkeypatch)
        limiter = handler._rate_limiter
        ws = _FakeWS()
        # Fill the window.
        for _ in range(limiter._max):
            handler._check_rate_limit(ws)
        assert len(limiter._buckets[id(ws)]) == limiter._max

        # Jump past the window — the next check prunes everything stale.
        clock["t"] += limiter._window + 1
        handler._check_rate_limit(ws)
        assert len(limiter._buckets[id(ws)]) == 1

    def test_over_limit_is_rejected_and_not_recorded(
        self, handler: QuizifyWebSocketHandler, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Hitting the cap returns False and does not append the rejected
        message (so a flood can't inflate the list past the cap)."""
        _freeze_clock(monkeypatch)
        limiter = handler._rate_limiter
        ws = _FakeWS()
        for _ in range(limiter._max):
            assert handler._check_rate_limit(ws) is True
        # One past the cap — rejected.
        assert handler._check_rate_limit(ws) is False
        assert handler._check_rate_limit(ws) is False
        assert len(limiter._buckets[id(ws)]) == limiter._max

    def test_distinct_connections_are_independent(
        self, handler: QuizifyWebSocketHandler, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Forgetting one connection does not affect another's state."""
        _freeze_clock(monkeypatch)
        buckets = handler._rate_limiter._buckets
        a, b = _FakeWS(), _FakeWS()
        handler._check_rate_limit(a)
        handler._check_rate_limit(b)
        assert len(buckets) == 2

        handler._forget_rate_limit(a)
        assert id(a) not in buckets
        assert id(b) in buckets


# ---------------------------------------------------------------------------
# 3a. Manifest version cache — consistent + mtime-keyed
# ---------------------------------------------------------------------------


class TestManifestCacheConsistent:
    """The disk re-read now lives in ``_refresh_live_version`` (run OFF the
    event loop, #343); ``_get_live_version`` is a pure in-memory reader. These
    guard that the off-loop refresh still picks up an mtime change (so the
    cache-buster updates after a direct-rsync deploy without an HA restart) and
    that the in-memory reader returns the refreshed value with zero I/O."""

    def test_repeated_reads_are_consistent(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        manifest = tmp_path / "manifest.json"
        manifest.write_text('{"version": "9.9.9"}', encoding="utf-8")
        monkeypatch.setattr(views, "_MANIFEST_PATH", manifest)
        monkeypatch.setattr(views, "_MANIFEST_CACHE", None)
        monkeypatch.setattr(views, "_LIVE_VERSION", None)

        assert views._refresh_live_version() == "9.9.9"
        # The in-memory reader now serves the refreshed value, no disk access.
        first = views._get_live_version("fallback")
        second = views._get_live_version("fallback")
        assert first == second == "9.9.9"

    def test_cache_refreshes_on_mtime_change(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        manifest = tmp_path / "manifest.json"
        manifest.write_text('{"version": "1.0.0"}', encoding="utf-8")
        os.utime(manifest, ns=(1_000_000_000, 1_000_000_000))
        monkeypatch.setattr(views, "_MANIFEST_PATH", manifest)
        monkeypatch.setattr(views, "_MANIFEST_CACHE", None)
        monkeypatch.setattr(views, "_LIVE_VERSION", None)
        views._refresh_live_version()
        assert views._get_live_version("fb") == "1.0.0"

        # New content + a distinct mtime → the cache key changes → re-read.
        manifest.write_text('{"version": "2.0.0"}', encoding="utf-8")
        os.utime(manifest, ns=(2_000_000_000, 2_000_000_000))
        views._refresh_live_version()
        assert views._get_live_version("fb") == "2.0.0"

    def test_missing_file_falls_back(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(views, "_MANIFEST_PATH", tmp_path / "nope.json")
        monkeypatch.setattr(views, "_MANIFEST_CACHE", None)
        monkeypatch.setattr(views, "_LIVE_VERSION", None)
        # Refresh returns None (file unreadable) and leaves the in-memory value
        # unset, so the reader serves the supplied fallback.
        assert views._refresh_live_version() is None
        assert views._get_live_version("the-fallback") == "the-fallback"

    @pytest.mark.asyncio
    async def test_async_refresh_runs_off_loop_and_picks_up_bump(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """``refresh_live_version`` must offload the blocking read to the
        runtime's executor (so the loop stays clean, #343) AND pick up a bumped
        manifest, proving the no-restart cache-buster still works."""
        manifest = tmp_path / "manifest.json"
        manifest.write_text('{"version": "1.0.0"}', encoding="utf-8")
        os.utime(manifest, ns=(1_000_000_000, 1_000_000_000))
        monkeypatch.setattr(views, "_MANIFEST_PATH", manifest)
        monkeypatch.setattr(views, "_MANIFEST_CACHE", None)
        monkeypatch.setattr(views, "_LIVE_VERSION", None)

        ran_via_executor = {"n": 0}

        async def _run_in_executor(func, *args):
            ran_via_executor["n"] += 1
            return func(*args)

        runtime = SimpleNamespace(run_in_executor=_run_in_executor)

        await views.refresh_live_version(runtime)
        assert ran_via_executor["n"] == 1
        assert views._get_live_version("fb") == "1.0.0"

        # Simulate a direct-rsync deploy: manifest bumped on disk, no reload.
        manifest.write_text('{"version": "1.0.1"}', encoding="utf-8")
        os.utime(manifest, ns=(2_000_000_000, 2_000_000_000))
        await views.refresh_live_version(runtime)
        assert views._get_live_version("fb") == "1.0.1"


# ---------------------------------------------------------------------------
# 3b. Asset-fingerprint cache — consistent within its TTL
# ---------------------------------------------------------------------------


class TestAssetFingerprintCacheConsistent:
    def test_repeated_calls_consistent_within_ttl(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(views, "_ASSET_FP_CACHE", None)
        first = views._get_asset_version("1.2.7")
        second = views._get_asset_version("1.2.7")
        assert first == second
        assert first.startswith("1.2.7-")
