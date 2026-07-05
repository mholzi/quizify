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


def test_reconnect_pill_toggled_in_ws_handlers() -> None:
    html = _text()
    # onclose shows the pill, onopen hides it.
    assert "els.reconnectPill.hidden = false" in html
    assert "els.reconnectPill.hidden = true" in html
    onclose = html[html.index("ws.onclose") : html.index("ws.onerror")]
    assert "els.reconnectPill.hidden = false" in onclose


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
