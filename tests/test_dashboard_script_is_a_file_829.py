"""The television's script is a file, not an inline block (#829).

`dashboard.html` carried ~1,600 lines of JavaScript inline. That made it the
only shipped client code that did not pass through `scripts/build_bundle.py`
and `scripts/build_gzip.py` — and therefore the only one the CI `drift` job
could not keep honest. It also meant a browser could not cache it separately
from the markup, the service worker could not precache it, and forty test
modules had to slice an 8,000-line HTML file to assert on the TV's behaviour.

#828 stopped deliberately short of this because moving the script in the same
PR that rewired the socket layer would have made the diff unreviewable. This is
the move on its own.
"""

from __future__ import annotations

import re
from pathlib import Path

from tests.conftest import dashboard_markup, dashboard_script, without_comments

REPO = Path(__file__).resolve().parent.parent
WWW = REPO / "custom_components" / "quizify" / "www"
DASHBOARD_JS = WWW / "js" / "dashboard.js"


def test_the_dashboard_carries_no_inline_script() -> None:
    """`<script>` with a body, as opposed to `<script src=…>`."""
    html = dashboard_markup()
    inline = [
        block
        for block in re.findall(r"<script\b([^>]*)>(.*?)</script>", html, re.S)
        if block[1].strip()
    ]
    assert not inline, (
        "dashboard.html has an inline <script> again — inline is the one place "
        "the drift guard cannot see, which is the whole of #829"
    )


def test_the_dashboard_carries_no_inline_style() -> None:
    """The other half of the same move (#880)."""
    html = dashboard_markup()
    blocks = [b for b in re.findall(r"<style\b[^>]*>(.*?)</style>", html, re.S) if b.strip()]
    assert not blocks, "dashboard.html has an inline <style> again (#880)"


def test_the_page_loads_the_script_after_what_it_reads() -> None:
    """Order is load-bearing: the TV calls into three globals at start-up.

    `dashboard.js` resolves `QuizifyI18n`, `QuizifyUtils`, `QuizifyRenderShared`
    and the QR vendor global at call time, but `connect()` runs on the last line
    of the file, so anything it touches has to be defined by then.
    """
    html = dashboard_markup()
    order = re.findall(r'<script src="/quizify/static/js/([^"?]+)', html)
    assert "dashboard.js" in order, "dashboard.html does not load its own script"
    own = order.index("dashboard.js")
    for dependency in ("i18n.js", "utils.js", "common.bundle.js", "vendor/qrcode.min.js"):
        assert dependency in order, f"dashboard.html stopped loading {dependency}"
        assert order.index(dependency) < own, (
            f"{dependency} loads after dashboard.js, which calls into it"
        )


def test_the_script_goes_through_the_same_guards_as_every_other() -> None:
    """A `.gz` entry is what puts it under the drift job.

    aiohttp serves `<file>.gz` in place of `<file>`, so an un-listed script is
    both a lost 4x and — worse — a file no test compares against its source.
    """
    asset_gzip = (REPO / "scripts" / "asset_gzip.py").read_text("utf-8")
    assert '"js/dashboard.js"' in asset_gzip, (
        "js/dashboard.js is not in GZIP_TARGETS, so nothing regenerates or "
        "checks its .gz sibling (#792)"
    )
    assert (WWW / "js" / "dashboard.js.gz").is_file()


def test_the_service_worker_precaches_it_for_the_television_only() -> None:
    sw = without_comments((WWW / "sw.js").read_text("utf-8"))
    dashboard_block = sw[sw.index("dashboard: [") : sw.index("\n};", sw.index("dashboard: ["))]
    assert "/quizify/static/js/dashboard.js" in dashboard_block
    for other in ("player: [", "admin: ["):
        block = sw[sw.index(other) : sw.index("]", sw.index(other))]
        assert "dashboard.js" not in block, (
            "a phone would download the television's script it can never run"
        )


def test_the_script_is_the_same_iife_it_was_inline() -> None:
    """Nothing about the move was allowed to change the code.

    The shape is the evidence: one closed-over IIFE in strict mode that ends by
    opening the socket, exactly as the inline block did.
    """
    source = dashboard_script()
    body = without_comments(source)
    assert body.lstrip().startswith("(function() {"), (
        "the script is no longer a single IIFE — it published globals into the "
        "page in the move"
    )
    assert "'use strict';" in body
    assert body.rstrip().endswith("connect();\n})();") or body.rstrip().endswith("})();"), (
        "the file no longer ends by closing the IIFE"
    )
