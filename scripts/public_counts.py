#!/usr/bin/env python3
"""The figures Quizify claims in public, counted from the packs (#987).

Four surfaces quote how much content ships: ``README.md``, the repository
description on GitHub, the landing page on the ``gh-pages`` branch, and the
release notes. Only the README was ever recounted at release time; the other
three were written once and carried forward. On 21.09.2026 they still said
**4,740 questions across 36 packs** while the repository held 5,796 across 45 —
the World Cup packs had grown to 160 each (#975) and three seasonal themes had
shipped since, so the line a stranger reads first in GitHub search, in HACS and
on the homepage undersold the project by a thousand questions.

Carrying a number forward is the failure. This script counts, so the release
step can paste rather than remember:

    python3 scripts/public_counts.py            # human-readable
    python3 scripts/public_counts.py --json     # for scripting

``--check`` compares the README against the packs and exits non-zero on drift;
``tests/test_docs_pack_counts.py`` already enforces that pairing in CI, and this
flag is the same check in a form a release run can call by hand.

The theme table is grouped by the three language siblings of one theme, which is
how the landing page lists them — a theme is a row there, not a file.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
_PACKS = _REPO / "custom_components" / "quizify" / "questions"
_README = _REPO / "README.md"

# One entry per theme: the pack files that carry its three languages. Kept
# explicit rather than derived from filenames — `sport-de`/`sport-en`/
# `deportes-es` share no stem, and guessing would silently drop a language.
THEMES: list[tuple[str, tuple[str, str, str]]] = [
    ("Geographie / Geography", ("geographie", "geography", "geografia-es")),
    ("Tiere & Natur / Animals & Nature",
     ("tiere-natur", "animals-nature", "naturaleza-es")),
    ("Popkultur / Pop Culture", ("popkultur", "pop-culture", "cultura-pop-es")),
    ("Sport", ("sport-de", "sport-en", "deportes-es")),
    ("Musik / Music", ("musik-de", "music-en", "musica-es")),
    ("Wissenschaft / Science", ("wissenschaft-de", "science-en", "ciencia-es")),
    ("Geschichte / History", ("geschichte-de", "history-en", "historia-es")),
    ("Essen & Trinken / Food & Drink", ("essen-de", "food-en", "comida-es")),
    ("Technik / Technology", ("technik-de", "tech-en", "tecnologia-es")),
    ("Weltmeisterschaft / World Cup",
     ("weltmeisterschaft", "world-cup", "copa-mundial-es")),
    ("Bilderrätsel / Picture Round",
     ("bilderraetsel-de", "picture-round-en", "imagenes-es")),
    ("Schätzfragen / Estimation",
     ("schaetzfragen-de", "estimation-en", "estimacion-es")),
    ("Weihnachten / Christmas", ("weihnachten-de", "christmas-en", "navidad-es")),
    ("Halloween", ("halloween-de", "halloween-en", "halloween-es")),
    ("Silvester / New Year's Eve",
     ("silvester-de", "new-years-eve-en", "nochevieja-es")),
]


def pack_counts() -> dict[str, int]:
    """Questions per pack file, excluding the version registry."""
    out: dict[str, int] = {}
    for path in sorted(_PACKS.glob("*.json")):
        if path.name == "versions.json":
            continue
        data = json.loads(path.read_text(encoding="utf-8"))
        out[path.stem] = len(data.get("questions", []))
    return out


def summary() -> dict[str, object]:
    counts = pack_counts()
    listed = {name for _, names in THEMES for name in names}
    # A pack that no theme lists would vanish from the table while still
    # counting towards the total — that mismatch is worth surfacing, not
    # smoothing over.
    unlisted = sorted(set(counts) - listed)
    missing = sorted(listed - set(counts))
    return {
        "packs": len(counts),
        "questions": sum(counts.values()),
        "themes": len(THEMES),
        "languages": 3,
        "per_theme": [
            {"theme": theme, "de": counts.get(de, 0),
             "en": counts.get(en, 0), "es": counts.get(es, 0)}
            for theme, (de, en, es) in THEMES
        ],
        "unlisted_packs": unlisted,
        "missing_packs": missing,
    }


def readme_claim() -> tuple[int, int] | None:
    """The questions/packs pair the README states, or None if unparsable."""
    text = _README.read_text(encoding="utf-8")
    m = re.search(r"([\d,]+) questions across (\d+) themed packs", text)
    if not m:
        return None
    return int(m.group(1).replace(",", "")), int(m.group(2))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    ap.add_argument("--check", action="store_true",
                    help="exit 1 if the README disagrees with the packs")
    args = ap.parse_args(argv)

    s = summary()
    rows: list[dict[str, object]] = s["per_theme"]  # type: ignore[assignment]
    unlisted: list[str] = s["unlisted_packs"]  # type: ignore[assignment]
    missing: list[str] = s["missing_packs"]  # type: ignore[assignment]
    if args.json:
        json.dump(s, sys.stdout, ensure_ascii=False, indent=2)
        print()
    else:
        print(f"{s['questions']:,} questions across {s['packs']} themed packs "
              f"in {s['themes']} themes, three languages")
        for row in rows:
            print(f"  {row['theme']:36s} {row['de']:4d} {row['en']:4d} {row['es']:4d}")
        if unlisted:
            print("  ! packs in no theme:", ", ".join(unlisted))
        if missing:
            print("  ! themes naming a missing pack:", ", ".join(missing))

    if args.check:
        claim = readme_claim()
        if claim is None:
            print("README does not state a questions/packs pair", file=sys.stderr)
            return 1
        if claim != (s["questions"], s["packs"]):
            print(f"README says {claim[0]:,}/{claim[1]}, packs say "
                  f"{s['questions']:,}/{s['packs']}", file=sys.stderr)
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
