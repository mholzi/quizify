#!/usr/bin/env python3
"""Generate the design-token outputs from ``design/tokens.json`` (#1178).

Before this there were several hand-kept copies of the same values: the
``:root`` block of ``www/css/src/00-tokens.css``, ``PLAYER_COLORS`` in
``game/player.py``, and DESIGN.md, which used different names again. They had
already drifted (the review behind #1178 found 16 ``var()`` references to
tokens that do not exist). ``design/tokens.json`` is now the one source, and
this script writes everything derived from it:

    www/css/src/00-tokens.css   the ``:root { … }`` block, in place. Everything
                                outside that block (font faces, focus ring,
                                reduced-motion overrides) stays hand-written.
    www/js/tokens.js            ``window.QuizifyTokens`` — the same values for
                                scripts. Not loaded by any page yet; the player
                                colour step of #1178 wires it in.
    game/palette.py             ``PLAYER_COLORS`` for the server.

The output is byte-identical to what was committed before the generator
existed, comments and alignment included, so introducing it changed nothing a
browser can see. ``tests/test_design_tokens_1178.py`` asserts that the
committed files equal what this script writes.

``build_css.py`` calls this first, so the usual

    python3 scripts/build_css.py

regenerates the tokens and the stylesheets in one go. It can also run alone.

Adding or changing a token
--------------------------
Edit ``design/tokens.json`` and rebuild. Each entry of ``root`` is either a
string (a comment or blank line, emitted verbatim) or an object
``{"name", "value", "note"?, "col"?}``; ``col`` is the column the trailing
``/* note */`` starts at, which is only there to keep the existing alignment.

If a branch edited the ``:root`` block of ``00-tokens.css`` directly (anything
written before #1178, or a merge from one), run

    python3 scripts/build_tokens.py --import

once: it reads the block back into ``tokens.json`` and keeps the rest of the
file. A plain build refuses to drop a token that the CSS defines and the JSON
does not, so a stale ``tokens.json`` can never silently delete one; pass
``--prune`` when the removal is intended.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
TOKENS_JSON = REPO / "design" / "tokens.json"
PKG = REPO / "custom_components" / "quizify"
TOKENS_CSS = PKG / "www" / "css" / "src" / "00-tokens.css"
TOKENS_JS = PKG / "www" / "js" / "tokens.js"
PALETTE_PY = PKG / "game" / "palette.py"

INDENT = "    "
_ROOT_OPEN = ":root {\n"
_ROOT_CLOSE = "\n}\n"
_TOKEN_LINE = re.compile(
    r"^    (?P<name>--[\w-]+): (?P<value>[^;]+);"
    r"(?:(?P<pad> +)/\* (?P<note>.*) \*/)?$"
)
_DEFINED = re.compile(r"^\s*(--[\w-]+)\s*:", re.M)


# ---------------------------------------------------------------------------
# :root block
# ---------------------------------------------------------------------------


def render_token(item: dict) -> str:
    line = f"{INDENT}{item['name']}: {item['value']};"
    note = item.get("note")
    if note is None:
        return line
    pad = max(1, int(item.get("col", len(line) + 1)) - len(line))
    return f"{line}{' ' * pad}/* {note} */"


def render_root_body(data: dict) -> str:
    lines = [
        entry if isinstance(entry, str) else render_token(entry)
        for entry in data["root"]
    ]
    return "\n".join(lines)


def _split_css(css: str) -> tuple[str, str, str]:
    """(before, body, after) around the first top-level ``:root { … }``."""
    start = css.find(_ROOT_OPEN)
    if start < 0 or (start and css[start - 1] != "\n"):
        raise SystemExit(f"no top-level ':root {{' block in {TOKENS_CSS}")
    body_start = start + len(_ROOT_OPEN)
    end = css.find(_ROOT_CLOSE, body_start)
    if end < 0:
        raise SystemExit(f"':root' block in {TOKENS_CSS} is not closed")
    return css[:body_start], css[body_start:end], css[end:]


def parse_root_body(body: str) -> list:
    """Turn the CSS ``:root`` body into ``tokens.json`` entries, losslessly."""
    entries: list = []
    for line in body.split("\n"):
        match = _TOKEN_LINE.match(line)
        if match:
            item: dict = {"name": match["name"], "value": match["value"]}
            if match["note"] is not None:
                item["note"] = match["note"]
                item["col"] = match.start("pad") + len(match["pad"])
            if render_token(item) == line:
                entries.append(item)
                continue
        entries.append(line)
    return entries


def render_css(data: dict, current: str) -> str:
    before, _, after = _split_css(current)
    return before + render_root_body(data) + after


def defined_tokens(data: dict) -> list[str]:
    return [e["name"] for e in data["root"] if isinstance(e, dict)]


# ---------------------------------------------------------------------------
# js/tokens.js and game/palette.py
# ---------------------------------------------------------------------------


def _js_str(value: str) -> str:
    return "'" + value.replace("\\", "\\\\").replace("'", "\\'") + "'"


def render_js(data: dict) -> str:
    players = ", ".join(_js_str(c["hex"]) for c in data["players"]["colors"])
    tokens = "\n".join(
        f"            {_js_str(e['name'])}: {_js_str(e['value'])},"
        for e in data["root"]
        if isinstance(e, dict)
    )
    return (
        "/* AUTOGENERATED by scripts/build_tokens.py from design/tokens.json"
        " — do not edit by hand. */\n"
        "/* Not loaded by any page yet (#1178 step 2): the player-colour step"
        " wires it in. */\n"
        "(function () {\n"
        "    'use strict';\n"
        "\n"
        "    window.QuizifyTokens = {\n"
        "        // Server player palette, same order as game/palette.py.\n"
        f"        players: [{players}],\n"
        "        // The :root custom properties of css/src/00-tokens.css.\n"
        "        vars: {\n"
        f"{tokens}\n"
        "        },\n"
        "    };\n"
        "})();\n"
    )


def render_palette(data: dict) -> str:
    rows = "\n".join(
        f'    "{c["hex"]}",  # {c["name"]}' for c in data["players"]["colors"]
    )
    return (
        '"""Player colour palette.\n'
        "\n"
        "AUTOGENERATED by scripts/build_tokens.py from design/tokens.json — do"
        " not\nedit by hand. Change the colours there and run\n"
        "``python3 scripts/build_css.py``.\n"
        '"""\n'
        "\n"
        "from __future__ import annotations\n"
        "\n"
        f"# {data['players']['note']}\n"
        "PLAYER_COLORS = [\n"
        f"{rows}\n"
        "]\n"
    )


