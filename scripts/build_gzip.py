#!/usr/bin/env python3
"""Regenerate every pre-compressed ``.gz`` sibling under www/ (#792).

Replaces ``scripts/minify.sh``, which had been dead for a long time: it minified
``player.js``, ``timer.js``, ``answers.js`` and ``fun-fact.js`` — files that no
longer exist — into ``*.min.js`` outputs no HTML ever referenced, and needed
``npx terser`` to do it. This needs nothing but the standard library and buys
more than minification would: ~3.7-4x off the wire for the three big assets,
because aiohttp serves the sibling by itself.

Run it after editing any front-end asset, or just let CI tell you:

    python3 scripts/build_gzip.py

``build_bundle.py`` and ``build_css.py`` each refresh their own sibling, so this
is the script for everything they do not generate (admin.js, the i18n bundles,
the small shared modules).

See ``scripts/asset_gzip.py`` for the target list and why the output is
byte-reproducible.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from asset_gzip import build_all  # noqa: E402

if __name__ == "__main__":
    build_all()
