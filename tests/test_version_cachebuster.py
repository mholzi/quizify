"""Tests for the cachebuster pipeline: manifest.json → AppContext.version
→ HTML / sw.js {{VERSION}} substitution → /api/quizify/status.

Guards against the drift class of bug we hit before: bumping
manifest.json but forgetting to re-version admin.html / sw.js, so
browsers happily serve stale assets and "fixed" code never reaches the
user. The pipeline now has a single source of truth — these tests
prove the wiring stays connected.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from aiohttp import web

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT))

from custom_components.quizify.server.context import (  # noqa: E402
    APP_CTX_KEY,
    AppContext,
    read_manifest_version,
)
from custom_components.quizify.server.views import (  # noqa: E402
    _apply_version,
    _compute_asset_fingerprint,
    _get_asset_version,
    admin_view,
    player_view,
    status_view,
    sw_view,
)
import custom_components.quizify.server.views as _views  # noqa: E402


@pytest.fixture(autouse=True)
def _stub_live_version(monkeypatch):
    """v1.1.43 made _serve_html / sw_view read the live manifest.json
    instead of trusting the cached ctx.version (so a direct-rsync deploy
    busts the browser cache without an integration reload). The view-level
    tests in this file pass a sentinel version through ctx and expect to
    see it in the response — patch _get_live_version to just echo the
    fallback so the sentinel reaches the assertions."""
    monkeypatch.setattr(_views, "_get_live_version", lambda fallback: fallback)


# ---------- Helpers ----------


def _read_manifest_version_from_disk() -> str:
    """Compare against the on-disk manifest so the test stays in lockstep
    when the file is bumped."""
    manifest = (
        _REPO_ROOT / "custom_components" / "quizify" / "manifest.json"
    )
    return json.loads(manifest.read_text())["version"]


def _fake_ctx(version: str, ha_language: str | None = None) -> AppContext:
    """Build a minimal AppContext for view-level tests."""
    runtime = MagicMock()

    async def _run_in_executor(func, *args):
        return func(*args)

    runtime.run_in_executor = AsyncMock(side_effect=_run_in_executor)
    return AppContext(
        runtime=runtime,
        game=MagicMock(),
        analytics=MagicMock(),
        ws_handler=MagicMock(),
        question_stats=None,
        version=version,
        ha_language=ha_language,
    )


# ---------- Unit tests ----------


class TestVersionSource:
    def test_read_manifest_version_returns_real_value(self) -> None:
        """The helper should match what's in manifest.json on disk."""
        assert read_manifest_version() == _read_manifest_version_from_disk()

    def test_appcontext_defaults_version_from_manifest(self) -> None:
        """An AppContext built without an explicit version still gets the
        real one — proves the default_factory is wired up."""
        ctx = AppContext(
            runtime=MagicMock(),
            game=MagicMock(),
            analytics=MagicMock(),
            ws_handler=MagicMock(),
        )
        assert ctx.version == _read_manifest_version_from_disk()


class TestTemplateSubstitution:
    def test_apply_version_replaces_all_tokens(self) -> None:
        text = (
            '<link href="/x.css?v={{VERSION}}">'
            '<script src="/y.js?v={{VERSION}}"></script>'
        )
        assert _apply_version(text, "9.9.9") == (
            '<link href="/x.css?v=9.9.9">'
            '<script src="/y.js?v=9.9.9"></script>'
        )

    def test_apply_version_no_token_is_passthrough(self) -> None:
        text = "no placeholder here"
        assert _apply_version(text, "1.2.3") == text


class TestAssetFingerprint:
    """The cache-buster must move on ANY asset change, not just a manifest
    version bump. These guard the #147 root cause: a reused version (1.2.0 was
    built twice) left ?v= unchanged, so HA's immutable static cache served
    stale CSS/JS until the version finally moved."""

    def _write(self, root, sub, name, body):
        d = root / sub
        d.mkdir(parents=True, exist_ok=True)
        (d / name).write_text(body, encoding="utf-8")

    def test_fingerprint_changes_when_asset_content_changes(self, tmp_path) -> None:
        self._write(tmp_path, "css", "styles.css", "a {}")
        fp1 = _compute_asset_fingerprint(tmp_path)
        # Edit the file (size changes) — same as shipping a new CSS build.
        self._write(tmp_path, "css", "styles.css", "a { color: red }")
        fp2 = _compute_asset_fingerprint(tmp_path)
        assert fp1 != fp2, "fingerprint must change when an asset's content changes"

    def test_fingerprint_changes_when_asset_added(self, tmp_path) -> None:
        self._write(tmp_path, "js", "admin.js", "x")
        fp1 = _compute_asset_fingerprint(tmp_path)
        self._write(tmp_path, "i18n", "de.json", "{}")
        fp2 = _compute_asset_fingerprint(tmp_path)
        assert fp1 != fp2, "fingerprint must change when an asset is added"

    def test_fingerprint_stable_when_nothing_changes(self, tmp_path) -> None:
        self._write(tmp_path, "js", "admin.js", "x")
        assert _compute_asset_fingerprint(tmp_path) == _compute_asset_fingerprint(tmp_path)

    def test_fingerprint_is_short_hex(self, tmp_path) -> None:
        self._write(tmp_path, "css", "styles.css", "a {}")
        fp = _compute_asset_fingerprint(tmp_path)
        assert len(fp) == 8 and all(c in "0123456789abcdef" for c in fp)

    def test_asset_version_is_version_plus_fingerprint(self) -> None:
        av = _get_asset_version("9.9.9")
        # Back-compat: starts with the version (so ?v=<version> assertions hold),
        # and carries a fingerprint suffix that busts on asset changes.
        assert av.startswith("9.9.9-")
        assert len(av) > len("9.9.9-")


