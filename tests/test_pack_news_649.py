"""Tests for the "new in this update" pack banner (#649).

Before #649 the setup screen compared the installed packs against GitHub and
offered *version updates* for packs the host already had. That was the wrong
half of the problem twice over: the endpoint iterated over ``installed``, so a
pack the host did not have could never appear no matter how new it was, and
packs ship inside the integration, so the fix it proposed (replace the JSON
files by hand, restart HA) was work the next HACS update does on its own.

``pack_news_view`` announces the other direction: packs that arrived with an
update the host has already installed. Nothing is fetched, so these tests need
no network double — the whole feature is a comparison against a small file.
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
from custom_components.quizify.server.pack_news import PackNewsStore  # noqa: E402


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
    def __init__(self, data_dir: Path) -> None:
        self.data_dir = data_dir

    async def run_in_executor(self, fn, *args):  # noqa: ANN001, ANN002
        return fn(*args)


class _FakeRequest:
    def __init__(self, ctx, app: dict, token: str | None = None) -> None:  # noqa: ANN001
        self.app = app
        self.app[APP_CTX_KEY] = ctx
        self.query: dict[str, str] = {}
        self.headers: dict[str, str] = {}
        if token is not None:
            self.headers["X-Quizify-Token"] = token


def _pack(name: str, count: int = 10, community: bool = False) -> dict:
    meta = {"version": "1.0", "name": name, "language": "en", "question_count": count}
    if community:
        meta["community"] = True
    return meta


def _make_ctx(bank: _FakeBank, tmp_path: Path) -> SimpleNamespace:
    return SimpleNamespace(
        game=SimpleNamespace(question_bank=bank),
        runtime=_FakeRuntime(tmp_path),
    )


def _news(ctx, app: dict) -> dict:  # noqa: ANN001
    resp = asyncio.run(views.pack_news_view(_FakeRequest(ctx, app)))  # type: ignore[arg-type]
    return json.loads(resp.body)


class TestFirstRunIsNotNews:
    def test_fresh_install_announces_nothing(self, tmp_path: Path) -> None:
        """On a fresh install every pack is new and none of it is news.

        Seeding the record from whatever is installed is what makes the
        banner mean "arrived since you last looked" rather than "exists".
        """
        bank = _FakeBank({"science": _pack("Science"), "film": _pack("Film")})
        app: dict = {}
        assert _news(_make_ctx(bank, tmp_path), app)["new_packs"] == []

    def test_pack_added_after_first_run_is_announced(self, tmp_path: Path) -> None:
        bank = _FakeBank({"science": _pack("Science")})
        app: dict = {}
        ctx = _make_ctx(bank, tmp_path)
        _news(ctx, app)

        bank._versions["worldcup"] = _pack("World Cup", count=100)
        result = _news(ctx, app)["new_packs"]

        assert [p["slug"] for p in result] == ["worldcup"]
        assert result[0]["name"] == "World Cup"
        assert result[0]["question_count"] == 100


class TestItSurvivesAndClears:
    def test_announcement_survives_a_reload(self, tmp_path: Path) -> None:
        """A host who updates on Monday and looks on Friday still sees it.

        The pending set lives on disk precisely so it is not lost with the
        page that would have shown it. Rebuilding the store (a new app dict)
        stands in for a fresh page load or an HA restart.
        """
        bank = _FakeBank({"science": _pack("Science")})
        ctx = _make_ctx(bank, tmp_path)
        _news(ctx, {})
        bank._versions["worldcup"] = _pack("World Cup")

        assert [p["slug"] for p in _news(ctx, {})["new_packs"]] == ["worldcup"]
        assert [p["slug"] for p in _news(ctx, {})["new_packs"]] == ["worldcup"]

    def test_dismiss_clears_it_for_good(self, tmp_path: Path) -> None:
        bank = _FakeBank({"science": _pack("Science")})
        ctx = _make_ctx(bank, tmp_path)
        app: dict = {}
        _news(ctx, app)
        bank._versions["worldcup"] = _pack("World Cup")
        assert _news(ctx, app)["new_packs"] != []

        asyncio.run(PackNewsStore(ctx.runtime).dismiss())

        assert _news(ctx, app)["new_packs"] == []

    def test_removed_pack_stops_being_news(self, tmp_path: Path) -> None:
        """A slug deleted from disk must not sit in the banner forever.

        Without intersecting the pending set against what is installed, the
        only way to clear a pack the host had already removed would be to
        dismiss a banner about a pack that no longer exists.
        """
        bank = _FakeBank({"science": _pack("Science")})
        ctx = _make_ctx(bank, tmp_path)
        _news(ctx, {})
        bank._versions["worldcup"] = _pack("World Cup")
        assert _news(ctx, {})["new_packs"] != []

        del bank._versions["worldcup"]

        assert _news(ctx, {})["new_packs"] == []


class TestWhatCountsAsNews:
    def test_community_pack_is_not_announced(self, tmp_path: Path) -> None:
        """The host dropped that file in themselves.

        Telling someone about a pack they just installed by hand is noise;
        the banner is about what an update brought.
        """
        bank = _FakeBank({"science": _pack("Science")})
        ctx = _make_ctx(bank, tmp_path)
        _news(ctx, {})

        bank._versions["community-mine"] = _pack("My Pack", community=True)

        assert _news(ctx, {})["new_packs"] == []

    def test_version_bump_is_not_announced(self, tmp_path: Path) -> None:
        """The thing #649 removed: a new version of a pack you already have.

        It arrives with the update on its own, so there is nothing to tell
        the host and nothing for them to do.
        """
        bank = _FakeBank({"science": _pack("Science")})
        ctx = _make_ctx(bank, tmp_path)
        _news(ctx, {})

        bank._versions["science"]["version"] = "2.0"

        assert _news(ctx, {})["new_packs"] == []


class TestCostAndGate:
    def test_steady_state_never_writes(self, tmp_path: Path) -> None:
        """The GET is unauthenticated, so it must not be a disk-write lever.

        Repeated calls with nothing new leave the file untouched — checked by
        mtime rather than by call count, because the point is the disk, not
        the code path.
        """
        bank = _FakeBank({"science": _pack("Science")})
        ctx = _make_ctx(bank, tmp_path)
        app: dict = {}
        _news(ctx, app)
        record = tmp_path / "pack_news.json"
        first = record.stat().st_mtime_ns

        for _ in range(5):
            _news(ctx, app)

        assert record.stat().st_mtime_ns == first

    def test_loaded_bank_is_not_reloaded(self, tmp_path: Path) -> None:
        bank = _FakeBank({"science": _pack("Science")}, loaded=True)
        _news(_make_ctx(bank, tmp_path), {})
        assert bank.load_calls == 0

    def test_dismiss_requires_the_admin_token(self, tmp_path: Path) -> None:
        """Dismissing is a write, and a guest with the join link is not the host."""
        bank = _FakeBank({"science": _pack("Science")})
        ctx = _make_ctx(bank, tmp_path)
        request = _FakeRequest(ctx, {})

        resp = asyncio.run(views.pack_news_dismiss_view(request))  # type: ignore[arg-type]

        assert resp.status == 401


class TestNoNetwork:
    def test_the_github_fetch_is_gone(self) -> None:
        """#649 removed the upstream check rather than throttling it.

        #360 had capped how often the unauthenticated endpoint could make the
        HA host call GitHub. Deleting the call removes that whole class of
        abuse, so this asserts the absence of the symbol rather than the
        behaviour of a cache that no longer exists.
        """
        assert not hasattr(views, "_fetch_upstream_versions")
        assert not hasattr(views, "_get_upstream_versions")
        assert not hasattr(views, "_PACK_VERSIONS_URL")

    def test_routes_expose_news_not_updates(self) -> None:
        paths = {path for _method, path, _handler in views.ROUTES}
        assert "/api/quizify/packs/news" in paths
        assert "/api/quizify/packs/updates" not in paths
