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
import re
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

    def test_asset_version_changes_on_version_bump(self) -> None:
        """The owner's invariant: a new release number (manifest.json version)
        MUST move the cache-buster even when no asset file changed — the
        version prefix guarantees that independent of the fingerprint."""
        assert _get_asset_version("1.0.0") != _get_asset_version("1.0.1")


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
        # #335: data-driven admin chip tokens must also be substituted — a raw
        # {{LANGUAGE_CHIPS}}/{{CATEGORY_CHIPS}} reaching the browser would leave
        # the language picker / pack grid empty.
        assert "{{LANGUAGE_CHIPS}}" not in resp.text
        assert "{{CATEGORY_CHIPS}}" not in resp.text
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
        """sw.js MUST be non-cacheable. An HTTP-cached sw.js carries the old
        CACHE_VERSION forever: the browser never re-fetches it, the old SW
        keeps running, old caches never get deleted → stale assets survive
        every release. This is the linchpin of the 'new release number resets
        the client cache' invariant."""
        resp = await sw_view(_fake_request("9.9.9-test"))
        cache_control = resp.headers.get("Cache-Control", "")
        assert "no-cache" in cache_control
        assert "no-store" in cache_control
        assert "must-revalidate" in cache_control

    async def test_sw_js_substitutes_asset_ver(self) -> None:
        """{{ASSET_VER}} must never leak into the served SW — it feeds
        CACHE_VERSION and the precache URLs."""
        resp = await sw_view(_fake_request("9.9.9-test"))
        assert "{{ASSET_VER}}" not in resp.text
        # Precache URLs carry the substituted buster.
        assert "?v=9.9.9-test-" in resp.text

    async def test_sw_served_with_service_worker_allowed_header(self) -> None:
        """Regression for #291: the SW lives at /quizify/static/sw.js but must
        control /quizify/* (where the pages live). The browser only permits a
        registration scope wider than the worker's own path if the response
        carries Service-Worker-Allowed for that scope. Without this header the
        {scope: '/quizify/'} registration is rejected and the worker controls
        nothing (dead fetch handler, no idle auto-reload)."""
        resp = await sw_view(_fake_request("9.9.9-test"))
        assert resp.headers.get("Service-Worker-Allowed") == "/quizify/"

    async def test_unversioned_precache_bypasses_http_cache(self) -> None:
        """Un-versioned precache entries must go to the server, not the browser
        HTTP cache. HA serves /quizify/static/* with a 31-day max-age, so a
        plain cache.add() would seed a brand-new release's cache with month-old
        bytes from the HTTP cache (stale from birth).

        Since #791 this is the *fallback* branch and covers the fonts only:
        a `?v=<version>-<fingerprint>` URL is immutable per content, so reusing
        the entry the page just filled is correct — and reloading it was a
        guaranteed second download of the 330 KB bundle. See
        tests/test_sw_precache_split_791.py for that half."""
        resp = await sw_view(_fake_request("9.9.9-test"))
        assert "cache: 'reload'" in resp.text

    async def test_sw_js_substitutes_default_lang(self) -> None:
        """The precache carries one i18n bundle, so it has to know which (#791)."""
        resp = await sw_view(_fake_request("9.9.9-test"))
        assert "{{DEFAULT_LANG}}" not in resp.text
        assert "var DEFAULT_LANG = '" in resp.text


class TestServiceWorkerSource:
    """Static guards on the raw www/ sources (no view involved)."""

    _WWW = _REPO_ROOT / "custom_components" / "quizify" / "www"

    def test_precache_assets_are_versioned_and_exist(self) -> None:
        """Every precache entry carries the ?v={{ASSET_VER}} buster (so the
        cache key matches what versioned pages request — caches.match is
        exact on the query string) and points at a real file (guards list
        drift like the old player-core.js entries after the bundle switch).

        Fonts are the one exception, and deliberately so (#737, #738): the
        @font-face rules live in the *static* styles.css, which is never
        templated, so the browser asks for the file plain. A versioned
        precache entry would be a cache key nothing ever requests. They are
        safe unbusted because a font file's bytes never change under a given
        name — a new face means a new filename."""
        sw_src = (self._WWW / "sw.js").read_text(encoding="utf-8")
        urls = re.findall(r"'(/quizify/static/[^']+)'", sw_src)
        precache = [u for u in urls if "{{ASSET_VER}}" in u or "?v=" in u]
        assert precache, "no precache URLs found in sw.js"
        for url in precache:
            assert url.endswith("?v={{ASSET_VER}}"), (
                f"precache URL missing cache-buster: {url}"
            )
            rel = url.split("?")[0].removeprefix("/quizify/static/")
            assert (self._WWW / rel).is_file(), (
                f"precache URL points at missing file: {url}"
            )

        # Trailing "/" is the routing prefix in the fetch handler, not an entry.
        fonts = [
            u
            for u in urls
            if u.startswith("/quizify/static/fonts/") and not u.endswith("/")
        ]
        assert fonts, "sw.js precaches no font — an offline install has no face"
        for url in fonts:
            assert "?" not in url, (
                f"font precache URL must match what the CSS requests, plain: {url}"
            )
            rel = url.removeprefix("/quizify/static/")
            assert (self._WWW / rel).is_file(), (
                f"font precache URL points at missing file: {url}"
            )

    def test_html_static_asset_refs_are_versioned(self) -> None:
        """Every /quizify/static/ reference in every served HTML page must
        carry the ?v={{ASSET_VER}} buster — one missed reference is one
        asset pinned in the 31-day HTTP cache across releases.

        Except fonts (#737, #738). Those are referenced from the static
        styles.css too, which gets no {{ASSET_VER}} substitution, so a
        versioned <link rel="preload"> would preload a URL the stylesheet
        never asks for — two downloads instead of one. Being pinned in the
        HTTP cache is the desired behaviour for a font: the bytes for a given
        filename are immutable, and a new face gets a new filename."""
        for page in self._WWW.glob("*.html"):
            src = page.read_text(encoding="utf-8")
            refs = re.findall(r"""["'](/quizify/static/[^"']+)["']""", src)
            for ref in refs:
                if ref.startswith("/quizify/static/fonts/"):
                    assert "?" not in ref, (
                        f"{page.name}: font reference must stay unbusted so it "
                        f"matches the stylesheet's request: {ref}"
                    )
                    continue
                assert "?v={{ASSET_VER}}" in ref, (
                    f"{page.name}: unversioned static reference {ref}"
                )

    def test_sw_registration_bypasses_http_cache(self) -> None:
        """The SW registration must opt out of the HTTP cache for the worker
        script (updateViaCache: 'none') — older browsers honored the HTTP
        cache for up to 24h, which kept the old CACHE_VERSION alive."""
        src = (self._WWW / "js" / "sw-update.js").read_text(encoding="utf-8")
        assert "updateViaCache: 'none'" in src

    def test_sw_registered_with_quizify_scope(self) -> None:
        """The SW must register with scope '/quizify/' (#291) — the default
        scope is the worker's own dir (/quizify/static/), which controls none
        of the actual pages. Pairs with the Service-Worker-Allowed header on
        sw_view."""
        src = (self._WWW / "js" / "sw-update.js").read_text(encoding="utf-8")
        assert "scope: '/quizify/'" in src


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
