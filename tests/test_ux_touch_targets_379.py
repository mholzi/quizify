"""Guard the two touch-target fixes for #379 (WCAG 2.5.8 / Apple HIG >=44px).

Text-level assertions over the generated styles.css so a later edit can't
silently shrink the tap targets back below 44px:
- ``.sound-toggle-btn`` keeps its 30px visible disc but a ``::before`` overlay
  with negative inset expands the hit area to 44x44.
- the base ``.chip`` gains a >=40px ``min-height`` (rounds/difficulty/timer
  chips in the admin setup).
"""

from __future__ import annotations

import re
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
STYLES = REPO / "custom_components" / "quizify" / "www" / "css" / "styles.css"


def _rule(css: str, selector: str) -> str:
    """Return the declaration block for the first rule matching ``selector``."""
    idx = css.index(selector)
    start = css.index("{", idx)
    end = css.index("}", start)
    return css[start + 1 : end]


def test_sound_toggle_hit_area_at_least_44px() -> None:
    css = STYLES.read_text("utf-8")
    # The visible disc stays 30px; the ::before overlay extends the hit area.
    block = _rule(css, ".sound-toggle-btn::before")
    assert "position: absolute" in block
    m = re.search(r"inset:\s*(-?\d+)px", block)
    assert m, ".sound-toggle-btn::before must set a negative inset"
    inset = int(m.group(1))
    # disc is 30px; hit area = 30 + 2*|inset| must be >= 44 -> |inset| >= 7
    assert 30 + 2 * abs(inset) >= 44, (
        ".sound-toggle-btn tap target must be >=44px (#379)"
    )


def test_base_chip_min_height_at_least_40px() -> None:
    css = STYLES.read_text("utf-8")
    block = _rule(css, ".chip {")
    m = re.search(r"min-height:\s*(\d+)px", block)
    assert m, "base .chip must declare a min-height (#379)"
    assert int(m.group(1)) >= 40, (
        "base .chip min-height must be >=40px for a finger-friendly target (#379)"
    )
