"""Guard the two UX/CSS fixes for #375 (iOS safe-area) and #382 (AA contrast).

These are text-level assertions over the committed CSS source modules — they
lock in the safe-area padding on the fixed bottom bars and the 12px font-size
floor on the celebratory end-screen labels so a later edit can't silently
regress them.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SRC = REPO / "custom_components" / "quizify" / "www" / "css" / "src"


def _rule(css: str, selector: str) -> str:
    """Return the declaration block for the first rule matching ``selector``."""
    idx = css.index(selector)
    start = css.index("{", idx)
    end = css.index("}", start)
    return css[start + 1 : end]


def test_admin_control_bar_has_safe_area_bottom() -> None:
    css = (SRC / "07-player.css").read_text("utf-8")
    block = _rule(css, ".admin-control-bar {")
    assert "env(safe-area-inset-bottom" in block, (
        ".admin-control-bar must pad its bottom by the iOS safe-area inset (#375)"
    )


def test_pl_result_admin_bar_has_safe_area_bottom() -> None:
    css = (SRC / "08-responsive.css").read_text("utf-8")
    block = _rule(css, ".pl-result-admin-bar {")
    assert "env(safe-area-inset-bottom" in block, (
        ".pl-result-admin-bar bottom offset must include the iOS safe-area inset (#375)"
    )


def test_end_screen_labels_have_12px_font_floor() -> None:
    """No sub-11px font-size on the celebratory end-screen labels (#382)."""
    css = (SRC / "07-player.css").read_text("utf-8")
    px_re = re.compile(r"font-size:\s*([0-9.]+)px")
    rem_re = re.compile(r"font-size:\s*([0-9.]+)rem")
    for selector in (".rchip-title {", ".rchip-who {", ".end-hero-label {", ".sb-you {", ".end-hint {"):
        block = _rule(css, selector)
        # A bare rem/px font-size under 11px (≈0.69rem at 16px root) is a regression.
        for m in px_re.finditer(block):
            assert float(m.group(1)) >= 11.0, f"{selector} has a sub-11px font-size (#382)"
        for m in rem_re.finditer(block):
            # A rem value only survives if it's floored via max(...) — checked below.
            assert "max(" in block, f"{selector} rem font-size needs a 12px floor via max() (#382)"
