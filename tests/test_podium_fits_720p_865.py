"""#865 — the podium's leaderboard clipped the last player on a 720p television.

Found on real hardware during the v1.16.0-RC4 live test, on the podium of a
four-player game. #836 fixed the lobby at 720p; this is the same shape of
defect one screen later. ``#finale-leaderboard`` is a scroll box, a television
cannot scroll, and the fourth of four players sat below its lower edge — so at
the moment the whole evening was building towards, one guest was not on the
final board at all.

Measured through CDP against a real Chrome, the finale plus the duel line that
arrives a beat later on ``analytics_recorded`` (without it the column is not
yet full and the bug does not appear), ``#finale-leaderboard`` clientHeight vs
scrollHeight, and how many rows end above the box's bottom edge:

                     BEFORE                         AFTER
                     4 players    8 players         4 players    8 players
    1280x720         202/263  3   202/513  3        265/265  4   287/287  8
    1366x768         239/271  3   239/531  3        273/273  4   303/303  8
    1920x1080        288/288  4   480/564  6        289/289  4   565/565  8

    (clientHeight/scrollHeight, then rows fully visible.)

Nothing is clipped afterwards at any of the three resolutions for three to
eight players, and 1920x1080 seats twelve. At 1280x720 and 1366x768 nine or
more players is arithmetic no layout beats: the board then shows what fits and
says how many names are missing, which is the "+N more" tail the in-game panel
has used since #429 — a stated count, not a name silently under an edge.

Three things had to change, and each is guarded below.

**The three-row floor was a lid.** ``fitAwards`` sized the awards against the
height the leaderboard was *guaranteed*, and #695 set that at header + three
rows. A four-player game therefore got three rows and scrolled the fourth,
which on a television means hiding it. The function now asks for every row in
the list.

**A 16px margin from the phone's stylesheet.** ``css/src/02-shared.css`` gives
every ``.card-header`` a ``margin-bottom``, and dashboard.html has loaded that
sheet since the day it was created — 16px of a 518px column spent on a band of
nothing between a rule and the first row. Exactly the species of leak #775
found in ``.award-card``'s ``max-width``. Reclaiming it is one more player on
the board at 1280x720. The measurement function counts the header's margins
now too, so the next stray rule cannot make it under-report in silence.

**A density ladder, and a floor under it.** ``fitFinaleBoard`` compacts the
rows a step at a time until every entrant is in the picture, and only then
shows fewer of them. The last step stops at 16px, which is the floor #376 set
for this screen: a rank nobody can read from a sofa is not a rank that was
shown.

Text-level guards; dashboard.html keeps its CSS and its JS inline, and the
pixel figures above cannot be asserted from pytest. What is asserted is that
the rules and the arithmetic the measurements came from are still in the file —
including, as in #836, that no rule here points at nothing.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
WWW = REPO / "custom_components" / "quizify" / "www"
DASHBOARD = WWW / "dashboard.html"
SHARED_CSS = WWW / "css" / "src" / "02-shared.css"


def _strip_comments(text: str) -> str:
    return re.sub(r"/\*.*?\*/", "", text, flags=re.DOTALL)


def _css() -> str:
    html = DASHBOARD.read_text(encoding="utf-8")
    blocks = re.findall(r"<style[^>]*>(.*?)</style>", html, re.DOTALL)
    assert blocks, "dashboard.html is expected to carry its CSS inline"
    return _strip_comments("\n".join(blocks))


def _js() -> str:
    html = DASHBOARD.read_text(encoding="utf-8")
    blocks = re.findall(r"<script(?![^>]*\bsrc=)[^>]*>(.*?)</script>", html, re.DOTALL)
    assert blocks, "dashboard.html is expected to carry its JS inline"
    return "\n".join(blocks)


def _js_function(name: str) -> str:
    source = _strip_comments(_js())
    signature = "function " + name + "("
    assert signature in source, f"{name}() is gone from dashboard.html"
    start = source.index(signature)
    depth = 0
    seen = False
    for i in range(start, len(source)):
        if source[i] == "{":
            depth += 1
            seen = True
        elif source[i] == "}":
            depth -= 1
            if seen and depth == 0:
                return source[start : i + 1]
    raise AssertionError(f"unbalanced braces after {name}()")


def _rule(css: str, selector: str) -> str:
    match = re.search(re.escape(selector) + r"\s*\{([^}]*)\}", css)
    assert match is not None, f"no rule for {selector}"
    return match.group(1)


def _density_classes() -> list[str]:
    """The ladder as the page declares it, read out of the JS."""
    match = re.search(r"var FINALE_DENSITY = \[([^\]]*)\]", _strip_comments(_js()))
    assert match is not None, "the finale board has no density ladder"
    return [
        step.strip().strip("'\"")
        for step in match.group(1).split(",")
        if step.strip().strip("'\"")
    ]


# ---------------------------------------------------------------------------
# The three-row lid
# ---------------------------------------------------------------------------


def _budget_function_name() -> str:
    """Whatever fitAwards() subtracts from the column for the leaderboard."""
    match = re.search(
        r"column\.clientHeight\s*\n?\s*-\s*(\w+)\(\)", _strip_comments(_js())
    )
    assert match is not None, "fitAwards no longer budgets around the leaderboard"
    return match.group(1)


def test_the_leaderboards_share_of_the_column_is_not_capped_at_three_rows() -> None:
    """The bug itself. A cap here is what put the fourth of four players below
    an edge a television cannot scroll past."""
    body = _js_function(_budget_function_name())
    assert "Math.min(3" not in body, (
        "the leaderboard is budgeted for three rows again — that is a lid, "
        "not a floor, and a four-player game is the smallest party this "
        "screen exists for"
    )


def test_the_leaderboard_asks_for_every_row_it_holds() -> None:
    body = _js_function(_budget_function_name())
    assert re.search(r"i\s*<\s*rows\.length", body), (
        "the height the board asks for no longer sums every row in the list"
    )


# ---------------------------------------------------------------------------
# The 16px the phone's stylesheet was spending
# ---------------------------------------------------------------------------


def test_the_shared_sheet_still_gives_every_card_header_a_margin() -> None:
    """The premise. If this ever goes away the override below is dead code and
    the test after it would pass for the wrong reason."""
    shared = _strip_comments(SHARED_CSS.read_text(encoding="utf-8"))
    rule = _rule(shared, ".card-header")
    assert re.search(r"margin-bottom:\s*(?!0)", rule), (
        "css/src/02-shared.css no longer sets .card-header's margin-bottom"
    )


def test_the_finale_card_header_does_not_pay_for_it() -> None:
    rule = _rule(_css(), ".finale-leaderboard-card .card-header")
    assert re.search(r"margin-bottom:\s*0", rule), (
        "16px of a 518px television column goes to a band of nothing between "
        "the header rule and the first player"
    )


def test_the_measurement_counts_the_headers_margins() -> None:
    """Belt and braces: the override above is a rule someone can delete, the
    arithmetic is what must never under-report again."""
    body = _js_function(_budget_function_name())
    assert "marginTop" in body and "marginBottom" in body, (
        "the leaderboard's height is measured without the header's margins — "
        "a rule in another file can silently make it wrong again"
    )


# ---------------------------------------------------------------------------
# The density ladder
# ---------------------------------------------------------------------------


def test_the_ladder_starts_at_the_board_as_it_was() -> None:
    """A four-player game must not pay for a twelve-player one: the first step
    is the untouched board, and the fit stops at the first step that works."""
    ladder = re.search(r"var FINALE_DENSITY = \[([^\]]*)\]", _strip_comments(_js()))
    assert ladder is not None
    first = ladder.group(1).split(",")[0].strip()
    assert first in ("''", '""'), f"the ladder starts compacted: {first}"


def test_every_density_step_has_rules_and_every_rule_has_a_step() -> None:
    """#836's lesson: a compaction written against a selector the page does not
    have is compaction that never happens. Both directions, so a renamed class
    cannot leave half the ladder dead."""
    css = _css()
    for cls in _density_classes():
        assert f".dashboard-leaderboard.{cls}" in css, (
            f"{cls} is in the ladder but nothing in the stylesheet uses it"
        )
    for cls in set(re.findall(r"\.dashboard-leaderboard\.(is-[\w-]+)", css)):
        assert cls in _density_classes(), (
            f"{cls} is styled but the fit never applies it"
        )


def test_the_ladder_is_scoped_to_the_result_screen() -> None:
    """The in-game panel and the lightning panels share .dashboard-leaderboard.
    None of them may shrink because the podium needed room."""
    for rule in re.findall(
        r"([^{}]*\.dashboard-leaderboard\.is-[\w-]+[^{}]*)\{", _css()
    ):
        assert "#finale-view" in rule, rule.strip()


def test_no_density_step_shrinks_the_type_below_the_couch_floor() -> None:
    """#376 scaled this screen for a couch and 16px is where that stopped. A
    rank nobody can read from a sofa is not a rank that was shown."""
    css = _css()
    for cls in _density_classes():
        for block in re.findall(
            r"#finale-view \.dashboard-leaderboard\." + cls + r"[^{]*\{([^}]*)\}", css
        ):
            for size in re.findall(r"font-size:\s*clamp\(\s*(\d+(?:\.\d+)?)px", block):
                assert float(size) >= 16, f"{cls}: {size}px is below the 16px floor"
            for size in re.findall(r"font-size:\s*(\d+(?:\.\d+)?)px\s*;", block):
                assert float(size) >= 16, f"{cls}: {size}px is below the 16px floor"


# ---------------------------------------------------------------------------
# What the board does when even the tightest rows are not enough
# ---------------------------------------------------------------------------


def test_the_finale_can_show_fewer_rows_and_say_how_many() -> None:
    """Nine guests on a 720p television is arithmetic no layout beats. Being
    told three names are missing is not the same defect as one name silently
    below the fold — so the "+N more" tail (#429) must be reachable here, not
    only on the in-game panel."""
    body = _js_function("renderLeaderboard")
    assert "limit" in body, "the finale cannot ask for fewer rows than it has"
    assert re.search(r"var hidden = players\.length - list\.length", body), (
        "the overflow tail is still computed only for the capped in-game panel"
    )


def test_the_fit_runs_again_when_the_duel_line_arrives() -> None:
    """It arrives a beat after the finale, on analytics_recorded, and it is the
    slice that turned a four-player board into a three-and-a-bit one."""
    body = _js_function("handleHeadToHead")
    assert "fitFinaleBoard()" in body, (
        "the duel line still re-fits only the awards, not the board"
    )


def test_the_fit_runs_again_when_the_column_changes_size() -> None:
    """A television that changes resolution, or a font that lands late."""
    source = _strip_comments(_js())
    match = re.search(r"setTimeout\((\w+), 100\)", source)
    assert match is not None, "the debounced re-fit is gone"
    assert match.group(1) == "fitFinaleBoard", (
        f"the column's observer re-fits {match.group(1)}, not the whole board"
    )


def test_the_board_is_fitted_when_the_finale_arrives() -> None:
    body = _js_function("handleFinale")
    assert "fitFinaleBoard()" in body
