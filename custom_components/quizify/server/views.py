"""HTTP handlers for Quizify (plain aiohttp, no Home Assistant coupling).

The HA integration and the standalone dev server both register the same
handlers — only the static-asset registration differs (HA uses
``async_register_static_paths``; standalone uses aiohttp's ``add_static``).
"""

from __future__ import annotations

import datetime as _dt
import hashlib
import html as _html
import json
import logging
import os
import time
from pathlib import Path
from typing import TYPE_CHECKING

import aiohttp
from aiohttp import web

from ..const import SUBMIT_POLL_INTERVAL_SECONDS
from ..game.seasons import is_in_season, parse_season, pick_active_season
from .context import APP_CTX_KEY
from .pack_submission import (
    submissions_list_view,
    submit_config_view,
    submit_pack_view,
)
from .rate_limit import SlidingWindowLimiter
from .serializers import build_game_status_response

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from ..runtime import Runtime
    from .context import AppContext

    # aiohttp request handler shape, matching what `UrlDispatcher.add_route`
    # accepts. Used to type the ROUTES table precisely instead of `object`.
    _RouteHandler = Callable[[web.Request], Awaitable[web.StreamResponse]]


# GitHub raw URL for pack version manifests
_PACK_VERSIONS_URL = (
    "https://raw.githubusercontent.com/mholzi/quizify/main/"
    "custom_components/quizify/questions/versions.json"
)

# In-memory cache for the upstream versions.json (#360). The
# ``GET /api/quizify/packs/updates`` endpoint is unauthenticated, so without a
# throttle any client could loop it and make the HA host hammer GitHub's raw
# endpoint on every request. Mirror pack_submission's hourly reconcile throttle
# (SUBMIT_POLL_INTERVAL_SECONDS) and reuse the cached copy within the window.
_UPSTREAM_CACHE_TTL_SECONDS = SUBMIT_POLL_INTERVAL_SECONDS
# (fetched_at_monotonic, versions_dict_or_None)
_upstream_versions_cache: tuple[float, dict | None] | None = None

_LOGGER = logging.getLogger(__name__)

_NO_CACHE_HEADERS = {
    "Cache-Control": "no-cache, no-store, must-revalidate",
    "Pragma": "no-cache",
    "Expires": "0",
}

_WWW_DIR = Path(__file__).parent.parent / "www"
_MANIFEST_PATH = Path(__file__).parent.parent / "manifest.json"

# Single placeholder substituted at serve time. Used in HTML asset URLs
# (``?v={{VERSION}}``), the ``meta[name="quizify-version"]`` tag, and the
# service worker's ``CACHE_VERSION``. Bumping manifest.json propagates
# everywhere — no more drift between admin.html / player.html / sw.js.
_VERSION_TOKEN = "{{VERSION}}"

# Substituted in served HTML with Home Assistant's configured language
# (``ctx.ha_language``). Empty string on the standalone dev server, where
# there's no hass — admin.js then falls back to browser locale. Replacing
# it unconditionally means the raw token never leaks into the page.
_HA_LANG_TOKEN = "{{HA_LANG}}"

# Cache-buster token for the ``?v=`` asset query strings and the service
# worker's ``CACHE_VERSION``. Distinct from {{VERSION}} (which stays the clean
# semantic version for the meta tag) — this one is ``<version>-<fingerprint>``
# where the fingerprint is a short hash of the served asset files. Because it
# changes whenever ANY css/js/i18n file changes, cache-busting no longer
# depends on remembering to bump manifest.json. That manual dependency was the
# recurring failure (#147, and 1.2.0 being built twice under one version): a
# reused version left ?v= unchanged, so HA's immutable static cache_headers
# kept serving stale assets.
_ASSET_VER_TOKEN = "{{ASSET_VER}}"

# Subdirs under www/ that hold the ?v=-busted assets.
_ASSET_SUBDIRS = ("css", "js", "i18n")

# Recompute the fingerprint at most this often — a small dir walk, bounded so
# a burst of player.html loads at game start doesn't re-walk per request.
_ASSET_FP_TTL_NS = 5 * 1_000_000_000  # 5s
_ASSET_FP_CACHE: tuple[int, str] | None = None  # (monotonic_ns, fingerprint)

# mtime-keyed cache for the live manifest version. Without this the
# integration would re-parse manifest.json on every refresh; with it we
# only re-read when the file actually changed (which means a deploy
# happened). Tuple of (mtime_ns, version).
_MANIFEST_CACHE: tuple[int, str] | None = None

# Pure in-memory "live" manifest version served on the HTML request hot path.
# Refreshed OFF the event loop by ``refresh_live_version`` (a background
# ``async_track_time_interval`` on the HA path, see __init__.py). The request
# handler reads this with zero ``os.stat``/``read_text`` on the loop (#343),
# so HA's loop-watcher no longer flags the launcher serve. ``None`` until the
# first refresh runs — until then the request path falls back to
# ``ctx.version`` (the value read off the loop at setup). A direct-rsync deploy
# (manifest bumped on disk without an integration reload) is still picked up:
# the background refresh re-reads the file on its next mtime-changed tick, so
# the asset cache-buster updates without a full HA restart (the v1.1.43
# mechanism), just without blocking the loop.
_LIVE_VERSION: str | None = None


def _refresh_live_version() -> str | None:
    """Re-read manifest.json from disk and update the in-memory live version.

    BLOCKING (``os.stat`` + ``read_text``) — must run OFF the event loop (in an
    executor thread, or in the standalone dev server which has no HA loop, or
    in tests). Returns the resolved version, or ``None`` if the file is missing
    or unreadable (the caller then keeps the previous in-memory value / the
    setup-time fallback). The mtime cache means an unchanged file is a single
    ``stat`` with no re-parse.
    """
    global _MANIFEST_CACHE, _LIVE_VERSION
    try:
        mtime_ns = os.stat(_MANIFEST_PATH).st_mtime_ns
        if _MANIFEST_CACHE is not None and _MANIFEST_CACHE[0] == mtime_ns:
            _LIVE_VERSION = _MANIFEST_CACHE[1]
            return _LIVE_VERSION
        data = json.loads(_MANIFEST_PATH.read_text(encoding="utf-8"))
        version = str(data["version"])
        _MANIFEST_CACHE = (mtime_ns, version)
        _LIVE_VERSION = version
        return version
    except (OSError, ValueError, KeyError) as exc:
        _LOGGER.debug("Could not read live version from manifest: %s", exc)
        return None


