"""Featured-pack rotation against *real* stats services (issue #313).

``test_seasonal_packs_276.py`` exercises ``featured_pack_view`` only with
``SimpleNamespace`` stubs for analytics/question_stats. This file wires the
real :class:`QuizifyAnalytics` and :class:`QuestionStatsService` (backed by a
tmp data dir) through the view so the most-played and most-difficult rotation
legs are pinned end to end — a regression in ``compute_metrics`` /
``aggregate_for_questions`` or the view's consumption of them now fails here.

The view picks its rotation leg from ``datetime.now().tm_yday`` parity and its
season check from ``date.today()``; both are injected via a fixed-date
``datetime`` module shim so the test is clock-independent and never seasonal.
"""

from __future__ import annotations

import asyncio
import datetime as _real_dt
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT))

from custom_components.quizify.analytics import QuizifyAnalytics  # noqa: E402
from custom_components.quizify.game.questions import (  # noqa: E402
    Answer,
    Question,
)
from custom_components.quizify.question_stats import (  # noqa: E402
    QuestionStatsService,
)
from custom_components.quizify.runtime import StandaloneRuntime  # noqa: E402
from custom_components.quizify.server.context import APP_CTX_KEY  # noqa: E402
from custom_components.quizify.server.views import (  # noqa: E402
    featured_pack_view,
)


class _Bank:
    """Minimal QuestionBank stand-in exposing only what the view reads."""

    def __init__(self, pack_versions: dict, categories: dict, qdir: Path) -> None:
        self._pv = pack_versions
        self._cats = categories
        self._qdir = qdir

    @property
    def categories(self) -> dict:
        return dict(self._cats)

    @property
    def questions_dir(self) -> Path:
        return self._qdir

    def get_pack_versions(self) -> dict:
        return {k: dict(v) for k, v in self._pv.items()}

    def load_all_categories(self) -> dict:
        return dict(self._cats)


class _Request:
    def __init__(self, ctx, query: dict) -> None:
        self.app = {APP_CTX_KEY: ctx}
        self.query = query


def _freeze_datetime(monkeypatch, year: int, month: int, day: int) -> None:
    """Pin BOTH ``datetime.date.today()`` (season check) and
    ``datetime.datetime.now()`` (day-rotation parity) to a fixed date.

    The view does ``import datetime as _dt`` locally, so patching the real
    module's ``date``/``datetime`` attributes with fixed subclasses flows the
    injected date through. ``tm_yday`` parity of the fixed date decides the
    rotation leg.
    """
    fixed_date = _real_dt.date(year, month, day)
    fixed_dt = _real_dt.datetime(year, month, day, 12, 0, 0)

    class _FixedDate(_real_dt.date):
        @classmethod
        def today(cls):
            return fixed_date

    class _FixedDateTime(_real_dt.datetime):
        @classmethod
        def now(cls, tz=None):
            return fixed_dt

    monkeypatch.setattr(_real_dt, "date", _FixedDate)
    monkeypatch.setattr(_real_dt, "datetime", _FixedDateTime)


def _yday_with_parity(even: bool) -> tuple[int, int, int]:
    """Return a (year, month, day) far from any season window whose day-of-year
    parity matches ``even`` (even → most-played, odd → most-difficult)."""
    # Feb is outside the seasonal packs used here. Feb 1 = yday 32 (odd),
    # Feb 2 = yday 33 (even-day-of-month but yday 33 odd)... compute directly.
    base = _real_dt.date(2026, 2, 1)
    for delta in range(0, 20):
        d = base + _real_dt.timedelta(days=delta)
        if (d.timetuple().tm_yday % 2 == 0) == even:
            return (d.year, d.month, d.day)
    raise AssertionError("no matching date found")  # pragma: no cover


def _make_ctx(bank, analytics, question_stats):
    return SimpleNamespace(
        game=SimpleNamespace(question_bank=bank),
        analytics=analytics,
        question_stats=question_stats,
        runtime=SimpleNamespace(
            run_in_executor=lambda func, *a: _Immediate(func(*a)),
        ),
    )


class _Immediate:
    """Awaitable wrapper so the view's ``await runtime.run_in_executor`` works
    synchronously in-test."""

    def __init__(self, value):
        self._value = value

    def __await__(self):
        async def _coro():
            return self._value

        return _coro().__await__()


def _featured(ctx, lang: str = "de") -> dict:
    return json.loads(asyncio.run(featured_pack_view(_Request(ctx, {"lang": lang}))).body)


