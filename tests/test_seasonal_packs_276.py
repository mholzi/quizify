"""Tests for seasonal packs with auto-surfacing (#276).

Covers four layers, all with an *injected* date so the suite never depends on
the real clock:

1. The :mod:`seasons` helper: parsing/validation, the active-window check
   (normal + wrap-around), multi-active deterministic pick, and back-compat
   (no ``season`` field).
2. The pack loader: a ``season`` field on a pack JSON is parsed onto the pack
   metadata; a pack without it is unchanged.
3. ``featured_pack_view``: an in-season pack is pinned over the day-rotation
   logic; outside the window behaviour is exactly as before.
4. ``pack_versions_view``: per-pack ``is_seasonal`` / ``season_label``
   annotation for the picker badge.

Dates are injected two ways: the pure helpers take a ``date`` argument
directly; the views call ``datetime.date.today()`` via a local
``import datetime``, so those tests monkeypatch ``datetime.date`` with a
fixed-``today`` subclass.
"""

from __future__ import annotations

import asyncio
import json
import sys
from datetime import date
from pathlib import Path
from types import SimpleNamespace

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT))

from custom_components.quizify.game import seasons  # noqa: E402
from custom_components.quizify.game.questions import QuestionBank  # noqa: E402
from custom_components.quizify.game.seasons import (  # noqa: E402
    Season,
    is_in_season,
    parse_season,
    pick_active_season,
)
from custom_components.quizify.server.context import APP_CTX_KEY  # noqa: E402
from custom_components.quizify.server.views import (  # noqa: E402
    featured_pack_view,
    pack_versions_view,
)


# ---------------------------------------------------------------------------
# 1. seasons helper — parsing / validation
# ---------------------------------------------------------------------------


def test_parse_season_valid_window() -> None:
    s = parse_season({"start": "12-01", "end": "12-26", "label": "🎄 Weihnachten"})
    assert s == Season(start=(12, 1), end=(12, 26), label="🎄 Weihnachten")


def test_parse_season_none_is_back_compat() -> None:
    """A pack with no season field → None, never raises."""
    assert parse_season(None) is None


def test_parse_season_rejects_garbage() -> None:
    assert parse_season("not a dict") is None
    assert parse_season({"start": "13-01", "end": "12-26"}) is None  # month 13
    assert parse_season({"start": "12-32", "end": "12-26"}) is None  # day 32
    assert parse_season({"start": "12-01"}) is None                  # missing end
    assert parse_season({"start": "12/01", "end": "12-26"}) is None  # wrong sep


def test_parse_season_accepts_feb_29() -> None:
    s = parse_season({"start": "02-15", "end": "02-29", "label": "Leap"})
    assert s is not None and s.end == (2, 29)


def test_parse_season_missing_label_gets_generic() -> None:
    s = parse_season({"start": "01-01", "end": "01-05"})
    assert s is not None and s.label == "Seasonal"


# ---------------------------------------------------------------------------
# 1b. seasons helper — active-window check (injected date)
# ---------------------------------------------------------------------------


def test_is_in_season_normal_window() -> None:
    s = parse_season({"start": "06-01", "end": "07-31", "label": "Summer"})
    assert is_in_season(s, date(2026, 6, 1))    # inclusive start
    assert is_in_season(s, date(2026, 7, 1))    # middle
    assert is_in_season(s, date(2026, 7, 31))   # inclusive end
    assert not is_in_season(s, date(2026, 5, 31))
    assert not is_in_season(s, date(2026, 8, 1))


def test_is_in_season_is_year_agnostic() -> None:
    """Recurring window: same month-day surfaces in any year."""
    s = parse_season({"start": "10-25", "end": "11-01", "label": "🎃"})
    assert is_in_season(s, date(2026, 10, 31))
    assert is_in_season(s, date(2099, 10, 31))


