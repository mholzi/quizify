#!/usr/bin/env python3
"""Pre-compressed ``.gz`` siblings for the assets HA serves from disk (#792).

Home Assistant serves ``/quizify/static/*`` through aiohttp's
``FileResponse``, which already looks for a ``<file>.gz`` next to the file it
was asked for and serves that instead when the client sent
``Accept-Encoding: gzip`` (``aiohttp/web_fileresponse.py``,
``_get_file_path_stat_encoding``). So the whole win is committing the siblings —
there is no server-side change, no middleware, and no npm dependency:
``player.bundle.js`` goes from 329,799 to 81,992 bytes on the wire, ``styles.css``
from 255,437 to 57,105, ``admin.js`` from 182,682 to 48,802.

**The sibling wins over the source file.** That is the point, and also the trap:
a ``.gz`` that was not regenerated after an edit is served in place of the fresh
source, silently, with no error anywhere — the release ships and the old code
runs. ``tests/test_generated_artifacts_in_sync.py`` and the CI ``drift`` job
therefore rebuild every sibling and compare bytes, exactly as they already do
for ``player.bundle.js`` and ``styles.css``.

Which is why the output has to be byte-reproducible: ``mtime=0`` in the gzip
header, and a ``BytesIO`` so no filename is recorded either. Same input, same
bytes, on any machine.

    python3 scripts/build_gzip.py     # regenerate every sibling

Not compressed: fonts (``.woff2`` is already Brotli-compressed internally) and
PNGs, both of which come out *larger*. Not compressed either: ``*.html`` and
``sw.js``, which are not served from disk at all — ``server/views.py`` templates
them into a ``web.Response`` and its routes are registered ahead of the static
handler, so a sibling would be dead weight at best and a stale, un-templated
copy at worst.
"""

from __future__ import annotations

import gzip
import io
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
WWW = REPO / "custom_components" / "quizify" / "www"

# Every text asset a browser loads over /quizify/static/, relative to www/.
# Generated artifacts are in here too: build_bundle.py and build_css.py write
# their own sibling as part of the build, so running one script on its own never
# leaves the tree half-compressed.
GZIP_TARGETS: tuple[str, ...] = (
    "css/styles.css",
    "js/player.bundle.js",
    "js/common.bundle.js",
    "js/admin.js",
    "js/pack-submit.js",
    "js/i18n.js",
    "js/utils.js",
    "js/icons.js",
    "js/sw-update.js",
    "js/vendor/qrcode.min.js",
    "i18n/de.json",
    "i18n/en.json",
    "i18n/es.json",
    "site.webmanifest",
)

COMPRESS_LEVEL = 9


def gzip_bytes(data: bytes) -> bytes:
    """Deterministic gzip: same input, same output, forever.

    ``gzip.compress`` stamps the current time into the header, which would make
    every rebuild a diff and turn the drift guard into noise. ``mtime=0`` and an
    anonymous ``BytesIO`` (no ``name``, so no FNAME field) remove both sources of
    variance.
    """
    buf = io.BytesIO()
    with gzip.GzipFile(
        fileobj=buf, mode="wb", compresslevel=COMPRESS_LEVEL, mtime=0
    ) as fh:
        fh.write(data)
    return buf.getvalue()


def gzip_path(source: Path) -> Path:
    """``styles.css`` -> ``styles.css.gz`` — the name aiohttp looks for."""
    return source.with_suffix(source.suffix + ".gz")


def write_gzip_sibling(source: Path) -> Path:
    """Write ``<source>.gz`` and return its path.

    Rewrites unconditionally rather than comparing first: the file is small, and
    a "skip if unchanged" branch is one more place for a stale sibling to hide.
    """
    out = gzip_path(source)
    out.write_bytes(gzip_bytes(source.read_bytes()))
    return out


def build_all(www: Path = WWW) -> list[Path]:
    written: list[Path] = []
    for rel in GZIP_TARGETS:
        source = www / rel
        if not source.is_file():
            raise SystemExit(f"gzip target missing: {source}")
        out = write_gzip_sibling(source)
        written.append(out)
        print(
            f"Wrote {out.relative_to(REPO)} "
            f"({source.stat().st_size:,} -> {out.stat().st_size:,} bytes)"
        )
    return written
