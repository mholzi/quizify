"""Perf regressions for pack_versions + analytics metrics (#417, #418).

#417: ``pack_versions_view`` (``GET /api/quizify/packs``) used to unconditionally
await ``run_in_executor(bank.load_all_categories)`` on every request even though
``load_all_categories`` is a guarded no-op once the preloaded bank is loaded.
The fix mirrors the ``pack_update_check_view`` guard so the executor hop is
skipped when the bank is already loaded.

#418: ``compute_metrics`` re-scanned up to 1000 ``GameRecord``s on every
analytics-data read and every even-day featured-pack request, though the game
history only changes at game end / prune. The fix memoizes the result per
period keyed on ``(total_games, last ended_at)`` and clears the cache in
``add_game``.
"""

from __future__ import annotations

import asyncio
import json
import sys
import time
from pathlib import Path
from types import SimpleNamespace

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT))

from custom_components.quizify.analytics import QuizifyAnalytics  # noqa: E402
from custom_components.quizify.server import views  # noqa: E402
from custom_components.quizify.server.context import APP_CTX_KEY  # noqa: E402


# --------------------------------------------------------------------------- #
# #417 — pack_versions_view executor guard
# --------------------------------------------------------------------------- #
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


def _call_packs(ctx) -> dict:  # noqa: ANN001
    resp = asyncio.run(views.pack_versions_view(_FakeRequest(ctx)))  # type: ignore[arg-type]
    return json.loads(resp.body)


def test_pack_versions_loaded_bank_skips_executor_reload() -> None:
    """A preloaded bank serves the packs view without an executor reload."""
    bank = _FakeBank(
        {"animals": {"version": "1.0.0", "name": "Animals"}}, loaded=True
    )
    ctx = _make_ctx(bank)

    out = _call_packs(ctx)

    assert bank.load_calls == 0
    assert ctx.runtime.exec_calls == 0
    # Response shape preserved + per-request season annotation added.
    assert out["animals"]["version"] == "1.0.0"
    assert out["animals"]["is_seasonal"] is False
    assert out["animals"]["season_label"] == ""


def test_pack_versions_unloaded_bank_loads_once_via_executor() -> None:
    """A cold bank is loaded through the executor exactly once."""
    bank = _FakeBank(
        {"animals": {"version": "1.0.0", "name": "Animals"}}, loaded=False
    )
    ctx = _make_ctx(bank)

    _call_packs(ctx)

    assert bank.load_calls == 1
    assert ctx.runtime.exec_calls == 1


# --------------------------------------------------------------------------- #
# #418 — compute_metrics memoization
# --------------------------------------------------------------------------- #
class _AnalyticsRuntime:
    def __init__(self, data_dir: Path) -> None:
        self.data_dir = data_dir

    async def run_in_executor(self, fn, *args):  # noqa: ANN001, ANN002
        return fn(*args)


def _game(game_id: str, ended_at: int, player_count: int = 3) -> dict:
    return {
        "game_id": game_id,
        "started_at": ended_at - 600,
        "ended_at": ended_at,
        "duration_seconds": 600,
        "player_count": player_count,
        "category": "animals",
        "question_count": 10,
        "rounds_played": 10,
        "average_score": 100.0,
        "difficulty": "medium",
        "player_scores": {"Alice": 120, "Bob": 80},
        "winner": "Alice",
    }


def _make_analytics(tmp_path: Path) -> QuizifyAnalytics:
    return QuizifyAnalytics(_AnalyticsRuntime(tmp_path))


def test_compute_metrics_returns_cached_result_until_add_game(tmp_path) -> None:
    """Repeat reads reuse the memoized result; add_game invalidates it."""
    analytics = _make_analytics(tmp_path)
    # Avoid scheduling real save tasks — we only exercise the cache path.
    analytics.schedule_save = lambda: None  # type: ignore[assignment]

    now = int(time.time())
    analytics._data["games"] = [_game("g1", now - 100), _game("g2", now - 50)]

    first = analytics.compute_metrics("30d")
    second = analytics.compute_metrics("30d")

    # Same fingerprint → the exact same cached object is returned (no recompute).
    assert second is first
    assert first["total_games"] == 2

    # Mutate an existing record *without* changing the fingerprint
    # (len + last ended_at unchanged). A memoized read stays stale.
    analytics._data["games"][-1]["player_count"] = 99
    stale = analytics.compute_metrics("30d")
    assert stale is first  # still the cached object, mutation not reflected

    # add_game clears the cache → the next read recomputes from live data.
    asyncio.run(analytics.add_game(_game("g3", now - 10)))
    fresh = analytics.compute_metrics("30d")

    assert fresh is not first
    assert fresh["total_games"] == 3
    # The previously-hidden mutation now flows through (99 + 3 + 3).
    assert fresh["avg_players_per_game"] == round((99 + 3 + 3) / 3, 1)


def test_compute_metrics_expires_on_hourly_bucket_rollover(
    tmp_path, monkeypatch
) -> None:
    """#473: with no new game, advancing wall-clock past an hourly bucket
    boundary must recompute so the windowed metrics don't freeze on the
    cache-write hour (aged-out games would otherwise keep counting)."""
    import custom_components.quizify.analytics as analytics_mod

    analytics = _make_analytics(tmp_path)
    analytics.schedule_save = lambda: None  # type: ignore[assignment]

    # Pin the clock to a bucket boundary for deterministic arithmetic.
    base = (int(time.time()) // 3600) * 3600
    clock = {"now": base}
    monkeypatch.setattr(analytics_mod.time, "time", lambda: clock["now"])

    day = 86400
    # Two games: one comfortably inside the 7d window, one that will age out
    # of it once the clock advances by ~1 day past the 7-day cutoff.
    fresh_game = _game("fresh", base - day)  # ~1 day old
    edge_game = _game("edge", base - 6 * day)  # ~6 days old, inside 7d
    analytics._data["games"] = [edge_game, fresh_game]

    first = analytics.compute_metrics("7d")
    assert first["total_games"] == 2
    # Same hour → cached object reused (no recompute).
    assert analytics.compute_metrics("7d") is first

    # Advance the wall clock by 2 days: crosses the hourly bucket boundary AND
    # pushes edge_game (now ~8 days old) out of the 7d window. Fingerprint's
    # hourly bucket changes even though games (len + last ended_at) did not.
    clock["now"] = base + 2 * day
    fresh = analytics.compute_metrics("7d")

    assert fresh is not first  # recomputed, not frozen
    assert fresh["total_games"] == 1  # aged-out edge_game no longer counted


def test_compute_metrics_cache_is_per_period(tmp_path) -> None:
    """Distinct periods are cached independently and stay correct."""
    analytics = _make_analytics(tmp_path)
    analytics.schedule_save = lambda: None  # type: ignore[assignment]

    now = int(time.time())
    analytics._data["games"] = [_game("g1", now - 100)]

    m30 = analytics.compute_metrics("30d")
    m7 = analytics.compute_metrics("7d")

    assert m30["period"] == "30d"
    assert m7["period"] == "7d"
    # Re-reads hit the cache per period.
    assert analytics.compute_metrics("30d") is m30
    assert analytics.compute_metrics("7d") is m7
