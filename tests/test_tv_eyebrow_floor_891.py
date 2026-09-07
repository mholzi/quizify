"""The television's small labels are sized for the room, not for a phone (#891).

`.award-name` was 11 px uppercase mono and `.award-winner` 1.05 rem (~17 px) —
phone sizes on the surface the room reads from three metres away. The award line
("BEST ROUND · Anna") is the finale's second read, straight after the podium.

The same 11–12 px eyebrow sat on `.dashboard-leaderboard-title`
("Leaderboard"), `.fun-fact-label` ("Did you know?"), `.podium-others-title`
and `.podium-other-rank`. #707 sized the four main strings for the television
and left these five behind; #808 caught `.dashboard-category` the same way.

The floor is what matters, not the exact curve: at 1280 px a `1.1vw` label
computes to 14 px, so without a `clamp()` minimum the label shrinks below what
#376 established as legible from a couch. Every rule here is therefore checked
for its floor, in pixels, rather than for the string `clamp(`.
"""

from __future__ import annotations

import re

import pytest

from tests.conftest import dashboard_css, without_comments

#: label -> (selector, minimum px the rule may resolve to)
#: The eyebrows share one step; `.award-winner` sits two above it because it
#: carries a name rather than a category.
EYEBROWS = {
    "the awards line's category": (".award-name", 16),
    "the leaderboard panel's title": (".dashboard-leaderboard-title", 16),
    "the fun fact's label": (".dashboard-funfact .fun-fact-label", 16),
    "the runners-up panel's title": (".podium-others-title", 16),
    "the runners-up rank": (".podium-other-rank", 16),
}
WINNER = (".award-winner", 20)

_MIN_PX = re.compile(r"clamp\(\s*(\d+(?:\.\d+)?)px")


def _last_rule(selector: str) -> str:
    """The declaration block the cascade actually resolves to.

    `tv.css` carries the shared modules ahead of `10-tv.css`, and several of
    these selectors are declared in both. The television gets the last one, so
    that is the one worth asserting on.
    """
    css = without_comments(dashboard_css())
    marker = selector + " {"
    assert marker in css, f"{selector} is not in the television's stylesheet"
    start = css.rindex(marker)
    return css[start : css.index("}", start)]


def _font_size(selector: str) -> str:
    body = _last_rule(selector)
    match = re.search(r"font-size:\s*([^;]+);", body)
    assert match, f"{selector} sets no font-size: {body}"
    return match.group(1).strip()


@pytest.mark.parametrize(
    "what,selector,floor",
    [(what, sel, floor) for what, (sel, floor) in EYEBROWS.items()],
    ids=list(EYEBROWS),
)
def test_the_eyebrow_labels_have_a_legible_floor(
    what: str, selector: str, floor: int
) -> None:
    value = _font_size(selector)
    minimum = _MIN_PX.match(value)
    assert minimum, (
        f"{what} ({selector}) is set to {value!r} — a fixed size on a screen "
        "read from three metres away. #891 asks for a clamp() with a floor."
    )
    assert float(minimum.group(1)) >= floor, (
        f"{what} ({selector}) floors at {minimum.group(1)}px, below the {floor}px "
        "#891 sets for the television"
    )


def test_the_award_winner_reads_louder_than_the_label_above_it() -> None:
    """"BEST ROUND · Anna" — the name is the half the room is looking for."""
    value = _font_size(WINNER[0])
    minimum = _MIN_PX.match(value)
    assert minimum, (
        f"{WINNER[0]} is set to {value!r}; 1.05rem is ~17px, a phone size on the "
        "finale's second read"
    )
    assert float(minimum.group(1)) >= WINNER[1]

    eyebrow = _MIN_PX.match(_font_size(".award-name"))
    assert eyebrow and float(minimum.group(1)) > float(eyebrow.group(1)), (
        "the winner's name must not shrink to the size of the label above it"
    )


def test_the_eyebrows_step_with_the_strings_707_and_808_already_sized() -> None:
    """One scale, not five hand-picked numbers.

    `.dashboard-category` is the rule #808 wrote and the one #891 names as the
    model; a new eyebrow should look like it rather than invent a curve.
    """
    category = _font_size(".dashboard-category")
    assert _MIN_PX.match(category), f".dashboard-category regressed to {category!r}"
    for selector, _floor in EYEBROWS.values():
        value = _font_size(selector)
        assert "vw" in value, (
            f"{selector} is {value!r} — the television's type scales with the "
            "viewport, so a floor alone is only half the rule"
        )
