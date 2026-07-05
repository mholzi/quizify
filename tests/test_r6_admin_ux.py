"""Guard tests for the round-6 admin UX/a11y fixes (#485–#487).

#485 — .toggle-switch-compact used a dark-theme white-alpha OFF track
(rgba(255,255,255,0.1)) that is invisible on the Soft Parlor cream cards
(lightning + TTS toggles). It now uses an ink-based alpha fill + border so
the unchecked switch reads on the light surface.

#486 — .toggle-compact input hid the native checkbox (opacity:0; width/height:0)
with no visible keyboard focus. A :focus-visible outline now renders on the
adjacent .toggle-switch-compact track when the input is keyboard-focused.

#487 — admin.js openConfirmModal managed initial/restore focus (#479) but did
not contain Tab, so Tab/Shift+Tab escaped behind the aria-modal backdrop of the
end/reset/kick dialogs. It now installs a keydown Tab-trap on open and removes
it on close.
"""

from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
_WWW = _REPO_ROOT / "custom_components" / "quizify" / "www"


# --- #485: toggle OFF track uses ink alpha, not white -------------------------

def test_toggle_track_ink_alpha_in_built_css() -> None:
    styles = (_WWW / "css" / "styles.css").read_text(encoding="utf-8")
    block = styles.split(".toggle-switch-compact {", 1)[1].split("}", 1)[0]
    assert "rgba(42, 40, 32" in block, (
        "toggle OFF track should use an ink-based alpha on the cream card (#485)"
    )
    assert "rgba(255, 255, 255" not in block, (
        "toggle OFF track still uses invisible dark-theme white alpha (#485)"
    )
    # Border gives the switch an edge on the light surface.
    assert "border" in block, "toggle OFF track should carry a border (#485)"


# --- #486: keyboard focus-visible outline on the toggle -----------------------

def test_toggle_focus_visible_rule_present() -> None:
    src = (_WWW / "css" / "src" / "02-shared.css").read_text(encoding="utf-8")
    assert ".toggle-compact input:focus-visible + .toggle-switch-compact" in src, (
        "no :focus-visible rule for the setup toggles (#486)"
    )
    styles = (_WWW / "css" / "styles.css").read_text(encoding="utf-8")
    assert ".toggle-compact input:focus-visible + .toggle-switch-compact" in styles, (
        "styles.css is stale — toggle focus-visible rule missing (#486)"
    )
    block = styles.split(
        ".toggle-compact input:focus-visible + .toggle-switch-compact", 1
    )[1].split("}", 1)[0]
    assert "outline" in block, "toggle focus-visible rule must draw an outline (#486)"


# --- #487: confirm-dialog Tab-trap installed ----------------------------------

def test_confirm_modal_tab_trap_present() -> None:
    js = (_WWW / "js" / "admin.js").read_text(encoding="utf-8")
    open_block = js.split("function openConfirmModal", 1)[1].split(
        "function closeConfirmModal", 1
    )[0]
    # A keydown handler that reacts to Tab is installed on open.
    assert "addEventListener('keydown'" in open_block, (
        "openConfirmModal must install a keydown handler for the Tab-trap (#487)"
    )
    assert "'Tab'" in open_block, (
        "openConfirmModal Tab-trap must key off the Tab key (#487)"
    )
    assert "shiftKey" in open_block, (
        "openConfirmModal must handle Shift+Tab backwards wrap (#487)"
    )
    # And the handler is removed again on close so it does not leak.
    close_block = js.split("function closeConfirmModal", 1)[1].split("}", 1)[0]
    assert "removeEventListener('keydown'" in close_block, (
        "closeConfirmModal must remove the Tab-trap handler (#487)"
    )
