"""The analytics page becomes reachable (issue #627).

`/quizify/analytics` is served and fully built — `analytics.html` is 526 lines
with a working period selector — and a grep for inbound links found **zero**.
Not from admin.html, player.html, dashboard.html, launcher.html or any
JavaScript. A host could only reach their own play history by guessing the URL.

Third case in one day of the same shape: the submission tracker (#619) and the
reveal controls (#618) were both finished code with no path to them. Nothing was
missing here except a line.

Placement was decided from a render, not a preference. A second row pushes the
primary action off a 390px viewport, so both links share one row: they are
equally secondary, which is also true of what they do.

The link carries **no** `?token=`. `analytics.html` reads the admin token from
localStorage via `QuizifyUtils.readAdminToken()` and only falls back to the URL
param, so appending it would put a full-control credential back into browser
history for nothing — the regression #359 removed and #608 found creeping back.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
_WWW = _REPO_ROOT / "custom_components" / "quizify" / "www"
_ADMIN_JS = _WWW / "js" / "admin.js"

LANGUAGES = ("de", "en", "es")


def _without_comments(source: str) -> str:
    source = re.sub(r"/\*.*?\*/", "", source, flags=re.S)
    return re.sub(r"^\s*//.*$", "", source, flags=re.M)


def _function_body(source: str, signature: str) -> str:
    start = source.index(signature)
    depth = 0
    for i in range(start, len(source)):
        if source[i] == "{":
            depth += 1
        elif source[i] == "}":
            depth -= 1
            if depth == 0:
                return source[start : i + 1]
    raise AssertionError(f"unbalanced braces after {signature!r}")


def test_the_label_ships_in_every_language() -> None:
    for code in LANGUAGES:
        setup = json.loads((_WWW / "i18n" / f"{code}.json").read_text("utf-8"))["setup"]
        assert setup.get("stats"), f"{code}.setup.stats missing or empty"


def test_the_link_exists_on_the_setup_screen() -> None:
    html = (_WWW / "admin.html").read_text("utf-8")

    assert 'id="setup-stats-btn"' in html
    assert 'data-i18n="setup.stats"' in html


def test_both_links_share_one_row() -> None:
    """A second row pushes the primary action off a 390px viewport.

    That is what the design round showed, and it is the whole reason this is a
    row rather than a stack.
    """
    html = (_WWW / "admin.html").read_text("utf-8")
    css = (_WWW / "css" / "styles.css").read_text("utf-8")

    row = html.split('class="setup-links-row"', 1)[1][:900]
    assert 'id="setup-tweak-btn"' in row
    assert 'id="setup-stats-btn"' in row

    block = _without_comments(css.split(".setup-links-row {", 1)[1].split("}", 1)[0])
    assert "display: flex" in block


def test_the_separator_is_hidden_from_screen_readers() -> None:
    """A middot between two links is decoration; read aloud it is noise."""
    html = (_WWW / "admin.html").read_text("utf-8")
    sep = html.split('class="setup-links-sep"', 1)[1][:120]

    assert "aria-hidden" in sep


def test_the_link_does_not_put_the_admin_token_in_the_url() -> None:
    """The regression #359 removed and #608 caught creeping back.

    `analytics.html` reads the token from localStorage and only falls back to
    the query param, so the link works without it — appending it would write a
    replayable full-control credential into browser history for no gain.
    """
    body = _without_comments(
        _function_body(_ADMIN_JS.read_text("utf-8"), "function initStatsLink(")
    )

    assert "token" not in body, "the stats link must not carry a token"
    assert "'/quizify/analytics'" in body


def test_the_link_survives_the_android_companion() -> None:
    """That WebView swallows target="_blank" (#348, #377).

    Opening a tab there does nothing at all, which is exactly the dead-button
    failure this issue is about — reintroducing it would be an unusually
    circular bug.
    """
    body = _function_body(_ADMIN_JS.read_text("utf-8"), "function initStatsLink(")

    assert "isAndroidCompanion()" in body
    assert "window.location.href" in body
    assert "window.open(" in body


def test_the_wiring_actually_runs() -> None:
    """A handler nobody calls is how this issue came to exist."""
    source = _ADMIN_JS.read_text("utf-8")

    assert "initStatsLink();" in source
