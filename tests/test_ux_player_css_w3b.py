"""Guard the player/global UX batch (w3b): #419, #420, #422, #423, #424, #426, #430.

Text-level assertions over the committed front-end sources (HTML, JS modules,
CSS source modules and the built styles.css). They lock in each fix so a later
edit can't silently regress it.

* #419 — a visible "Invite players" button exists in the lobby and is wired to
  openInviteModal via #invite-players-btn.
* #420 — the lobby kick button is revealed on coarse pointers.
* #422 — the admin-bar scroll/reaction headroom includes the iOS safe-area inset.
* #423 — theme-tab / spotlight-play-btn / flag-question-btn / question-image-zoom
  all carry a ::before hit-area extension in the built stylesheet.
* #424 — a polite sr-only connection-status live region exists and is populated.
* #426 — a join error is written into #name-validation-msg (and cleared on input).
* #430 — the dead #confirm-modal markup and its CSS were removed.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
WWW = REPO / "custom_components" / "quizify" / "www"
SRC = WWW / "css" / "src"
JS = WWW / "js"


def _rule(css: str, selector: str) -> str:
    """Return the declaration block for the first rule matching ``selector``."""
    idx = css.index(selector)
    start = css.index("{", idx)
    end = css.index("}", start)
    return css[start + 1 : end]


# ---------------------------------------------------------------------------
# #419 — invite button exists + wired
# ---------------------------------------------------------------------------

def test_invite_button_present_in_lobby() -> None:
    html = (WWW / "player.html").read_text("utf-8")
    assert 'id="invite-players-btn"' in html, (
        "player lobby must expose a visible #invite-players-btn (#419)"
    )
    assert 'data-i18n="lobby.invitePlayers"' in html


def test_invite_button_wired_to_modal() -> None:
    js = (JS / "player-lobby.js").read_text("utf-8")
    # setupInviteModal looks up the button and binds openInviteModal.
    assert "getElementById('invite-players-btn')" in js
    assert "openInviteModal" in js


# ---------------------------------------------------------------------------
# #426 — name-validation-msg populated on join error, cleared on input
# ---------------------------------------------------------------------------

def test_join_error_populates_validation_msg() -> None:
    js = (JS / "player-core.js").read_text("utf-8")
    block = js[js.index("function handleError"):]
    block = block[: block.index("function setupJoinForm")]
    assert "name-validation-msg" in block, (
        "handleError must write the reason into #name-validation-msg on join errors (#426)"
    )


def test_validation_msg_cleared_on_input() -> None:
    js = (JS / "player-core.js").read_text("utf-8")
    setup = js[js.index("function setupJoinForm"):]
    setup = setup[: setup.index("function handleJoinClick")]
    assert "name-validation-msg" in setup, (
        "the name input handler must clear #name-validation-msg on edit (#426)"
    )


# ---------------------------------------------------------------------------
# #423 — sub-44px targets get a ::before hit-area in the built stylesheet
# ---------------------------------------------------------------------------

def test_sub_44_targets_have_hit_area_extension() -> None:
    css = (WWW / "css" / "styles.css").read_text("utf-8")
    for selector in (
        ".theme-tab::before",
        ".spotlight-play-btn::before",
        ".flag-question-btn::before",
        ".question-image-zoom::before",
    ):
        assert selector in css, f"missing hit-area extension {selector} (#423)"
        block = _rule(css, selector)
        assert "inset:" in block and "-" in block, (
            f"{selector} needs a negative inset to enlarge the tap target (#423)"
        )


def test_flag_button_resting_opacity_raised() -> None:
    css = (SRC / "00-tokens.css").read_text("utf-8")
    block = _rule(css, ".flag-question-btn {")
    m = re.search(r"opacity:\s*([0-9.]+)", block)
    assert m and float(m.group(1)) >= 0.55, (
        ".flag-question-btn resting opacity should be raised to ~0.6 (#423)"
    )


# ---------------------------------------------------------------------------
# #422 — safe-area calc on the admin-bar headroom
# ---------------------------------------------------------------------------

def test_admin_bar_headroom_has_safe_area_calc() -> None:
    css = (SRC / "07-player.css").read_text("utf-8")
    # #reveal-view / #game-view scroll headroom
    idx = css.index("body:has(.admin-control-bar:not(.hidden)) #game-view")
    block = css[css.index("{", idx) + 1 : css.index("}", idx)]
    assert "calc(96px + env(safe-area-inset-bottom" in block, (
        "reveal/game scroll headroom must add the safe-area inset (#422)"
    )
    # reaction bar margin
    reaction = _rule(css, "body.is-admin .reaction-bar {")
    assert "calc(80px + env(safe-area-inset-bottom" in reaction, (
        ".reaction-bar margin must add the safe-area inset (#422)"
    )


# ---------------------------------------------------------------------------
# #420 — kick button visible on touch (coarse pointer)
# ---------------------------------------------------------------------------

def test_kick_button_visible_on_touch() -> None:
    css = (SRC / "07-player.css").read_text("utf-8")
    assert "@media (hover: none)" in css
    # locate the hover:none block that sets a non-zero resting opacity on the kick.
    idx = css.index("@media (hover: none)")
    block = css[idx : idx + 400]
    assert "player-chip-kick" in block and "opacity: 0.45" in block, (
        "kick button must show at ~0.45 opacity on coarse pointers (#420)"
    )


def test_kick_button_has_hit_area() -> None:
    css = (SRC / "07-player.css").read_text("utf-8")
    block = _rule(css, ".lobby-e-row-card .player-chip-kick::before")
    assert "inset:" in block and "-" in block, (
        "kick button needs a ::before hit-area extension (#420)"
    )


# ---------------------------------------------------------------------------
# #424 — connection status sr-only live region
# ---------------------------------------------------------------------------

def test_conn_sr_live_region_present() -> None:
    html = (WWW / "player.html").read_text("utf-8")
    idx = html.index('id="conn-status-announce"')
    frag = html[idx - 120 : idx + 120]
    assert 'aria-live="polite"' in frag, (
        "connection status needs a polite sr-only aria-live region (#424)"
    )


def test_conn_indicator_updates_live_region() -> None:
    js = (JS / "player-utils.js").read_text("utf-8")
    block = js[js.index("function updateConnectionIndicator"):]
    block = block[: block.index("Reconnecting Overlay")]
    assert "conn-status-announce" in block, (
        "updateConnectionIndicator must populate the sr-only live region (#424)"
    )
    # not hue-only: a shape/glyph marker for non-connected states.
    assert "glyph" in block or "⊘" in block, (
        "disconnected state must pair with a shape/glyph, not hue alone (#424)"
    )


# ---------------------------------------------------------------------------
# #430 — dead confirm-modal removed
# ---------------------------------------------------------------------------

def test_confirm_modal_markup_removed() -> None:
    html = (WWW / "player.html").read_text("utf-8")
    assert 'id="confirm-modal"' not in html, (
        "dead #confirm-modal markup must be removed (#430)"
    )


def test_confirm_modal_css_removed() -> None:
    css = (SRC / "07-player.css").read_text("utf-8")
    assert ".confirm-modal-title" not in css and ".confirm-modal-message" not in css, (
        "dead confirm-modal CSS must be removed (#430)"
    )
