"""#836 — the TV lobby overflowed 720p and cut the head-to-head line in half.

Measured through CDP against a real Chrome, four guests and a duel line, with
the QR at the size the short-screen block gives it:

    before  1280x720   innerHeight 720  scrollHeight 733  -> 13.1px clipped
    before  1366x768   innerHeight 768  scrollHeight 768  -> fits
    before  1920x1080  innerHeight 1080 scrollHeight 1080 -> fits
    after   1280x720   innerHeight 720  scrollHeight 720  -> 42.7px to spare
    after   1366x768   innerHeight 768  scrollHeight 768  -> 59.8px to spare
    after   1920x1080  unchanged (the block does not apply above 850px)

``body`` is ``overflow: hidden`` and a television does not scroll, so those
13px were simply not in the picture: "last 90 days" was sliced through the
middle.

The cause was two dead selectors. The short-screen compaction added for the
lobby was written against ``#lobby-view`` — which is the PHONE's lobby, in
``css/src/07-player.css``. The television's is ``#waiting-view``, so neither
rule has ever matched anything on this page and half the compaction never
happened here. The column's own 24px rhythm is the rest of it: four children
means three gaps, and 72px of air is a 1080p proportion on a screen with
549.8px of budget (#680/#688 measured that budget).

Text-level guards over the television's stylesheet and markup. The pixel
numbers above cannot be asserted from pytest — what is asserted is that the
rules point at elements this page actually has.
"""

from __future__ import annotations

import re

from tests.conftest import dashboard_css, dashboard_markup


def _css() -> str:
    # #829/#880: the television's code and styles are their own files now.
    return re.sub(r"/\*.*?\*/", "", dashboard_css(), flags=re.DOTALL)


def _short_screen_block() -> str:
    css = _css()
    start = css.index("@media (max-height: 850px)")
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


def _ids_in_markup() -> set[str]:
    # #829/#880: dashboard.html is markup only, so no <style> to skip past.
    return set(re.findall(r'\bid="([^"]+)"', dashboard_markup()))


def test_the_lobby_the_television_actually_renders_is_waiting_view() -> None:
    """The premise of the whole fix, pinned so it cannot silently invert."""
    ids = _ids_in_markup()
    assert "waiting-view" in ids
    assert "lobby-view" not in ids, "that id belongs to the phone"


def test_no_short_screen_rule_targets_the_phone_s_lobby() -> None:
    block = _short_screen_block()
    assert "#lobby-view" not in block, (
        "#lobby-view is the phone's lobby — a rule written against it here "
        "has never matched anything, which is how 13px went missing"
    )


def test_the_compaction_the_dead_rules_intended_is_in_place() -> None:
    block = _short_screen_block()
    for selector in (
        "#waiting-view .dashboard-waiting-text",
        "#waiting-view .dashboard-join-fallback",
    ):
        assert selector in block, f"{selector}: the retargeted rule is missing"


def test_the_column_rhythm_is_compacted_on_a_short_screen() -> None:
    """Retargeting alone recovers 12px of the 51.5px the column was over."""
    block = _short_screen_block()
    match = re.search(
        r"#waiting-view\s+\.dashboard-waiting\s*\{([^}]*)\}", block
    )
    assert match is not None, "the lobby column keeps its 1080p gap on a 720p TV"
    gap = re.search(r"gap:\s*(\d+)px", match.group(1))
    assert gap is not None and int(gap.group(1)) < 24


def test_a_tall_screen_keeps_the_lobby_it_had() -> None:
    """1080p was never the problem and must not be shrunk to fix 720p.

    Every rule of this fix lives inside the short-screen block; none of them
    may leak into the base cascade.
    """
    css = _css()
    outside = css[: css.index("@media (max-height: 850px)")]
    assert "#waiting-view .dashboard-waiting" not in outside
    assert "#waiting-view .dashboard-join-fallback" not in outside
