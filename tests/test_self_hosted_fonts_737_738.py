"""Fonts are served from the integration, never from a third-party CDN.

Two findings, one fix (#737, #738):

* **#738 — privacy and offline.** Every player phone, the admin page and the
  television used to fetch DM Sans and JetBrains Mono from
  ``fonts.googleapis.com`` / ``fonts.gstatic.com`` and Cabinet Grotesk from
  ``api.fontshare.com`` on every load. The README promises "No data leaves
  your network"; each guest's IP and user agent went to Google and to the
  Indian Type Foundry instead. An offline install simply never got the faces.
* **#737 — first paint.** Those were ``<link rel="stylesheet">`` tags ahead of
  ``styles.css``. A stylesheet link blocks rendering until it answers *or
  fails*, so an isolated guest network meant a white join screen until the
  connect timeout expired.

Self-hosting settles both, so these tests guard both: no CDN reference may
come back anywhere under ``www/``, the ``.woff2`` files must actually be on
disk, every ``@font-face`` must point at one of them, and every face must
carry ``font-display`` so text is never invisible while a face loads.

Cabinet Grotesk is deliberately absent: it is Fontshare / ITF Free Font
License, which forbids redistributing the font files, so it cannot ship
inside this MIT repo. The display role falls to DM Sans.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
WWW = REPO / "custom_components" / "quizify" / "www"
FONTS = WWW / "fonts"

# The hosts that used to be contacted on every page load.
FORBIDDEN_HOSTS = ("fonts.googleapis.com", "fonts.gstatic.com", "api.fontshare.com")

# Pages that carry a <head> and used to carry the blocking font links.
PAGES = ("player.html", "admin.html", "dashboard.html", "launcher.html")

# Every face that must be on disk, with the floor its byte size may not fall
# through (a truncated or LFS-pointer file would otherwise pass "exists").
EXPECTED_FACES = {
    "dm-sans-latin.woff2": 40_000,
    "dm-sans-latin-ext.woff2": 20_000,
    "dm-sans-italic-latin.woff2": 15_000,
    "dm-sans-italic-latin-ext.woff2": 8_000,
    "jetbrains-mono-latin.woff2": 20_000,
    "jetbrains-mono-latin-ext.woff2": 6_000,
}

# The two faces every screen needs. Preloaded on each page and precached by
# the service worker, so a cold offline install still renders in the right type.
ALWAYS_USED = ("dm-sans-latin.woff2", "jetbrains-mono-latin.woff2")


# Everything under www/ that a browser actually loads. Markdown is out on
# purpose: www/fonts/README.md has to be able to name the CDNs and the face
# this change removed, and no browser ever fetches it.
SERVED_SUFFIXES = {".html", ".css", ".js", ".json", ".webmanifest", ".svg"}


def _served_files() -> list[Path]:
    """Every file under www/ that a browser loads."""
    return [
        p
        for p in sorted(WWW.rglob("*"))
        if p.is_file() and p.suffix.lower() in SERVED_SUFFIXES
    ]


def test_no_font_cdn_anywhere_under_www() -> None:
    """#738: not one byte under www/ may point at Google Fonts or Fontshare.

    Deliberately a whole-tree grep and not a per-file check: the CDN reference
    lived in six places at once (four <head>s, the CSS source, the built CSS)
    and the built artifact is easy to forget.
    """
    offenders: list[str] = []
    for path in _served_files():
        text = path.read_text("utf-8", errors="replace")
        for host in FORBIDDEN_HOSTS:
            if host in text:
                offenders.append(f"{path.relative_to(REPO)} -> {host}")

    assert not offenders, (
        "font CDN reference is back under www/ — this breaks the README's "
        "'no data leaves your network' promise (#738) and blocks the first "
        "paint on a slow or offline guest network (#737). Serve the face from "
        "www/fonts/ instead:\n  " + "\n  ".join(offenders)
    )


@pytest.mark.parametrize("page", PAGES)
def test_page_head_loads_no_external_stylesheet(page: str) -> None:
    """#737: no <link rel="stylesheet"> may point off-origin on any page.

    The blocking-render cost is the point, so the assertion is about the tag,
    not about the host: any absolute http(s) stylesheet href is a first-paint
    dependency on someone else's DNS, TLS and uptime.
    """
    html = (WWW / page).read_text("utf-8")
    external = [
        tag
        for tag in re.findall(r"<link\b[^>]*>", html, re.I)
        if "stylesheet" in tag.lower() and re.search(r'href="https?://', tag, re.I)
    ]
    assert not external, (
        f"{page} loads a stylesheet from a foreign host, which blocks the first "
        f"paint until it answers or times out (#737): {external}"
    )


@pytest.mark.parametrize("filename,floor", sorted(EXPECTED_FACES.items()))
def test_font_file_is_present_and_real(filename: str, floor: int) -> None:
    """The vendored faces exist, are woff2, and are not truncated."""
    path = FONTS / filename
    assert path.is_file(), (
        f"missing vendored face {filename} — the CSS references it, so an "
        f"install would 404 and fall back to system type"
    )
    blob = path.read_bytes()
    assert blob[:4] == b"wOF2", f"{filename} is not a woff2 file (magic={blob[:4]!r})"
    assert len(blob) >= floor, f"{filename} looks truncated ({len(blob)} bytes)"


def test_only_woff2_is_shipped() -> None:
    """One format, not six. Every browser this game runs on supports woff2."""
    extra = [
        p.name
        for p in FONTS.iterdir()
        if p.is_file() and p.suffix.lower() in {".woff", ".ttf", ".otf", ".eot", ".svg"}
    ]
    assert not extra, f"only woff2 belongs in www/fonts/, found: {extra}"


def test_font_faces_resolve_to_files_on_disk() -> None:
    """Every @font-face in the built CSS points at a file that is actually here."""
    css = (WWW / "css" / "styles.css").read_text("utf-8")
    blocks = re.findall(r"@font-face\s*\{(.*?)\}", css, re.S)
    assert blocks, "styles.css declares no @font-face — the faces would never load"

    referenced = set()
    for block in blocks:
        urls = re.findall(r"url\(['\"]?([^'\")]+)['\"]?\)", block)
        assert urls, f"@font-face block with no src url:\n{block}"
        for url in urls:
            assert url.startswith("/quizify/static/fonts/"), (
                f"@font-face src must be served by the integration, got {url!r}"
            )
            name = url.rsplit("/", 1)[-1]
            assert (FONTS / name).is_file(), f"@font-face points at missing file {name}"
            referenced.add(name)

    assert referenced == set(EXPECTED_FACES), (
        "the faces declared in styles.css and the files in www/fonts/ have "
        f"drifted apart: declared={sorted(referenced)} on-disk="
        f"{sorted(EXPECTED_FACES)}"
    )


def test_every_face_sets_font_display() -> None:
    """#737: text must never be invisible while a face is still loading."""
    sources = [WWW / "css" / "styles.css", WWW / "css" / "src" / "00-tokens.css",
               WWW / "launcher.html"]
    for path in sources:
        text = path.read_text("utf-8")
        for block in re.findall(r"@font-face\s*\{(.*?)\}", text, re.S):
            assert "font-display" in block, (
                f"{path.name} has an @font-face without font-display — a slow "
                f"face would hide the text behind it:\n{block}"
            )


