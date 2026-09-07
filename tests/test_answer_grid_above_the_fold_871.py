"""Three answers reachable without scrolling, in every question layout (#871).

Measured on real hardware during the v1.16.0-RC5 live test — iPhone 14
portrait, 390x844 CSS px at DPR 2, six players in the room::

    ordinary question     #answer-buttons  top 506  bottom 714   fits
    picture question      #answer-buttons  top 722  bottom 958   A half cut
    final round           #answer-buttons  top 555  bottom 867   C clipped by 23

#864 fixed the worst of it — a grid that started 173 px *below* the fold with
nothing on screen to suggest scrolling. These two are the same squeeze one
notch down, and they are not a bug in any one block. ``.game-container`` is a
flex column, so every child above ``#answers-container`` pushes the grid down
by its own height PLUS the column gap, and the two failing screens are simply
the ones with the most children: a 200 px picture banner on one, the FINAL
ROUND pill on the other, and on both a six-player chip row wrapped to a second
line.

So this file is arithmetic, not a browser. It reads the numbers out of the
stylesheet that ships, adds up the stack the way the flex column does, and
checks the bottom of the grid against 844 for the four screens the game can
build — including the one nobody measured, a picture question that is also the
final round, which was the tallest of them all at 1002 px.

What it does NOT do is render anything. It cannot catch a rule that fails to
apply (a typo in a selector, a specificity loss to a later module) or an image
whose intrinsic size defeats ``object-fit``. It is a budget, and it fails when
the budget is spent — which is what happened here.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO))

_CSS = _REPO / "custom_components" / "quizify" / "www" / "css" / "styles.css"

# ---------------------------------------------------------------------------
# The phone, and what the live test read off it
# ---------------------------------------------------------------------------

#: iPhone 14 portrait. Markus' test device, and the viewport of every reading
#: in the issue and in #864 before it.
VIEWPORT_W = 390
VIEWPORT_H = 844

#: ``#answer-buttons`` on an ordinary question with six players: the screen
#: that already fits, and therefore the baseline everything else is measured
#: as a delta from.
BASE_TOP = 506
BASE_GRID_H = 714 - 506

#: The picture question, same room, same moment in the game. The 216 px is the
#: banner: 200 px of ``.question-media`` plus its 8 px margin top and bottom.
PICTURE_TOP = 722
PICTURE_GRID_H = 958 - 722

#: The FINAL ROUND pill costs its own height plus one column gap.
PILL_COST = 555 - 506

#: Six chips wrap to two lines. #870 measured this as the difference between a
#: phone that had the row and one that did not: 64 px.
CHIP_ROW_TWO_LINES = 64

#: …and one line of chips is one 30 px chip high (24 px avatar, 2 px padding
#: and a 1 px border top and bottom), so folding the row onto one line is
#: worth the difference.
CHIP_ROW_ONE_LINE = 30

#: The design tokens, from 00-tokens.css. Read rather than hard-coded would be
#: nicer; they have not moved since the file was written and a change to them
#: would move every number in this file at once, which is a different test.
SPACE = {"--space-xs": 4, "--space-sm": 8, "--space-md": 16, "--space-lg": 24}


# ---------------------------------------------------------------------------
# The stylesheet, as it ships
# ---------------------------------------------------------------------------


def _css() -> str:
    return _CSS.read_text("utf-8")


def _phone_block(css: str | None = None) -> str:
    """The body of the ``@media (max-width: 480px)`` block, or "".

    Nothing else in the stylesheet uses that breakpoint, so this is the phone
    layout in one piece. An empty string is the RC5 client: no phone block at
    all, and the arithmetic below then adds up to the readings in the issue —
    which is how the model checks itself, without ever writing to the
    stylesheet other agents and other tests are reading.
    """
    # Comments first: a rule preceded by one would otherwise read as a
    # selector with the comment glued onto its front, and every declaration in
    # it would come back missing — which is indistinguishable here from a rule
    # that is not there.
    source = re.sub(r"/\*.*?\*/", "", _css() if css is None else css, flags=re.S)
    marker = "@media (max-width: 480px) {"
    if marker not in source:
        return ""
    start = source.index(marker) + len(marker)
    depth = 1
    for i in range(start, len(source)):
        if source[i] == "{":
            depth += 1
        elif source[i] == "}":
            depth -= 1
            if depth == 0:
                return source[start:i]
    raise AssertionError("unterminated @media block in styles.css")


def _declaration(block: str, selector: str, prop: str) -> str | None:
    """The value of one property in one rule of ``block``, or None."""
    for match in re.finditer(r"([^{}]+)\{([^{}]*)\}", block):
        selectors = [s.strip() for s in match.group(1).split(",")]
        if selector not in selectors:
            continue
        found = re.search(
            rf"(?<![\w-]){re.escape(prop)}\s*:\s*([^;]+);", match.group(2)
        )
        if found:
            return found.group(1).strip()
    return None


def _px(value: str | None, default: int) -> int:
    """A length, resolving the one level of ``var(--space-*)`` the CSS uses.

    Only the FIRST length in the value: every property read here is either a
    single length or a ``padding``/``margin`` shorthand whose first value is
    the vertical one, which is the only axis this budget is about.
    """
    if value is None:
        return default
    token = value.split()[0]
    var = re.fullmatch(r"var\((--space-[a-z]+)\)", token)
    if var:
        return SPACE[var.group(1)]
    number = re.fullmatch(r"(\d+)px", token)
    assert number, f"unreadable length: {value!r}"
    return int(number.group(1))


# ---------------------------------------------------------------------------
# The budget
# ---------------------------------------------------------------------------


def _savings(css: str | None = None) -> dict[str, int]:
    """What the phone block takes off the stack, per block, in px."""
    phone = _phone_block(css)

    def trimmed(selector: str, prop: str, base: int, sides: int) -> int:
        value = _px(_declaration(phone, selector, prop), base)
        assert value <= base, (
            f"{selector} {{{prop}}} is bigger on a phone than off one"
        )
        return (base - value) * sides

    chips = 0
    if _declaration(phone, ".submitted-players", "flex-wrap") == "nowrap":
        assert _declaration(phone, ".submitted-players", "overflow-x") == "auto", (
            "a row that cannot wrap and cannot scroll simply loses its last "
            "chips off the side of the screen"
        )
        chips = CHIP_ROW_TWO_LINES - CHIP_ROW_ONE_LINE

    return {
        # ``.game-container`` is the flex column; the gap is charged once per
        # boundary between visible children, so this is per-gap.
        "gap": trimmed(".game-container", "gap", SPACE["--space-md"], 1),
        "header": trimmed(
            ".game-header-compact", "padding", SPACE["--space-sm"], 2
        ),
        "card": trimmed(".question-card", "padding", SPACE["--space-lg"], 2),
        "card_container": trimmed(
            ".question-card-container", "margin-bottom", SPACE["--space-md"], 1
        ),
        "timer": trimmed(".timer-container", "padding", SPACE["--space-sm"], 2),
        "chips": chips,
        "answer_section": trimmed(
            ".answer-section", "margin-top", SPACE["--space-md"], 1
        ),
        "banner": trimmed(".question-media", "height", 200, 1),
    }


def _grid(
    *, picture: bool, final_round: bool, css: str | None = None
) -> tuple[int, int]:
    """``(top, bottom)`` of ``#answer-buttons`` for one screen, in px."""
    saving = _savings(css)

    # Every screen pays for the header, the card, the timer row, the chip row
    # and the answer section's own margin.
    fixed = (
        saving["header"]
        + saving["card"]
        + saving["card_container"]
        + saving["timer"]
        + saving["chips"]
        + saving["answer_section"]
    )

    # Visible children of .game-container above the grid: the header, the
    # question card, the timer, the chip row — plus the pill when it is up.
    # Four boundaries, five with the pill.
    gaps = (4 + (1 if final_round else 0)) * saving["gap"]

    top = PICTURE_TOP if picture else BASE_TOP
    height = PICTURE_GRID_H if picture else BASE_GRID_H
    if final_round:
        top += PILL_COST

    top -= fixed + gaps + (saving["banner"] if picture else 0)
    return top, top + height


