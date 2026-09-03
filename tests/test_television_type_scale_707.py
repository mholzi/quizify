"""#707 — television type that the room has to read is sized for a room.

#376 sized the board for a sofa and #667 fixed the lobby chips and "Scan to
join". Four strings that carry *content* were in neither pass and kept their
phone sizes, measured at the 16 px root:

* `.dashboard-lightning-recap-row` at 0.95rem — **15.2 px** — although the
  recap is the only moment the room ever sees the lightning answers;
* `.podium-name` at 1.1rem — **17.6 px** — the winner's name, smaller than the
  `clamp(16px, 1.6vw, 24px)` leaderboard rows beside it, and truncated at
  160 px;
* `.dashboard-round` at **12 px**, which carries "Question 4 / 10";
* `.dashboard-reconnect-pill` at **14 px**, the one signal that the board has
  gone dead.

The eyebrow labels ("LEADERBOARD", "FUN FACT", the award captions) stay small
on purpose — they name a block, they are not the block. This pins the four
that are read, not everything with a font-size.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

DASHBOARD = (
    Path(__file__).resolve().parent.parent
    / "custom_components/quizify/www/dashboard.html"
)

# selector → the smallest acceptable upper end of its clamp, in px
CONTENT_RULES = {
    ".dashboard-lightning-recap-row": 24,
    ".podium-name": 26,
    ".dashboard-round": 20,
    ".dashboard-reconnect-pill": 20,
}


def _rule_body(selector: str) -> str:
    css = DASHBOARD.read_text(encoding="utf-8")
    start = css.index(selector + " {")
    return css[start : css.index("}", start)]


@pytest.mark.parametrize("selector", sorted(CONTENT_RULES))
def test_content_type_scales_with_the_screen(selector: str) -> None:
    """A fixed font-size here is a phone size on a television."""
    body = _rule_body(selector)
    match = re.search(r"font-size:\s*([^;]+);", body)
    assert match, f"{selector} lost its font-size"
    value = match.group(1).strip()
    assert value.startswith("clamp("), (
        f"{selector} is back to a fixed {value} — the board is metres away"
    )


@pytest.mark.parametrize("selector,ceiling", sorted(CONTENT_RULES.items()))
def test_the_upper_end_is_big_enough_for_a_room(selector: str, ceiling: int) -> None:
    """clamp() alone is not enough: the top of the range is what a 4K TV gets."""
    body = _rule_body(selector)
    value = re.search(r"font-size:\s*clamp\(([^)]*)\);", body).group(1)
    largest = value.split(",")[-1].strip()
    assert largest.endswith("px"), f"{selector} caps in {largest}, expected px"
    assert float(largest[:-2]) >= ceiling, (
        f"{selector} tops out at {largest}, below the {ceiling}px this carries"
    )


def test_the_winners_name_is_not_truncated_at_a_phone_width() -> None:
    """160 px cut ordinary names off at the moment they are being celebrated."""
    body = _rule_body(".podium-name")
    match = re.search(r"max-width:\s*([^;]+);", body)
    assert match, ".podium-name lost its max-width"
    value = match.group(1).strip()
    assert value.startswith("clamp("), f"max-width is a fixed {value} again"


def test_the_podium_name_is_not_smaller_than_the_rows_beside_it() -> None:
    """The winner's name reading smaller than the also-rans was the tell."""
    name = re.search(
        r"font-size:\s*clamp\(([^)]*)\);", _rule_body(".podium-name")
    ).group(1)
    row = re.search(
        r"font-size:\s*clamp\(([^)]*)\);",
        _rule_body(".dashboard-leaderboard .leaderboard-row"),
    ).group(1)

    def top(clamp_args: str) -> float:
        return float(clamp_args.split(",")[-1].strip().rstrip("px"))

    def bottom(clamp_args: str) -> float:
        return float(clamp_args.split(",")[0].strip().rstrip("px"))

    assert bottom(name) >= bottom(row)
    assert top(name) >= top(row)
