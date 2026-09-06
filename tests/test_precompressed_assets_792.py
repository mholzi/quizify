"""Pre-compressed ``.gz`` siblings for the served assets (#792).

Nothing under ``www/`` was minified and no compressed siblings existed, so
``player.bundle.js`` went out at 329,799 B, ``styles.css`` at 255,437 B and
``admin.js`` at 182,682 B — uncompressed, to every phone in the room. Thirteen
phones joining inside a minute is roughly 8 MB off the HA box for the page load
alone.

The fix needs no server change at all, which is the whole reason to prefer it
over a minifier and an npm toolchain: aiohttp's ``FileResponse`` already looks
for ``<file>.gz`` and serves it when the request carries
``Accept-Encoding: gzip``. This module pins both halves of that claim — that
aiohttp really does it, and that the siblings are worth having — because the
first half is an assumption about somebody else's library and would fail
silently if it ever changed.

The staleness guard lives in ``test_generated_artifacts_in_sync.py``, next to
the other drift checks.
"""

from __future__ import annotations

import gzip
import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

REPO = Path(__file__).resolve().parent.parent
WWW = REPO / "custom_components" / "quizify" / "www"
SCRIPTS = REPO / "scripts"

# The three that dominate the join minute.
HEADLINE = ("js/player.bundle.js", "css/styles.css", "js/admin.js")


def _asset_gzip() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "_qz_asset_gzip_792", SCRIPTS / "asset_gzip.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


async def _static_client() -> TestClient:
    """A server that serves www/ exactly the way HA serves /quizify/static/."""
    app = web.Application()
    app.router.add_static("/quizify/static/", str(WWW))
    client = TestClient(TestServer(app))
    await client.start_server()
    return client


# ---- the assumption the whole fix rests on ----


@pytest.mark.parametrize("rel", HEADLINE)
async def test_aiohttp_serves_the_sibling_to_a_gzip_client(rel: str) -> None:
    """A plain static route, no middleware, no configuration — just the file."""
    client = await _static_client()
    try:
        resp = await client.get(
            f"/quizify/static/{rel}", headers={"Accept-Encoding": "gzip"}
        )
        raw = await resp.read()
    finally:
        await client.close()

    assert resp.status == 200
    assert resp.headers.get("Content-Encoding") == "gzip", (
        f"{rel}: aiohttp did not pick up the .gz sibling — the entire win of "
        "#792 is that it does this on its own"
    )
    # The type must still describe the *source*, not the container. A browser
    # handed application/gzip for styles.css drops the stylesheet on the floor.
    assert resp.headers["Content-Type"].split(";")[0] in {
        "text/css",
        "text/javascript",
        "application/javascript",
    }
    assert resp.headers.get("Vary") == "Accept-Encoding", (
        "a proxy without Vary would hand the compressed body to a client that "
        "cannot read it"
    )
    # aiohttp decompresses transparently, so compare against the source bytes.
    assert raw == (WWW / rel).read_bytes()


@pytest.mark.parametrize("rel", HEADLINE)
async def test_a_client_without_gzip_still_gets_the_plain_file(rel: str) -> None:
    """Committing siblings must not break the un-negotiated path."""
    client = await _static_client()
    try:
        resp = await client.get(
            f"/quizify/static/{rel}", headers={"Accept-Encoding": "identity"}
        )
        raw = await resp.read()
    finally:
        await client.close()

    assert resp.status == 200
    assert resp.headers.get("Content-Encoding") is None
    assert raw == (WWW / rel).read_bytes()


async def test_the_sibling_is_what_travels_not_just_what_arrives() -> None:
    """Content-Length is the compressed size — the point of the exercise.

    ``resp.read()`` hides the win by decompressing, so read the header instead.
    """
    client = await _static_client()
    try:
        resp = await client.get(
            "/quizify/static/js/player.bundle.js",
            headers={"Accept-Encoding": "gzip"},
            auto_decompress=False,
        )
        body = await resp.read()
    finally:
        await client.close()

    source_size = (WWW / "js/player.bundle.js").stat().st_size
    assert len(body) < source_size / 3, (
        f"the bundle travelled as {len(body):,} B against {source_size:,} B "
        "uncompressed — that is not the 4x this issue is about"
    )
    assert gzip.decompress(body) == (WWW / "js/player.bundle.js").read_bytes()


# ---- the siblings are worth committing ----


def test_every_sibling_is_smaller_than_its_source() -> None:
    """The obvious sanity check, and the one that catches a bad target list.

    Adding a ``.woff2`` or a ``.png`` to ``GZIP_TARGETS`` would produce a
    *larger* sibling that aiohttp then dutifully serves in preference.
    """
    ag = _asset_gzip()
    for rel in ag.GZIP_TARGETS:
        source = ag.WWW / rel
        sibling = ag.gzip_path(source)
        assert sibling.stat().st_size < source.stat().st_size, (
            f"{rel}.gz is bigger than {rel} — serving it is a slower download"
        )


def test_the_join_minute_gets_meaningfully_cheaper() -> None:
    """What a player phone pulls for its page load, before and after.

    Counted over the assets player.html actually references, which is the
    number a host feels when the room joins at once.
    """
    ag = _asset_gzip()
    player_assets = (
        "css/styles.css",
        "js/player.bundle.js",
        "js/i18n.js",
        "js/utils.js",
        "js/icons.js",
        "js/sw-update.js",
        "js/vendor/qrcode.min.js",
        "i18n/en.json",
    )
    plain = sum((WWW / rel).stat().st_size for rel in player_assets)
    wire = sum(ag.gzip_path(WWW / rel).stat().st_size for rel in player_assets)

    assert plain > 600_000, "baseline sanity: the player page is a big download"
    assert wire < plain / 3, (
        f"{wire:,} B on the wire against {plain:,} B uncompressed — under a 3x "
        "reduction the siblings are not paying for the drift risk they add"
    )


def test_the_dead_minify_script_is_gone() -> None:
    """It targeted player.js / timer.js / answers.js / fun-fact.js.

    None of those files have existed for a long time, it wrote ``*.min.js``
    outputs no HTML referenced, and it needed ``npx terser`` to do it. Leaving it
    in the tree is an invitation to run it.
    """
    assert not (SCRIPTS / "minify.sh").exists()
    assert not list(WWW.rglob("*.min.css"))
    # vendor/qrcode.min.js ships pre-minified from upstream and is not ours.
    ours = [p for p in WWW.rglob("*.min.js") if "vendor" not in p.parts]
    assert not ours, f"leftover minify.sh output: {ours}"
