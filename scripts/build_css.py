#!/usr/bin/env python3
"""Concatenate the CSS source modules into the served per-surface stylesheets.

styles.css used to be one ~6.7k-line god-file. It is now split into
small per-screen / per-component modules under www/css/src/ for
maintainability. CSS has no module system, so a served file is just
the modules concatenated **in the exact original cascade order** —
order matters, later rules override earlier ones.

Since #880 there is more than one output. The television used to load the
phone's whole stylesheet — 8,347 lines, of which nine modules are phone or
admin — and then re-declare the same class names in a 1,900-line inline block
to out-cascade it. The two pages share twelve element ids and a dozen generic
class names, and every width query in the tree is ``min-width``, so by
construction the un-queried base rule **is** the phone rule and it reaches
1280 px unless something overrides it. Four shipped bugs (#775, #836, #865 and
the v1.16.0 podium fix) were that same shape, and a fifth was live in the tree:
``05-finale.css`` hid ``.podium-avatar`` for a page that no longer rendered it,
and the rule landed on the television, which does.

So each surface now gets its own sheet, built from its own module list:

    styles.css   the phone and the host console (admin, launcher, analytics)
    tv.css       the television

``tv.css`` omits ``07-player.css`` (489 rules), ``09-a11y.css`` (3) and the
un-marked part of ``08-responsive.css`` (203) — 135 KB of the 235 KB, none of
which can match anything the television renders.
``tests/test_surface_stylesheets_880.py`` proves that claim rule by rule
instead of asking anyone to take it on trust, so the split is a *removal*, not
a rewrite: no rule changes place, no selector changes specificity, and every
sheet keeps the original cascade order of whatever it does contain.

Run once before each release; the output is committed so the dev server, HACS
install and CI all pick it up unchanged. Each page keeps loading a single
stylesheet, so the cache-buster ?v= query is unaffected.

    python3 scripts/build_css.py

This is the CSS analogue of scripts/build_bundle.py (player JS).
"""

from __future__ import annotations

import hashlib
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from asset_gzip import write_gzip_sibling  # noqa: E402

REPO = Path(__file__).resolve().parent.parent
CSS_DIR = REPO / "custom_components" / "quizify" / "www" / "css"
SRC_DIR = CSS_DIR / "src"

# Order matters — this is the cascade order. The modules are byte-exact
# slices of the original styles.css, split at the existing section
# banners. Concatenating them in this order reproduces the original
# rules in the original order (only difference: the header comment
# prepended below, which is inert CSS).
CSS_MODULES = [
    "00-tokens.css",      # Design tokens, dark mode
    "01-base.css",        # Base styles, brand wordmark, layout, header
    "02-shared.css",      # Buttons, inputs, cards, chips, pack-cards, settings
    "03-question.css",    # Timer bar, question display, answers, power-up, score, fun fact
    "04-lobby.css",       # Lobby, QR code, self-join, admin setup + question preview
    "05-finale.css",      # Leaderboard, final leaderboard, podium, awards
    "06-admin.css",       # Launcher, modal, glow/badge/utility helpers
    "07-player.css",      # Player END/GAME/REVEAL/LOBBY views + setup/lobby variants + modals/misc
    "08-responsive.css",  # Responsive, reduced motion, reveal grids, result page, finale, community pack
    # Last on purpose: 09 redefines the --font-size-* tokens from 00-tokens
    # under .a11y. Both selectors have the same specificity and hit the same
    # element (<html>), so only source order makes the override win (#372).
    "09-a11y.css",        # Accessibility mode — larger type, motion held still
]