async def refresh_live_version(runtime: Runtime) -> None:
    """Refresh the in-memory live manifest version off the event loop.

    Wired as a periodic ``async_track_time_interval`` callback on the HA path
    (see ``async_setup_entry``) and called once at setup. Runs the blocking
    re-read in an executor thread so the loop is never blocked (#343).
    """
    await runtime.run_in_executor(_refresh_live_version)


def _get_ctx(request: web.Request) -> AppContext:
    """Pull the AppContext stashed on the aiohttp application."""
    return request.app[APP_CTX_KEY]


def _is_admin_authenticated(request: web.Request) -> bool:
    """Validate the admin session token on a host-only data request (#356).

    The sensitive read endpoints (flags, all-time, analytics data, tts-entities,
    question-stats, pack submissions) expose host-only data — player names +
    free-text flag reasons, per-person play history, HA entity ids. They are
    registered on ``hass.http.app.router`` with NO HA auth, so without a gate
    any client that can reach port 8123 (including the internet on a
    port-forwarded install) can read them. Gate them on the admin session token
    the admin page already holds, sent as ``?token=`` or the ``X-Quizify-Token``
    header. Same credential as the admin WebSocket — no new secret, no HA-login
    requirement, and the unauthenticated player/dashboard flow is untouched.
    """
    token = request.query.get("token") or request.headers.get("X-Quizify-Token")
    if not token:
        return False
    conn = getattr(getattr(_get_ctx(request), "ws_handler", None), "conn", None)
    if conn is None:  # pragma: no cover — ws_handler is always wired in prod
        return False
    return conn.validate_admin_token(token)


def _unauthorized() -> web.Response:
    """401 for a request that failed the admin-token gate (#356)."""
    return web.json_response({"error": "unauthorized"}, status=401)


def _get_live_version(fallback: str) -> str:
    """Return the live manifest version — pure in-memory, NO loop I/O (#343).

    The actual disk read happens in ``_refresh_live_version`` (off the loop:
    a background interval on the HA path, an executor refresh, or the
    standalone dev server). This reader just returns the last-refreshed
    in-memory value, falling back to ``fallback`` (typically ``ctx.version``,
    the value read off the loop at setup) until the first refresh has run or
    if the manifest was unreadable. Safe to call on the event loop on every
    HTML request — that's the whole point of moving the read out of here.
    """
    return _LIVE_VERSION if _LIVE_VERSION is not None else fallback


def _compute_asset_fingerprint(www_dir: Path = _WWW_DIR) -> str:
    """Short hash over the served assets' (relative path, mtime, size).

    Changes whenever any css/js/i18n file is added, removed, or edited — so the
    cache-buster moves on any real asset change, with no manifest bump needed.
    Cheap: a handful of ``stat`` calls. Falls back to an empty-tree hash if the
    dirs are missing (defensive — runs on the HTML serve path).
    """
    h = hashlib.md5(usedforsecurity=False)
    for sub in _ASSET_SUBDIRS:
        d = www_dir / sub
        if not d.is_dir():
            continue
        for p in sorted(d.rglob("*")):
            if not p.is_file():
                continue
            try:
                st = p.stat()
            except OSError:
                continue
            h.update(str(p.relative_to(www_dir)).encode())
            h.update(str(st.st_mtime_ns).encode())
            h.update(str(st.st_size).encode())
    return h.hexdigest()[:8]


def _cached_fingerprint() -> str | None:
    """Return the cached fingerprint if still within its TTL, else ``None``.

    Cheap, non-blocking — just a monotonic-clock check against the cached
    timestamp. Used to take the fast path without touching the filesystem.
    """
    if _ASSET_FP_CACHE is not None and (
        time.monotonic_ns() - _ASSET_FP_CACHE[0] < _ASSET_FP_TTL_NS
    ):
        return _ASSET_FP_CACHE[1]
    return None


def _refresh_fingerprint() -> str:
    """Recompute the asset fingerprint and refresh the cache.

    Performs the blocking ``rglob``/``stat`` walk over the asset dirs, so it
    must run off the event loop (see ``_get_asset_version_async``). The cache
    write is a single tuple assignment — safe even if two threads race; the
    loser just overwrites with an equivalent value.
    """
    global _ASSET_FP_CACHE
    fingerprint = _compute_asset_fingerprint()
    _ASSET_FP_CACHE = (time.monotonic_ns(), fingerprint)
    return fingerprint


def _get_asset_version(version: str) -> str:
    """Cache-buster value ``<version>-<asset_fingerprint>``.

    The version prefix keeps it readable (which release) and back-compatible
    with assertions that look for ``?v=<version>``; the fingerprint suffix is
    what makes it move on asset changes. Fingerprint recompute is throttled to
    ``_ASSET_FP_TTL_NS``.

    Synchronous variant — only safe to call off the event loop (tests, the
    standalone dev server). The HTML serve path uses
    ``_get_asset_version_async`` so the dir walk never blocks the loop (#213).
    """
    fingerprint = _cached_fingerprint()
    if fingerprint is None:
        fingerprint = _refresh_fingerprint()
    return f"{version}-{fingerprint}"


async def _get_asset_version_async(ctx: AppContext, version: str) -> str:
    """Async cache-buster value, never blocking the event loop (#213).

    Fast path (cache fresh): no filesystem access, returns inline. Slow path
    (TTL expired): the blocking ``rglob``/``stat`` walk runs in an executor
    thread via ``ctx.runtime.run_in_executor`` — the same offload pattern the
    HTML/sw read path already uses — so a burst of page loads at game start
    can't stall the loop.
    """
    fingerprint = _cached_fingerprint()
    if fingerprint is None:
        fingerprint = await ctx.runtime.run_in_executor(_refresh_fingerprint)
    return f"{version}-{fingerprint}"


