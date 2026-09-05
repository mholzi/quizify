"""#735 — the estimate reveal is sized like the rest of the television.

#707 sized the lightning recap row, the podium name, the round indicator and
the reconnect pill for a sofa. The estimate reveal was not in that pass and
kept its phone sizes: the guesses along the number line were 17px on a 1920px
screen, while the multiple-choice reveal beside them scales and the true value
itself is 56px. Who guessed what is the thing the room leans forward for.

The point of this entry is *consistency*, not new sizes. Every rule here takes
a step #707/#376 already settled, by role:

* `.dnl-lbl`, `.dnl-truth-flag`, `.dnl-scale-ends` — a name plus a number in a
  row, so the leaderboard/recap step `clamp(16px, 1.6vw, 24px)`;
* `.dnl-truth-label` — an eyebrow naming the block, so the round-indicator step
  `clamp(14px, 1.2vw, 20px)`;
* `.dashboard-estimate-hint` — the range the room is guessing inside, so the
  podium-name step `clamp(18px, 1.8vw, 28px)`.

The label offsets move to `em` so the clearance from the dots grows with the
type instead of the type creeping towards the axis as it scales.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

DASHBOARD = (
    Path(__file__).resolve().parent.parent
    / "custom_components/quizify/www/dashboard.html"
)

# The steps #707 settled, quoted verbatim. Nothing here invents a size.
ROW_STEP = "clamp(16px, 1.6vw, 24px)"
EYEBROW_STEP = "clamp(14px, 1.2vw, 20px)"
HEADLINE_STEP = "clamp(18px, 1.8vw, 28px)"

# selector → the #707 step its role earns
ESTIMATE_RULES = {
    ".dnl-lbl": ROW_STEP,
    ".dnl-truth-flag": ROW_STEP,
    ".dnl-scale-ends": ROW_STEP,
    ".dnl-truth-label": EYEBROW_STEP,
    ".dashboard-estimate-hint": HEADLINE_STEP,
}


def _rule_body(selector: str) -> str:
    css = DASHBOARD.read_text(encoding="utf-8")
    start = css.index(selector + " {")
    return css[start : css.index("}", start)]


def _font_size(selector: str) -> str:
    match = re.search(r"font-size:\s*([^;]+);", _rule_body(selector))
    assert match, f"{selector} lost its font-size"
    return match.group(1).strip()


@pytest.mark.parametrize("selector", sorted(ESTIMATE_RULES))
def test_the_estimate_reveal_scales_with_the_screen(selector: str) -> None:
    """A fixed font-size here is a phone size on a television."""
    value = _font_size(selector)
    assert value.startswith("clamp("), (
        f"{selector} is a fixed {value} — the estimate reveal is read from a sofa"
    )


@pytest.mark.parametrize("selector,step", sorted(ESTIMATE_RULES.items()))
def test_it_reuses_a_step_707_already_settled(selector: str, step: str) -> None:
    """#735 is a consistency entry: no new sizes, only #707's."""
    value = _font_size(selector)
    assert value == step, (
        f"{selector} is {value}; #707 settled {step} for what it carries. "
        "A new size here re-opens a decision that was already made."
    )


@pytest.mark.parametrize("selector", sorted(ESTIMATE_RULES))
def test_the_upper_end_is_big_enough_for_a_room(selector: str) -> None:
    """The top of the range is what a 1920px television actually gets."""
    largest = _font_size(selector).rstrip(")").split(",")[-1].strip()
    assert largest.endswith("px"), f"{selector} caps in {largest}, expected px"
    assert float(largest[:-2]) >= 20, (
        f"{selector} tops out at {largest} — still a phone size on a television"
    )


def test_the_guesses_are_not_smaller_than_the_leaderboard_rows() -> None:
    """17px next to clamp(16px, 1.6vw, 24px) rows was the tell."""

    def ends(value: str) -> tuple[float, float]:
        args = value[len("clamp(") : -1].split(",")
        return (
            float(args[0].strip().rstrip("px")),
            float(args[-1].strip().rstrip("px")),
        )

    low, high = ends(_font_size(".dnl-lbl"))
    row_low, row_high = ends(_font_size(".dashboard-leaderboard .leaderboard-row"))
    assert low >= row_low
    assert high >= row_high


def test_the_axis_endpoints_clear_the_labels_hanging_below_the_line() -> None:
    """A fixed 16px band is overrun by the labels that hang into it."""
    match = re.search(r"margin-top:\s*([^;]+);", _rule_body(".dnl-scale-ends"))
    assert match, ".dnl-scale-ends lost its margin-top"
    value = match.group(1).strip()
    assert value.endswith("em"), (
        f".dnl-scale-ends keeps a fixed {value} below the axis — the guesses "
        "hanging under the line land on top of the range endpoints"
    )


@pytest.mark.parametrize("selector", [".dnl-lbl--above", ".dnl-lbl--below"])
def test_the_label_offsets_scale_with_the_label(selector: str) -> None:
    """A fixed 24px offset shrinks, in effect, as the type around it grows."""
    body = _rule_body(selector)
    match = re.search(r"(?:top|bottom):\s*([^;]+);", body)
    assert match, f"{selector} lost its offset"
    value = match.group(1).strip()
    assert value.endswith("em"), (
        f"{selector} offsets by a fixed {value} — it no longer keeps its "
        "clearance from the dot once the label scales"
    )