# The television's sheet (#880). Same modules in the same order, minus the ones
# that address the phone, plus the surface's own module last — where the inline
# <style> block used to sit in dashboard.html's <head>, i.e. after the
# stylesheet link. Nothing is reordered, so every rule that survives keeps
# exactly the neighbours it had.
#
# Left out, and why:
#   07-player.css  the phone's views. Not one of its 489 rules names a class or
#                  id the television's markup or dashboard.js ever produces.
#   09-a11y.css    the `.a11y` comfort toggle lives in the player header; the
#                  television has no toggle and never sets the class.
#   08-responsive  all but the two `@surface tv` regions — the width queries and
#                  the finale/podium emphasis block, which do address the TV.
TV_MODULES = [
    "00-tokens.css",
    "01-base.css",
    "02-shared.css",
    "03-question.css",
    "04-lobby.css",
    "05-finale.css",
    "06-admin.css",
    "08-responsive.css",
    "10-tv.css",          # The television's own rules (was inline in dashboard.html)
]

# Modules that carry `/* @surface … */` regions. For a surface listed here, only
# the marked regions are emitted; every other surface gets the module whole.
# Regions are dropped, never moved, so the surviving rules keep their order.
SURFACE_SLICED = {"08-responsive.css"}

# Which sheet each page loads. The launcher and the analytics page are host-side
# surfaces and read the same sheet the console does.
SURFACES: dict[str, list[str]] = {
    "styles.css": CSS_MODULES,
    "tv.css": TV_MODULES,
}

# The surface a sheet is built for, for the `@surface` markers above. Only the
# television has a slice today; a sheet with no entry gets whole modules.
SURFACE_NAME = {"tv.css": "tv"}

OUT = CSS_DIR / "styles.css"
TV_OUT = CSS_DIR / "tv.css"

HEADER = (
    "/* AUTOGENERATED by scripts/build_css.py — do not edit by hand. */\n"
    "/* Edit the modules in www/css/src/ then run python3 scripts/build_css.py */\n"
)

_REGION = re.compile(
    r"/\*\s*@surface\s+(?P<surfaces>[\w ,]+?)\s*(?:—|--).*?\*/\n(?P<body>.*?)"
    r"/\*\s*@endsurface\s*\*/\n",
    re.S,
)


def slice_for_surface(text: str, surface: str) -> str:
    """Keep only the `@surface <name>` regions of a module.

    A module reaches this path when it is listed in ``SURFACE_SLICED``: most of
    it belongs to one surface, a couple of blocks belong to another, and
    splitting the file in two would move those blocks in the cascade. Marking
    them in place and dropping the rest keeps the order of what remains exactly
    as it reads in the source.
    """
    kept: list[str] = []
    for match in _REGION.finditer(text):
        surfaces = {s.strip() for s in match.group("surfaces").split(",")}
        if surface in surfaces:
            kept.append(match.group("body"))
    if not kept:
        raise SystemExit(
            f"no '@surface {surface}' region found — a sliced module must carry "
            "at least one, or it should not be in SURFACE_SLICED"
        )
    return "".join(kept)


def module_text(name: str, sheet: str) -> str:
    path = SRC_DIR / name
    if not path.exists():
        print(f"  ERROR (missing module): {name}", file=sys.stderr)
        sys.exit(1)
    text = path.read_text("utf-8")
    surface = SURFACE_NAME.get(sheet)
    if surface and name in SURFACE_SLICED:
        return slice_for_surface(text, surface)
    return text


def build_sheet(sheet: str) -> Path:
    parts: list[str] = [HEADER]
    for name in SURFACES[sheet]:
        parts.append(module_text(name, sheet))
    text = "".join(parts)
    out = CSS_DIR / sheet
    out.write_text(text, "utf-8")

    digest = hashlib.sha1(text.encode("utf-8")).hexdigest()[:10]
    print(f"Wrote {out.relative_to(REPO)} ({len(text):,} bytes, sha1:{digest})")

    # Same reason as in build_bundle.py: the .gz sibling wins over this file
    # when a browser sends Accept-Encoding, so it is part of the build, not an
    # optional extra step (#792).
    gz = write_gzip_sibling(out)
    print(f"Wrote {gz.relative_to(REPO)} ({gz.stat().st_size:,} bytes)")
    return out


def build_css() -> Path:
    written = [build_sheet(sheet) for sheet in SURFACES]
    return written[0]


if __name__ == "__main__":
    build_css()