def test_launcher_declares_its_own_faces() -> None:
    """The launcher does not load styles.css, so it needs its own @font-face.

    Easy to miss: dropping the CDN <link> from a page that has no stylesheet
    of its own leaves it with no font declarations at all.
    """
    html = (WWW / "launcher.html").read_text("utf-8")
    assert not re.search(r'<link[^>]+href="[^"]*styles\.css', html), (
        "launcher.html now loads styles.css — this test's premise is stale"
    )
    assert "@font-face" in html, (
        "launcher.html is self-contained and would render in system type "
        "without its own @font-face declarations"
    )
    for name in ALWAYS_USED:
        assert name in html, f"launcher.html does not declare {name}"


@pytest.mark.parametrize("page", PAGES)
def test_page_preloads_the_always_used_faces(page: str) -> None:
    """#737: start the font download with the stylesheet, not after it."""
    html = (WWW / page).read_text("utf-8")
    for name in ALWAYS_USED:
        pattern = (
            r'<link[^>]+rel="preload"[^>]+href="/quizify/static/fonts/'
            + re.escape(name)
        )
        assert re.search(pattern, html), f"{page} does not preload {name}"


def test_service_worker_precaches_fonts_without_cache_buster() -> None:
    """Offline installs need the faces in the precache.

    The URLs must carry no ``?v=`` — ``caches.match`` is exact on the query
    string and the CSS asks for the files plain, so a versioned precache entry
    would be dead weight that never serves a request.
    """
    sw = (WWW / "sw.js").read_text("utf-8")
    for name in ALWAYS_USED:
        assert f"'/quizify/static/fonts/{name}'" in sw, (
            f"sw.js does not precache {name}; an offline install would have no face"
        )
    assert f"fonts/{ALWAYS_USED[0]}?v=" not in sw, (
        "precached font URL carries a cache-buster the CSS never sends"
    )


def test_ofl_licences_ship_with_the_fonts() -> None:
    """A licence audit looks next to the files. Both OFL texts are there."""
    for name, holder in (
        ("DM_Sans-OFL.txt", "DM Sans"),
        ("JetBrains_Mono-OFL.txt", "JetBrains Mono"),
    ):
        path = FONTS / name
        assert path.is_file(), f"missing licence text {name} for a redistributed font"
        text = path.read_text("utf-8")
        assert "SIL OPEN FONT LICENSE" in text.upper(), f"{name} is not an OFL text"
        assert holder in text, f"{name} does not name {holder}"


def test_cabinet_grotesk_is_not_vendored() -> None:
    """The one face that may not be redistributed is not in the tree.

    Cabinet Grotesk is Indian Type Foundry's, under the ITF Free Font License,
    which Fontshare itself files as a closed-source licence: free to use, not
    free to redistribute. Shipping it inside an MIT repo would be exactly the
    redistribution it forbids, so it is dropped rather than vendored.
    """
    assert not list(FONTS.glob("*abinet*")), (
        "a Cabinet Grotesk file is in www/fonts/ — the ITF Free Font License "
        "does not permit redistributing it inside this MIT repo"
    )
    kinds = {".html", ".css", ".js"}
    runtime = [p for p in _served_files() if p.suffix.lower() in kinds]
    offenders = [
        str(p.relative_to(REPO)) for p in runtime
        if "Cabinet Grotesk" in p.read_text("utf-8", errors="replace")
    ]
    assert not offenders, (
        "a stylesheet or page still asks for Cabinet Grotesk, which is neither "
        f"bundled nor fetched — it would silently fall back: {offenders}"
    )
