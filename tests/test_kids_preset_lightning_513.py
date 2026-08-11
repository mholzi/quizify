"""The kids preset must disarm the auto Lightning Round (#513).

The "With kids" preset (#506) exists for one reason: an external host reported
that small children cannot read a question plus four answers in time, so the
bundle carries a 180 s timer. The auto Lightning Round (#285) is 5 questions at
15 s each and fires mid-game — with the toggle left armed it silently overrode
the exact setting the preset was created for.

The fix makes ``lightning`` part of the preset bundle, which means it now lives
in the same two places as rounds/difficulty/timer: ``data-lightning`` on the
card in ``admin.html`` and the ``_PRESETS`` array in ``admin.js``. The drift
guard for that pair is in ``test_kids_preset_506.py``; what follows pins the
behaviour that guard cannot see — that applying a preset actually writes the
checkbox, and that the active-card match takes Lightning into account.

Pure text parsing — no Home Assistant imports, so this runs everywhere.
"""

from __future__ import annotations

import re
from pathlib import Path

_WWW = Path(__file__).resolve().parent.parent / "custom_components" / "quizify" / "www"
_ADMIN_HTML = _WWW / "admin.html"
_ADMIN_JS = _WWW / "js" / "admin.js"


def _presets_from_js() -> dict[str, dict[str, str]]:
    js = _ADMIN_JS.read_text(encoding="utf-8")
    block = re.search(r"var _PRESETS = \[(.*?)\];", js, re.DOTALL)
    assert block is not None, "_PRESETS array not found in admin.js"
    presets: dict[str, dict[str, str]] = {}
    for line in re.findall(r"\{[^}]*\}", block.group(1)):
        fields = dict(re.findall(r"(\w+):\s*'?([\w.]+)'?", line))
        presets[fields["id"]] = fields
    return presets


def _cards_from_html() -> dict[str, dict[str, str]]:
    html = _ADMIN_HTML.read_text(encoding="utf-8")
    cards: dict[str, dict[str, str]] = {}
    for match in re.finditer(
        r'<button[^>]*class="preset-card[^"]*"[^>]*data-preset="(?P<id>[^"]+)"'
        r"(?P<attrs>[^>]*)>",
        html,
    ):
        attrs = dict(re.findall(r'data-(\w+)="([^"]+)"', match.group("attrs")))
        cards[match.group("id")] = attrs
    return cards


def _fn_body(name: str) -> str:
    """Source of a top-level ``function name(...) { … }`` in admin.js."""
    js = _ADMIN_JS.read_text(encoding="utf-8")
    start = js.index(f"function {name}(")
    depth = 0
    for i in range(js.index("{", start), len(js)):
        if js[i] == "{":
            depth += 1
        elif js[i] == "}":
            depth -= 1
            if depth == 0:
                return js[start : i + 1]
    raise AssertionError(f"unbalanced braces reading {name}()")


def test_kids_preset_has_lightning_off() -> None:
    """The whole point of #513."""
    assert _presets_from_js()["kinder"]["lightning"] == "false"
    assert _cards_from_html()["kinder"]["lightning"] == "0"


def test_every_other_preset_keeps_lightning_on() -> None:
    """#513 is scoped to the kids bundle — nothing else changes behaviour."""
    for preset_id, preset in _presets_from_js().items():
        if preset_id == "kinder":
            continue
        assert preset["lightning"] == "true", preset_id


def test_every_preset_declares_lightning() -> None:
    """A missing value would leave the toggle at whatever the last run set.

    ``_applyPreset`` skips the field when it is null, so an undeclared preset
    would silently inherit the previous bundle's Lightning state instead of
    applying its own.
    """
    cards = _cards_from_html()
    for preset_id, preset in _presets_from_js().items():
        assert "lightning" in preset, preset_id
        assert cards[preset_id].get("lightning") in {"0", "1"}, preset_id


def test_apply_preset_writes_the_checkbox_not_just_the_variable() -> None:
    """``_buildStartGamePayload`` reads the DOM first, ``selectedLightning`` second.

    Setting only the variable would ship the host's choice correctly right up
    until anything re-read the checkbox — and would show the wrong switch
    position the moment the host opened "Eigene".
    """
    body = _fn_body("_applyPreset")
    assert "lightning-enabled-toggle" in body
    assert re.search(r"\.checked\s*=\s*lightning", body)
    assert "selectedLightning = lightning" in body


def test_matching_preset_compares_lightning() -> None:
    """Otherwise a kids run with Lightning switched back on still reads "Mit Kindern".

    #433 moved the field-by-field comparison out of ``_matchingPreset`` into
    ``_sameBundle``, which both the built-in and the saved presets go through
    — so that is where the lightning check now has to be. The invariant is
    unchanged: lightning is part of the bundle, and flipping it by hand must
    stop the run from matching the preset.
    """
    body = _fn_body("_sameBundle")
    assert "p.lightning === selectedLightning" in body
    # …and the matcher must actually route through that helper.
    assert "_sameBundle(" in _fn_body("_matchingPreset")


def test_toggling_lightning_by_hand_refreshes_the_active_card() -> None:
    """The change handler has to re-run the match, or the highlight goes stale.

    ``updateSettingsSummary`` is what repaints both the hero line and the
    active preset card (it calls ``updateHeroSummary`` + ``markActivePreset``).
    """
    js = _ADMIN_JS.read_text(encoding="utf-8")
    handler = re.search(
        r"on\(lightningToggle, 'change', function \(\) \{(.*?)\}\);",
        js,
        re.DOTALL,
    )
    assert handler is not None, "lightning toggle change handler not found"
    assert "updateSettingsSummary()" in handler.group(1)
