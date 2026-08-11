"""Saved game presets (#433).

Two halves, matching where the feature can break:

* the store — a small JSON file that must survive a reload, refuse the
  things the spec caps, and never hand the caller a half-written file;
* the admin UI — asserted over the shipped JS/CSS text, because the browser
  half has no test runner here (same approach as the host-card tests).

The UI assertions are not decoration. `_matchingPreset()` deciding only over
the four built-in bundles is precisely what would make a freshly saved preset
read back as "Eigene", and that failure is silent: nothing errors, the host
just sees their preset not being recognised.
"""

from __future__ import annotations

import asyncio
import json
import re
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from custom_components.quizify.server.preset_store import (  # noqa: E402
    MAX_NAME_LENGTH,
    MAX_PRESETS,
    PresetStore,
    PresetValidationError,
)

WWW = REPO / "custom_components" / "quizify" / "www"
ADMIN_JS = WWW / "js" / "admin.js"
ADMIN_HTML = WWW / "admin.html"
CSS = WWW / "css" / "src" / "07-player.css"
VIEWS = REPO / "custom_components" / "quizify" / "server" / "views.py"


class _FakeRuntime:
    def __init__(self, tmp_path: Path) -> None:
        self.data_dir = tmp_path

    async def run_in_executor(self, func, *args):  # noqa: ANN001, ANN002
        return func(*args)


def _store(tmp_path: Path) -> PresetStore:
    return PresetStore(_FakeRuntime(tmp_path))


FAMILY = {
    "name": "Familienabend",
    "rounds": 5,
    "difficulty": "easy",
    "timer": 180,
    "lightning": False,
    "category": "multi",
    "packs": ["geographie", "tiere-natur"],
}


# ---------------------------------------------------------------- store


def test_round_trip_survives_a_reload(tmp_path: Path) -> None:
    async def go() -> None:
        store = _store(tmp_path)
        saved = await store.save(dict(FAMILY))
        assert saved["id"]
        # A second store over the same directory is what a restart looks like.
        again = await _store(tmp_path).list()
        assert [p["name"] for p in again] == ["Familienabend"]
        assert again[0]["packs"] == ["geographie", "tiere-natur"]
        assert again[0]["lightning"] is False

    asyncio.run(go())


def test_saving_with_an_id_updates_instead_of_duplicating(tmp_path: Path) -> None:
    async def go() -> None:
        store = _store(tmp_path)
        first = await store.save(dict(FAMILY))
        renamed = dict(FAMILY, id=first["id"], name="Familienabend XL", rounds=10)
        await store.save(renamed)
        presets = await store.list()
        assert len(presets) == 1, "an id must update in place, not append"
        assert presets[0]["name"] == "Familienabend XL"
        assert presets[0]["rounds"] == 10
        assert presets[0]["created"] == first["created"]

    asyncio.run(go())


def test_delete_removes_and_reports_unknown_ids(tmp_path: Path) -> None:
    async def go() -> None:
        store = _store(tmp_path)
        saved = await store.save(dict(FAMILY))
        assert await store.delete(saved["id"]) is True
        assert await store.list() == []
        assert await store.delete(saved["id"]) is False

    asyncio.run(go())


def test_caps_are_enforced(tmp_path: Path) -> None:
    """Without caps the file is a slow leak and the chip row stops being one tap."""

    async def go() -> None:
        store = _store(tmp_path)
        for i in range(MAX_PRESETS):
            await store.save(dict(FAMILY, name=f"P{i}"))
        with pytest.raises(PresetValidationError):
            await store.save(dict(FAMILY, name="one too many"))

        with pytest.raises(PresetValidationError):
            await store.save(dict(FAMILY, name="x" * (MAX_NAME_LENGTH + 1)))
        with pytest.raises(PresetValidationError):
            await store.save(dict(FAMILY, name="   "))

    asyncio.run(go())


def test_a_corrupt_file_degrades_to_no_presets(tmp_path: Path) -> None:
    """A broken presets file must never stop a game from being set up."""
    (tmp_path / "presets.json").write_text("{not json at all")

    async def go() -> None:
        assert await _store(tmp_path).list() == []
        # …and it stays usable afterwards.
        await _store(tmp_path).save(dict(FAMILY))
        assert len(await _store(tmp_path).list()) == 1

    asyncio.run(go())


def test_tts_and_house_settings_are_not_persisted(tmp_path: Path) -> None:
    """Presets describe an evening, not which speaker is in the room."""

    async def go() -> None:
        store = _store(tmp_path)
        await store.save(dict(FAMILY, tts_entity="tts.google", house_enabled=True))
        stored = (await store.list())[0]
        assert "tts_entity" not in stored
        assert "house_enabled" not in stored

    asyncio.run(go())


