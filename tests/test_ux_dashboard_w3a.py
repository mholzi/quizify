"""Guard the W3A batch of TV-dashboard UX fixes (#421,#425,#427,#428,#429).

The dashboard is a self-contained ``www/dashboard.html`` with an inline
``<style>`` block and an inline script (it is NOT bundled from src). These
text-level guards lock in the five fixes so a later edit can't silently
regress them:

* #421 — a "Reconnecting…" pill wired into ``ws.onclose`` / ``ws.onopen``.
* #425 — ``handleLightningTick`` uses the round's ``seconds_per_question``
  (via ``lightningSeconds``) instead of a hardcoded ``15``.
* #427 — the shared countdown ``.dashboard-timer`` is thicker than the old 4px.
* #428 — the QR fallback URL is dark ink on the cream body, not ``#fff``.
* #429 — the capped in-game leaderboard appends a "+N more" overflow hint.
"""

from __future__ import annotations

import re
from pathlib import Path

from tests.conftest import without_comments

REPO = Path(__file__).resolve().parent.parent
DASHBOARD = REPO / "custom_components" / "quizify" / "www" / "dashboard.html"


def _text() -> str:
    return DASHBOARD.read_text(encoding="utf-8")


def _rule(css: str, selector: str) -> str:
    """Return the declaration block for the first rule matching ``selector``."""
    idx = css.index(selector)
    start = css.index("{", idx)
    end = css.index("}", start)
    return css[start + 1 : end]


# ---- #421: reconnect pill ----
def test_reconnect_pill_element_present() -> None:
    html = _text()
    assert 'id="reconnect-pill"' in html
    assert ".dashboard-reconnect-pill" in html


def _onclose_handler(html: str) -> str:
    """The body of the close handler, and nothing else (#811).

    Anchored on the *declaration*, not the identifier, and on comment-free
    source. ``html.index("ws.onclose")`` used to land on the CSS comment at
    line 250 ("When ws.onclose fires mid-question…") — a region covering most
    of the page, including the open handler.

    Since #787 the socket is opened through ``QuizifyClientCore``, so the
    handler is an ``onClose:`` entry in an options object rather than a
    ``ws.onclose =`` assignment. The slice is taken by brace balance, which is
    tighter than the old end-anchor was.
    """
    start = html.index("onClose: function")
    depth = 0
    for i in range(html.index("{", start), len(html)):
        if html[i] == "{":
            depth += 1
        elif html[i] == "}":
            depth -= 1
            if depth == 0:
                return html[start : i + 1]
    raise AssertionError("unbalanced onClose handler")


def test_reconnect_pill_toggled_in_ws_handlers() -> None:
    html = without_comments(_text())
    # onclose shows the pill, onopen hides it.
    assert "els.reconnectPill.hidden = false" in html
    assert "els.reconnectPill.hidden = true" in html
    assert "els.reconnectPill.hidden = false" in _onclose_handler(html)


def test_the_onclose_region_is_the_handler_and_not_half_the_page() -> None:
    """The slice has to be small enough for the assertion above to mean anything.

    The open handler sits *above* the close handler in the file, so the old
    comment-anchored region swallowed it whole: both branches of the pill were
    inside the "onclose" slice, and the test could not tell them apart.
    """
    region = _onclose_handler(without_comments(_text()))

    assert region.count("\n") < 30, (
        f"the onclose slice is {region.count(chr(10))} lines — it is supposed "
        "to be one handler (#811)"
    )
    assert "onOpen" not in region
    assert "els.reconnectPill.hidden = true" not in region, (
        "the hide branch belongs to ws.onopen; if it is inside this region the "
        "anchor drifted again"
    )


def test_the_old_anchor_could_not_see_a_misplaced_pill() -> None:
    """Proof that the old slicing was blind, not merely imprecise (#811).

    Move the show-the-pill line out of ``ws.onclose`` and into ``ws.onopen``
    — the exact regression the test exists to catch. The old anchor keeps
    passing on that mutated page; the assignment anchor fails, which is what a
    guard is for.
    """
    html = _text()
    show = "if (els.reconnectPill) els.reconnectPill.hidden = false;"
    assert html.count(show) == 1, "expected exactly one show-the-pill line"

    # Delete it from the close handler, re-insert it into the open one.
    moved = html.replace(show, "/* moved away */", 1)
    open_marker = "onOpen: function () {"
    assert open_marker in moved
    moved = moved.replace(open_marker, open_marker + "\n                    " + show, 1)

    # The naive anchor: the first mention of the identifier anywhere in the
    # file, prose included. That is the CSS comment near the top, and the
    # region runs past both handlers.
    old_region = moved[
        moved.index("ws.onclose") : moved.index("function handleMessage(msg)")
    ]
    assert "els.reconnectPill.hidden = false" in old_region, (
        "the comment anchor is supposed to stay green on the broken page — "
        "that is the bug being fixed"
    )

    new_region = _onclose_handler(without_comments(moved))
    assert "els.reconnectPill.hidden = false" not in new_region, (
        "the assignment anchor must notice that the pill is no longer shown "
        "from ws.onclose"
    )


# ---- #425: lightning uses real seconds_per_question ----
def test_lightning_tick_no_hardcoded_total() -> None:
    html = _text()
    tick = html[html.index("function handleLightningTick") :]
    tick = tick[: tick.index("}")]
    assert "var total = 15" not in tick
    assert "lightningSeconds" in tick


def test_lightning_seconds_state_seeded() -> None:
    html = _text()
    assert "var lightningSeconds" in html
    # Seeded from the splash payload and the game_state LIGHTNING snapshot.
    assert "lightningSeconds = s" in html
    assert re.search(
        r"lightningSeconds = Math\.round\(msg\.lightning\.seconds_per_question\)", html
    )


# ---- #427: thicker timer bar ----
def test_timer_bar_thickened() -> None:
    html = _text()
    block = _rule(html, ".dashboard-timer {")
    m = re.search(r"height:\s*(\d+)px", block)
    assert m, "timer bar must declare a px height"
    assert int(m.group(1)) >= 10, "timer bar must be >=10px (was 4px, illegible)"


# ---- #428: QR fallback color ----
def test_qr_fallback_not_white() -> None:
    html = _text()
    fn = html[html.index("function renderLobbyQr") :]
    fn = fn[: fn.index("function renderLobbyPlayers")]
    assert "color:#fff" not in fn
    assert "color: #fff" not in fn
    assert "var(--dash-text-white)" in fn


# ---- #429: leaderboard overflow hint ----
def test_leaderboard_overflow_hint() -> None:
    html = _text()
    fn = html[html.index("function renderLeaderboard") :]
    fn = fn[: fn.index("function renderPodium")]
    assert "leaderboard-more" in fn
    assert "more" in fn
    assert "players.length" in fn