def _pack_files(qdir: Path, slugs: list[str]) -> None:
    qdir.mkdir(parents=True, exist_ok=True)
    for slug in slugs:
        (qdir / f"{slug}.json").write_text(
            json.dumps({"theme": slug, "name": slug.title()})
        )


def _pv(slug: str, count: int = 20) -> dict:
    return {"language": "de", "question_count": count, "name": slug.title()}


# ---------------------------------------------------------------------------
# most-played — real QuizifyAnalytics
# ---------------------------------------------------------------------------


def test_featured_most_played_real_analytics(tmp_path: Path, monkeypatch) -> None:
    """An even day-of-year picks the most-played leg; a real analytics store
    seeded with games for 'geschichte' must surface it over the default."""
    y, m, d = _yday_with_parity(even=True)
    _freeze_datetime(monkeypatch, y, m, d)

    qdir = tmp_path / "questions"
    _pack_files(qdir, ["geographie", "geschichte"])
    bank = _Bank({"geographie": _pv("geographie"), "geschichte": _pv("geschichte")},
                 {"geographie": [], "geschichte": []}, qdir)

    runtime = StandaloneRuntime(tmp_path / "data")
    analytics = QuizifyAnalytics(runtime)
    asyncio.run(analytics.load())
    # Seed two real completed games on 'geschichte' (>= _FEATURED_MIN_PLAYS).
    for i in range(2):
        asyncio.run(analytics.record_game(
            game_id=f"g{i}", category="geschichte", difficulty="medium",
            num_rounds=10, players={"Alice": 50 + i, "Bob": 30},
            duration_seconds=300,
        ))

    ctx = _make_ctx(bank, analytics, None)
    out = _featured(ctx)
    assert out["logic"] == "most-played"
    assert out["value"] == "geschichte"


def test_featured_most_played_below_threshold_falls_back(
    tmp_path: Path, monkeypatch
) -> None:
    """With NO recorded games the most-played leg finds nothing above the
    threshold and the view falls back to the default pack."""
    y, m, d = _yday_with_parity(even=True)
    _freeze_datetime(monkeypatch, y, m, d)

    qdir = tmp_path / "questions"
    _pack_files(qdir, ["geographie", "geschichte"])
    bank = _Bank({"geographie": _pv("geographie"), "geschichte": _pv("geschichte")},
                 {"geographie": [], "geschichte": []}, qdir)

    runtime = StandaloneRuntime(tmp_path / "data")
    analytics = QuizifyAnalytics(runtime)
    asyncio.run(analytics.load())  # empty store

    ctx = _make_ctx(bank, analytics, None)
    out = _featured(ctx)
    assert out["logic"] == "default"
    assert out["value"] == "geographie"  # _FEATURED_DEFAULT_DE


# ---------------------------------------------------------------------------
# most-difficult — real QuestionStatsService
# ---------------------------------------------------------------------------


def _q(qid: str) -> Question:
    return Question(
        id=qid, question="?",
        answers=[Answer(text="a", correct=True), Answer(text="b", correct=False),
                 Answer(text="c", correct=False)],
    )


def test_featured_most_difficult_real_stats(tmp_path: Path, monkeypatch) -> None:
    """An odd day-of-year picks the most-difficult leg; a real
    QuestionStatsService where 'hard' has the lowest correct-rate (and enough
    shown samples) must surface it."""
    y, m, d = _yday_with_parity(even=False)
    _freeze_datetime(monkeypatch, y, m, d)

    qdir = tmp_path / "questions"
    _pack_files(qdir, ["easy", "hard"])
    easy_qs = [_q("e1"), _q("e2")]
    hard_qs = [_q("h1"), _q("h2")]
    bank = _Bank(
        {"easy": _pv("easy"), "hard": _pv("hard")},
        {"easy": easy_qs, "hard": hard_qs},
        qdir,
    )

    runtime = StandaloneRuntime(tmp_path / "data")
    stats = QuestionStatsService(runtime)
    asyncio.run(stats.load())
    # Easy pack: mostly correct. Hard pack: mostly wrong. >= _FEATURED_MIN_SHOWN
    # (10) submissions aggregated per pack.
    for q in easy_qs:
        stats.record_round(q.id, [(True, 1.0)] * 6)   # 12 shown, 12 correct
    for q in hard_qs:
        stats.record_round(q.id, [(True, 1.0), (False, 1.0), (False, 1.0),
                                  (False, 1.0), (False, 1.0), (False, 1.0)])  # 12 shown, 2 correct

    ctx = _make_ctx(bank, None, stats)
    out = _featured(ctx)
    assert out["logic"] == "most-difficult"
    assert out["value"] == "hard"
