#!/usr/bin/env python3
"""Count hard-coded design values per front-end file (#1178, ratchet lint).

The design tokens in ``css/src/00-tokens.css`` exist, but most rules spell
their values out instead: the review behind #1178 counted 148 different
font sizes and 41 different box shadows. Fixing that is a migration of many
PRs, so the lint does not demand zero. It counts, per file, how many values
of each kind bypass the token layer, and
``tests/test_design_tokens_1178.py`` compares the counts with the committed
baseline ``design/lint-baseline.json``: a count may fall, never rise.

Categories (``var(…)`` expressions, fallbacks included, are removed first, so
a value that goes through a token never counts):

    color        #hex, rgb()/rgba()/hsl()/hsla() literals
    font_size    font-size with a length (px, rem, em, vw, …)
    radius       border-radius with a px/rem/em length (% and 0 are free)
    shadow       box-shadow / text-shadow other than ``none``
    z_index      z-index other than 0 / auto
    spacing_px   non-zero px lengths in padding / margin / gap
    duration     non-zero s/ms literals in transition / animation

Scanned: every ``css/src/*.css`` module (for ``00-tokens.css`` only the part
outside the generated ``:root`` block), the ``<style>`` blocks and ``style=""``
attributes of the pages, and the string literals and ``.style.x = …``
assignments of the hand-written scripts. Skipped: the generated bundles and
stylesheets, ``js/tokens.js`` (generated from the token file) and
``js/vendor/``.

When a PR lowers a count, refresh the baseline so the lower number is the new
ceiling:

    python3 scripts/design_lint.py --update-baseline

The counting is rule-based, not a CSS parser; it can be off by a case or two
on odd syntax. That is fine for a ratchet: the same rules run on both sides of
the comparison.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
WWW = REPO / "custom_components" / "quizify" / "www"
BASELINE = REPO / "design" / "lint-baseline.json"

CATEGORIES = (
    "color",
    "font_size",
    "radius",
    "shadow",
    "z_index",
    "spacing_px",
    "duration",
)

#: Generated or third-party files, never counted.
SKIP_JS = {"player.bundle.js", "common.bundle.js", "tokens.js"}

_BLOCK_COMMENT = re.compile(r"/\*.*?\*/", re.S)
_LINE_COMMENT = re.compile(r"(?m)(^|[\s;{}()])//[^\n]*")
_VAR = re.compile(r"var\((?:[^()]|\((?:[^()]|\([^()]*\))*\))*\)")
_HEX = re.compile(
    r"(?<![\w&-])#(?:[0-9a-fA-F]{8}|[0-9a-fA-F]{6}|[0-9a-fA-F]{3,4})(?![\w-])"
)
_FUNC_COLOR = re.compile(r"\b(?:rgba?|hsla?)\(")
_INNER_BLOCK = re.compile(r"\{([^{}]*)\}")
_STYLE_BLOCK = re.compile(r"<style[^>]*>(.*?)</style>", re.S)
_STYLE_ATTR = re.compile(r"""\sstyle\s*=\s*("([^"]*)"|'([^']*)')""")
_JS_STRING = re.compile(r"`(?:[^`\\]|\\.)*`|\"(?:[^\"\\\n]|\\.)*\"|'(?:[^'\\\n]|\\.)*'")
_JS_STYLE_ASSIGN = re.compile(
    r"\.style\.([a-zA-Z]+)\s*=\s*(`[^`]*`|\"[^\"\n]*\"|'[^'\n]*')"
)
_DECL = re.compile(r"(?<![\w-])([a-z-]+)\s*:\s*([^;{}\"'`]+)")
_LENGTH = re.compile(r"(?<![\w.])-?\d*\.?\d+(?:px|rem|em|vw|vh|vmin|vmax|pt|ch)\b")
_PX = re.compile(r"(?<![\w.])-?(\d*\.?\d+)px\b")
_DURATION = re.compile(r"(?<![\w.#-])(\d*\.?\d+)(ms|s)\b")
_INT = re.compile(r"-?\d+")

_SPACING_PROPS = re.compile(r"^(?:padding|margin)(?:-[a-z-]+)?$|^(?:row-|column-)?gap$")
_DURATION_PROPS = re.compile(r"^(?:transition|animation)(?:-duration|-delay)?$")


def _strip_vars(value: str) -> str:
    previous = None
    while previous != value:
        previous = value
        value = _VAR.sub(" ", value)
    return value


def _camel_to_kebab(name: str) -> str:
    return re.sub(r"[A-Z]", lambda m: "-" + m.group(0).lower(), name)


