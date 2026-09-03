"""#680 — the question view must fit a 720p television.

`.dashboard-left` centres its children with nowhere to put the excess. At
1280x720 the question view needed 823.3px of a 549.8px column, and plain
`center` splits an overflow across *both* ends — so the top of it climbed into
the fixed header and the category label sat 106px behind the timer bar.

Two halves to the fix, and they are not interchangeable:

* `safe center` stops the upward climb. It is a floor, not a solution: it only
  decides which end the overflow comes out of.
* The short-screen block makes the content actually fit, so there is no
  overflow to place. Measured after: 536.3px of a 549.8px column at reveal,
  461.6px in the question phase, overlap 0 at both 1280x720 and 1920x1080.

These are text-level guards; `dashboard.html` keeps its CSS inline.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
DASHBOARD = REPO / "custom_components" / "quizify" / "www" / "dashboard.html"


def _css() -> str:
    html = DASHBOARD.read_text(encoding="utf-8")
    blocks = re.findall(r"<style[^>]*>(.*?)</style>", html, re.DOTALL)
    assert blocks, "dashboard.html is expected to carry its CSS inline"
    return re.sub(r"/\*.*?\*/", "", "\n".join(blocks), flags=re.DOTALL)


def _balanced(css: str, header: str) -> str:
    start = css.index(header)
    start = css.index("{", start)
    depth, j = 0, start
    while True:
        if css[j] == "{":
            depth += 1
        elif css[j] == "}":
            depth -= 1
            if depth == 0:
                return css[start + 1 : j]
        j += 1


def _rule(css: str, selector: str) -> str:
    blocks = [
        m.group(2)
        for m in re.finditer(r"([^{}]+)\{([^{}]*)\}", css)
        if selector in [s.strip() for s in m.group(1).split(",")]
    ]
    assert blocks, f"{selector}: no rule found"
    return blocks[0]


def test_the_column_cannot_centre_content_into_the_header() -> None:
    assert "justify-content: safe center" in _rule(_css(), ".dashboard-left")


def test_the_safe_keyword_keeps_a_plain_center_beneath_it() -> None:
    """A renderer that does not know `safe` drops the whole declaration, and
    the initial value is `flex-start`, not `center`. Without the fallback an
    old browser would top-align every question rather than behave as it does
    today."""
    column = _rule(_css(), ".dashboard-left")
    plain = column.index("justify-content: center")
    safe = column.index("justify-content: safe center")
    assert plain < safe, "the fallback has to come first to be overridden"


def test_short_screens_get_one_answer_column() -> None:
    """Three columns are why the grid was 383.5px tall: a ~221px tile cannot
    hold an answer and its chart side by side, so both wrap.

    Two columns (this issue's first answer) were a step in the right direction
    that stopped one short — measured live on a 720p television, a 343px tile
    still wrapped a long answer onto three lines and pushed the chart under it,
    241.7px per tile. One column gives the tile the whole 699px column and it
    drops to 125px. The four-answer argument for two columns does not apply
    here: every multiple-choice question in the library carries exactly three
    answers."""
    short = _balanced(_css(), "@media (max-height: 850px)")
    grid = _rule(short, "#question-view .dashboard-answers")
    cols = [ln for ln in grid.splitlines() if "grid-template-columns" in ln]
    assert cols, grid
    assert "1fr 1fr" not in cols[0], "two columns still wrap a long answer"
    assert "1fr" in cols[0]


def test_the_question_is_sized_by_the_budget_that_is_scarce() -> None:
    """The trade this file used to forbid, now made openly.

    The old rule was "spacing, not type", so that shrinking #376's sizes could
    never happen by accident. Playing the game on a 720p television showed what
    it cost: a 150-character question rendered 6.5 lines at 53.8px, the body
    came to 837.3px of 579.9px, and the third answer tile was off the screen
    entirely. Type that cannot be read because it is not on the screen is the
    worse end of that trade.

    The cause is a mismatch of axes — `4.2vw` sizes the question by *width*,
    and width is the one thing a 1280x720 television has enough of. So on short
    screens it is sized by height instead. What still may not happen silently:

    * only inside the short-screen block (1080p keeps #376 exactly),
    * only the question — the answers keep their size,
    * and with a floor, so it can never scale to nothing.
    """
    css = _css()
    short = _balanced(css, "@media (max-height: 850px)")
    rules = {
        sel.strip(): m.group(2)
        for m in re.finditer(r"([^{}]+)\{([^{}]*)\}", short)
        for sel in m.group(1).split(",")
    }

    question = rules.get("#question-view .dashboard-question", "")
    assert "vh" in question, "the question has to be sized by the scarce axis"
    floor = re.search(r"font-size:\s*clamp\(\s*(\d+)px", question)
    assert floor and int(floor.group(1)) >= 24, question

    for selector, body in rules.items():
        if selector.startswith("#question-view") and "font-size" in body:
            assert selector.endswith(".dashboard-question"), (
                f"{selector} spends type as well — only the question may"
            )

    base = _rule(css, ".dashboard-question")
    assert "clamp(32px, 4.2vw, 56px)" in base, "#376's room-sized type stays"


def test_the_fun_fact_does_not_weigh_on_this_column_at_all() -> None:
    """At 1280x720 the fun fact's top edge measured 726.5px of a 720px
    viewport — below the fold before this change and after it — while still
    holding 83.6px of column and pushing the last answer row towards the same
    fold. This issue took the space back by hiding it; #685 then moved it to
    the right column, which removes the cause instead of the symptom.

    Either way the property that matters here is the same: nothing that the
    left column cannot show may take height from the answers.
    """
    html = DASHBOARD.read_text(encoding="utf-8")
    start = html.index("<!-- QUESTION VIEW -->")
    view = html[start : html.index("<!-- FINALE VIEW -->", start)]
    left = view.index('id="dashboard-left"')
    right = view.index('class="dashboard-right"')
    fact = view.index('id="fun-fact"')
    assert left < right, "the left column is expected to come first"
    assert fact > right, "the fun fact is back inside the column that overflows"


def test_the_one_column_rule_still_matches_what_the_library_ships() -> None:
    """The single column was measured against three answers, because that is
    all this library has: 4.545 multiple-choice questions, every one of them
    with exactly three. Four long answers do not fit one column at 720p — they
    do not fit two either, and they do not exist. If a pack ever ships four,
    the measurement behind the rule above is void and wants redoing rather than
    trusting."""
    import json

    questions = REPO / "custom_components" / "quizify" / "questions"
    counts: set[int] = set()
    for path in sorted(questions.glob("*.json")):
        if path.name == "versions.json":
            continue
        data = json.loads(path.read_text(encoding="utf-8"))
        items = data.get("questions") if isinstance(data, dict) else data
        for item in items or []:
            answers = item.get("answers") or item.get("options") or []
            if isinstance(answers, list) and answers:
                counts.add(len(answers))

    assert counts == {3}, f"answer counts other than three shipped: {sorted(counts)}"
