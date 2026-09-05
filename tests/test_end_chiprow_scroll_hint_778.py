"""The end-screen highlights strip has to look like a strip (#778).

Measured on the player's end screen at 390px (iPhone width):

    .end-chiprow  scrollWidth - clientWidth = 338   clientWidth = 358

Nearly a second screenful sits off to the right, and ``document.body`` does
not scroll — so nothing is lost, the row just never said it was scrollable.
"FASTEST FINGER / avg 1.5s per correct" ended flush at the screen edge and read
as broken text rather than as something to swipe.

The row was not missing a mask; it had one. It was 16px wide, fading a white
card into a cream page — at that contrast and that width there is nothing to
see, which is why the live test reported "no edge fade". ``.theme-tabs`` solved
the same problem with 24px against much higher contrast, so this row gets 40.

The leading 12px fade is gone with it: at ``scrollLeft: 0``, where the row sits
until someone swipes, it only nibbled the first chip's left border and told the
reader nothing.
"""

from __future__ import annotations

import re
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
_CSS_SRC = _REPO_ROOT / "custom_components" / "quizify" / "www" / "css" / "src"


def _rule(source: str, selector: str) -> str:
    """Return the declaration block of the first ``selector { … }`` rule."""
    match = re.search(
        r"(?:^|\n)" + re.escape(selector) + r"\s*\{(.*?)\}", source, flags=re.S
    )
    assert match, f"no rule for {selector}"
    return match.group(1)


def _player_css() -> str:
    return (_CSS_SRC / "07-player.css").read_text("utf-8")


def _fade_widths(block: str) -> list[int]:
    return [int(px) for px in re.findall(r"calc\(100% - (\d+)px\)", block)]


def test_the_right_edge_fade_is_wide_enough_to_be_seen() -> None:
    """16px of white-on-cream is not a fade, it is a rounding error. The
    .theme-tabs precedent is 24px over a dark pill; this row's chips are white
    cards on a cream page, so it needs more, not less."""
    block = _rule(_player_css(), ".end-chiprow")

    widths = _fade_widths(block)
    assert widths, "the right-edge fade is gone entirely"
    assert min(widths) >= 32, f"fade too narrow to read: {widths}"


def test_both_the_prefixed_and_plain_mask_are_declared() -> None:
    """iOS Safari is the primary target device and still wants -webkit-."""
    block = _rule(_player_css(), ".end-chiprow")

    assert "-webkit-mask-image:" in block
    assert re.search(r"(?<!-)\bmask-image:", block), "unprefixed mask-image missing"


def test_the_fade_only_touches_the_trailing_edge() -> None:
    """A permanent left fade eats the first chip's border for the whole time
    the row is unscrolled, which is most of its life."""
    block = _rule(_player_css(), ".end-chiprow")

    for line in block.splitlines():
        if "mask-image" not in line:
            continue
        assert "to right" in line, line
        # "transparent 0" / "transparent 0%" at the start is the leading fade.
        assert not re.search(r"transparent\s+0(px|%)?\s*,", line), line


def test_a_swipe_lands_on_a_chip_edge() -> None:
    """The other half of "this is a strip": free scrolling parks the row
    mid-card again, which is the exact thing that read as broken."""
    row = _rule(_player_css(), ".end-chiprow")
    chip = _rule(_player_css(), ".rchip")

    assert "scroll-snap-type: x proximity" in row, (
        "proximity, not mandatory: a two-chip row that nearly fits must not be "
        "forced to snap"
    )
    assert "scroll-snap-align: start" in chip


def test_the_row_still_scrolls_and_still_hides_its_scrollbar() -> None:
    """The fix is about the hint, not about taking the scroll away."""
    row = _rule(_player_css(), ".end-chiprow")

    assert "overflow-x: auto" in row
    assert "scrollbar-width: none" in row
    assert ".end-chiprow::-webkit-scrollbar { display: none; }" in _player_css()