# ---------------------------------------------------------------- wiring


def test_all_three_verbs_are_admin_gated() -> None:
    text = VIEWS.read_text(encoding="utf-8")
    for verb in ("GET", "POST", "DELETE"):
        assert f'("{verb}", "/api/quizify/presets"' in text, f"{verb} route missing"
    for view in ("presets_view", "preset_save_view", "preset_delete_view"):
        body = text.split(f"async def {view}")[1].split("async def ")[0]
        assert "_is_admin_authenticated" in body, (
            f"{view} must be gated — these routes carry no HA auth and they "
            "change what the next game will be"
        )


def test_matching_preset_considers_saved_presets_and_packs() -> None:
    """The silent-failure pin: without this a saved preset reads as "Eigene"."""
    text = ADMIN_JS.read_text(encoding="utf-8")
    body = text.split("function _matchingPreset()")[1].split("\n    function ")[0]
    assert "_customPresets" in body, (
        "_matchingPreset must iterate the saved presets, not only _PRESETS"
    )
    assert "_samePacks" in body, (
        "saved presets can express a pack choice, so packs must be compared"
    )
    built_in = body.index("_PRESETS")
    custom = body.index("_customPresets")
    assert built_in < custom, (
        "built-ins are checked first so a saved preset equal to Klassiker "
        "still reads as Klassiker"
    )


def test_delete_does_not_also_select() -> None:
    text = ADMIN_JS.read_text(encoding="utf-8")
    remove = text.split("preset-chip-remove")[1][:600]
    assert "stopPropagation" in remove, (
        "the × sits inside the chip; without stopPropagation deleting also "
        "applies the preset being deleted"
    )


def test_presets_load_only_once_a_token_exists() -> None:
    """#501's lesson: an admin-gated fetch on page load races the token."""
    text = ADMIN_JS.read_text(encoding="utf-8")
    handler = text.split("function handleGameState")[1][:900]
    assert "_loadCustomPresets" in handler
    assert "admin_session_token" in handler


def test_chips_repaint_whenever_the_cards_do() -> None:
    """Found in the browser, not by the suite: a chip stayed lit after the
    host changed a setting, because the chips were only drawn on
    load/save/apply while the cards were repainted on every change."""
    text = ADMIN_JS.read_text(encoding="utf-8")
    body = text.split("function markActivePreset()")[1].split("\n    function ")[0]
    assert "_renderCustomPresets()" in body, (
        "the chips answer the same question as the cards and must be "
        "repainted from the same place"
    )


def test_a_matching_saved_preset_lights_the_custom_card() -> None:
    """Otherwise the whole card row shows nothing selected.

    A saved preset has no card of its own, so `match.id` matches no card and
    the "Eigene" fallback is skipped because a match exists — leaving every
    card unhighlighted, which reads as "no mode chosen".
    """
    text = ADMIN_JS.read_text(encoding="utf-8")
    body = text.split("function markActivePreset()")[1].split("\n    function ")[0]
    assert "!match.custom" in body, (
        "a saved-preset match must fall through to the Eigene card"
    )


def test_row_is_hidden_until_something_is_saved() -> None:
    assert 'id="my-presets"' in ADMIN_HTML.read_text(encoding="utf-8")
    text = ADMIN_JS.read_text(encoding="utf-8")
    assert "_customPresets.length === 0" in text, (
        "an empty labelled row is furniture that explains nothing"
    )


def test_chip_touch_targets_are_at_least_44px() -> None:
    css = CSS.read_text(encoding="utf-8")
    chip = css.split(".preset-chip {")[1].split("}")[0]
    match = re.search(r"min-height:\s*(\d+)px", chip)
    assert match and int(match.group(1)) >= 44, (
        "chips carry a destructive × — 44px is the floor"
    )
    remove = css.split(".preset-chip-remove {")[1].split("}")[0]
    assert re.search(r"min-width:\s*(\d+)px", remove), (
        "the × needs its own hit area or deleting is a lottery"
    )


def test_built_in_preset_cards_are_untouched() -> None:
    """The feature adds a row; it does not restyle the mode chooser."""
    html = ADMIN_HTML.read_text(encoding="utf-8")
    for preset in ("schnellrunde", "klassiker", "kinder", "marathon", "eigene"):
        assert f'data-preset="{preset}"' in html


def test_i18n_keys_exist_in_every_shipped_language() -> None:
    for lang in ("de", "en", "es"):
        data = json.loads((WWW / "i18n" / f"{lang}.json").read_text(encoding="utf-8"))
        setup = data["setup"]
        for key in ("myPresets", "savePreset", "savePresetPrompt",
                    "deletePreset", "deletePresetConfirm"):
            assert setup.get(key), f"{lang}.json is missing setup.{key}"
