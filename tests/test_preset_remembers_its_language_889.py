"""A saved preset remembers the language its packs are written in (#889).

The store persisted packs, rounds, timer, difficulty and the four mode toggles
— everything except the one field that decides whether those packs contain any
questions at all. A host on a German Home Assistant who runs Spanish evenings
saves *Noche española* with ``geografia-es`` and ``deportes-es``; next week the
admin page loads with ``selectedLanguage = 'de'`` (#152), the preset activates
the ES chips — which are ``display:none`` under a German UI — and Start sends
``language: 'de'`` with ES pack ids. ``build_pool`` filters by language, comes
back empty, and ``game/state.py`` raises ``ERR_NO_QUESTIONS_REMAINING``. The
host reads "no questions" with the chosen packs invisible on screen. Fourteen
ES packs ship, so this is a path, not a hypothetical.

Three halves, which is one more than the issue names:

* the **store** now carries ``language`` — and a preset saved before it does
  not, so the round trip has to stay silent about a missing field rather than
  invent one;
* the **admin page** applies that language before the packs, through the same
  body the chip tap runs, so the pack filter, the hero chips and the
  translation sweep all happen exactly as they do for a human tap;
* the **service path** (``quizify.start_game`` with a preset name, i.e. the
  voice door) resolves the same language, because a preset started by voice
  would otherwise walk into the identical empty pool.

The browser half runs the real ``_applyCustomPreset`` / ``_applyPacks`` /
``_applyLanguage`` out of ``admin.js`` against the DOM stub, so what is
asserted is what the chips end up in — not that a line of source exists.
"""

from __future__ import annotations

import asyncio
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO))

from custom_components.quizify.server.preset_store import (  # noqa: E402
    PresetStore,
)
from custom_components.quizify.server.websocket import (  # noqa: E402
    QuizifyWebSocketHandler,
    _preset_to_start_settings,
)

_ADMIN_JS = _REPO / "custom_components" / "quizify" / "www" / "js" / "admin.js"
_STUB = Path(__file__).resolve().parent / "fixtures" / "dom_stub.js"

_NEEDS_NODE = pytest.mark.skipif(
    shutil.which("node") is None, reason="node not installed"
)


# ---------------------------------------------------------------------------
# The store
# ---------------------------------------------------------------------------


class _FakeRuntime:
    def __init__(self, tmp_path: Path) -> None:
        self.data_dir = tmp_path

    async def run_in_executor(self, func, *args):  # noqa: ANN001, ANN002
        return func(*args)


def _store(tmp_path: Path) -> PresetStore:
    return PresetStore(_FakeRuntime(tmp_path))


SPANISH_EVENING = {
    "name": "Noche española",
    "rounds": 10,
    "difficulty": "medium",
    "timer": 30,
    "lightning": True,
    "hot_seat": False,
    "powerups": True,
    "wager": True,
    "language": "es",
    "category": "multi",
    "packs": ["geografia-es", "deportes-es"],
}


def test_the_language_survives_a_reload(tmp_path: Path) -> None:
    """The whole issue in one assertion: the field has to come back."""

    async def go() -> list[dict]:
        store = _store(tmp_path)
        await store.save(dict(SPANISH_EVENING))
        return await _store(tmp_path).list()  # a fresh instance == a reload

    presets = asyncio.run(go())

    assert len(presets) == 1
    assert presets[0]["language"] == "es"
    assert presets[0]["packs"] == ["geografia-es", "deportes-es"]


def test_a_preset_saved_before_this_still_loads(tmp_path: Path) -> None:
    """The migration case. Every preset already on disk was written without a
    language, and the store must neither refuse it nor fill one in — a guessed
    ``en`` would silently retune somebody's German Friday night."""
    old = {k: v for k, v in SPANISH_EVENING.items() if k != "language"}

    async def go() -> list[dict]:
        store = _store(tmp_path)
        await store.save(old)
        return await _store(tmp_path).list()

    presets = asyncio.run(go())

    assert len(presets) == 1
    assert "language" not in presets[0]
    assert presets[0]["rounds"] == 10  # the rest of the record is untouched


# ---------------------------------------------------------------------------
# The service / voice path
# ---------------------------------------------------------------------------


class _StubGame:
    last_settings: dict = {}


def test_a_preset_started_by_voice_carries_its_language() -> None:
    resolved = QuizifyWebSocketHandler.resolve_start_settings(
        _StubGame(), preset=dict(SPANISH_EVENING)
    )

    assert resolved["language"] == "es"
    assert resolved["categories"] == ["geografia-es", "deportes-es"]


