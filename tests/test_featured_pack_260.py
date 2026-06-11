"""Tests for the Featured Spotlight pack picker (#260 coverage gap).

``featured_pack_view`` rotates daily between two selection logics (most-played
on even days, most-difficult on odd days, per Markus msg 283) and falls back to
a hardcoded geography default when no analytics/stats data exists yet. These
tests cover the day rotation, both selection logics, the language filter, and
the empty-bank / fresh-install fallback paths — none of which had coverage.
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from types import SimpleNamespace

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT))

import custom_components.quizify.server.views as views  # noqa: E402
from custom_components.quizify.server.context import APP_CTX_KEY  # noqa: E402
from custom_components.quizify.server.views import featured_pack_view  # noqa: E402


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class _FakeRuntime:
    """Runs the executor inline — the view only uses it for the pack JSON read."""

    async def run_in_executor(self, func, *args):  # noqa: ANN001, ANN002
        return func(*args)


class _FakeQuestion:
    def __init__(self, qid: str) -> None:
        self.id = qid


class _FakeBank:
    def __init__(self, questions_dir: Path, pack_versions: dict, categories: dict) -> None:
        self._questions_dir = questions_dir
        self._pack_versions = pack_versions
        self._categories = categories

    def get_pack_versions(self) -> dict:
        return dict(self._pack_versions)


class _FakeAnalytics:
    def __init__(self, category_stats: list) -> None:
        self._category_stats = category_stats

    def compute_metrics(self, _period: str) -> dict:
        return {"category_stats": self._category_stats}


class _FakeQuestionStats:
    def __init__(self, questions: dict) -> None:
        self._data = {"questions": questions}


class _FakeRequest:
    def __init__(self, ctx, query: dict) -> None:  # noqa: ANN001
        self.app = {APP_CTX_KEY: ctx}
        self.query = query


def _write_pack(qdir: Path, slug: str, theme: str, name: str) -> None:
    qdir.mkdir(parents=True, exist_ok=True)
    (qdir / f"{slug}.json").write_text(json.dumps({"theme": theme, "name": name}))


def _call(ctx, lang: str = "de") -> dict:
    req = _FakeRequest(ctx, {"lang": lang})
    resp = asyncio.run(featured_pack_view(req))  # type: ignore[arg-type]
    return json.loads(resp.body)


def _force_day(monkeypatch, yday: int) -> None:
    """Pin datetime.now().timetuple().tm_yday so the even/odd rotation is
    deterministic. The view does a local ``import datetime as _dt``, which
    resolves the real stdlib module — patch its ``datetime`` attribute."""
    import datetime as _real_dt

    class _FixedDateTime(_real_dt.datetime):
        @classmethod
        def now(cls, tz=None):  # noqa: ANN001
            return _real_dt.datetime(2026, 1, 1) + _real_dt.timedelta(days=yday - 1)

    monkeypatch.setattr(_real_dt, "datetime", _FixedDateTime)


# ---------------------------------------------------------------------------
# Empty / fallback paths
# ---------------------------------------------------------------------------


def test_no_bank_returns_empty() -> None:
    """No question bank loaded → empty object (admin UI hides the spotlight)."""
    ctx = SimpleNamespace(game=None, analytics=None, question_stats=None,
                          runtime=_FakeRuntime())
    assert _call(ctx) == {}


def test_no_packs_for_language_returns_empty(tmp_path: Path) -> None:
    """All packs are German; an ``?lang=en`` request finds none → empty."""
    bank = _FakeBank(
        tmp_path,
        {"geographie": {"language": "de", "question_count": 10, "name": "Geo"}},
        {"geographie": []},
    )
    ctx = SimpleNamespace(game=SimpleNamespace(_question_bank=bank),
                          analytics=None, question_stats=None, runtime=_FakeRuntime())
    assert _call(ctx, lang="en") == {}


def test_fresh_install_falls_back_to_default(tmp_path: Path, monkeypatch) -> None:  # noqa: ANN001
    """No analytics + no stats → fall back to the geography default with the
    'default' logic and family-friendly subtitle."""
    _force_day(monkeypatch, yday=2)  # even → most-played branch, but no data
    _write_pack(tmp_path, "geographie", "geography", "Geographie")
    bank = _FakeBank(
        tmp_path,
        {
            "geographie": {"language": "de", "question_count": 30, "name": "Geographie"},
            "tiere": {"language": "de", "question_count": 20, "name": "Tiere"},
        },
        {"geographie": [], "tiere": []},
    )
    ctx = SimpleNamespace(game=SimpleNamespace(_question_bank=bank),
                          analytics=None, question_stats=None, runtime=_FakeRuntime())
    out = _call(ctx)
    assert out["value"] == "geographie"
    assert out["logic"] == "default"
    assert "Familienfreundlich" in out["meta"]
    assert out["title"].startswith("🌍")  # geography theme icon


# ---------------------------------------------------------------------------
# Selection logics
# ---------------------------------------------------------------------------


def test_most_played_picks_top_category_on_even_day(tmp_path: Path, monkeypatch) -> None:  # noqa: ANN001
    """Even day → most-played logic selects the highest games_played pack."""
    _force_day(monkeypatch, yday=2)  # even
    _write_pack(tmp_path, "tiere", "nature", "Tiere")
    bank = _FakeBank(
        tmp_path,
        {
            "geographie": {"language": "de", "question_count": 30, "name": "Geographie"},
            "tiere": {"language": "de", "question_count": 20, "name": "Tiere"},
        },
        {"geographie": [], "tiere": []},
    )
    analytics = _FakeAnalytics([
        {"category": "geographie", "games_played": 1},
        {"category": "tiere", "games_played": 9},  # most played → winner
    ])
    ctx = SimpleNamespace(game=SimpleNamespace(_question_bank=bank),
                          analytics=analytics, question_stats=None, runtime=_FakeRuntime())
    out = _call(ctx)
    assert out["value"] == "tiere"
    assert out["logic"] == "most-played"
    assert "Beliebt diese Woche" in out["meta"]


def test_most_difficult_picks_lowest_correct_rate_on_odd_day(tmp_path: Path, monkeypatch) -> None:  # noqa: ANN001
    """Odd day → most-difficult logic selects the pack with the lowest
    correct/shown ratio (only packs with >= _FEATURED_MIN_SHOWN shown count
    qualify)."""
    _force_day(monkeypatch, yday=1)  # odd
    _write_pack(tmp_path, "wissen", "science", "Wissen")
    bank = _FakeBank(
        tmp_path,
        {
            "geographie": {"language": "de", "question_count": 30, "name": "Geographie"},
            "wissen": {"language": "de", "question_count": 20, "name": "Wissen"},
        },
        {
            "geographie": [_FakeQuestion("g1")],
            "wissen": [_FakeQuestion("w1")],
        },
    )
    qstats = _FakeQuestionStats({
        # geographie: 18/20 correct = 0.90
        "g1": {"shown_count": 20, "correct_count": 18},
        # wissen: 3/20 correct = 0.15 → hardest → winner
        "w1": {"shown_count": 20, "correct_count": 3},
    })
    ctx = SimpleNamespace(game=SimpleNamespace(_question_bank=bank),
                          analytics=None, question_stats=qstats, runtime=_FakeRuntime())
    out = _call(ctx)
    assert out["value"] == "wissen"
    assert out["logic"] == "most-difficult"
    assert "Härteste Herausforderung" in out["meta"]


def test_most_difficult_below_min_shown_falls_back(tmp_path: Path, monkeypatch) -> None:  # noqa: ANN001
    """On an odd day, if no pack reaches _FEATURED_MIN_SHOWN aggregate shown
    count, the difficult logic finds nothing and we fall back to default."""
    _force_day(monkeypatch, yday=1)  # odd
    _write_pack(tmp_path, "geographie", "geography", "Geographie")
    bank = _FakeBank(
        tmp_path,
        {"geographie": {"language": "de", "question_count": 30, "name": "Geographie"}},
        {"geographie": [_FakeQuestion("g1")]},
    )
    qstats = _FakeQuestionStats({"g1": {"shown_count": 2, "correct_count": 1}})  # < 10
    ctx = SimpleNamespace(game=SimpleNamespace(_question_bank=bank),
                          analytics=None, question_stats=qstats, runtime=_FakeRuntime())
    out = _call(ctx)
    assert out["value"] == "geographie"
    assert out["logic"] == "default"
