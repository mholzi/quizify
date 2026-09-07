"""#691 — the lightning round has to fit a 720p television too.

#689 sized the question and dropped the answers to one column on short
screens, and scoped both rules to ``#question-view``. ``#lightning-view`` is
the same layout under a different id: same ``.dashboard-question-body``, same
``.dashboard-question``, same ``.dashboard-answers``. It got neither rule.

Played on a real television, its answer grid ended 938.1px down a 720px
picture — worse than the question view ever was, and in the phase where the
clock is fifteen seconds and reading speed is the whole game. After the fix:
602.5px at 1280x720, 631.4px at 1366x768, nothing below the fold at either,
and 1080p unchanged.

The second test here is a different bug in the same view, found in the same
screenshot: the intro splash stayed on screen over the running question.

These are text-level guards over the television's built stylesheet
(``css/tv.css``, #880).
"""

from __future__ import annotations

import re

from tests.conftest import dashboard_css, dashboard_markup, dashboard_script


def _css() -> str:
    # #829/#880: the television's code and styles are their own files now.
    return re.sub(r"/\*.*?\*/", "", dashboard_css(), flags=re.DOTALL)


def _short_screen_block(css: str) -> str:
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


def _selectors_for(block: str, prop: str, klass: str) -> list[str]:
    """Every selector list in ``block`` whose body sets ``prop`` on ``klass``."""
    found = []
    for match in re.finditer(r"([^{}]+)\{([^{}]*)\}", block):
        selectors = [s.strip() for s in match.group(1).split(",")]
        if any(s.endswith(klass) for s in selectors) and prop in match.group(2):
            found.append(selectors)
    return found


def test_both_question_views_get_the_short_screen_sizing() -> None:
    """The lightning view is the question view under another id.

    Written against the property rather than a selector string: whatever rule
    sizes the question on a short screen must name both views, so a third view
    with this layout fails here instead of failing on someone's television.
    """
    block = _short_screen_block(_css())

    for prop, klass in (
        ("font-size", ".dashboard-question"),
        ("grid-template-columns", ".dashboard-answers"),
    ):
        rules = _selectors_for(block, prop, klass)
        assert rules, f"no short-screen rule sets {prop} on {klass}"
        for selectors in rules:
            named = {s.split()[0] for s in selectors if s.startswith("#")}
            assert {"#question-view", "#lightning-view"} <= named, (
                f"{prop} on {klass} is scoped to {sorted(named)} — the lightning "
                "view shares this layout and needs the same rule"
            )


def test_the_lightning_splash_can_actually_be_hidden() -> None:
    """`hidden` is a UA rule; a class that sets `display` beats it.

    `handleLightningQuestion` sets `splash.hidden = true` and always did — the
    attribute simply had no effect against `.dashboard-lightning-splash
    { display: flex }`, so the "Get ready!" card sat over the running question
    for the whole round. The pill above it learned the same lesson earlier.
    """
    css = _css()
    assert re.search(
        r"\.dashboard-lightning-splash\[hidden\]\s*\{[^}]*display:\s*none",
        css,
    ), "the splash sets display in a class rule and needs a [hidden] guard"


def test_every_element_the_dashboard_hides_by_attribute_survives_its_own_css() -> None:
    """The general form of the bug above, over the whole file.

    Any element that JavaScript hides with the `hidden` attribute, and whose
    class sets `display`, needs a `[hidden]` guard — otherwise the attribute is
    a no-op and the failure is invisible in review.
    """
    # #829/#880: the hiding is in the script, the classes in the markup, the
    # guard in the stylesheet — the one assertion that spans all three.
    script = dashboard_script()
    markup = dashboard_markup()
    css = _css()

    hidden_by_js = {
        match.group(1)
        for match in re.finditer(r"els\.(\w+)\.hidden\s*=\s*true", script)
    }
    assert hidden_by_js, "expected the dashboard to hide elements by attribute"

    # els.<name> -> the element's id, then the id -> its classes.
    ids = dict(
        re.findall(r"(\w+):\s*document\.getElementById\('([\w-]+)'\)", script)
    )
    for name in sorted(hidden_by_js):
        element_id = ids.get(name)
        if not element_id:
            continue
        tag = re.search(rf'id="{element_id}"[^>]*class="([^"]+)"', markup)
        if not tag:
            continue
        for klass in tag.group(1).split():
            sets_display = re.search(
                rf"(^|[^-\w])\.{re.escape(klass)}\s*\{{[^}}]*display:\s*(?!none)",
                css,
                re.MULTILINE,
            )
            if not sets_display:
                continue
            guarded = re.search(
                rf"\.{re.escape(klass)}\[hidden\]\s*\{{[^}}]*display:\s*none", css
            )
            assert guarded, (
                f"#{element_id} is hidden via the `hidden` attribute, but "
                f".{klass} sets `display` with no [hidden] guard — the "
                "attribute does nothing"
            )
