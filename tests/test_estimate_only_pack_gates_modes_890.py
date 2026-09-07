"""#890 — an estimate-only pack silently drops three of the evening's modes.

``Schätzfragen`` / ``Estimation`` / ``Estimación`` are fifteen estimate
questions out of fifteen. A host picks one on its own, leaves Lightning, the
Hot Seat and the final wager switched on, and the evening has none of the
three:

* ``game/lightning.py`` skips estimate questions when it fills its pool — "an
  all-estimate pool simply yields no questions";
* ``game/hot_seat.py`` skips them too and logs ``Hot seat skipped: no question
  available``;
* ``game/state.py`` never opens a betting window on an estimate final.

All three toggles stay on and nothing on any screen says why nothing happened.
The fix greys the three rows out on the setup screen and shows one line of
explanation under each, driven by an ``mc_count`` the pack metadata now carries
so the admin page can decide it without loading a pack.

The JS tests below run the real ``admin.js`` selection code against the real
chips ``views.py`` renders, under the node DOM stub — behaviour the host sees,
not the shape of the source.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from custom_components.quizify.game.questions import QuestionBank
from custom_components.quizify.server.views import _render_category_chips

_REPO = Path(__file__).resolve().parent.parent
_PKG = _REPO / "custom_components" / "quizify"
_WWW = _PKG / "www"
_JS = _WWW / "js"
_STUB = Path(__file__).resolve().parent / "fixtures" / "dom_stub.js"

_NEEDS_NODE = pytest.mark.skipif(
    shutil.which("node") is None, reason="node not installed"
)

# The shipped estimate-only packs, one per language. Named here so this file
# fails loudly if one is ever renamed rather than quietly testing nothing.
_ESTIMATE_ONLY = {"schaetzfragen-de", "estimation-en", "estimacion-es"}

# Two English packs plus the estimate-only one: enough that "Mixed" is not
# degenerate and that adding a pack to the selection is a real change.
_PACKS = {
    "science-en": {
        "language": "en",
        "name": "Science",
        "theme": "science",
        "question_count": 160,
        "mc_count": 155,
    },
    "music-en": {
        "language": "en",
        "name": "Music",
        "theme": "music",
        "question_count": 160,
        "mc_count": 155,
    },
    "estimation-en": {
        "language": "en",
        "name": "Estimation",
        "theme": "mixed",
        "question_count": 15,
        "mc_count": 0,
    },
}


# ---------------------------------------------------------------------------
# The metadata the client decides on
# ---------------------------------------------------------------------------


def test_the_bank_counts_the_answer_grid_questions_per_pack() -> None:
    """``mc_count`` is question_count minus the estimates, per pack."""
    bank = QuestionBank()
    bank.load_all_categories()
    versions = bank.get_pack_versions()
    assert versions, "no packs loaded"

    for slug, meta in versions.items():
        questions = bank.categories.get(slug, [])
        expected = sum(1 for q in questions if not q.is_estimate)
        assert meta["mc_count"] == expected, slug


def test_the_shipped_estimate_packs_report_no_multiple_choice() -> None:
    """The premise of the whole issue, asserted rather than assumed."""
    bank = QuestionBank()
    bank.load_all_categories()
    versions = bank.get_pack_versions()

    for slug in _ESTIMATE_ONLY:
        assert slug in versions, f"{slug} is no longer a shipped pack"
        assert versions[slug]["mc_count"] == 0, slug
        assert versions[slug]["question_count"] > 0, slug

    # And the ordinary packs are not accidentally caught by the same gate.
    others = {
        slug: meta["mc_count"]
        for slug, meta in versions.items()
        if slug not in _ESTIMATE_ONLY
    }
    assert others, "no non-estimate packs to compare against"
    assert all(count > 0 for count in others.values()), others


def test_the_pack_cards_carry_the_count_to_the_setup_screen() -> None:
    """The admin page reads it off the chip, so the chip has to ship it."""
    markup = _render_category_chips(_PACKS, "en")
    for slug, meta in _PACKS.items():
        card = re.search(rf'<button[^>]*data-value="{slug}"[^>]*>', markup)
        assert card, slug
        assert f'data-mc-count="{meta["mc_count"]}"' in card.group(0), slug


def test_a_pack_without_the_count_gets_no_attribute() -> None:
    """A missing count is *unknown*, not zero — and unknown must reach the
    client as a missing attribute, or an older bank would grey out toggles
    that work perfectly well."""
    packs = {"science-en": dict(_PACKS["science-en"])}
    del packs["science-en"]["mc_count"]
    assert "data-mc-count" not in _render_category_chips(packs, "en")


# ---------------------------------------------------------------------------
# What the host sees on the setup screen
# ---------------------------------------------------------------------------


def _server_chips() -> list[dict]:
    """The real ``#category-chips`` markup, taken apart for the DOM stub."""
    markup = _render_category_chips(_PACKS, "en")
    chips = []
    for button in re.finditer(
        r'<button type="button" class="([^"]*)"([^>]*)>(.*?)</button>', markup
    ):
        chips.append(
            {
                "cls": button.group(1),
                "attrs": dict(re.findall(r'([\w-]+)="([^"]*)"', button.group(2))),
                "inner": button.group(3),
            }
        )
    assert chips, "the server rendered no category chips"
    return chips