def _apply_version(text: str, version: str) -> str:
    """Replace every {{VERSION}} token with the live integration version."""
    return text.replace(_VERSION_TOKEN, version)


# mtime-keyed raw-text cache for the served HTML pages + service worker (#452).
# ``_serve_html`` / ``sw_view`` used to do a full executor ``read_text`` on EVERY
# request (player.html is ~52 KB) even though the file only changes on a deploy.
# Cache the RAW file text keyed by path → (mtime_ns, text) and re-read only when
# the mtime changes — same pattern as ``_MANIFEST_CACHE``. The per-request
# {{VERSION}} / {{ASSET_VER}} / {{HA_LANG}} / chip substitutions still run on
# every request (their inputs change without the file mtime), so only the disk
# read is elided; a direct-rsync deploy bumps the mtime and is picked up.
_TEMPLATE_TEXT_CACHE: dict[str, tuple[int, str]] = {}


def _read_template_cached(path: Path) -> str:
    """Return the file's text, re-reading from disk only when its mtime changed.

    BLOCKING (``os.stat`` + ``read_text``) — must run OFF the event loop (in an
    executor thread or the standalone dev server). An unchanged file costs a
    single ``stat`` with no re-read. The cache-write is a plain dict assignment;
    a race between two threads just overwrites with an equivalent value (#452).
    """
    key = str(path)
    mtime_ns = os.stat(path).st_mtime_ns
    cached = _TEMPLATE_TEXT_CACHE.get(key)
    if cached is not None and cached[0] == mtime_ns:
        return cached[1]
    text = path.read_text(encoding="utf-8")
    _TEMPLATE_TEXT_CACHE[key] = (mtime_ns, text)
    return text


async def _serve_html(request: web.Request, filename: str) -> web.Response:
    """Read a file from www/, substitute {{VERSION}}, return as HTML."""
    html_path = _WWW_DIR / filename
    if not html_path.exists():
        _LOGGER.error("Page not found: %s", html_path)
        return web.Response(text=f"{filename} not found", status=500)

    ctx = _get_ctx(request)
    version = _get_live_version(ctx.version)
    html_content = await ctx.runtime.run_in_executor(_read_template_cached, html_path)
    html_content = _apply_version(html_content, version)
    asset_version = await _get_asset_version_async(ctx, version)
    html_content = html_content.replace(_ASSET_VER_TOKEN, asset_version)
    html_content = html_content.replace(_HA_LANG_TOKEN, ctx.ha_language or "")
    # Data-driven admin chips (#335): substitute the language + category chip
    # tokens with markup derived from the loaded packs. Only admin.html carries
    # these tokens; ``replace`` is a no-op on every other page, so the guard is
    # just to skip the (cheap) bank read for non-admin pages.
    if _LANGUAGE_CHIPS_TOKEN in html_content or _CATEGORY_CHIPS_TOKEN in html_content:
        html_content = _apply_admin_chip_tokens(ctx, html_content)
    return web.Response(
        text=html_content, content_type="text/html", headers=_NO_CACHE_HEADERS
    )


def _apply_admin_chip_tokens(ctx: AppContext, html_content: str) -> str:
    """Substitute the {{LANGUAGE_CHIPS}} / {{CATEGORY_CHIPS}} tokens in-place.

    Reads the already-loaded pack metadata (same in-memory source the
    featured-pack endpoint uses — no blocking re-load on the HTML hot path).
    If the bank is unavailable (standalone dev server before any game state),
    the language token degrades to a static DE/EN pair and the category token
    to just the Mixed tile so the page still renders something sane.
    """
    default_lang = _resolve_default_lang(ctx.ha_language)
    bank = ctx.game.question_bank if ctx.game else None
    pack_versions = bank.get_pack_versions() if bank is not None else {}
    html_content = html_content.replace(
        _LANGUAGE_CHIPS_TOKEN, _render_language_chips(pack_versions, default_lang)
    )
    html_content = html_content.replace(
        _CATEGORY_CHIPS_TOKEN, _render_category_chips(pack_versions, default_lang)
    )
    return html_content


async def sw_view(request: web.Request) -> web.Response:
    """Serve the service worker with {{VERSION}} substituted.

    Registered ahead of the static file handler so the templated copy
    wins over the raw file on disk. Served with no-cache headers so
    browsers always revalidate — a stale SW can't keep serving stale
    asset caches.
    """
    sw_path = _WWW_DIR / "sw.js"
    if not sw_path.exists():
        return web.Response(text="sw.js not found", status=404)

    ctx = _get_ctx(request)
    version = _get_live_version(ctx.version)
    body = await ctx.runtime.run_in_executor(_read_template_cached, sw_path)
    body = _apply_version(body, version)
    body = body.replace(_ASSET_VER_TOKEN, await _get_asset_version_async(ctx, version))
    # The SW is served from /quizify/static/sw.js but must control /quizify/*
    # (the actual pages). A worker can only claim a scope above its own path
    # if the response carries Service-Worker-Allowed for that wider scope —
    # without it the browser rejects the {scope: '/quizify/'} registration
    # and the worker controls nothing (#291).
    headers = {**_NO_CACHE_HEADERS, "Service-Worker-Allowed": "/quizify/"}
    return web.Response(
        text=body, content_type="application/javascript", headers=headers
    )


async def admin_view(request: web.Request) -> web.Response:
    """Serve the admin HTML page."""
    return await _serve_html(request, "admin.html")


async def launcher_view(request: web.Request) -> web.Response:
    """Serve the launcher HTML page."""
    return await _serve_html(request, "launcher.html")


async def player_view(request: web.Request) -> web.Response:
    """Serve the player HTML page."""
    return await _serve_html(request, "player.html")


async def dashboard_view(request: web.Request) -> web.Response:
    """Serve the dashboard HTML page."""
    return await _serve_html(request, "dashboard.html")


async def analytics_view(request: web.Request) -> web.Response:
    """Serve the analytics HTML page."""
    return await _serve_html(request, "analytics.html")


