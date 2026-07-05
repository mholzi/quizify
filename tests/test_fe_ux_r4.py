"""Guard tests for the round-4 frontend UX batch.

Text-level assertions over the *built* assets (styles.css, player.bundle.js)
plus the HTML/i18n source so a later edit can't silently regress any of:

  #458 submission chips        #463 theme filter group + aria-pressed
  #459 finale card flex        #464 hero CTA relabelled (Open lobby)
  #460 timer reset at reveal   #465 wager slider styled
  #462 localized close aria     #466 destructive confirm buttons btn-danger
  #467 image alt i18n wired
"""

from __future__ import annotations

import json
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
WWW = REPO / "custom_components" / "quizify" / "www"

STYLES = (WWW / "css" / "styles.css").read_text("utf-8")
PLAYER_CSS = (WWW / "css" / "src" / "07-player.css").read_text("utf-8")
BUNDLE = (WWW / "js" / "player.bundle.js").read_text("utf-8")
ADMIN_HTML = (WWW / "admin.html").read_text("utf-8")
PLAYER_HTML = (WWW / "player.html").read_text("utf-8")
DASH_HTML = (WWW / "dashboard.html").read_text("utf-8")
ADMIN_JS = (WWW / "js" / "admin.js").read_text("utf-8")
LIGHTNING_JS = (WWW / "js" / "player-lightning.js").read_text("utf-8")
I18N = {
    lang: json.loads((WWW / "i18n" / f"{lang}.json").read_text("utf-8"))
    for lang in ("en", "de", "es")
}


# -- #458 submission chips -------------------------------------------------
def test_458_submission_chip_styled() -> None:
    # The avatar circle must actually be styled (>=24px), and the chip row a flex.
    assert ".player-indicator .player-avatar" in STYLES
    assert "width: 24px" in STYLES and "height: 24px" in STYLES
    # Container lays chips out in a wrapping flex row.
    assert ".submitted-players" in STYLES
    tracker = STYLES[STYLES.index(".submitted-players {"):]
    block = tracker[: tracker.index("}")]
    assert "flex" in block and "wrap" in block
    # The old bare-dot rule (8x8) must be gone.
    assert "width: 8px" not in PLAYER_CSS or ".player-name" in PLAYER_CSS


# -- #459 finale card flex -------------------------------------------------
def test_459_finale_card_is_flex_column() -> None:
    # Locate the base .finale-leaderboard-card rule (the one with width:100%).
    search = 0
    block = ""
    while True:
        idx = DASH_HTML.index(".finale-leaderboard-card {", search)
        blk = DASH_HTML[idx: DASH_HTML.index("}", idx)]
        if "width: 100%" in blk:
            block = blk
            break
        search = idx + 1
    assert "display: flex" in block
    assert "flex-direction: column" in block
    assert "min-height: 0" in block


# -- #460 timer reset at round_summary -------------------------------------
def test_460_round_summary_resets_timer() -> None:
    idx = DASH_HTML.index("function handleRoundSummary(")
    body = DASH_HTML[idx: idx + 800]
    assert "timerFill.style.width = '0%'" in body
    assert "timerFill.className = 'dashboard-timer-fill'" in body


# -- #462 localized close aria ---------------------------------------------
def test_462_close_buttons_localized() -> None:
    # The icon-only iOS hint close gets a localized aria-label key.
    assert 'id="pwa-ios-hint-close"' in ADMIN_HTML
    hint = ADMIN_HTML[ADMIN_HTML.index('id="pwa-ios-hint-close"') - 120:
                      ADMIN_HTML.index('id="pwa-ios-hint-close"') + 120]
    assert 'data-i18n-aria-label="common.close"' in hint
    # The text-labelled modal close buttons no longer carry a hardcoded English aria.
    for anchor in ('id="qr-modal-close"', 'id="invite-modal-close"'):
        seg = PLAYER_HTML[PLAYER_HTML.index(anchor): PLAYER_HTML.index(anchor) + 160]
        assert "aria-label=" not in seg, f"{anchor} should drop the hardcoded aria-label"
        assert 'data-i18n="common.close"' in seg


# -- #463 theme filter group + aria-pressed --------------------------------
def test_463_theme_tabs_group_semantics() -> None:
    tabs = ADMIN_HTML[ADMIN_HTML.index('id="theme-tabs"'):]
    header = tabs[: tabs.index(">") + 1]
    assert 'role="group"' in header
    assert 'role="tablist"' not in header
    # Every theme-tab button exposes aria-pressed in the markup.
    block = tabs[: tabs.index("</div>")]
    assert block.count("aria-pressed=") >= 10
    # JS keeps aria-pressed in sync with .active.
    assert "setAttribute('aria-pressed'" in ADMIN_JS


# -- #464 hero CTA relabelled ----------------------------------------------
def test_464_hero_cta_relabelled() -> None:
    seg = ADMIN_HTML[ADMIN_HTML.index('id="start-game-btn"'):
                     ADMIN_HTML.index('id="start-game-btn"') + 260]
    assert 'data-i18n="admin.openLobby"' in seg
    assert 'data-i18n="admin.startGame"' not in seg
    for lang in ("en", "de", "es"):
        assert "openLobby" in I18N[lang]["admin"]


# -- #465 wager slider styled ----------------------------------------------
def test_465_wager_slider_styled() -> None:
    assert "#wager-slider::-webkit-slider-thumb" in STYLES
    assert "#wager-slider::-moz-range-thumb" in STYLES
    assert "#wager-slider:focus-visible" in STYLES
    # Shares the 34px thumb treatment.
    thumb = STYLES[STYLES.index("#wager-slider::-webkit-slider-thumb"):]
    assert "34px" in thumb[: thumb.index("}")]


# -- #466 destructive confirm buttons --------------------------------------
def test_466_confirm_buttons_danger() -> None:
    for anchor in ('id="end-game-confirm-btn"', 'id="reset-game-confirm-btn"'):
        seg = ADMIN_HTML[ADMIN_HTML.index(anchor) - 90: ADMIN_HTML.index(anchor) + 60]
        assert "btn-danger" in seg
        assert "btn-primary" not in seg


# -- #467 image alt i18n wired ---------------------------------------------
def test_467_image_alt_i18n() -> None:
    for lang in ("en", "de", "es"):
        assert I18N[lang]["game"].get("questionImageAlt")
    # Player (bundled), lightning, and dashboard renderers all set the alt.
    assert "game.questionImageAlt" in BUNDLE
    assert "game.questionImageAlt" in LIGHTNING_JS
    assert "game.questionImageAlt" in DASH_HTML
