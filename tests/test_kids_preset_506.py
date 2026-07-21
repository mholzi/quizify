"""The kids preset card from #506, and the invariants it depends on.

#507 widened the timer picker to 180 s, but the picker lives in the "Eigene"
flow — a host who taps a preset card never sees it. So #506 also needs a preset
that carries the long timer. This adds one ("Mit Kindern": 5 rounds, easy,
180 s), placed before Marathon so the cards stay ordered by session length.

The values behind a preset live in *two* places: as ``data-rounds`` /
``data-difficulty`` / ``data-timer`` on the button in ``admin.html`` (read by
``_applyPreset``) and in the ``_PRESETS`` array in ``admin.js`` (read by
``_matchingPreset`` to decide which card to mark active). If the two ever drift
apart, tapping a card would apply one bundle while the highlight tracks
another — a bug with no error anywhere. These tests pin them together.

They also pin the quieter dependency: a preset's timer must be one of the
``#timer-chips`` values, because ``_applyPreset`` hands it to ``_activateChip``,
which just walks the chips and highlights the one whose ``data-value`` matches.
A preset timer with no matching chip highlights nothing and leaves the host
looking at an unselected picker.

Pure text parsing — no Home Assistant imports, so this runs everywhere.
"""

from __future__ import annotations

import re
from pathlib import Path

_WWW = Path(__file__).resolve().parent.parent / "custom_components" / "quizify" / "www"
_ADMIN_HTML = _WWW / "admin.html"
_ADMIN_JS = _WWW / "js" / "admin.js"
_I18N = _WWW / "i18n"

# Mirrors the backend clamp in websocket.py::_handle_start_game.
_MIN_TIMER = 5
_MAX_TIMER = 300


def _cards_from_html() -> dict[str, dict[str, str]]:
    """Every preset card in admin.html, keyed by its data-preset id."""
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


def _presets_from_js() -> dict[str, dict[str, str]]:
    """The _PRESETS array in admin.js, keyed by id."""
    js = _ADMIN_JS.read_text(encoding="utf-8")
    block = re.search(r"var _PRESETS = \[(.*?)\];", js, re.DOTALL)
    assert block is not None, "_PRESETS array not found in admin.js"
    presets: dict[str, dict[str, str]] = {}
    for line in re.findall(r"\{[^}]*\}", block.group(1)):
        fields = dict(re.findall(r"(\w+):\s*'?([\w.]+)'?", line))
        presets[fields["id"]] = fields
    return presets


def _timer_chip_values() -> list[int]:
    html = _ADMIN_HTML.read_text(encoding="utf-8")
    group = re.search(r'id="timer-chips".*?</div>', html, re.DOTALL)
    assert group is not None, "#timer-chips group not found in admin.html"
    return [int(v) for v in re.findall(r'data-value="(\d+)"', group.group(0))]


def test_kids_preset_card_exists_with_the_long_timer() -> None:
    """#506: the long timer must be reachable from a preset card."""
    card = _cards_from_html()["kinder"]
    assert card["rounds"] == "5"
    assert card["difficulty"] == "easy"
    assert card["timer"] == "180"


def test_kids_preset_sits_before_marathon() -> None:
    """Cards stay ordered by session length (~3 / ~8 / ~15 / ~20 min)."""
    ids = list(_cards_from_html())
    assert ids.index("kinder") < ids.index("marathon")


def test_html_cards_and_js_presets_agree() -> None:
    """The two places a preset's values live must not drift apart.

    admin.html drives what tapping a card applies; _PRESETS drives which card
    is shown as active. A mismatch applies one bundle and highlights another,
    silently.
    """
    cards = _cards_from_html()
    presets = _presets_from_js()

    # "eigene" is a mode switch, not a value bundle — it has no data-* values
    # and no _PRESETS entry.
    assert set(presets) == set(cards) - {"eigene"}

    for preset_id, preset in presets.items():
        card = cards[preset_id]
        assert card["rounds"] == preset["rounds"], preset_id
        assert card["difficulty"] == preset["difficulty"], preset_id
        assert card["timer"] == preset["timer"], preset_id


def test_every_preset_timer_has_a_matching_chip() -> None:
    """_applyPreset highlights the chip whose data-value equals the timer.

    A preset timer with no matching chip leaves the picker with nothing
    selected — no error, just a setup screen that looks broken.
    """
    chips = _timer_chip_values()
    for preset_id, preset in _presets_from_js().items():
        assert int(preset["timer"]) in chips, (
            f"preset {preset_id} uses timer {preset['timer']}s, which is not "
            f"one of the timer chips {chips}"
        )


def test_every_preset_timer_is_accepted_by_the_backend() -> None:
    for preset_id, preset in _presets_from_js().items():
        assert _MIN_TIMER <= int(preset["timer"]) <= _MAX_TIMER, preset_id


def test_kids_preset_is_translated_everywhere() -> None:
    """Every shipped locale carries the new card's name and meta line."""
    import json

    for path in sorted(_I18N.glob("*.json")):
        preset = json.loads(path.read_text(encoding="utf-8"))["setup"]["preset"]
        assert preset.get("kidsName"), path.name
        assert preset.get("kidsMeta"), path.name
        assert "180" in preset["kidsMeta"], path.name