def test_an_old_preset_leaves_the_language_to_the_room() -> None:
    """No field, no opinion — the layer that owned it before still does."""
    old = {k: v for k, v in SPANISH_EVENING.items() if k != "language"}

    assert "language" not in _preset_to_start_settings(old)
    assert "language" not in _preset_to_start_settings({**old, "language": ""})


def test_the_explicit_service_field_still_wins_over_the_preset() -> None:
    resolved = QuizifyWebSocketHandler.resolve_start_settings(
        _StubGame(),
        preset=dict(SPANISH_EVENING),
        overrides={"language": "de"},
    )

    assert resolved["language"] == "de"


# ---------------------------------------------------------------------------
# The admin page
# ---------------------------------------------------------------------------


def _slice(start: str, end: str) -> str:
    source = _ADMIN_JS.read_text("utf-8")
    a = source.index(start)
    return source[a : source.index(end, a)]


_HARNESS = """
require({stub});

// The setup screen as the host left it: a German session, the German packs
// visible and the Spanish ones display:none (admin.js's own init does this).
let selectedLanguage = 'de';
let selectedCategory = 'geographie';
let selectedCategories = ['geographie'];
let selectedRounds = 5, selectedDifficulty = 'easy', selectedTimer = 30;
let selectedLightning = true, selectedHotSeat = false;
let selectedPowerups = true, selectedWager = true;

const CHIPS = [
    ['mixed', null],
    ['geographie', 'de'],
    ['tiere-natur', 'de'],
    ['geografia-es', 'es'],
    ['deportes-es', 'es']
];

QZ.els(['category-chips', 'language-chips']);
const categoryChips = document.getElementById('category-chips');
CHIPS.forEach(function (spec) {{
    const chip = document.createElement('div');
    chip.className = 'chip';
    chip.setAttribute('class', 'chip');
    chip.setAttribute('data-value', spec[0]);
    if (spec[1]) chip.setAttribute('data-lang', spec[1]);
    chip.style.display = (!spec[1] || spec[1] === 'de') ? '' : 'none';
    categoryChips.appendChild(chip);
}});
// The chip the host had lit. The stub keeps className and classList apart, so
// the class string is what `.chip.active` can match on.
const german = categoryChips.querySelectorAll('[data-value="geographie"]')[0];
german.className = 'chip active';
german.setAttribute('class', 'chip active');
german.classList.add('active');

const languageChips = document.getElementById('language-chips');
['de', 'en', 'es'].forEach(function (code) {{
    const chip = document.createElement('div');
    chip.className = 'chip';
    chip.setAttribute('class', 'chip');
    chip.setAttribute('data-value', code);
    chip.classList.toggle('active', code === 'de');
    languageChips.appendChild(chip);
}});

const els = {{ categoryChips: categoryChips, languageChips: languageChips }};

// Collaborators the two functions call. Recorded, not asserted on directly,
// except `sent` — a language the phones are never told about is #776's bug.
const sent = [];
const calls = [];
function send(type, payload) {{ sent.push([type, payload]); }}
function _pushLanguage() {{
    send('set_language', {{ language: selectedLanguage }});
}}
function updatePackUIScaling(v) {{ calls.push(['scaling', v]); }}
function buildHeroPackChips() {{ calls.push(['heroChips', selectedLanguage]); }}
function updateSettingsSummary() {{}}
function updateCategorySummary() {{}}
function markActivePreset() {{}}
function _applyPreset() {{ calls.push(['applyPreset']); }}
let posted = null;
function _presetFetch(opts) {{
    posted = JSON.parse(opts.body);
    return Promise.resolve({{
        ok: true,
        json: function () {{ return Promise.resolve({{}}); }}
    }});
}}
function closeConfirmModal() {{}}
function _loadCustomPresets() {{}}
function showErrorToast() {{}}

{apply}

{language}

{post}

function chipState() {{
    const out = {{}};
    categoryChips.querySelectorAll('[data-value]').forEach(function (chip) {{
        out[chip.getAttribute('data-value')] = {{
            active: chip.classList.contains('active'),
            visible: chip.style.display !== 'none'
        }};
    }});
    return out;
}}
function languageChipState() {{
    const out = {{}};
    languageChips.querySelectorAll('[data-value]').forEach(function (chip) {{
        out[chip.getAttribute('data-value')] = chip.classList.contains('active');
    }});
    return out;
}}

const out = {{}};
{body}
console.log(JSON.stringify(out));
"""


