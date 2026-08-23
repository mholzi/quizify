"""Four high-traffic strings bypassed the i18n bundles (issue #625).

The bundles are otherwise in exact parity — 616 leaf keys per language, the
work of v1.5.0 and v1.9.0 — but four spots built their text by hand:

* the pack-count summary and the questions-only line on the host's setup screen,
* a flag chosen by ``selectedLanguage === 'en' ? 🇬🇧 : 🇩🇪``, so a Spanish game
  flew a German flag,
* a literal ``(you)`` on every phone's leaderboard, for the whole game,
* a hardcoded ``Starting...`` on the player start button.

Three of the four had a key waiting for them already (``lobby.you``,
``admin.starting``); only the pack line needed new ones.

Two traps this fix walked into, both worth naming because a test that only
checked "no hardcoded German remains" would have missed both:

1. ``player-game.js`` and ``player-lobby.js`` have **no** module-wide ``_t``.
   They declare ``var t = (window.QuizifyI18n && window.QuizifyI18n.t) || …``
   per function. A bare ``_t(…)`` there is a ReferenceError that takes the whole
   leaderboard render down — worse than the untranslated string it replaces.
2. ``player.html`` loads ``player.bundle.js``, not the individual modules, so
   editing the sources alone ships nothing. The bundle has to be rebuilt.

Both are pinned below.
"""

from __future__ import annotations

import json
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
_WWW = _REPO_ROOT / "custom_components" / "quizify" / "www"
_I18N = _WWW / "i18n"
_JS = _WWW / "js"

LANGUAGES = ("de", "en", "es")
NEW_KEYS = ("packsAndQuestions", "questionsOnly")


def _leaves(d: dict, prefix: str = "") -> dict[str, str]:
    out: dict[str, str] = {}
    for key, value in d.items():
        name = f"{prefix}.{key}" if prefix else key
        if isinstance(value, dict):
            out.update(_leaves(value, name))
        else:
            out[name] = value
    return out


def _bundle(code: str) -> dict:
    return json.loads((_I18N / f"{code}.json").read_text("utf-8"))


def test_the_three_bundles_stay_in_key_parity() -> None:
    """The property the four spots were quietly breaking.

    Compares key sets rather than counts: two bundles can hold the same number
    of keys and still disagree about which ones.
    """
    keysets = {code: set(_leaves(_bundle(code))) for code in LANGUAGES}
    reference = keysets["de"]
    for code in LANGUAGES:
        assert keysets[code] == reference, (
            f"{code} differs: "
            f"missing={sorted(reference - keysets[code])} "
            f"extra={sorted(keysets[code] - reference)}"
        )


def test_the_new_keys_exist_everywhere_and_interpolate() -> None:
    """A key present in two of three bundles is how this class of bug starts."""
    for code in LANGUAGES:
        admin = _bundle(code)["admin"]
        for key in NEW_KEYS:
            assert key in admin, f"{code}.admin.{key} missing"
        assert "{packs}" in admin["packsAndQuestions"]
        assert "{questions}" in admin["packsAndQuestions"]
        assert "{questions}" in admin["questionsOnly"]


def test_the_setup_screen_no_longer_builds_german_by_hand() -> None:
    source = (_JS / "admin.js").read_text("utf-8")

    assert "' Packs · '" not in source
    assert "' Fragen'" not in source
    assert "admin.packsAndQuestions" in source
    assert "admin.questionsOnly" in source


def test_the_flag_is_a_map_with_spanish_in_it() -> None:
    """The ternary was "English, or else German" — there is no third branch to
    add, which is why it has to stop being a ternary."""
    source = (_JS / "admin.js").read_text("utf-8")

    assert "selectedLanguage === 'en' ? '🇬🇧' : '🇩🇪'" not in source
    assert "es: '🇪🇸'" in source


def test_the_player_files_use_their_own_translation_helper() -> None:
    """Trap 1: neither file defines a module-wide ``_t``.

    Calling one would raise instead of rendering, so this asserts the local
    ``t`` shape is present in both edited spots and that no bare ``_t(`` crept
    in with a copy-paste from admin.js.
    """
    for name in ("player-game.js", "player-lobby.js"):
        source = (_JS / name).read_text("utf-8")
        assert "_t(" not in source, f"{name} calls _t, which is not defined there"
        assert "window.QuizifyI18n && window.QuizifyI18n.t" in source


def test_the_translated_strings_reached_the_shipped_bundle() -> None:
    """Trap 2: player.html loads the bundle, not the modules.

    Without a rebuild every change here is a no-op on real phones. The drift
    job in CI checks the same thing from the other side; this fails locally,
    before the push.
    """
    bundle = (_JS / "player.bundle.js").read_text("utf-8")

    assert "'lobby.you'" in bundle
    assert "'admin.starting'" in bundle
    assert '"you-badge">(you)' not in bundle
    # Asserts the *rendered* markup, not the bare words: the fix's own comment
    # quotes "Starting..." to explain what it replaced, and a substring check
    # would trip over that and call a correct bundle broken.
    assert "<span>Starting...</span>" not in bundle