async def game_status_view(request: web.Request) -> web.Response:
    """Return current game status."""
    ctx = _get_ctx(request)
    game_id = request.query.get("game_id")
    return web.json_response(build_game_status_response(ctx.game, game_id))


async def status_view(request: web.Request) -> web.Response:
    """Return integration status."""
    ctx = _get_ctx(request)
    return web.json_response({"version": ctx.version, "status": "ok"})


# Theme → emoji map. Mirrors admin.html data-icon attributes.
# Single source of truth; both featured-pack and any future chip-rendering
# helper read from here.
_THEME_ICONS = {
    "geography": "🌍",
    "nature": "🦋",
    "popculture": "🎬",
    "sport": "⚽",
    "music": "🎵",
    "science": "🔬",
    "history": "📜",
    "food": "🍔",
    "tech": "💡",
    "worldcup": "🏆",
}

# ---------------------------------------------------------------------------
# Data-driven admin chips (#335). The language selector and the category/pack
# grid in admin.html are no longer hand-maintained HTML — they are rendered
# server-side from the languages + packs actually loaded in the question bank,
# so adding a new-language pack (e.g. a Spanish pack) needs zero HTML edits.
# Two tokens in admin.html are substituted at serve time:
#   {{LANGUAGE_CHIPS}}  → flag-only language chips (Selector B)
#   {{CATEGORY_CHIPS}}  → the "Mixed" tile + one card per pack (Architecture A)
# The rendered DOM preserves the exact contract admin.js depends on
# (data-value / data-lang / data-theme / data-icon / data-count, .pack-card-*,
# .active) so the existing selection wiring keeps working unchanged.
# ---------------------------------------------------------------------------
_LANGUAGE_CHIPS_TOKEN = "{{LANGUAGE_CHIPS}}"
_CATEGORY_CHIPS_TOKEN = "{{CATEGORY_CHIPS}}"

# Language code → (flag emoji, native display name). Drives the flag-only
# language chips: the flag is the visible glyph, the name is exposed via
# aria-label/title for accessibility. de/en/es are the known set; any other
# language present in a pack falls back to a neutral 🌐 flag + the upper-cased
# code so an unexpected pack language still renders a usable (if generic) chip
# rather than crashing or vanishing.
_LANGUAGE_META = {
    "de": ("🇩🇪", "Deutsch"),
    "en": ("🇬🇧", "English"),
    "es": ("🇪🇸", "Español"),
}
# Deterministic ordering for the language chips. Known languages first in this
# order; any unknown language code is appended afterwards, alphabetically.
_LANGUAGE_ORDER = ["de", "en", "es"]

# Per-language unit word for the pack-card question count ("149 Fragen" /
# "150 questions" / "150 preguntas"). The count itself is data-driven; only
# the trailing noun is language-specific. Unknown languages fall back to the
# English unit.
_COUNT_UNITS = {"de": "Fragen", "en": "questions", "es": "preguntas"}


def _language_chip_meta(lang: str) -> tuple[str, str]:
    """Return (flag, display-name) for a language code, with a safe fallback."""
    return _LANGUAGE_META.get(lang, ("🌐", lang.upper()))


def _sorted_pack_languages(pack_versions: dict[str, dict]) -> list[str]:
    """Languages present across loaded packs, in stable display order."""
    present = {meta.get("language", "de") for meta in pack_versions.values()}
    known = [code for code in _LANGUAGE_ORDER if code in present]
    extra = sorted(present - set(_LANGUAGE_ORDER))
    return known + extra


def _render_language_chips(pack_versions: dict[str, dict], default_lang: str) -> str:
    """Render flag-only language chips from the languages present in packs.

    Selector B (#335): each chip shows just the flag glyph; the full language
    name lives on ``aria-label`` + ``title`` for screen readers and hover.
    ``default_lang`` gets the ``active`` class so the initial selection matches
    the page's resolved language. Returns the inner HTML for ``#language-chips``.
    """
    langs = _sorted_pack_languages(pack_versions)
    if not langs:
        # No packs loaded (shouldn't happen on the HA path, but be safe):
        # fall back to the historical DE/EN pair so the selector is never empty.
        langs = ["de", "en"]
    # If the resolved default isn't among the present languages, fall back to
    # the first present one so exactly one chip is active.
    active_lang = default_lang if default_lang in langs else langs[0]
    buttons = []
    for code in langs:
        flag, name = _language_chip_meta(code)
        cls = "chip active" if code == active_lang else "chip"
        name_esc = _html.escape(name, quote=True)
        buttons.append(
            f'<button type="button" class="{cls}" data-value="{_html.escape(code)}" '
            f'aria-label="{name_esc}" title="{name_esc}">{flag}</button>'
        )
    return "".join(buttons)


