"""The per-player UI-language picker (#492) and the traps around it.

The feature itself is small — flag chips on the join screen that call
``QuizifyI18n.setLanguage`` and remember the choice. What makes it worth
testing is everything that quietly undoes it:

1. **The server used to stamp over the choice.** ``game_state`` carries the
   host's game language, and ``player-core.js`` applied it unconditionally so
   "a German game shows German labels even if the player's browser is
   English". That default is right for a player who never chose, and fatal for
   one who did — every incoming ``game_state`` would revert the picker within
   milliseconds and the feature would look broken rather than absent. The
   override now yields to a stored choice, and that is pinned here.

2. **Two different questions about "languages".** ``{{LANGUAGE_CHIPS}}`` lists
   the languages the host has *packs* for; ``{{UI_LANGUAGE_CHIPS}}`` lists the
   languages the *interface* is translated into. On a German-only install
   those differ, and the difference is exactly the user this feature serves —
   the Spanish-speaking guest at a German host's party. Sourcing the player
   picker from pack languages would silently offer them nothing.

3. **Two lists of supported languages.** Python renders the chips from the
   shipped ``www/i18n/*.json`` bundles; ``i18n.js`` gates ``setLanguage``
   against its own ``SUPPORTED_LANGUAGES`` array. If those drift, either a
   chip appears that the client refuses to load (tap does nothing) or a
   supported language has no chip. They are compared to each other below.

Pure text/JSON parsing — no Home Assistant imports, so this runs everywhere.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
_WWW = _REPO / "custom_components" / "quizify" / "www"
_PLAYER_HTML = _WWW / "player.html"
_CORE_JS = _WWW / "js" / "player-core.js"
_I18N_JS = _WWW / "js" / "i18n.js"
_BUNDLE = _WWW / "js" / "player.bundle.js"
_I18N_DIR = _WWW / "i18n"
_LOBBY_CSS = _WWW / "css" / "src" / "04-lobby.css"
_STYLES = _WWW / "css" / "styles.css"
_VIEWS = _REPO / "custom_components" / "quizify" / "server" / "views.py"


# --------------------------------------------------------------------------
# 1. The override that would have made the picker look broken
# --------------------------------------------------------------------------


def test_server_language_yields_to_a_stored_choice() -> None:
    """A chosen language must survive the next game_state broadcast."""
    core = _CORE_JS.read_text("utf-8")

    match = re.search(
        r"function _syncServerLanguage\(msg\) \{(.*?)\n    \}", core, re.S
    )
    assert match, "_syncServerLanguage is gone — the override is unguarded again"
    body = match.group(1)

    assert "_storedPlayerLang()" in body, (
        "the server-language sync no longer checks for a stored player choice; "
        "every game_state will stamp over the picker"
    )
    # The guard has to come before the setLanguage call, not after it.
    guard = body.index("_storedPlayerLang()")
    apply_at = body.index("setLanguage(msg.language)")
    assert guard < apply_at, "stored-choice guard runs after the language is applied"


def test_game_state_handler_routes_through_the_guard() -> None:
    """No second, unguarded setLanguage path on the game_state handler."""
    core = _CORE_JS.read_text("utf-8")
    assert "_syncServerLanguage(msg)" in core

    # setLanguage may appear in: the guarded sync, and the chip click handler.
    calls = re.findall(r"(?:QuizifyI18n|window\.QuizifyI18n)\.setLanguage\(", core)
    assert len(calls) == 2, (
        f"expected exactly 2 setLanguage call sites (guarded sync + chip tap), "
        f"found {len(calls)} — a new unguarded one would revert the picker"
    )


# --------------------------------------------------------------------------
# 2. Player picker is sourced from UI bundles, not pack languages
# --------------------------------------------------------------------------


def test_player_uses_its_own_token_not_the_admin_one() -> None:
    html = _PLAYER_HTML.read_text("utf-8")
    assert "{{UI_LANGUAGE_CHIPS}}" in html
    assert "{{LANGUAGE_CHIPS}}" not in html, (
        "player.html pulls the admin's pack-language chips — a German-only "
        "install would then offer a Spanish guest no Spanish"
    )


def test_ui_chips_are_rendered_from_the_shipped_bundles() -> None:
    views = _VIEWS.read_text("utf-8")
    assert "_UI_LANGUAGE_CHIPS_TOKEN" in views
    assert "_available_ui_languages" in views

    match = re.search(
        r"def _available_ui_languages\(\).*?\n\ndef ", views, re.S
    )
    assert match, "_available_ui_languages not found"
    assert 'glob("*.json")' in match.group(0), (
        "the UI language list is no longer read from www/i18n/ — a new bundle "
        "would need a Python edit to surface"
    )


def test_ui_chip_renderer_marks_nothing_active() -> None:
    """The stored choice is client-side; a server-picked active chip flickers."""
    views = _VIEWS.read_text("utf-8")
    match = re.search(
        r"def _render_ui_language_chips\(.*?\n    return \"\"\.join\(buttons\)", views, re.S
    )
    assert match, "_render_ui_language_chips not found"
    assert 'class="{cls}"' not in match.group(0), (
        "player chips render an active class server-side; it would paint the "
        "wrong flag for one frame on every load"
    )


# --------------------------------------------------------------------------
# 3. Python's language list vs the client's gate
# --------------------------------------------------------------------------


def test_bundles_on_disk_match_the_clients_supported_languages() -> None:
    """A chip the client refuses to load is a dead tap; a missing chip hides a language."""
    on_disk = {p.stem for p in _I18N_DIR.glob("*.json")}

    match = re.search(r"var SUPPORTED_LANGUAGES = \[([^\]]+)\]", _I18N_JS.read_text("utf-8"))
    assert match, "SUPPORTED_LANGUAGES vanished from i18n.js"
    supported = set(re.findall(r"'([a-z]{2})'", match.group(1)))

    assert on_disk == supported, (
        f"i18n bundles on disk {sorted(on_disk)} disagree with i18n.js "
        f"SUPPORTED_LANGUAGES {sorted(supported)} — a chip would either do "
        "nothing when tapped, or be missing for a language the client supports"
    )


def test_every_bundle_has_the_picker_label() -> None:
    for path in sorted(_I18N_DIR.glob("*.json")):
        data = json.loads(path.read_text("utf-8"))
        label = data.get("join", {}).get("languageAria")
        assert label, f"{path.name} is missing join.languageAria"
        assert label.strip()


# --------------------------------------------------------------------------
# 4. Wiring
# --------------------------------------------------------------------------


def test_picker_is_wired_and_stores_the_choice() -> None:
    core = _CORE_JS.read_text("utf-8")
    assert "setupLanguagePicker" in core
    assert re.search(r"function init\(\)[\s\S]{0,800}setupLanguagePicker\(\);", core), (
        "setupLanguagePicker is defined but never called from init()"
    )
    assert "PLAYER_LANG_KEY = 'quizify-player-lang'" in core
    assert "localStorage.setItem(PLAYER_LANG_KEY" in core


def test_stored_choice_wins_at_startup() -> None:
    """Without this the picker only holds until the next reload."""
    core = _CORE_JS.read_text("utf-8")
    assert re.search(r"QuizifyI18n\.init\(_storedPlayerLang\(\)", core), (
        "i18n init ignores the stored choice; the picker would not survive a reload"
    )


def test_unsubstituted_token_hides_the_row() -> None:
    """Served by something that doesn't know the token → no raw braces on screen."""
    core = _CORE_JS.read_text("utf-8")
    match = re.search(r"function setupLanguagePicker\(\) \{(.*?)\n    \}", core, re.S)
    assert match
    assert "'{{'" in match.group(1) or '"{{"' in match.group(1), (
        "no guard against an unsubstituted {{UI_LANGUAGE_CHIPS}} token"
    )


def test_built_assets_carry_the_feature() -> None:
    """styles.css and the bundle are committed; a missed rebuild ships a dead row."""
    assert ".player-lang-chips" in _LOBBY_CSS.read_text("utf-8")
    assert ".player-lang-chips" in _STYLES.read_text("utf-8"), "run scripts/build_css.py"
    assert "quizify-player-lang" in _BUNDLE.read_text("utf-8"), (
        "run scripts/build_bundle.py"
    )
