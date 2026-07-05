"""Guard tests for the round-5 frontend UX fixes (#476–#480).

#476 — player-game.js emits `player-indicator--disconnected` on the submission
tracker but no CSS rule existed, so a dropped player looked identical to a
connected one. A dimmed + dashed rule now exists.

#477 — the submission chips used dark-theme white alphas on the cream light
theme (near-invisible) and white initials on the sage success avatar (~2.2:1).
Now ink-based alphas + ink initials.

#478 — #wager-slider carried a hardcoded aria-label with no data-i18n-aria-label
(unlike #estimate-slider) and #wager-value lacked for="wager-slider".

#479 — the end/reset confirm dialogs toggled .hidden with no focus management.
admin.js now moves focus to Cancel on open and restores it on close.

#480 — kick_player used window.confirm(); it now uses a themed btn-danger modal.
"""

import json
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
_WWW = _REPO_ROOT / "custom_components" / "quizify" / "www"
_I18N = _WWW / "i18n"
_LANGS = ("en", "de", "es")


def _load(lang: str) -> dict:
    return json.loads((_I18N / f"{lang}.json").read_text(encoding="utf-8"))


def _get(obj: dict, dotted: str):
    cur = obj
    for part in dotted.split("."):
        assert isinstance(cur, dict), f"{dotted}: {part} parent not a dict"
        assert part in cur, f"missing key: {dotted}"
        cur = cur[part]
    return cur


# --- #476: disconnected chip rule present -------------------------------------

def test_disconnected_chip_rule_present() -> None:
    css = (_WWW / "css" / "src" / "07-player.css").read_text(encoding="utf-8")
    assert ".player-indicator--disconnected" in css, (
        "no CSS rule for the disconnected submission chip (#476)"
    )
    # Distinct visual treatment: dimmed and/or dashed muted border.
    block = css.split(".player-indicator--disconnected", 1)[1].split("}", 1)[0]
    assert "opacity" in block, "disconnected chip should be dimmed (#476)"
    assert "dashed" in block, "disconnected chip should use a dashed border (#476)"
    # And it must survive into the generated stylesheet.
    styles = (_WWW / "css" / "styles.css").read_text(encoding="utf-8")
    assert ".player-indicator--disconnected" in styles, (
        "styles.css is stale — disconnected chip rule missing (#476)"
    )


# --- #477: chip ink-alpha + initials contrast ---------------------------------

def test_chip_uses_ink_alpha_not_white() -> None:
    css = (_WWW / "css" / "src" / "07-player.css").read_text(encoding="utf-8")
    block = css.split(".player-indicator {", 1)[1].split("}", 1)[0]
    assert "rgba(255, 255, 255" not in block, (
        "player-indicator still uses white alphas on the cream light theme (#477)"
    )
    assert "rgba(42, 40, 32" in block, (
        "player-indicator should use ink-based alphas (#477)"
    )


def test_initials_not_pure_white() -> None:
    css = (_WWW / "css" / "src" / "07-player.css").read_text(encoding="utf-8")
    block = css.split(".player-indicator .player-initials {", 1)[1].split("}", 1)[0]
    # Ignore comment prose; assert the color *declaration* is no longer white.
    decls = [ln.split("/*", 1)[0].strip() for ln in block.splitlines()]
    color_decls = [d for d in decls if d.startswith("color")]
    assert color_decls, "player-initials has no color declaration"
    assert not any("#fff" in d.lower() for d in color_decls), (
        "player-initials still #fff (~2.2:1 on the sage avatar) — should be ink (#477)"
    )
    assert "var(--color-text-primary)" in block, (
        "player-initials should use the ink token for >=3:1 contrast (#477)"
    )


def test_submitted_players_centered() -> None:
    css = (_WWW / "css" / "src" / "07-player.css").read_text(encoding="utf-8")
    block = css.split(".submitted-players {", 1)[1].split("}", 1)[0]
    assert "justify-content: center" in block, (
        "submitted-players should center the chips (#477)"
    )


# --- #478: wager slider aria + for --------------------------------------------

def test_wager_slider_aria_and_for() -> None:
    html = (_WWW / "player.html").read_text(encoding="utf-8")
    slider = html.split('id="wager-slider"', 1)[1].split(">", 1)[0]
    assert 'data-i18n-aria-label="wager.sliderAria"' in slider, (
        "#wager-slider missing data-i18n-aria-label (#478)"
    )
    output = html.split('id="wager-value"', 1)[1].split(">", 1)[0]
    assert 'for="wager-slider"' in output, (
        '#wager-value <output> missing for="wager-slider" (#478)'
    )


def test_wager_slider_aria_i18n_key() -> None:
    for lang in _LANGS:
        val = _get(_load(lang), "wager.sliderAria")
        assert isinstance(val, str) and val.strip(), (
            f"{lang}: wager.sliderAria empty/non-string (#478)"
        )


# --- #479: confirm-dialog focus management ------------------------------------

def test_confirm_dialog_focus_code_present() -> None:
    js = (_WWW / "js" / "admin.js").read_text(encoding="utf-8")
    assert "openConfirmModal" in js and "closeConfirmModal" in js, (
        "confirm-modal focus helpers missing (#479)"
    )
    # Focus moves to the Cancel button on open and is restored on close.
    assert "cancelBtn.focus()" in js, "open should focus the Cancel button (#479)"
    assert "trigger.focus()" in js, "close should restore focus to the trigger (#479)"
    # End/reset now route through the helpers.
    assert "openConfirmModal('end-game-modal'" in js
    assert "openConfirmModal('reset-game-modal'" in js


# --- #480: styled kick modal --------------------------------------------------

def test_kick_modal_markup_present() -> None:
    html = (_WWW / "admin.html").read_text(encoding="utf-8")
    assert 'id="kick-player-modal"' in html, "kick modal markup missing (#480)"
    assert 'id="kick-player-confirm-btn"' in html and "btn-danger" in html.split(
        'id="kick-player-confirm-btn"', 1
    )[1].split(">", 1)[0], "kick confirm must be a btn-danger (#480)"
    assert 'id="kick-player-cancel-btn"' in html


def test_kick_uses_modal_not_confirm() -> None:
    js = (_WWW / "js" / "admin.js").read_text(encoding="utf-8")
    assert "openKickModal(" in js, "kick should open the themed modal (#480)"
    # The kick action is unchanged.
    assert "send('kick_player'" in js


def test_kick_modal_i18n_keys() -> None:
    for lang in _LANGS:
        data = _load(lang)
        for key in ("admin.kickModalTitle", "admin.kickConfirmBtn"):
            val = _get(data, key)
            assert isinstance(val, str) and val.strip(), (
                f"{lang}: {key} empty/non-string (#480)"
            )