def _render_category_chips(pack_versions: dict[str, dict], default_lang: str) -> str:
    """Render the Mixed tile + one pack card per loaded pack (Architecture A).

    The Mixed tile carries no ``data-lang`` (always visible, "all packs") and is
    ``active`` by default. Each pack card mirrors the old hand-written contract:
    ``data-value`` (pack slug), ``data-lang``, ``data-theme``, ``data-icon``,
    ``data-count`` + the ``.pack-card-icon/name/count`` spans. Names come from
    each pack's own metadata so they are already native to the pack's language;
    the count unit is per-language. Returns the inner HTML for ``#category-chips``.

    Ordering: packs are grouped by language (display order), then by pack name
    within a language, so the grid is stable and readable regardless of bank
    insertion order.
    """
    # Mixed tile — i18n-driven label + count, no data-lang (always shown).
    mixed = (
        '<button type="button" class="chip active" data-value="mixed" data-icon="🎲">'
        '<span class="pack-card-icon">🎲</span>'
        '<span class="pack-card-name" data-i18n="admin.categoryMixed">Mixed</span>'
        '<span class="pack-card-count" data-i18n="admin.mixedAllPacks">All packs</span>'
        "</button>"
    )

    lang_rank = {
        code: i for i, code in enumerate(_sorted_pack_languages(pack_versions))
    }

    def _order_key(item: tuple[str, dict]) -> tuple[int, str]:
        slug, meta = item
        lang = meta.get("language", "de")
        name = str(meta.get("name", slug))
        return (lang_rank.get(lang, len(lang_rank)), name.lower())

    cards = []
    for slug, meta in sorted(pack_versions.items(), key=_order_key):
        lang = str(meta.get("language", "de"))
        name = str(meta.get("name", slug))
        theme = meta.get("theme", "") or ""
        if not isinstance(theme, str):
            theme = ""
        icon = _THEME_ICONS.get(theme, "🎲")
        count = meta.get("question_count", 0)
        unit = _COUNT_UNITS.get(lang, _COUNT_UNITS["en"])
        name_esc = _html.escape(name)
        attrs = (
            f'data-value="{_html.escape(slug, quote=True)}" '
            f'data-lang="{_html.escape(lang, quote=True)}" '
            f'data-theme="{_html.escape(theme, quote=True)}" '
            f'data-icon="{icon}" data-count="{int(count)}"'
        )
        cards.append(
            f'<button type="button" class="chip" {attrs}>'
            f'<span class="pack-card-icon">{icon}</span>'
            f'<span class="pack-card-name">{name_esc}</span>'
            f'<span class="pack-card-count">{int(count)} {unit}</span>'
            "</button>"
        )
    return mixed + "".join(cards)


def _resolve_default_lang(ha_language: str | None) -> str:
    """Normalise HA's configured language to the in-game UI set (de/en/es).

    Mirrors the client-side ``normalize()`` in admin.js so the server-rendered
    active chip matches the language the page will resolve to. Empty/unknown →
    English (the historical default on the standalone dev server).
    """
    code = (ha_language or "").lower()
    if code.startswith("de"):
        return "de"
    if code.startswith("es"):
        return "es"
    return "en"


# Per Markus 2026-05-29 (msg 283): the Featured Spotlight rotates between
# two logics, alternating by day-of-year so the same logic doesn't lock
# in for weeks. Day 0/2/4… = Most-Played (this-week winners surface).
# Day 1/3/5… = Most-Difficult (lowest correct rate; challenges people).
# Fallback to Geographie / Geography when no analytics data has built
# up yet.
_FEATURED_DEFAULT_DE = "geographie"
_FEATURED_DEFAULT_EN = "geography"
_FEATURED_MIN_PLAYS = 1   # need at least 1 play to qualify for most-played
_FEATURED_MIN_SHOWN = 10  # aggregate shown_count for most-difficult


async def featured_pack_view(request: web.Request) -> web.Response:
    """Pick the Featured Spotlight pack for the admin setup screen.

    Query: ``?lang=de|en``.

    Returns ``{value, title, meta, logic}`` where logic is the rule that
    picked the pack — useful for tooltips and for the frontend to know
    whether to show a "Popular" or "Hardest" badge.

    Falls back to a hardcoded default if analytics + question_stats are
    both empty (fresh install).
    """
    ctx = _get_ctx(request)
    # #335: no longer clamp to de/en. Any language present in the loaded packs
    # (e.g. ``es``) flows through; the ``lang_packs`` filter + the empty-set
    # guard below already degrade cleanly to ``{}`` for a language with no
    # packs, so the spotlight simply has nothing to feature rather than
    # wrong-language fallback content.
    lang = (request.query.get("lang") or "de").lower()

    # Even day → most-played, odd day → most-difficult.
    # tm_yday is 1-based (Jan 1 = 1), so day 1 starts with most-difficult.
    day_of_year = _dt.datetime.now().timetuple().tm_yday
    logic = "most-played" if day_of_year % 2 == 0 else "most-difficult"

    bank = ctx.game.question_bank if ctx.game else None
    if bank is None:
        return web.json_response({})

    # Filter packs to the requested language.
    pack_versions = bank.get_pack_versions()
    lang_packs = {
        cat: meta for cat, meta in pack_versions.items()
        if meta.get("language", "de") == lang
    }
    if not lang_packs:
        return web.json_response({})

    # Seasonal auto-surfacing (#276) takes priority over the day-rotation:
    # if a pack's recurring season window is active *today*, it is pinned as
    # the Featured Spotlight regardless of most-played/most-difficult. Outside
    # every window this is a no-op and behaviour is exactly as before.
    today = _dt.date.today()
    seasons = {
        cat: parse_season(meta.get("season"))
        for cat, meta in lang_packs.items()
    }
    seasonal_slug = pick_active_season(seasons, today)

    chosen: str | None = None
    seasonal_active = False
    if seasonal_slug is not None:
        chosen = seasonal_slug
        seasonal_active = True
    elif logic == "most-played" and ctx.analytics is not None:
        try:
            metrics = ctx.analytics.compute_metrics("30d")
            cat_plays = {
                c["category"]: c.get("games_played", 0)
                for c in metrics.get("category_stats", [])
            }
            candidates = [
                (cat, cat_plays.get(cat, 0)) for cat in lang_packs
            ]
            candidates.sort(key=lambda x: x[1], reverse=True)
            if candidates and candidates[0][1] >= _FEATURED_MIN_PLAYS:
                chosen = candidates[0][0]
        except (KeyError, AttributeError, TypeError):
            chosen = None
    elif logic == "most-difficult" and ctx.question_stats is not None:
        try:
            bank_categories = bank.categories
            pack_rates: dict[str, float] = {}
            for cat in lang_packs:
                shown, correct = ctx.question_stats.aggregate_for_questions(
                    q.id for q in bank_categories.get(cat, [])
                )
                if shown >= _FEATURED_MIN_SHOWN:
                    pack_rates[cat] = correct / shown
            if pack_rates:
                chosen = min(pack_rates, key=pack_rates.__getitem__)
        except (KeyError, AttributeError, TypeError):
            chosen = None

    if seasonal_active:
        logic_used = "seasonal"
    elif chosen is None:
        # Fallback: prefer Geographie/Geography if present, else first pack.
        default = _FEATURED_DEFAULT_EN if lang == "en" else _FEATURED_DEFAULT_DE
        chosen = default if default in lang_packs else next(iter(lang_packs))
        logic_used = "default"
    else:
        logic_used = logic

    # Invariant: every branch above resolves `chosen` to a real pack slug — the
    # `chosen is None` fallback always assigns one, and the seasonal/logic
    # branches only reach the final `else` when `chosen` is already set. Assert
    # it so the downstream `seasons.get` / `lang_packs[...]` accesses are typed
    # as `str` (mypy can't correlate the separate `seasonal_active` flag).
    assert chosen is not None

    # The pinned pack's label (e.g. "🎄 Weihnachten") for the spotlight subtitle
    # and the picker badge. ``season_label`` is the active label or "".
    chosen_season = seasons.get(chosen)
    season_label = chosen_season.label if (seasonal_active and chosen_season) else ""

    meta = lang_packs[chosen]
    # Theme is captured into pack metadata at load time (#309), so read it from
    # there instead of re-opening + re-parsing the pack JSON per request. The
    # old per-request read used ``questions_dir / f"{chosen}.json"``, which
    # never resolved community packs (they live at
    # ``questions/community/<stem>.json`` under a ``community-`` slug) — so a
    # featured seasonal/most-played community pack always got the 🎲 default
    # icon instead of its real theme. Reading from metadata fixes that and drops
    # a blocking file read from the hot path.
    theme = meta.get("theme", "") or ""
    icon = _THEME_ICONS.get(theme, "🎲")

    count = meta.get("question_count", 0)
    # Localised subtitle strings per spotlight logic. In-season packs show their
    # own label (e.g. "🎄 Weihnachten") instead of a generic rotation reason.
    # Unknown languages fall back to the English copy (#335).
    sub_text = {
        "de": {
            "most-played": "Beliebt diese Woche",
            "most-difficult": "Härteste Herausforderung",
            "default": "Familienfreundlich",
            "seasonal": season_label or "Saisonal",
        },
        "en": {
            "most-played": "Popular this week",
            "most-difficult": "Hardest challenge",
            "default": "Family-friendly",
            "seasonal": season_label or "Seasonal",
        },
        "es": {
            "most-played": "Popular esta semana",
            "most-difficult": "El reto más difícil",
            "default": "Para toda la familia",
            "seasonal": season_label or "De temporada",
        },
    }
    unit = _COUNT_UNITS.get(lang, _COUNT_UNITS["en"])
    sub = sub_text.get(lang, sub_text["en"])[logic_used]

    return web.json_response({
        "value": chosen,
        "title": f"{icon} {meta.get('name', chosen)}",
        "meta": f"{count} {unit} · {sub}",
        "logic": logic_used,
        "is_seasonal": seasonal_active,
        "season_label": season_label,
    })