SCREENS = {
    "ordinary question": {"picture": False, "final_round": False},
    "picture question": {"picture": True, "final_round": False},
    "final round": {"picture": False, "final_round": True},
    "picture question on the final round": {"picture": True, "final_round": True},
}


# ---------------------------------------------------------------------------
# The tests
# ---------------------------------------------------------------------------


def test_the_model_reproduces_the_live_tests_readings() -> None:
    """Before anything is asserted about the fix: with no phone block the
    arithmetic has to produce the numbers the issue measured off a real phone,
    or the budget below is arithmetic about nothing.
    """
    rc5 = _CSS.read_text("utf-8").replace(
        "@media (max-width: 480px) {", "@media (min-width: 99999px) {"
    )
    assert _phone_block(rc5) == "", "the fix survived being taken away"

    assert _grid(picture=False, final_round=False, css=rc5) == (506, 714)
    assert _grid(picture=True, final_round=False, css=rc5) == (722, 958)
    assert _grid(picture=False, final_round=True, css=rc5) == (555, 763)
    # The screen nobody measured, and the worst of them.
    assert _grid(picture=True, final_round=True, css=rc5) == (771, 1007)


def test_the_three_answers_fit_on_every_question_screen() -> None:
    """#871. A guest with a phone in their hand can reach answer C without
    scrolling, whatever the question is."""
    for name, screen in SCREENS.items():
        top, bottom = _grid(**screen)
        assert bottom <= VIEWPORT_H, (
            f"{name}: the answer grid runs to {bottom} px against a "
            f"{VIEWPORT_H} px viewport — the last answer is below the fold"
        )
        assert top > 0, f"{name}: the model has gone negative"


def test_there_is_room_left_for_a_line_of_text_to_wrap() -> None:
    """The readings above assume the answers are one line each. They are not
    always: the final-round grid the live test measured was 312 px, not 208,
    because two of the answers wrapped. A budget with nothing left in it is a
    budget that fails on the first long answer, so each screen keeps at least
    one answer button's height (60 px) in hand.
    """
    for name, screen in SCREENS.items():
        _, bottom = _grid(**screen)
        assert VIEWPORT_H - bottom >= 60, (
            f"{name}: only {VIEWPORT_H - bottom} px of slack — one wrapped "
            "answer puts the grid back through the fold"
        )


def test_the_phone_block_does_not_shrink_a_tap_target() -> None:
    """The budget is taken out of the blocks ABOVE the grid, never out of the
    answers themselves. 44 px is the WCAG 2.5.8 floor the zoom button was
    lifted to in #423, and the answer buttons sit at 60."""
    phone = _phone_block()

    for selector, prop in (
        (".answer-btn", "min-height"),
        (".question-image-zoom", "width"),
        (".question-image-zoom", "height"),
    ):
        assert _declaration(phone, selector, prop) is None, (
            f"the phone block resizes {selector} — the fold is not paid for "
            "out of what people have to hit"
        )