def test_is_in_season_wrap_around_window() -> None:
    """A window spanning the new year (Dec 20 → Jan 6) is handled."""
    s = parse_season({"start": "12-20", "end": "01-06", "label": "🎄 Festtage"})
    assert is_in_season(s, date(2026, 12, 20))   # start edge
    assert is_in_season(s, date(2026, 12, 31))   # before new year
    assert is_in_season(s, date(2027, 1, 1))     # after new year
    assert is_in_season(s, date(2027, 1, 6))     # end edge
    assert not is_in_season(s, date(2027, 1, 7))
    assert not is_in_season(s, date(2026, 12, 19))
    assert not is_in_season(s, date(2026, 7, 1))  # mid-year, clearly outside


def test_is_in_season_none_always_false() -> None:
    assert not is_in_season(None, date(2026, 12, 25))


# ---------------------------------------------------------------------------
# 1c. seasons helper — multi-active deterministic pick
# ---------------------------------------------------------------------------


def test_pick_active_season_none_when_no_window_active() -> None:
    seasons_map = {
        "xmas": parse_season({"start": "12-01", "end": "12-26", "label": "🎄"}),
    }
    assert pick_active_season(seasons_map, date(2026, 7, 1)) is None


def test_pick_active_season_soonest_ending_wins() -> None:
    """Two overlapping windows → the one ending sooner is pinned."""
    seasons_map = {
        "broad": parse_season({"start": "06-01", "end": "08-31", "label": "Broad"}),
        "tight": parse_season({"start": "06-10", "end": "06-15", "label": "Tight"}),
    }
    assert pick_active_season(seasons_map, date(2026, 6, 12)) == "tight"


def test_pick_active_season_tiebreak_by_slug() -> None:
    """Identical end dates → stable alphabetical slug tie-break."""
    seasons_map = {
        "bbb": parse_season({"start": "06-01", "end": "06-30", "label": "B"}),
        "aaa": parse_season({"start": "06-01", "end": "06-30", "label": "A"}),
    }
    assert pick_active_season(seasons_map, date(2026, 6, 15)) == "aaa"


# ---------------------------------------------------------------------------
# 2. pack loader — season parsed onto metadata; back-compat preserved
# ---------------------------------------------------------------------------


def _write_pack(qdir: Path, slug: str, extra: dict) -> None:
    qdir.mkdir(parents=True, exist_ok=True)
    pack = {
        "name": slug.title(),
        "language": "de",
        "version": "1.0",
        "questions": [
            {
                "id": f"{slug}_1",
                "question": "Q?",
                "answers": [
                    {"text": "a", "correct": True},
                    {"text": "b", "correct": False},
                    {"text": "c", "correct": False},
                ],
            }
        ],
    }
    pack.update(extra)
    (qdir / f"{slug}.json").write_text(json.dumps(pack))


def test_loader_parses_season_onto_metadata(tmp_path: Path) -> None:
    _write_pack(
        tmp_path, "xmas",
        {"season": {"start": "12-01", "end": "12-26", "label": "🎄 Weihnachten"}},
    )
    bank = QuestionBank(tmp_path)
    bank.load_all_categories()
    meta = bank.get_pack_versions()["xmas"]
    assert meta["season"] == {
        "start": "12-01", "end": "12-26", "label": "🎄 Weihnachten",
    }


def test_loader_normalizes_unpadded_window(tmp_path: Path) -> None:
    _write_pack(tmp_path, "x", {"season": {"start": "1-5", "end": "1-9", "label": "L"}})
    bank = QuestionBank(tmp_path)
    bank.load_all_categories()
    assert bank.get_pack_versions()["x"]["season"]["start"] == "01-05"


def test_loader_pack_without_season_unchanged(tmp_path: Path) -> None:
    """Back-compat: no season field → no 'season' key on metadata."""
    _write_pack(tmp_path, "plain", {})
    bank = QuestionBank(tmp_path)
    bank.load_all_categories()
    assert "season" not in bank.get_pack_versions()["plain"]


