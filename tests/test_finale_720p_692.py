"""#692 — the end screen has to fit a 720p television.

Played on a real one: the right column ran nine elements past the bottom edge.
What a 720p room saw at the end of a game was the podium and two award cards —
the leaderboard rows and the head-to-head line, the headline of v1.12.0, were
not in the picture at all.

Nothing here was too tall on its own. The column simply stacked more than the
screen has room for: two award cards came to 337.1px of a 517.8px column, and
the leaderboard began at 624px of 720. So this is a content decision, not a
sizing one, and the decision is recorded in the CSS comment: the awards lose
their *detail* line on short screens and keep everything else.

That line is the footnote ("25 pts in round 6") under a heading that already
carries the point ("TOP SCORE · ANNA"). Capping the awards box was measured
and rejected — it clips a card halfway, which is the same defect one box
further in. Dropping a whole line beats cutting one.

Measured after, on the television: awards 171.9px, every leaderboard row in
the picture, the head-to-head line at 608.7–665.6px, nothing below the fold at
1280x720 or 1366x768. 1080p keeps the detail line.
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


def _short_screen_blocks(css: str) -> list[str]:
    """Every `@media (max-height: 850px)` block in the file, not just the first."""
    out = []
    for match in re.finditer(r"@media \(max-height: 850px\)", css):
        start = css.index("{", match.start())
        depth, j = 0, start
        while True:
            if css[j] == "{":
                depth += 1
            elif css[j] == "}":
                depth -= 1
                if depth == 0:
                    out.append(css[start + 1 : j])
                    break
            j += 1
    return out


def test_short_screens_drop_the_award_footnote_and_nothing_else() -> None:
    short = "\n".join(_short_screen_blocks(_css()))
    rule = re.search(
        r"\.dashboard-finale--split \.award-detail\s*\{([^}]*)\}", short
    )
    assert rule, "the end screen has no short-screen rule for the award detail"
    assert "display: none" in rule.group(1), rule.group(1)


def test_the_award_footnote_survives_on_a_1080p_television() -> None:
    """The trade is bought for short screens only. On 1080p the detail is the
    reason the award is interesting, and there is room for it."""
    css = _css()
    outside = css
    for block in _short_screen_blocks(css):
        outside = outside.replace(block, "")
    hidden = re.search(r"\.award-detail\s*\{[^}]*display:\s*none", outside)
    assert not hidden, (
        "the award detail is hidden everywhere, not only on short screens"
    )


def test_the_head_to_head_line_is_the_last_block_in_the_column() -> None:
    """It is the newest block and the one furthest from the fold, so it is the
    first thing an overflow eats. Keeping it last is what makes the leaderboard
    — which knows how to give way — the block that shrinks instead."""
    html = DASHBOARD.read_text(encoding="utf-8")
    start = html.index('<div class="dashboard-finale-right">')
    right = html[start : html.index("</div>", html.index('id="end-h2h"'))]
    assert right.index('class="finale-leaderboard-card"') < right.index('id="end-h2h"')


def test_the_leaderboard_outranks_the_awards() -> None:
    """Markus, 03.09.2026, after a three-player game on a 720p television.

    #694 freed the space and the awards took it: the leaderboard card was
    squeezed to 25.7px, "Leaderboard" was cut in half, and not one player row
    was on screen while both award cards sat above it in full. 1080p failed the
    same way, so this is a player-count problem and not a screen-height one —
    which is why the rule is not inside a media query.

    The order is now fixed in CSS: the leaderboard reserves room for a header
    and three rows, and the awards are the block that gives way.
    """
    css = _css()

    def declarations(selector: str) -> str:
        """Every rule for this selector, joined — CSS cascades, so a single
        `re.search` would read the first of them and miss the one that wins."""
        pattern = re.escape(selector) + r"\s*\{([^}]*)\}"
        found = re.findall(pattern, css)
        assert found, f"no rule found for {selector}"
        return "\n".join(found)

    card = declarations(".dashboard-finale--split .finale-leaderboard-card")
    floor = re.search(r"min-height:\s*(\d+)px", card)
    assert floor and int(floor.group(1)) >= 200, (
        f"the leaderboard needs a floor that holds three rows: {card}"
    )

    awards = declarations(".dashboard-finale--split .awards-section")
    assert "flex: 1 1 auto" in awards, awards
    assert "min-height: 0" in awards, awards