async def tts_entities_view(request: web.Request) -> web.Response:
    """List the TTS engines + media players the admin can pick for narration.

    Backs the two dropdowns in the admin TTS panel (#281): instead of forcing
    the host to type ``tts.*`` / ``media_player.*`` entity ids in the
    integration options, the panel offers the live entities directly. The
    chosen pair rides the start_game payload (per-game, localStorage-backed);
    the config-entry options stay as the fallback default.

    Returns ``{"tts": [...], "media_players": [...]}`` where each item is
    ``{entity_id, friendly_name}``, sorted by friendly_name. On the standalone
    dev server (no hass) both lists are empty — the dropdowns then show the
    graceful "configure in HA" fallback.
    """
    if not _is_admin_authenticated(request):
        return _unauthorized()
    ctx = _get_ctx(request)
    # Only the HA runtime exposes ``hass``; the standalone dev server does not.
    hass = getattr(ctx.runtime, "hass", None)
    if hass is None:
        return web.json_response({"tts": [], "media_players": []})

    def _entities(domain: str) -> list[dict]:
        items = [
            {
                "entity_id": state.entity_id,
                "friendly_name": state.attributes.get(
                    "friendly_name", state.entity_id
                ),
            }
            for state in hass.states.async_all(domain)
        ]
        items.sort(key=lambda e: e["friendly_name"].lower())
        return items

    return web.json_response(
        {"tts": _entities("tts"), "media_players": _entities("media_player")}
    )


async def analytics_data_view(request: web.Request) -> web.Response:
    """Return analytics data as JSON."""
    if not _is_admin_authenticated(request):
        return _unauthorized()
    ctx = _get_ctx(request)
    if not ctx.analytics:
        return web.json_response({"total_games": 0})

    period = request.query.get("period", "30d")
    return web.json_response(ctx.analytics.compute_metrics(period))


async def question_stats_view(request: web.Request) -> web.Response:
    """Return per-question difficulty stats.

    Query params:
      - ``mode``: ``hardest`` (default) or ``easiest``
      - ``limit``: 1..100, default 25
      - ``min_shown``: minimum times a question must have been shown
        before it counts, default 3 (filters out noisy one-off misses)
    """
    if not _is_admin_authenticated(request):
        return _unauthorized()
    ctx = _get_ctx(request)
    if ctx.question_stats is None:
        return web.json_response({"questions": []})

    mode = request.query.get("mode", "hardest")
    try:
        limit = max(1, min(int(request.query.get("limit", "25")), 100))
    except (TypeError, ValueError):
        limit = 25
    try:
        min_shown = max(1, int(request.query.get("min_shown", "3")))
    except (TypeError, ValueError):
        min_shown = 3

    if mode == "easiest":
        items = ctx.question_stats.get_easiest(limit=limit, min_shown=min_shown)
    else:
        items = ctx.question_stats.get_hardest(limit=limit, min_shown=min_shown)
    return web.json_response({"mode": mode, "questions": items})


async def all_time_leaderboard_view(request: web.Request) -> web.Response:
    """Return the all-time leaderboard (across every recorded game).

    Optional ``?limit=N`` (default 25, max 100). The list is sorted by
    total score with wins as tiebreaker. Each entry includes:
    games_played, total_score, wins, best_streak, streak_milestones_hit,
    last_played (unix seconds).
    """
    if not _is_admin_authenticated(request):
        return _unauthorized()
    ctx = _get_ctx(request)
    if not ctx.analytics:
        return web.json_response({"players": []})

    try:
        limit = int(request.query.get("limit", "25"))
    except (TypeError, ValueError):
        limit = 25
    limit = max(1, min(limit, 100))

    return web.json_response({
        "players": ctx.analytics.get_all_time_leaderboard(limit=limit),
    })