def test_loader_drops_malformed_season(tmp_path: Path) -> None:
    """A malformed window is dropped (logged), pack still loads."""
    _write_pack(tmp_path, "bad", {"season": {"start": "99-99", "end": "12-26"}})
    bank = QuestionBank(tmp_path)
    bank.load_all_categories()
    meta = bank.get_pack_versions()["bad"]
    assert "season" not in meta
    assert meta["question_count"] == 1  # questions still loaded


# ---------------------------------------------------------------------------
# 3 + 4. views — date injected via a fixed-today datetime.date subclass
# ---------------------------------------------------------------------------


class _FakeRuntime:
    async def run_in_executor(self, func, *args):  # noqa: ANN001, ANN002
        return func(*args)


class _FakeBank:
    def __init__(self, qdir: Path, pack_versions: dict, categories: dict) -> None:
        self._qdir = qdir
        self._pack_versions = pack_versions
        self._categories = categories

    @property
    def categories(self) -> dict:
        return dict(self._categories)

    @property
    def questions_dir(self) -> Path:
        return self._qdir

    def get_pack_versions(self) -> dict:
        return {k: dict(v) for k, v in self._pack_versions.items()}

    def load_all_categories(self) -> dict:
        return dict(self._categories)


class _FakeRequest:
    def __init__(self, ctx, query: dict) -> None:  # noqa: ANN001
        self.app = {APP_CTX_KEY: ctx}
        self.query = query


def _freeze_today(monkeypatch, year: int, month: int, day: int) -> None:
    """Pin ``datetime.date.today()`` to a fixed date for the views.

    Both views do a local ``import datetime as _dt`` then call
    ``_dt.date.today()``; patching the real module's ``date`` attribute with a
    subclass overriding ``today`` makes the injected date flow through.
    """
    import datetime as _real_dt

    fixed = _real_dt.date(year, month, day)

    class _FixedDate(_real_dt.date):
        @classmethod
        def today(cls):
            return fixed

    monkeypatch.setattr(_real_dt, "date", _FixedDate)


def _write_pack_file(qdir: Path, slug: str, theme: str, name: str) -> None:
    qdir.mkdir(parents=True, exist_ok=True)
    (qdir / f"{slug}.json").write_text(json.dumps({"theme": theme, "name": name}))


def _featured(ctx, lang: str = "de") -> dict:
    req = _FakeRequest(ctx, {"lang": lang})
    return json.loads(asyncio.run(featured_pack_view(req)).body)


def test_featured_pins_in_season_pack(tmp_path: Path, monkeypatch) -> None:  # noqa: ANN001
    """During the season window the seasonal pack is pinned over the
    day-rotation logic — even though analytics would pick another pack."""
    _freeze_today(monkeypatch, 2026, 6, 12)  # inside 06-01..07-31
    _write_pack_file(tmp_path, "wm", "worldcup", "Weltmeisterschaft")
    _write_pack_file(tmp_path, "geographie", "geography", "Geographie")
    bank = _FakeBank(
        tmp_path,
        {
            "geographie": {"language": "de", "question_count": 30, "name": "Geographie"},
            "wm": {
                "language": "de", "question_count": 20, "name": "Weltmeisterschaft",
                "season": {"start": "06-01", "end": "07-31", "label": "⚽ WM-Saison"},
            },
        },
        {"geographie": [], "wm": []},
    )
    # Analytics strongly favours geographie — the seasonal pin must override it.
    analytics = SimpleNamespace(
        compute_metrics=lambda _p: {
            "category_stats": [{"category": "geographie", "games_played": 999}]
        }
    )
    ctx = SimpleNamespace(game=SimpleNamespace(question_bank=bank),
                          analytics=analytics, question_stats=None, runtime=_FakeRuntime())
    out = _featured(ctx)
    assert out["value"] == "wm"
    assert out["logic"] == "seasonal"
    assert out["is_seasonal"] is True
    assert out["season_label"] == "⚽ WM-Saison"
    assert "⚽ WM-Saison" in out["meta"]


