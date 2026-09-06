"""The service worker precaches per surface, not one list for everyone (#791).

`PRECACHE_ASSETS` was a single list shared by admin, player and dashboard, so a
player phone downloaded `admin.js` (183 KB) and `pack-submit.js` (22 KB) it can
never execute, both icon PNGs and both i18n bundles — 1,148,049 bytes for a page
that loads roughly 700 KB. Every entry went out with `cache: 'reload'`, which
bypasses the HTTP cache, so `player.bundle.js` and `styles.css` were fetched
twice on a phone's first visit and again after every release, because `?v=`
moves.

Thirteen phones and a TV join inside a minute at the start of a game. That was
about 25 MB off a Pi over party Wi-Fi in exactly the minute the host needs the
lobby to fill.

These are source-level guards on `www/sw.js`: the lists are parsed out of the
file and checked against the assets each page's HTML actually references, and
against their real size on disk. A regression here is invisible in the browser
until someone counts bytes on a phone, which is why it survived this long.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from tests.conftest import without_comments

REPO = Path(__file__).resolve().parent.parent
WWW = REPO / "custom_components" / "quizify" / "www"
SW = WWW / "sw.js"

# The precache list as it stood before this fix, byte for byte. Kept as the
# number to beat rather than as prose.
LEGACY_PRECACHE = (
    "css/styles.css",
    "js/i18n.js",
    "js/utils.js",
    "js/icons.js",
    "js/admin.js",
    "js/pack-submit.js",
    "js/player.bundle.js",
    "js/sw-update.js",
    "js/vendor/qrcode.min.js",
    "i18n/de.json",
    "i18n/en.json",
    "site.webmanifest",
    "img/icon-256.png",
    "img/icon-512.png",
    "fonts/dm-sans-latin.woff2",
    "fonts/jetbrains-mono-latin.woff2",
)

PAGE_FOR_SURFACE = {
    "player": "player.html",
    "admin": "admin.html",
    "dashboard": "dashboard.html",
}


def _sw() -> str:
    """The SW source with comments stripped (#811's helper, earning its keep).

    The prose around these lists is full of apostrophes — "a font file's bytes"
    — and a raw `'([^']+)'` scan reads them as string literals.
    """
    return without_comments(SW.read_text("utf-8"))


def _sw_raw() -> str:
    return SW.read_text("utf-8")


def _string_list(source: str, name: str) -> list[str]:
    """Pull a `var NAME = [ '…', '…' ];` array of string literals out of the SW."""
    start = source.index(f"{name} = [")
    end = source.index("]", start)
    return re.findall(r"'([^']+)'", source[start:end])


def _by_page(source: str) -> dict[str, list[str]]:
    """Pull the `PRECACHE_BY_PAGE = { player: [...], … }` object out of the SW."""
    start = source.index("PRECACHE_BY_PAGE = {")
    end = source.index("\n};", start)
    block = source[start:end]
    out: dict[str, list[str]] = {}
    for match in re.finditer(r"(\w+)\s*:\s*\[([^\]]*)\]", block):
        out[match.group(1)] = re.findall(r"'([^']+)'", match.group(2))
    return out


def _rel(url: str) -> str:
    return url.split("?")[0].removeprefix("/quizify/static/")


def _bytes(rels: list[str]) -> int:
    return sum((WWW / rel).stat().st_size for rel in rels)


def _precache_for(surface: str, *, default_lang: str = "de") -> list[str]:
    """What a device showing ``surface`` precaches, as relative www/ paths.

    ``default_lang`` is the worst case: an install that renders in a language
    other than English carries `en.json` (i18n.js's fallback dictionary) plus
    its own bundle.
    """
    source = _sw()
    urls = _string_list(source, "PRECACHE_CORE") + _by_page(source)[surface]
    rels = [_rel(url) for url in urls]
    if default_lang != "en":
        rels.append(f"i18n/{default_lang}.json")
    return rels


# ---- the split exists at all ----


def test_the_precache_is_split_into_a_core_and_per_page_extras() -> None:
    source = _sw()
    assert "PRECACHE_ASSETS" not in source, (
        "the one-list-for-everyone precache is what #791 removes"
    )
    core = _string_list(source, "PRECACHE_CORE")
    by_page = _by_page(source)
    assert core, "PRECACHE_CORE is empty"
    assert set(by_page) == {"player", "admin", "dashboard"}


@pytest.mark.parametrize("surface", sorted(PAGE_FOR_SURFACE))
def test_every_precached_file_exists(surface: str) -> None:
    """Guards list drift — the old player-core.js entries outlived the bundle."""
    for rel in _precache_for(surface):
        assert (WWW / rel).is_file(), f"{surface}: precache points at missing {rel}"


# ---- the split is complete: nothing a page loads went missing ----


@pytest.mark.parametrize("surface,page", sorted(PAGE_FOR_SURFACE.items()))
def test_the_surface_precaches_everything_its_page_loads(
    surface: str, page: str
) -> None:
    """Splitting a list is only safe if the split is driven by the pages.

    Read the `/quizify/static/` references straight out of the page and require
    every one of them in that surface's precache. Adding a script to player.html
    and forgetting the SW now fails here instead of costing a round trip on a
    phone in a lobby.
    """
    html = (WWW / page).read_text("utf-8")
    referenced = {
        _rel(ref)
        for ref in re.findall(r"""["'](/quizify/static/[^"']+)["']""", html)
        if not ref.endswith("/")
    }
    precached = set(_precache_for(surface))
    missing = sorted(referenced - precached)
    assert not missing, f"{page} loads {missing}, which {surface} does not precache"


# ---- the split is worth something: the admin-only bytes are gone ----


def test_a_player_phone_no_longer_precaches_the_admin_console() -> None:
    player = set(_precache_for("player"))
    for admin_only in ("js/admin.js", "js/pack-submit.js"):
        assert admin_only not in player, (
            f"a player phone still downloads {admin_only}, which it never executes"
        )


def test_the_host_console_no_longer_precaches_the_player_bundle() -> None:
    assert "js/player.bundle.js" not in set(_precache_for("admin"))


def test_only_the_fallback_and_the_active_language_bundle_are_precached() -> None:
    """Three bundles ship (de/en/es); a device renders in one of them.

    en.json is not optional — i18n.js loads it as the fallback dictionary
    whatever the active language is — so the rule is "en plus the active one",
    never "all of them".
    """
    shipped = {p.name for p in (WWW / "i18n").glob("*.json")}
    assert len(shipped) > 2, "expected more than two bundles, else this proves nothing"

    core = {_rel(url) for url in _string_list(_sw(), "PRECACHE_CORE")}
    bundles = {rel for rel in core if rel.startswith("i18n/")}
    assert bundles == {"i18n/en.json"}, (
        f"the static core precaches {sorted(bundles)}; only the fallback belongs "
        "there, the active one is appended from {{DEFAULT_LANG}}"
    )
    assert "{{DEFAULT_LANG}}" in _sw_raw(), (
        "sw.js must learn the active language from the server, or it cannot "
        "precache one bundle instead of all of them"
    )


def test_the_512px_icon_is_left_to_the_runtime_cache() -> None:
    """107 KB that only the install prompt and the splash screen ever read."""
    for surface in PAGE_FOR_SURFACE:
        assert "img/icon-512.png" not in set(_precache_for(surface))


def test_a_player_phone_precaches_substantially_fewer_bytes() -> None:
    """The number this issue is about.

    Worst case on both sides: a non-English install, counting uncompressed
    on-disk sizes.
    """
    legacy = _bytes(list(LEGACY_PRECACHE))
    player = _bytes(_precache_for("player"))
    assert legacy > 1_100_000, "the legacy list is the baseline; it should be huge"
    assert player < legacy * 0.75, (
        f"player precache is {player:,} B against the old {legacy:,} B — "
        "the split is not buying enough to be worth the machinery"
    )


# ---- versioned URLs are not downloaded twice ----


def test_versioned_precache_entries_reuse_the_http_cache() -> None:
    """`cache: 'reload'` on a `?v=` URL is a guaranteed second download.

    The buster is `<version>-<fingerprint>`: that exact URL can only ever mean
    those exact bytes, so the entry the page just filled is by definition the
    right answer. Reload bypassed it and pulled the 330 KB bundle down again.
    """
    source = _sw()
    assert "precacheRequest" in source, "the reload decision must be one function"

    start = source.index("function precacheRequest(")
    end = source.index("\n}", start)
    body = source[start:end]

    assert "indexOf('?v=')" in body, (
        "precacheRequest must branch on whether the URL is versioned"
    )
    reload_at = body.index("cache: 'reload'")
    plain_at = body.index("return new Request(url);")
    assert plain_at < reload_at, (
        "the versioned branch must return the plain Request; reload is the "
        "un-versioned fallback"
    )


def test_the_unversioned_fonts_still_bypass_the_http_cache() -> None:
    """The reason `cache: 'reload'` existed, and the one case that keeps it.

    Font URLs carry no buster, and HA serves /quizify/static/* with a 31-day
    max-age, so a plain cache.add() could seed a brand-new release's cache from
    a month-old HTTP entry.
    """
    core = [_rel(url) for url in _string_list(_sw(), "PRECACHE_CORE")]
    assert any(rel.startswith("fonts/") for rel in core)
    assert "cache: 'reload'" in _sw()


# ---- the surface is resolved, not guessed ----


def test_an_unknown_surface_falls_back_to_the_core_list() -> None:
    """The failure mode has to be "one round trip", never "download everything".

    `pageForClientUrl` returns null for /quizify/launcher, /quizify/analytics and
    anything unrecognised, and the union starts from the core list — so a SW that
    installs with no window it can place still precaches only shared assets.
    """
    source = _sw()
    assert "function pageForClientUrl(" in source
    assert "PRECACHE_CORE.slice()" in source, (
        "the union must start from the core list, not from every page's extras"
    )
    assert "includeUncontrolled: true" in source, (
        "matchAll during install sees the registering window only with "
        "includeUncontrolled"
    )