# ---------- Integration: real view fns + real HTML / sw.js on disk ----------


def _fake_request(version: str, ha_language: str | None = None) -> web.Request:
    """Minimal request stub: views only read `request.app[APP_CTX_KEY]`."""
    req = MagicMock(spec=web.Request)
    req.app = {APP_CTX_KEY: _fake_ctx(version, ha_language)}
    return req


@pytest.mark.asyncio
class TestHtmlSubstitution:
    async def test_player_html_has_no_unresolved_tokens(self) -> None:
        resp = await player_view(_fake_request("9.9.9-test"))
        assert resp.status == 200
        body = resp.text
        # Drift guard: no {{VERSION}} should ever reach the browser.
        assert "{{VERSION}}" not in body
        # Live version landed in the cache-busters.
        assert "?v=9.9.9-test" in body
        # Meta tag carries the version (parity with Beatify's
        # `<meta name="beatify-version">`).
        assert 'name="quizify-version" content="9.9.9-test"' in body

    async def test_admin_html_has_no_unresolved_tokens(self) -> None:
        resp = await admin_view(_fake_request("9.9.9-test", ha_language="en"))
        assert resp.status == 200
        assert "{{VERSION}}" not in resp.text
        assert "{{ASSET_VER}}" not in resp.text
        assert "{{HA_LANG}}" not in resp.text
        # ?v= now carries <version>-<fingerprint>, not the bare version.
        assert "?v=9.9.9-test-" in resp.text
        # The meta tag keeps the clean semantic version.
        assert 'name="quizify-version" content="9.9.9-test"' in resp.text

    async def test_admin_html_injects_ha_language(self) -> None:
        """HA's configured language lands in the meta tag admin.js reads to
        pick the initial UI language (#152)."""
        resp = await admin_view(_fake_request("9.9.9-test", ha_language="de"))
        assert 'name="quizify-ha-lang" content="de"' in resp.text

    async def test_admin_html_empty_ha_language_without_hass(self) -> None:
        """Standalone dev server has no hass (ha_language is None): the token
        resolves to an empty string, never leaks the literal {{HA_LANG}}."""
        resp = await admin_view(_fake_request("9.9.9-test", ha_language=None))
        assert "{{HA_LANG}}" not in resp.text
        assert 'name="quizify-ha-lang" content=""' in resp.text

    async def test_html_served_with_no_cache_headers(self) -> None:
        """HTML must always revalidate — otherwise a months-old admin.html
        could be served from the browser cache and the new ?v= URLs never
        get fetched."""
        resp = await player_view(_fake_request("9.9.9-test"))
        cache_control = resp.headers.get("Cache-Control", "")
        assert "no-cache" in cache_control
        assert "no-store" in cache_control
        assert "must-revalidate" in cache_control


@pytest.mark.asyncio
class TestServiceWorkerSubstitution:
    async def test_sw_js_substitutes_cache_version(self) -> None:
        resp = await sw_view(_fake_request("9.9.9-test"))
        assert resp.status == 200
        body = resp.text
        assert "{{VERSION}}" not in body
        # Bumping manifest.json must rotate the SW cache name — that's
        # the whole point. Match the canonical 'quizify-v<VERSION>'.
        assert "quizify-v9.9.9-test" in body

    async def test_sw_js_served_as_javascript(self) -> None:
        resp = await sw_view(_fake_request("9.9.9-test"))
        # MIME matters: browsers refuse to register a SW served as text/plain.
        assert resp.content_type == "application/javascript"

    async def test_sw_js_no_cache(self) -> None:
        resp = await sw_view(_fake_request("9.9.9-test"))
        cache_control = resp.headers.get("Cache-Control", "")
        assert "no-cache" in cache_control


@pytest.mark.asyncio
class TestStatusEndpoint:
    async def test_status_returns_live_version(self) -> None:
        """/api/quizify/status used to report a hardcoded 1.0.13 — wildly
        stale. It should report the current AppContext.version."""
        resp = await status_view(_fake_request("9.9.9-test"))
        assert resp.status == 200
        payload = json.loads(resp.body)
        assert payload["version"] == "9.9.9-test"
        assert payload["status"] == "ok"