def test_featured_outside_season_is_unchanged(tmp_path: Path, monkeypatch) -> None:  # noqa: ANN001
    """Outside every window the rotation behaves exactly as before — the
    seasonal pack does NOT surface and is_seasonal is False."""
    _freeze_today(monkeypatch, 2026, 2, 1)  # outside 06-01..07-31; even-ish day
    _write_pack_file(tmp_path, "geographie", "geography", "Geographie")
    bank = _FakeBank(
        tmp_path,
        {
            "geographie": {"language": "de", "question_count": 30, "name": "Geographie"},
            "wm": {
                "language": "de", "question_count": 20, "name": "Weltmeisterschaft",
                "season": {"start": "06-01", "end": "07-31", "label": "⚽ WM-Saison"},
            },
        },
        {"geographie": [], "wm": []},
    )
    ctx = SimpleNamespace(game=SimpleNamespace(question_bank=bank),
                          analytics=None, question_stats=None, runtime=_FakeRuntime())
    out = _featured(ctx)
    assert out["logic"] != "seasonal"
    assert out["is_seasonal"] is False
    assert out["season_label"] == ""
    assert out["value"] == "geographie"  # falls back to default as before


def test_featured_wrap_around_window_pins(tmp_path: Path, monkeypatch) -> None:  # noqa: ANN001
    """A wrap-around window pins on a date after the new year."""
    _freeze_today(monkeypatch, 2027, 1, 3)  # inside 12-20..01-06
    _write_pack_file(tmp_path, "xmas", "worldcup", "Festtage")
    bank = _FakeBank(
        tmp_path,
        {
            "xmas": {
                "language": "de", "question_count": 12, "name": "Festtage",
                "season": {"start": "12-20", "end": "01-06", "label": "🎄 Festtage"},
            },
        },
        {"xmas": []},
    )
    ctx = SimpleNamespace(game=SimpleNamespace(question_bank=bank),
                          analytics=None, question_stats=None, runtime=_FakeRuntime())
    out = _featured(ctx)
    assert out["value"] == "xmas"
    assert out["is_seasonal"] is True


def test_pack_versions_view_annotates_in_season(tmp_path: Path, monkeypatch) -> None:  # noqa: ANN001
    """The picker endpoint flags in-season packs for badging."""
    _freeze_today(monkeypatch, 2026, 6, 12)
    bank = _FakeBank(
        tmp_path,
        {
            "geographie": {"language": "de", "question_count": 30, "name": "Geographie"},
            "wm": {
                "language": "de", "question_count": 20, "name": "Weltmeisterschaft",
                "season": {"start": "06-01", "end": "07-31", "label": "⚽ WM-Saison"},
            },
        },
        {"geographie": [], "wm": []},
    )
    ctx = SimpleNamespace(game=SimpleNamespace(question_bank=bank), runtime=_FakeRuntime())
    req = _FakeRequest(ctx, {})
    out = json.loads(asyncio.run(pack_versions_view(req)).body)
    assert out["wm"]["is_seasonal"] is True
    assert out["wm"]["season_label"] == "⚽ WM-Saison"
    assert out["geographie"]["is_seasonal"] is False
    assert out["geographie"]["season_label"] == ""


def test_pack_versions_view_annotation_does_not_leak(tmp_path: Path, monkeypatch) -> None:  # noqa: ANN001
    """Per-request season flags must not mutate the bank's cached metadata."""
    _freeze_today(monkeypatch, 2026, 6, 12)
    raw = {
        "wm": {
            "language": "de", "question_count": 20, "name": "Weltmeisterschaft",
            "season": {"start": "06-01", "end": "07-31", "label": "⚽ WM-Saison"},
        },
    }
    bank = _FakeBank(tmp_path, raw, {"wm": []})
    ctx = SimpleNamespace(game=SimpleNamespace(question_bank=bank), runtime=_FakeRuntime())
    asyncio.run(pack_versions_view(_FakeRequest(ctx, {})))
    assert "is_seasonal" not in raw["wm"]  # source metadata untouched
