"""Guard the #374 TV-lobby join-QR + live player roster.

The dashboard is a self-contained ``www/dashboard.html`` with an inline
``<style>`` block and its own inline script (it does NOT bundle player-*.js).
Before #374 its ``#waiting-view`` (LOBBY) rendered only the wordmark + a
"Waiting for game to start…" line, even though the ``.dashboard-qr-section`` /
``.dashboard-player-list`` / ``.dashboard-player-chip`` CSS already existed but
was dormant, and ``qrcode.min.js`` was never loaded on the dashboard.

This test locks in that the dashboard now:
  * loads the ``qrcode.min.js`` vendor lib (same one admin.html uses), and
  * wires the dormant QR-section + player-list markup into ``#waiting-view``,

so a later edit can't silently regress the lobby back to a blank waiting view.
"""

from __future__ import annotations

from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
DASHBOARD = REPO / "custom_components" / "quizify" / "www" / "dashboard.html"


def _waiting_view_block(html: str) -> str:
    """Return the ``#waiting-view`` markup up to the next dashboard view."""
    start = html.index('id="waiting-view"')
    # The QUESTION VIEW is the next sibling dashboard-view after waiting.
    end = html.index('id="question-view"', start)
    return html[start:end]


def test_dashboard_loads_qrcode_lib() -> None:
    html = DASHBOARD.read_text("utf-8")
    assert "js/vendor/qrcode.min.js" in html, (
        "dashboard.html must load the qrcode.min.js vendor lib for the "
        "TV-lobby join QR (#374)"
    )
    # Cache-buster like every other asset include on the page.
    assert "js/vendor/qrcode.min.js?v={{ASSET_VER}}" in html, (
        "the qrcode.min.js include needs the ?v={{ASSET_VER}} cache-buster (#374)"
    )


def test_waiting_view_uses_dormant_qr_and_roster_css() -> None:
    html = DASHBOARD.read_text("utf-8")
    waiting = _waiting_view_block(html)
    assert "dashboard-qr-section" in waiting, (
        "#waiting-view must render the join QR via the existing "
        ".dashboard-qr-section markup (#374)"
    )
    assert "dashboard-player-list" in waiting, (
        "#waiting-view must render the live roster via the existing "
        ".dashboard-player-list markup (#374)"
    )


def test_dashboard_join_url_matches_admin() -> None:
    """The lobby QR encodes the SAME join URL admin.js builds.

    admin.js uses ``window.location.origin + '/quizify/player'`` — the dashboard
    must derive it identically so the phone that scans the TV lands on the same
    join page the admin QR points at.
    """
    html = DASHBOARD.read_text("utf-8")
    assert "window.location.origin + '/quizify/player'" in html, (
        "dashboard lobby QR must encode origin + '/quizify/player', the same "
        "join URL admin.js uses (#374)"
    )