def count_declarations(text: str) -> dict[str, int]:
    """Count the hard-coded values in a run of ``prop: value`` declarations."""
    counts = dict.fromkeys(CATEGORIES, 0)
    for prop, raw in _DECL.findall(text):
        value = _strip_vars(raw)
        counts["color"] += len(_HEX.findall(value)) + len(_FUNC_COLOR.findall(value))
        if prop == "font-size" and _LENGTH.search(value):
            counts["font_size"] += 1
        elif prop.startswith("border") and prop.endswith("radius"):
            if any(float(n) for n in re.findall(r"(\d*\.?\d+)(?:px|rem|em)\b", value)):
                counts["radius"] += 1
        elif prop in ("box-shadow", "text-shadow"):
            if value.strip() and value.strip() != "none" and re.search(r"\d", value):
                counts["shadow"] += 1
        elif prop == "z-index":
            if any(int(n) for n in _INT.findall(value)):
                counts["z_index"] += 1
        elif _SPACING_PROPS.match(prop):
            counts["spacing_px"] += sum(1 for n in _PX.findall(value) if float(n))
        elif _DURATION_PROPS.match(prop):
            counts["duration"] += sum(
                1 for n, _ in _DURATION.findall(value) if float(n)
            )
    return counts


def _add(total: dict[str, int], part: dict[str, int]) -> None:
    for key in CATEGORIES:
        total[key] += part[key]


def _css_declarations(css: str) -> str:
    css = _BLOCK_COMMENT.sub("", css)
    return "\n".join(m.group(1) + ";" for m in _INNER_BLOCK.finditer(css))


def _outside_root(css: str) -> str:
    start = css.find(":root {\n")
    end = css.find("\n}\n", start)
    return css[:start] + css[end + 3 :]


def count_css(css: str) -> dict[str, int]:
    return count_declarations(_css_declarations(css))


def count_html(html: str) -> dict[str, int]:
    total = dict.fromkeys(CATEGORIES, 0)
    html = re.sub(r"<!--.*?-->", "", html, flags=re.S)
    for block in _STYLE_BLOCK.findall(html):
        _add(total, count_css(block))
    for match in _STYLE_ATTR.finditer(html):
        _add(total, count_declarations((match.group(2) or match.group(3) or "") + ";"))
    return total


def count_js(js: str) -> dict[str, int]:
    total = dict.fromkeys(CATEGORIES, 0)
    js = _BLOCK_COMMENT.sub("", js)
    js = _LINE_COMMENT.sub(r"\1", js)
    for prop, literal in _JS_STYLE_ASSIGN.findall(js):
        _add(total, count_declarations(f"{_camel_to_kebab(prop)}:{literal[1:-1]};"))
    for literal in _JS_STRING.findall(js):
        body = literal[1:-1]
        if ":" in body:
            _add(total, count_declarations(body + ";"))
        else:
            # A bare colour value assigned elsewhere ('#D65858', palettes).
            total["color"] += len(_HEX.findall(body)) + len(_FUNC_COLOR.findall(body))
    return total


def scanned_files() -> list[Path]:
    files = sorted((WWW / "css" / "src").glob("*.css"))
    files += sorted(WWW.glob("*.html"))
    files += sorted(p for p in (WWW / "js").glob("*.js") if p.name not in SKIP_JS)
    files += sorted((WWW / "cards").glob("*.js"))
    return files


def count_file(path: Path) -> dict[str, int]:
    text = path.read_text("utf-8")
    if path.suffix == ".css":
        if path.name == "00-tokens.css":
            text = _outside_root(text)
        return count_css(text)
    if path.suffix == ".html":
        return count_html(text)
    return count_js(text)


def current_counts() -> dict[str, dict[str, int]]:
    return {
        path.relative_to(WWW).as_posix(): count_file(path) for path in scanned_files()
    }


def totals(counts: dict[str, dict[str, int]]) -> dict[str, int]:
    out = dict.fromkeys(CATEGORIES, 0)
    for per_file in counts.values():
        _add(out, per_file)
    return out


def write_baseline() -> None:
    counts = current_counts()
    data = {
        "$comment": (
            "Ratchet baseline for hard-coded design values (#1178). Counts may "
            "only fall. Regenerate with: python3 scripts/design_lint.py "
            "--update-baseline"
        ),
        "totals": totals(counts),
        "files": counts,
    }
    BASELINE.write_text(json.dumps(data, indent=2) + "\n", "utf-8")
    print(f"Wrote {BASELINE.relative_to(REPO)}")


def main(argv: list[str]) -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--update-baseline", action="store_true")
    args = parser.parse_args(argv)
    if args.update_baseline:
        write_baseline()
    counts = current_counts()
    print(json.dumps(totals(counts), indent=2))


if __name__ == "__main__":
    main(sys.argv[1:])