async def pack_versions_view(request: web.Request) -> web.Response:
    """Return installed pack version metadata.

    Seasonal packs (#276) are annotated per request with ``is_seasonal``
    (whether the recurring window is active *today*) and ``season_label`` (the
    label to badge, e.g. "🎄 Weihnachten") so the admin picker can render the
    badge without re-implementing the date math client-side.
    """
    ctx = _get_ctx(request)
    bank = ctx.game.question_bank
    # Packs are cached in memory after the first load; only pay the executor
    # hop + disk read when they haven't been loaded yet (mirrors
    # ``pack_update_check_view``). The bank is preloaded on setup, so this GET
    # is normally a pure in-memory read.
    if not bank.is_loaded:
        await ctx.runtime.run_in_executor(bank.load_all_categories)
    installed = bank.get_pack_versions()

    today = _dt.date.today()
    # ``get_pack_versions`` returns a shallow copy: the inner meta dicts are the
    # bank's own objects. Copy each before annotating so the per-request season
    # flags never leak into the cached pack metadata.
    annotated = {}
    for slug, meta in installed.items():
        meta = dict(meta)
        season = parse_season(meta.get("season"))
        in_season = season is not None and is_in_season(season, today)
        meta["is_seasonal"] = in_season
        meta["season_label"] = season.label if (in_season and season) else ""
        annotated[slug] = meta

    return web.json_response(annotated)


async def _fetch_upstream_versions(runtime: Runtime) -> dict | None:
    """Fetch versions.json from GitHub (best-effort, 5s timeout).

    Uses the runtime's shared client session (#456) instead of building a
    throwaway ``ClientSession`` per fetch. Returns the parsed manifest, or
    ``None`` on any non-200/error so callers can fall back gracefully.
    """
    try:
        timeout = aiohttp.ClientTimeout(total=5)
        session = runtime.get_client_session()
        async with session.get(_PACK_VERSIONS_URL, timeout=timeout) as resp:
            if resp.status == 200:
                return await resp.json(content_type=None)
    except Exception as exc:  # noqa: BLE001
        _LOGGER.warning("Pack update check failed: %s", exc)
    return None


async def _get_upstream_versions(runtime: Runtime) -> dict | None:
    """Return the upstream versions.json, refreshing on cache-miss/TTL expiry.

    Repeated calls within ``_UPSTREAM_CACHE_TTL_SECONDS`` reuse the cached copy
    so the unauthenticated update-check endpoint can't be looped to hammer
    GitHub (#360). Only successful fetches are cached; a failed fetch returns
    ``None`` without poisoning the cache, so the next request retries — the same
    best-effort behaviour as before caching.
    """
    global _upstream_versions_cache
    now = time.monotonic()
    cache = _upstream_versions_cache
    if cache is not None and now - cache[0] < _UPSTREAM_CACHE_TTL_SECONDS:
        return cache[1]

    upstream = await _fetch_upstream_versions(runtime)
    if upstream is not None:
        _upstream_versions_cache = (now, upstream)
    return upstream


async def pack_update_check_view(request: web.Request) -> web.Response:
    """Check GitHub for updated question packs.

    The upstream versions.json fetch is cached in-memory (#360) so repeated
    unauthenticated calls don't make the HA host hammer GitHub. The pack reload
    is likewise skipped once the bank is already loaded.
    """
    ctx = _get_ctx(request)
    bank = ctx.game.question_bank
    # Packs are cached in memory after the first load; only pay the executor
    # hop + disk read when they haven't been loaded yet.
    if not bank.is_loaded:
        await ctx.runtime.run_in_executor(bank.load_all_categories)
    installed = bank.get_pack_versions()

    upstream = await _get_upstream_versions(ctx.runtime)

    updates = []
    if upstream:
        for slug, meta in installed.items():
            upstream_version = upstream.get(slug)
            if upstream_version and upstream_version != meta["version"]:
                updates.append(
                    {
                        "slug": slug,
                        "name": meta["name"],
                        "installed_version": meta["version"],
                        "upstream_version": upstream_version,
                    }
                )

    return web.json_response(
        {
            "installed": installed,
            "upstream": upstream,
            "updates": updates,
            "upstream_available": upstream is not None,
        }
    )


# ---------------------------------------------------------------------------
# Question flagging
# ---------------------------------------------------------------------------
#
# Append-only JSONL log of player-flagged questions. Lives next to the
# other dev/HA state in the runtime's data_dir. Each line is one report;
# the pack maintainer can `jq` through it to find ambiguous or wrong
# questions surfaced by real play.

_FLAG_FILE = "flagged.jsonl"
_FLAG_MAX_BYTES = 256 * 1024  # cap at ~256 KB to bound disk use
_FLAG_REASON_MAX = 200
# Cap the caller-supplied question_id (#357). Without a bound an anonymous POST
# could append a ~1 MB entry, and the size-trim runs BEFORE the append so an
# oversized single line would still persist. Real ids are short slugs.
_FLAG_QUESTION_ID_MAX = 64
# Per-IP rate limit on the unauthenticated flag POST (#357). Mirrors the
# pack-submit guard: generous for a real "flag a few questions" burst, tight
# enough that the endpoint can't be hammered into unbounded executor disk
# writes. Empty buckets are evicted by the limiter so the dict stays bounded.
_FLAG_RATE_LIMIT_REQUESTS = 5
_FLAG_RATE_LIMIT_WINDOW = 60  # seconds
_flag_rate_limiter = SlidingWindowLimiter(
    _FLAG_RATE_LIMIT_REQUESTS, _FLAG_RATE_LIMIT_WINDOW
)