def _js_function(source: str, signature: str) -> str:
    start = source.index(signature)
    depth = 0
    seen = False
    for i in range(start, len(source)):
        if source[i] == "{":
            depth += 1
            seen = True
        elif source[i] == "}":
            depth -= 1
            if seen and depth == 0:
                return source[start : i + 1]
    raise AssertionError(f"unbalanced braces after {signature!r}")


def _js_var(source: str, signature: str) -> str:
    """Lift a ``var X = [...]`` / ``var X = {...}`` declaration out of admin.js."""
    if signature not in source:
        return ""
    start = source.index(signature)
    end = source.index(";", source.index("]", start))
    return source[start : end + 1]


_SCRIPT = """
require({stub});

var categoryChips = QZ.el('category-chips');

// The chips exactly as the server ships them.
{chips}.forEach(function (c) {{
    var b = document.createElement('button');
    b.className = c.cls;
    Object.keys(c.attrs).forEach(function (k) {{ b.setAttribute(k, c.attrs[k]); }});
    if (c.cls.indexOf('active') !== -1) b.classList.add('active');
    categoryChips.appendChild(b);
}});

// The three toggle rows + their notes, as admin.html has them.
var IDS = [
    ['lightning-enabled-toggle', 'lightning-toggle-row', 'lightning-mc-note'],
    ['hot-seat-enabled-toggle', 'hot-seat-toggle-row', 'hot-seat-mc-note'],
    ['wager-enabled-toggle', 'wager-toggle-row', 'wager-mc-note']
];
IDS.forEach(function (row) {{
    var input = QZ.el(row[0]);
    input.checked = true;
    QZ.el(row[1]).className = 'toggle-compact setup-lightning-toggle';
    QZ.el(row[2]).classList.add('hidden');
}});

// The module context the selection helpers close over in admin.js.
var els = {{ categoryChips: categoryChips }};
var selectedLanguage = 'en';
var selectedCategory = 'mixed';
var selectedCategories = [];

{modes}
{chipsFn}
{hasMc}
{gate}

function state() {{
    return IDS.map(function (row) {{
        return {{
            disabled: !!document.getElementById(row[0]).disabled,
            checked: !!document.getElementById(row[0]).checked,
            greyed: document.getElementById(row[1]).classList.contains('is-disabled'),
            noteHidden: document.getElementById(row[2]).classList.contains('hidden')
        }};
    }});
}}

function select(values) {{
    selectedCategories = values;
    selectedCategory = values.length === 0 ? 'mixed'
        : (values.length === 1 ? values[0] : 'multi');
    applyEstimateOnlyModeGate();
    return state();
}}

var out = {{}};
out.mixed = select([]);
out.estimateOnly = select(['estimation-en']);
out.plusScience = select(['estimation-en', 'science-en']);
out.backToEstimateOnly = select(['estimation-en']);
out.scienceOnly = select(['science-en']);

// A server that reports no count at all — the toggles must stay usable.
categoryChips.querySelectorAll('.chip[data-lang]').forEach(function (c) {{
    c.removeAttribute('data-mc-count');
}});
out.unknownCount = select(['estimation-en']);

console.log(JSON.stringify(out));
"""