# ---------------------------------------------------------------------------
# entry points
# ---------------------------------------------------------------------------


def load() -> dict:
    return json.loads(TOKENS_JSON.read_text("utf-8"))


def outputs(data: dict | None = None) -> dict[Path, str]:
    """Every generated file and the exact text it must contain."""
    data = load() if data is None else data
    return {
        TOKENS_CSS: render_css(data, TOKENS_CSS.read_text("utf-8")),
        TOKENS_JS: render_js(data),
        PALETTE_PY: render_palette(data),
    }


def build(prune: bool = False) -> None:
    data = load()
    _, body, _ = _split_css(TOKENS_CSS.read_text("utf-8"))
    dropped = sorted(set(_DEFINED.findall(body)) - set(defined_tokens(data)))
    if dropped and not prune:
        raise SystemExit(
            "00-tokens.css defines tokens that design/tokens.json does not: "
            f"{dropped}.\nThe CSS was edited by hand (or a branch that did so "
            "was merged). Run `python3 scripts/build_tokens.py --import` to "
            "take them into tokens.json, or pass --prune if they are meant to "
            "go."
        )
    for path, text in outputs(data).items():
        if path.exists() and path.read_text("utf-8") == text:
            continue
        path.write_text(text, "utf-8")
        print(f"Wrote {path.relative_to(REPO)}")


def import_css() -> None:
    """Read the CSS ``:root`` block back into tokens.json (keeps other keys)."""
    data = load() if TOKENS_JSON.exists() else {}
    _, body, _ = _split_css(TOKENS_CSS.read_text("utf-8"))
    data["root"] = parse_root_body(body)
    TOKENS_JSON.write_text(
        json.dumps(data, indent=2, ensure_ascii=False) + "\n", "utf-8"
    )
    print(f"Wrote {TOKENS_JSON.relative_to(REPO)} from {TOKENS_CSS.name}")


def main(argv: list[str]) -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument(
        "--import",
        dest="do_import",
        action="store_true",
        help="read the :root block of 00-tokens.css into tokens.json",
    )
    parser.add_argument(
        "--prune",
        action="store_true",
        help="allow removing tokens the CSS defines but tokens.json does not",
    )
    args = parser.parse_args(argv)
    if args.do_import:
        import_css()
    build(prune=args.prune)


if __name__ == "__main__":
    main(sys.argv[1:])