async def flag_question_view(request: web.Request) -> web.Response:
    """Record a player's flag on a question.

    POST body: {"question_id": "geo_037", "reason": "...", "player_name": "..."}
    All fields optional except question_id. reason is truncated; player_name
    is best-effort (clients without an auth model can lie, but that's fine
    for a "raise the maintainer's attention" signal).
    """
    ctx = _get_ctx(request)

    # Per-IP rate limit (#357). Reject early — before the JSON parse and the
    # executor disk write — so a flood can't pin the loop or grow the file.
    if not _flag_rate_limiter.check(request.remote or ""):
        return web.json_response({"error": "rate_limited"}, status=429)

    try:
        body = await request.json()
    except (ValueError, TypeError):
        return web.json_response({"error": "invalid_json"}, status=400)

    question_id = (body or {}).get("question_id")
    if not isinstance(question_id, str) or not question_id:
        return web.json_response({"error": "missing_question_id"}, status=400)
    # Bound the caller-supplied id before it is persisted (#357).
    question_id = question_id[:_FLAG_QUESTION_ID_MAX]

    reason = str((body or {}).get("reason", ""))[:_FLAG_REASON_MAX]
    player_name = str((body or {}).get("player_name", ""))[:50]

    entry = {
        "ts": int(time.time()),
        "question_id": question_id,
        "reason": reason,
        "player_name": player_name,
        "remote": request.remote or "",
    }

    flag_path = ctx.runtime.data_dir / _FLAG_FILE

    def _append() -> None:
        flag_path.parent.mkdir(parents=True, exist_ok=True)
        # Refuse to grow unbounded — drop oldest half if we hit the cap.
        # JSONL is append-friendly, this is a rare event, so a copy-trim
        # is acceptable. Keeps the cap from being silently bypassed.
        if flag_path.exists() and flag_path.stat().st_size >= _FLAG_MAX_BYTES:
            try:
                lines = flag_path.read_text("utf-8").splitlines()
                flag_path.write_text("\n".join(lines[len(lines) // 2:]) + "\n", "utf-8")
            except OSError:
                pass
        with flag_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")

    await ctx.runtime.run_in_executor(_append)
    _LOGGER.info("Question flagged: %s (reason=%r)", question_id, reason[:40])
    return web.json_response({"ok": True})


async def flag_list_view(request: web.Request) -> web.Response:
    """Return all flagged questions as JSON.

    Used by the analytics dashboard. Host-only — gated on the admin session
    token (#356) because the entries carry player names + free-text flag
    reasons. The stored client IP is additionally stripped from the response
    (#305).
    """
    if not _is_admin_authenticated(request):
        return _unauthorized()
    ctx = _get_ctx(request)
    flag_path = ctx.runtime.data_dir / _FLAG_FILE

    def _read() -> list[dict]:
        if not flag_path.exists():
            return []
        entries: list[dict] = []
        for line in flag_path.read_text("utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                entries.append(json.loads(line))
            except ValueError:
                continue
        return entries

    entries = await ctx.runtime.run_in_executor(_read)
    # #305: never return the stored client IP (``remote``) to callers — the
    # /api/quizify/* routes are added to hass.http.app.router with NO HA auth,
    # so /flags is readable unauthenticated. The IP is still stored on disk for
    # operator forensics; it is simply stripped from the response so an
    # anonymous caller can't enumerate the IPs of everyone who flagged a
    # question. Strip it defensively per entry (older entries may pre-date this).
    sanitized = [{k: v for k, v in e.items() if k != "remote"} for e in entries]
    return web.json_response({"flags": sanitized})


# ---------------------------------------------------------------------------
# Route registration
# ---------------------------------------------------------------------------

# Each entry is (method, path, handler). Kept as data so the HA adapter and
# the standalone server can register the same set without duplication.
async def _gated_submissions_list_view(request: web.Request) -> web.StreamResponse:
    """Admin-token gate (#356) in front of the imported submissions list.

    The submissions list is a host-review surface (community pack proposals);
    keep it behind the same admin session token as the other data endpoints
    without editing the pack_submission module.
    """
    if not _is_admin_authenticated(request):
        return _unauthorized()
    return await submissions_list_view(request)


ROUTES: list[tuple[str, str, _RouteHandler]] = [
    ("GET", "/quizify/admin", admin_view),
    ("GET", "/quizify/launcher", launcher_view),
    ("GET", "/quizify/player", player_view),
    ("GET", "/quizify/dashboard", dashboard_view),
    ("GET", "/quizify/analytics", analytics_view),
    # Service worker is a Python view (NOT a static file) so the
    # {{VERSION}} placeholder in sw.js gets substituted. Registered
    # before add_static so the templated copy wins over the raw file.
    ("GET", "/quizify/static/sw.js", sw_view),
    ("GET", "/api/quizify/game-status", game_status_view),
    ("GET", "/api/quizify/status", status_view),
    ("GET", "/api/quizify/featured-pack", featured_pack_view),
    ("GET", "/api/quizify/tts-entities", tts_entities_view),
    ("GET", "/api/quizify/analytics/data", analytics_data_view),
    ("GET", "/api/quizify/all-time", all_time_leaderboard_view),
    ("GET", "/api/quizify/question-stats", question_stats_view),
    ("GET", "/api/quizify/packs", pack_versions_view),
    ("GET", "/api/quizify/packs/updates", pack_update_check_view),
    ("POST", "/api/quizify/flag-question", flag_question_view),
    ("GET", "/api/quizify/flags", flag_list_view),
    # Community pack submission (#180). Inert until community_submit_url is set:
    # the config endpoint reports enabled:false and POSTs are refused.
    ("GET", "/api/quizify/pack-submit/config", submit_config_view),
    ("GET", "/api/quizify/pack-submit/submissions", _gated_submissions_list_view),
    ("POST", "/api/quizify/pack-submit", submit_pack_view),
]


def register_routes(router: web.UrlDispatcher) -> None:
    """Register Quizify HTTP routes on the given aiohttp router.

    Used by both the HA adapter (``hass.http.app.router``) and the
    standalone server (``aiohttp.web.Application().router``).
    """
    for method, path, handler in ROUTES:
        router.add_route(method, path, handler)
