"""Guard tests for the UX i18n + accessibility fixes (#378, #380).

#378 — the wager panel and copy-feedback shipped hardcoded German strings
("Pkt.", static "Wagerunde"/"Einsetzen") and an English literal "Copied!"
that never localized. These now flow through i18n keys.

#380 — the visible #timer changed every second while carrying
aria-live="polite", so VoiceOver read each tick. It is now aria-live="off"
with a separate polite region announcing only at 10s / 5s left.
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


def test_all_i18n_json_parses() -> None:
    for lang in _LANGS:
        _load(lang)  # raises on invalid JSON


def test_new_i18n_keys_exist_in_all_langs() -> None:
    required = ("wager.valueFmt", "game.timerTenLeft", "game.timerFiveLeft")
    for lang in _LANGS:
        data = _load(lang)
        for key in required:
            val = _get(data, key)
            assert isinstance(val, str) and val.strip(), (
                f"{lang}: {key} empty/non-string"
            )


def test_wager_value_fmt_has_placeholders() -> None:
    for lang in _LANGS:
        val = _get(_load(lang), "wager.valueFmt")
        assert "{pct}" in val and "{pts}" in val, (
            f"{lang}: wager.valueFmt lost a placeholder: {val!r}"
        )


def test_timer_is_aria_live_off() -> None:
    html = (_WWW / "player.html").read_text(encoding="utf-8")
    # The main #timer must no longer be polite (would announce every tick).
    assert 'id="timer" class="timer" role="timer" aria-live="off"' in html, (
        "#timer must be aria-live=off (#380)"
    )
    assert 'id="timer-sr-announce"' in html, (
        "expected a separate polite SR announce region (#380)"
    )
    assert "aria-live=\"polite\"" in html.split('id="timer-sr-announce"')[1][:120]


def test_static_wager_and_copy_strings_are_localized() -> None:
    html = (_WWW / "player.html").read_text(encoding="utf-8")
    assert 'id="wager-panel-title" data-i18n="wager.title"' in html
    assert 'id="wager-submit-btn"' in html
    assert 'data-i18n="wager.submit"' in html
    assert 'id="invite-copy-feedback"' in html
    assert 'data-i18n="lobby.copied"' in html


def test_no_bare_literals_in_wager_copy_paths() -> None:
    src = (_WWW / "js" / "player-game.js").read_text(encoding="utf-8")
    # The old hardcoded German unit must be gone from the wager formatter.
    assert "' Pkt.)'" not in src and "Pkt.)" not in src, (
        "bare 'Pkt.' literal still present in player-game.js wager path (#378)"
    )
    assert "t('wager.valueFmt'" in src, "wager value must use i18n (#378)"
    # The regenerated bundle must reflect the source (drift guard covers exact
    # equality; here we just confirm the localized call made it in).
    bundle = (_WWW / "js" / "player.bundle.js").read_text(encoding="utf-8")
    assert "t('wager.valueFmt'" in bundle
    assert "Pkt.)" not in bundle
