"""HTTP handlers for Quizify (plain aiohttp, no Home Assistant coupling).

The HA integration and the standalone dev server both register the same
handlers — only the static-asset registration differs (HA uses
``async_register_static_paths``; standalone uses aiohttp's ``add_static``).
"""

from __future__ import annotations

import datetime as _dt
import hashlib
import json
import logging
import os
import time
from pathlib import Path
from typing import TYPE_CHECKING

import aiohttp
from aiohttp import web

from ..game.seasons import is_in_season, parse_season, pick_active_season
from .context import APP_CTX_KEY
from .pack_submission import (
    submissions_list_view,
    submit_config_view,
    submit_pack_view,
)
from .serializers import build_game_status_response

if TYPE_CHECKING:
    from .context import AppContext


# GitHub raw URL for pack version manifests
_PACK_VERSIONS_URL = (
    "https://raw.githubusercontent.com/mholzi/quizify/main/"
    "custom_components/quizify/questions/versions.json"
)

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
# integration would re-parse manifest.json on every request; with it we
# only re-read when the file actually changed (which means a deploy
# happened). Tuple of (mtime_ns, version).
_MANIFEST_CACHE: tuple[int, str] | None = None


def _get_ctx(request: web.Request) -> AppContext:
    """Pull the AppContext stashed on the aiohttp application."""
    return request.app[APP_CTX_KEY]


def _get_live_version(fallback: str) -> str:
    """Read the live manifest version, busting the cache when the file changes.

    Why this exists: ``ctx.version`` is set at integration setup time, so a
    direct-rsync deploy (manifest.json updated on disk without an HA
    integration reload) would leave ``ctx.version`` stale. The HTML asset
    URLs use ``?v={{VERSION}}`` for cache-busting; if VERSION never moves,
    browsers serve the stale CSS/JS forever after a deploy and the user
    has to manually clear cache. Re-reading manifest.json on mtime change
    fixes the cache-bust round-trip end-to-end.

    Falls back to ``fallback`` (typically ``ctx.version``) if the file is
    missing or unreadable — defensive, since this code path runs on every
    HTML request.
    """
    global _MANIFEST_CACHE
    try:
        mtime_ns = os.stat(_MANIFEST_PATH).st_mtime_ns
        if _MANIFEST_CACHE is not None and _MANIFEST_CACHE[0] == mtime_ns:
            return _MANIFEST_CACHE[1]
        data = json.loads(_MANIFEST_PATH.read_text(encoding="utf-8"))
        version = str(data.get("version", fallback))
        _MANIFEST_CACHE = (mtime_ns, version)
        return version
    except (OSError, ValueError, KeyError) as exc:
        _LOGGER.debug("Could not read live version from manifest: %s", exc)
        return fallback


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


async def _serve_html(request: web.Request, filename: str) -> web.Response:
    """Read a file from www/, substitute {{VERSION}}, return as HTML."""
    html_path = _WWW_DIR / filename
    if not html_path.exists():
        _LOGGER.error("Page not found: %s", html_path)
        return web.Response(text=f"{filename} not found", status=500)

    ctx = _get_ctx(request)
    version = _get_live_version(ctx.version)
    html_content = await ctx.runtime.run_in_executor(html_path.read_text, "utf-8")
    html_content = _apply_version(html_content, version)
    asset_version = await _get_asset_version_async(ctx, version)
    html_content = html_content.replace(_ASSET_VER_TOKEN, asset_version)
    html_content = html_content.replace(_HA_LANG_TOKEN, ctx.ha_language or "")
    return web.Response(
        text=html_content, content_type="text/html", headers=_NO_CACHE_HEADERS
    )


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
    body = await ctx.runtime.run_in_executor(sw_path.read_text, "utf-8")
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
    lang = (request.query.get("lang") or "de").lower()
    if lang not in ("de", "en"):
        lang = "de"

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
    if lang == "de":
        unit = "Fragen"
        sub = {
            "most-played": "Beliebt diese Woche",
            "most-difficult": "Härteste Herausforderung",
            "default": "Familienfreundlich",
            # In-season packs show their own label (e.g. "🎄 Weihnachten") as
            # the spotlight subtitle instead of a generic rotation reason.
            "seasonal": season_label or "Saisonal",
        }[logic_used]
    else:
        unit = "questions"
        sub = {
            "most-played": "Popular this week",
            "most-difficult": "Hardest challenge",
            "default": "Family-friendly",
            "seasonal": season_label or "Seasonal",
        }[logic_used]

    return web.json_response({
        "value": chosen,
        "title": f"{icon} {meta.get('name', chosen)}",
        "meta": f"{count} {unit} · {sub}",
        "logic": logic_used,
        "is_seasonal": seasonal_active,
        "season_label": season_label,
    })


async def analytics_data_view(request: web.Request) -> web.Response:
    """Return analytics data as JSON."""
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
    await ctx.runtime.run_in_executor(ctx.game.question_bank.load_all_categories)
    installed = ctx.game.question_bank.get_pack_versions()

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


async def pack_update_check_view(request: web.Request) -> web.Response:
    """Check GitHub for updated question packs."""
    ctx = _get_ctx(request)
    await ctx.runtime.run_in_executor(ctx.game.question_bank.load_all_categories)
    installed = ctx.game.question_bank.get_pack_versions()

    # Fetch upstream versions.json from GitHub (best-effort, 5s timeout)
    upstream: dict | None = None
    try:
        timeout = aiohttp.ClientTimeout(total=5)
        async with (
            aiohttp.ClientSession(timeout=timeout) as session,
            session.get(_PACK_VERSIONS_URL) as resp,
        ):
            if resp.status == 200:
                upstream = await resp.json(content_type=None)
    except Exception as exc:  # noqa: BLE001
        _LOGGER.warning("Pack update check failed: %s", exc)

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


async def flag_question_view(request: web.Request) -> web.Response:
    """Record a player's flag on a question.

    POST body: {"question_id": "geo_037", "reason": "...", "player_name": "..."}
    All fields optional except question_id. reason is truncated; player_name
    is best-effort (clients without an auth model can lie, but that's fine
    for a "raise the maintainer's attention" signal).
    """
    ctx = _get_ctx(request)
    try:
        body = await request.json()
    except (ValueError, TypeError):
        return web.json_response({"error": "invalid_json"}, status=400)

    question_id = (body or {}).get("question_id")
    if not isinstance(question_id, str) or not question_id:
        return web.json_response({"error": "missing_question_id"}, status=400)

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

    Used by the analytics dashboard. Not exposed to players — but no auth
    is enforced here (matches the rest of the API). On HA the auth layer
    above us handles it; on standalone the home LAN is trusted.
    """
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
ROUTES: list[tuple[str, str, object]] = [
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
    ("GET", "/api/quizify/pack-submit/submissions", submissions_list_view),
    ("POST", "/api/quizify/pack-submit", submit_pack_view),
]


def register_routes(router: web.UrlDispatcher) -> None:
    """Register Quizify HTTP routes on the given aiohttp router.

    Used by both the HA adapter (``hass.http.app.router``) and the
    standalone server (``aiohttp.web.Application().router``).
    """
    for method, path, handler in ROUTES:
        router.add_route(method, path, handler)