def _run() -> dict:
    source = (_JS / "admin.js").read_text(encoding="utf-8")
    script = _SCRIPT.format(
        stub=json.dumps(str(_STUB)),
        chips=json.dumps(_server_chips()),
        modes=_js_var(source, "var _MC_ONLY_MODES = ["),
        chipsFn=_js_function(source, "function _selectedPackChips("),
        hasMc=_js_function(source, "function _selectionHasMultipleChoice("),
        gate=_js_function(source, "function applyEstimateOnlyModeGate("),
    )
    out = subprocess.run(
        ["node", "-e", script], capture_output=True, text=True, check=True
    )
    return json.loads(out.stdout)


@_NEEDS_NODE
def test_a_normal_selection_leaves_all_three_modes_alone() -> None:
    """Guards the guard: a gate that greyed everything would prove nothing."""
    result = _run()
    for name in ("mixed", "scienceOnly"):
        for row in result[name]:
            assert row["disabled"] is False, name
            assert row["greyed"] is False, name
            assert row["noteHidden"] is True, name


@_NEEDS_NODE
def test_an_estimate_only_selection_greys_out_the_three_toggles() -> None:
    """The reported bug: three modes the evening cannot have, still switched on
    and saying nothing."""
    for row in _run()["estimateOnly"]:
        assert row["disabled"] is True
        assert row["greyed"] is True
        assert row["noteHidden"] is False


@_NEEDS_NODE
def test_adding_a_multiple_choice_pack_gives_the_toggles_back() -> None:
    """The other half of the promise — and the reason the gate cannot be a
    one-way door taken at first paint."""
    result = _run()
    for row in result["plusScience"]:
        assert row["disabled"] is False
        assert row["greyed"] is False
        assert row["noteHidden"] is True
    # ... and removing it again grey them out a second time.
    for row in result["backToEstimateOnly"]:
        assert row["disabled"] is True


@_NEEDS_NODE
def test_greying_a_row_does_not_throw_the_hosts_choice_away() -> None:
    """The toggles are only greyed, never rewritten: a host who comes back from
    an estimate-only detour finds the settings they had."""
    result = _run()
    for name in ("estimateOnly", "plusScience"):
        for row in result[name]:
            assert row["checked"] is True, name


@_NEEDS_NODE
def test_an_unreported_count_leaves_the_toggles_usable() -> None:
    """No ``data-mc-count`` means the server did not say, which is not the same
    as saying zero."""
    for row in _run()["unknownCount"]:
        assert row["disabled"] is False
        assert row["greyed"] is False
        assert row["noteHidden"] is True


# ---------------------------------------------------------------------------
# The explanation itself
# ---------------------------------------------------------------------------


def test_the_setup_screen_carries_the_note_under_each_of_the_three() -> None:
    markup = (_WWW / "admin.html").read_text(encoding="utf-8")
    for note_id in ("lightning-mc-note", "hot-seat-mc-note", "wager-mc-note"):
        assert f'id="{note_id}"' in markup, note_id
    assert markup.count('data-i18n="setup.eigene.needsMultipleChoice"') == 3
    for row_id in ("lightning-toggle-row", "hot-seat-toggle-row", "wager-toggle-row"):
        assert f'id="{row_id}"' in markup, row_id


def test_the_explanation_is_written_in_all_three_languages() -> None:
    """One key, three real translations — no English placeholder standing in."""
    texts = {}
    for lang in ("en", "de", "es"):
        bundle = json.loads(
            (_WWW / "i18n" / f"{lang}.json").read_text(encoding="utf-8")
        )
        text = bundle["setup"]["eigene"]["needsMultipleChoice"]
        assert text.strip(), lang
        texts[lang] = text
    assert len(set(texts.values())) == 3, texts