def _run(body: str) -> dict:
    script = _HARNESS.format(
        stub=json.dumps(str(_STUB)),
        apply=_slice(
            "    function _applyCustomPreset(p) {",
            "    function _saveCurrentPreset(triggerEl) {",
        ),
        language=_slice(
            "    function _applyLanguage(v) {",
            "    setupChips(els.languageChips, _applyLanguage);",
        ),
        post=_slice(
            "    function _postPreset(name, errorEl, modalId) {",
            "    function _deleteCustomPreset(",
        ),
        body=body,
    )
    proc = subprocess.run(
        ["node", "-e", script], capture_output=True, text=True, check=True
    )
    return json.loads(proc.stdout)


_SPANISH_PRESET_JS = json.dumps(
    {
        "id": "p_abc123",
        "name": "Noche española",
        "rounds": 10,
        "difficulty": "medium",
        "timer": 30,
        "lightning": True,
        "hot_seat": False,
        "powerups": True,
        "wager": True,
        "language": "es",
        "packs": ["geografia-es", "deportes-es"],
    }
)


@_NEEDS_NODE
def test_the_spanish_preset_switches_the_evening_to_spanish() -> None:
    """THE #889 case, on the screen the host is looking at: the packs the
    preset chose are visible and lit, and the language that goes with them is
    the one Start will send."""
    result = _run(
        "_applyCustomPreset(" + _SPANISH_PRESET_JS + ");\n"
        "out.language = selectedLanguage;\n"
        "out.chips = chipState();\n"
        "out.languageChips = languageChipState();\n"
        "out.categories = selectedCategories;\n"
        "out.sent = sent;"
    )

    assert result["language"] == "es"
    assert result["categories"] == ["geografia-es", "deportes-es"]

    # Before the fix these two were display:none — the host saw an empty pack
    # row and no explanation for the "no questions" that followed.
    assert result["chips"]["geografia-es"] == {"active": True, "visible": True}
    assert result["chips"]["deportes-es"] == {"active": True, "visible": True}
    assert result["chips"]["geographie"]["visible"] is False

    # The chip row has to show what the state now is; nothing clicked it.
    assert result["languageChips"] == {"de": False, "en": False, "es": True}

    # #776: phones already in the lobby re-render on the pick.
    assert result["sent"] == [["set_language", {"language": "es"}]]


@_NEEDS_NODE
def test_the_language_is_applied_before_the_packs() -> None:
    """Order is the whole trick. ``_applyLanguage`` drops an active chip that
    belongs to another language and resets the selection to mixed — run it
    after ``_applyPacks`` and it throws away the preset's own packs."""
    result = _run(
        "_applyCustomPreset(" + _SPANISH_PRESET_JS + ");\n"
        "out.categories = selectedCategories;\n"
        "out.category = selectedCategory;"
    )

    assert result["categories"] == ["geografia-es", "deportes-es"]
    assert result["category"] == "multi"


@_NEEDS_NODE
def test_a_preset_saved_before_this_leaves_the_language_alone() -> None:
    """The migration case on the browser side: no field, no switch — and no
    exception either."""
    old = json.loads(_SPANISH_PRESET_JS)
    old.pop("language")
    old["packs"] = ["geographie"]

    result = _run(
        "_applyCustomPreset(" + json.dumps(old) + ");\n"
        "out.language = selectedLanguage;\n"
        "out.chips = chipState();\n"
        "out.categories = selectedCategories;\n"
        "out.sent = sent;"
    )

    assert result["language"] == "de"
    assert result["categories"] == ["geographie"]
    assert result["chips"]["geographie"]["active"] is True
    assert result["chips"]["geografia-es"]["visible"] is False
    # Nothing changed, so nothing is announced to the room.
    assert result["sent"] == []


@_NEEDS_NODE
def test_saving_a_preset_records_the_language_it_was_built_in() -> None:
    """The other end of the round trip: without this the store has nothing to
    persist and every one of the tests above is about a field nobody writes."""
    result = _run(
        "selectedLanguage = 'es';\n"
        "selectedCategories = ['geografia-es'];\n"
        "selectedCategory = 'geografia-es';\n"
        "_postPreset('Noche española', null, null);\n"
        "out.posted = posted;"
    )

    assert result["posted"]["language"] == "es"
    assert result["posted"]["packs"] == ["geografia-es"]
    assert result["posted"]["name"] == "Noche española"


def test_the_chip_row_and_the_preset_run_the_same_body() -> None:
    """Two copies of this logic is how the two paths drift apart again."""
    source = _ADMIN_JS.read_text("utf-8")
    assert "setupChips(els.languageChips, _applyLanguage);" in source
    assert len(re.findall(r"function _applyLanguage\(", source)) == 1
