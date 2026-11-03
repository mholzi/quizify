"""One token source, and a lint that only lets hard-coded values fall (#1178).

Two guards from the design-system migration:

* **The generated token files equal what the generator writes.**
  ``design/tokens.json`` is the source; ``scripts/build_tokens.py`` writes the
  ``:root`` block of ``00-tokens.css``, ``js/tokens.js``, ``css/tokens.css``
  and ``game/palette.py``. A hand edit to any of them, or a JSON edit nobody
  rebuilt, fails here instead of drifting.
* **Hard-coded design values may only become fewer.** ``scripts/design_lint.py``
  counts colour literals, font sizes, radii, shadows, z-indexes, px spacing and
  durations per file; ``design/lint-baseline.json`` holds the counts at the
  time the lint landed. A count above its baseline fails. A count below it is
  progress: refresh the baseline so it cannot climb back.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType

import pytest

REPO = Path(__file__).resolve().parent.parent


def _load_script(name: str) -> ModuleType:
    path = REPO / "scripts" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(f"_qz_{name}", path)
    assert spec and spec.loader, f"cannot load {path}"
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


build_tokens = _load_script("build_tokens")
design_lint = _load_script("design_lint")

_REBUILD = "Run: python3 scripts/build_css.py (it calls build_tokens.py), then commit."


# ---------------------------------------------------------------------------
# generator
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "path",
    list(build_tokens.outputs()),
    ids=lambda p: p.name,
)
def test_generated_token_file_is_in_sync(path: Path) -> None:
    expected = build_tokens.outputs()[path]
    assert path.read_text("utf-8") == expected, (
        f"{path.relative_to(REPO)} differs from what design/tokens.json "
        f"generates. {_REBUILD} If the CSS :root block was edited by hand, "
        "run python3 scripts/build_tokens.py --import first."
    )


def test_every_css_token_is_in_the_json() -> None:
    """The build refuses to drop a token; this says which one, earlier."""
    css = build_tokens.TOKENS_CSS.read_text("utf-8")
    _, body, _ = build_tokens._split_css(css)
    in_css = set(build_tokens._DEFINED.findall(body))
    in_json = set(build_tokens.defined_tokens(build_tokens.load()))
    assert in_css == in_json, (
        f"only in 00-tokens.css: {sorted(in_css - in_json)}; "
        f"only in tokens.json: {sorted(in_json - in_css)}"
    )


def test_import_round_trips_the_root_block() -> None:
    """--import followed by a build must reproduce the CSS byte for byte."""
    css = build_tokens.TOKENS_CSS.read_text("utf-8")
    _, body, _ = build_tokens._split_css(css)
    data = {"root": build_tokens.parse_root_body(body)}
    assert build_tokens.render_root_body(data) == body
    assert data["root"] == build_tokens.load()["root"]


def test_player_colours_come_from_the_palette_module() -> None:
    from custom_components.quizify.game import palette, player

    assert player.PLAYER_COLORS is palette.PLAYER_COLORS
    expected = [c["hex"] for c in build_tokens.load()["players"]["colors"]]
    assert expected == palette.PLAYER_COLORS
    assert len(set(palette.PLAYER_COLORS)) == len(palette.PLAYER_COLORS)


def test_player_palette_is_soft_parlor() -> None:
    """Decision 5 of #1178: the six DESIGN.md colours, then moss and rose."""
    from custom_components.quizify.game import palette

    assert palette.PLAYER_COLORS == [
        "#E88A7F",
        "#7FA897",
        "#7FA8C4",
        "#E8C47F",
        "#B78FC7",
        "#D89E6F",
        "#9DB38A",
        "#C7A3A0",
    ]


def test_tokens_js_leads_the_common_bundle() -> None:
    """Step 6: every page gets ``window.QuizifyTokens`` through the bundle."""
    build_bundle = _load_script("build_bundle")
    assert build_bundle.COMMON_MODULES[0] == "tokens.js"
    bundle = (build_tokens.PKG / "www" / "js" / "common.bundle.js").read_text(
        "utf-8"
    )
    assert "window.QuizifyTokens" in bundle


@pytest.mark.parametrize("script", ["admin.js", "player-reveal.js"])
def test_no_script_keeps_its_own_player_palette(script: str) -> None:
    """The host lobby and the phone standings used to carry their own lists."""
    text = (build_tokens.PKG / "www" / "js" / script).read_text("utf-8")
    assert "window.QuizifyTokens.players" in text
    for old in ("#FF6B6B", "#4ECDC4", "#6E9685", "#6A9AB6"):
        assert old not in text, f"{script} still spells out {old}"


def test_tv_has_no_token_copy() -> None:
    """Step 7: the television reads the shared tokens, not a --dash-* copy."""
    www = build_tokens.PKG / "www"
    for path in (www / "css" / "src" / "10-tv.css", www / "js" / "dashboard.js"):
        assert "--dash-" not in path.read_text("utf-8"), path.name


def test_launcher_takes_the_generated_sheet() -> None:
    """Step 7: no hand-kept :root copy in the launcher."""
    html = (build_tokens.PKG / "www" / "launcher.html").read_text("utf-8")
    assert "/quizify/static/css/tokens.css" in html
    assert ":root {" not in html


# ---------------------------------------------------------------------------
# ratchet lint
# ---------------------------------------------------------------------------


def _baseline() -> dict[str, dict[str, int]]:
    return json.loads(design_lint.BASELINE.read_text("utf-8"))["files"]


def test_the_lint_sees_hard_coded_values() -> None:
    """A regex that stopped matching would turn the ratchet into a no-op."""
    sample = (
        "color: #E88A7F; background: rgba(0, 0, 0, .5); font-size: 0.85rem;"
        "border-radius: 12px; box-shadow: 0 2px 8px #000; z-index: 9999;"
        "padding: 4px 8px; transition: opacity 0.2s ease, transform 120ms;"
        "margin: var(--space-sm, 8px); color: var(--color-text, #000);"
        "border-radius: 50%; z-index: 0; box-shadow: none; gap: 0px;"
    )
    assert design_lint.count_declarations(sample) == {
        "color": 3,
        "font_size": 1,
        "radius": 1,
        "shadow": 1,
        "z_index": 1,
        "spacing_px": 2,
        "duration": 2,
    }
    totals = design_lint.totals(design_lint.current_counts())
    assert all(totals[c] > 0 for c in design_lint.CATEGORIES), totals


def test_hard_coded_design_values_do_not_increase() -> None:
    baseline = _baseline()
    zero = dict.fromkeys(design_lint.CATEGORIES, 0)
    risen: list[str] = []
    for name, counts in design_lint.current_counts().items():
        allowed = baseline.get(name, zero)
        for category in design_lint.CATEGORIES:
            if counts[category] > allowed.get(category, 0):
                risen.append(
                    f"{name}: {category} {allowed.get(category, 0)} -> "
                    f"{counts[category]}"
                )
    assert not risen, (
        "More hard-coded design values than design/lint-baseline.json allows:\n  "
        + "\n  ".join(risen)
        + "\nUse a token from css/src/00-tokens.css (see docs/DESIGN.md) instead "
        "of a literal. If a new token is what is missing, add it to "
        "design/tokens.json."
    )
