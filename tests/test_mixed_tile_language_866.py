"""#866 — the "Mixed / All packs" tile kept the language the host just left.

Found on real hardware during the v1.16.0-RC4 live test. An English game, host
opens *Adjust settings… → Custom settings*, taps 🇪🇸, taps **Apply**: every
pack tile comes back in Spanish and the first one does not.

    ["MixedAll packs", "Ciencia160 preguntas", "Comida y bebida160 preguntas", …]

Coming from a German game the same tile read ``"GemischtAlle Packs"`` above
eleven Spanish ones, so it is the *previous* language, not English.

Every other tile on that grid carries a pack's own name, which arrives already
native to the pack's language and is replaced wholesale when the grid is
rebuilt. The Mixed tile is the only one whose two lines come from the UI
bundle — the server ships it with ``data-i18n="admin.categoryMixed"`` and
``data-i18n="admin.mixedAllPacks"`` (``_render_category_chips``) — and
``buildHeroPackChips`` copied the *text* out of that chip with ``textContent``
and left the keys behind. It also runs BEFORE ``setLanguage()`` resolves, so
the text it copies is the old bundle's, and the sweep that follows skips a
node with no key on it.

Same root cause as #776, #809 and #851, and the same fix: the element says
which string it is, and ``initPageTranslations`` owns it from then on.

The tests below run the real ``buildHeroPackChips`` from ``js/admin.js``
against the real chips ``views.py`` renders and the real i18n bundles, in the
order admin.js actually does it: build, then switch, then sweep.
"""

from __future__ import annotations

import html as _html
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from custom_components.quizify.server.views import _render_category_chips

_REPO = Path(__file__).resolve().parent.parent
_WWW = _REPO / "custom_components" / "quizify" / "www"
_JS = _WWW / "js"
_I18N = _WWW / "i18n"
_STUB = Path(__file__).resolve().parent / "fixtures" / "dom_stub.js"

_NEEDS_NODE = pytest.mark.skipif(
    shutil.which("node") is None, reason="node not installed"
)

# Three languages, so switching one away actually changes the grid, and TWO
# packs in each — with only one the Mixed tile is degenerate and the grid drops
# it (#335 AC2), which would make this whole file assert nothing.
_PACKS = {
    "science": {
        "language": "en",
        "name": "Science",
        "theme": "science",
        "question_count": 160,
    },
    "music": {
        "language": "en",
        "name": "Music",
        "theme": "music",
        "question_count": 160,
    },
    "wissenschaft": {
        "language": "de",
        "name": "Wissenschaft",
        "theme": "science",
        "question_count": 160,
    },
    "musik": {
        "language": "de",
        "name": "Musik",
        "theme": "music",
        "question_count": 160,
    },
    "ciencia": {
        "language": "es",
        "name": "Ciencia",
        "theme": "science",
        "question_count": 160,
    },
    "musica": {
        "language": "es",
        "name": "Música",
        "theme": "music",
        "question_count": 160,
    },
}


def _server_chips() -> list[dict]:
    """The real ``#category-chips`` markup, taken apart for the DOM stub.

    Parsed rather than hand-written so this test fails if the server ever stops
    shipping the two ``data-i18n`` keys the fix hangs on.
    """
    markup = _render_category_chips(_PACKS, "en")
    chips = []
    for button in re.finditer(
        r'<button type="button" class="([^"]*)"([^>]*)>(.*?)</button>', markup
    ):
        attrs = dict(re.findall(r'([\w-]+)="([^"]*)"', button.group(2)))
        chips.append(
            {
                "cls": button.group(1),
                "attrs": attrs,
                "inner": button.group(3),
                "spans": [
                    {
                        "attrs": dict(re.findall(r'([\w-]+)="([^"]*)"', span.group(1))),
                        "text": _html.unescape(span.group(2)),
                    }
                    for span in re.finditer(
                        r"<span([^>]*)>(.*?)</span>", button.group(3)
                    )
                ],
            }
        )
    assert chips, "the server rendered no category chips"
    return chips


def _js_function(source: str, signature: str, optional: bool = False) -> str:
    if optional and signature not in source:
        return ""
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


_SCRIPT = """
require({stub});
QZ.serveI18n({i18n});
QZ.load({i18njs});

var categoryChips = QZ.el('category-chips');
var heroPackChips = QZ.el('hero-pack-chips');

// The chips exactly as the server ships them.
{chips}.forEach(function (c) {{
    var b = document.createElement('button');
    b.className = c.cls;
    Object.keys(c.attrs).forEach(function (k) {{ b.setAttribute(k, c.attrs[k]); }});
    if (c.cls.indexOf('active') !== -1) b.classList.add('active');
    b.innerHTML = c.inner;
    categoryChips.appendChild(b);
}});

// The module context buildHeroPackChips closes over in admin.js.
var els = {{ categoryChips: categoryChips, heroPackChips: heroPackChips }};
var selectedLanguage = 'en';
var selectedCategory = 'mixed';
var selectedCategories = [];
var _CATEGORY_TINT = {{}};
var _FEATURED_PACK_THEME = 'worldcup';
function _categoryIconSvg() {{ return ''; }}
function syncHeroFeatureCardState() {{}}
function updateCategorySummary() {{}}
// Overridden by the real helper below when admin.js has one. Declared here so
// that a checkout WITHOUT the fix still runs the page's own code path and
// fails on what the host sees, rather than on a missing symbol.
function _carryI18nKey() {{}}

{carry}
{grid}
{sync}
{build}

function tiles() {{
    return heroPackChips.querySelectorAll('.hero-cat-tile').map(function (t) {{
        return {{
            name: t.querySelector('.hct-name').textContent,
            count: t.querySelector('.hct-count').textContent
        }};
    }});
}}

(async function () {{
    // The host page opens in English and sweeps once, like admin.js init does.
    await window.QuizifyI18n.init('en');
    window.QuizifyI18n.initPageTranslations();
    buildHeroPackChips();
    var english = tiles();

    // Tapping the Spanish flag, in admin.js's own order: the grid is rebuilt
    // synchronously and the bundle swap is a promise that lands afterwards.
    selectedLanguage = 'es';
    buildHeroPackChips();
    await window.QuizifyI18n.setLanguage('es');
    window.QuizifyI18n.initPageTranslations();
    var spanish = tiles();

    // And back the other way, because the bug is "the previous language",
    // not "English".
    selectedLanguage = 'de';
    buildHeroPackChips();
    await window.QuizifyI18n.setLanguage('de');
    window.QuizifyI18n.initPageTranslations();
    var german = tiles();

    console.log(JSON.stringify({{
        english: english, spanish: spanish, german: german
    }}));
}})();
"""


def _run() -> dict:
    source = (_JS / "admin.js").read_text(encoding="utf-8")
    script = _SCRIPT.format(
        stub=json.dumps(str(_STUB)),
        i18n=json.dumps(str(_I18N)),
        i18njs=json.dumps(str(_JS / "i18n.js")),
        chips=json.dumps(_server_chips()),
        carry=_js_function(source, "function _carryI18nKey(", optional=True),
        grid=_js_function(source, "function _gridPackChipsForLang("),
        sync=_js_function(source, "function syncHeroPackChips("),
        build=_js_function(source, "function buildHeroPackChips("),
    )
    out = subprocess.run(
        ["node", "-e", script], capture_output=True, text=True, check=True
    )
    return json.loads(out.stdout)


def test_the_server_still_ships_the_two_keys_the_fix_hangs_on() -> None:
    """The premise. If the Mixed chip ever loses its ``data-i18n``, carrying
    the key across carries nothing and the tests below would pass vacuously."""
    mixed = _server_chips()[0]
    assert mixed["attrs"]["data-value"] == "mixed"
    keys = [span["attrs"].get("data-i18n") for span in mixed["spans"]]
    assert "admin.categoryMixed" in keys
    assert "admin.mixedAllPacks" in keys


@_NEEDS_NODE
def test_the_mixed_tile_reads_the_language_it_was_built_in() -> None:
    """Guards the guard: a broken English tile would prove nothing below."""
    tiles = _run()["english"]
    assert tiles[0] == {"name": "Mixed", "count": "All packs"}


@_NEEDS_NODE
def test_switching_to_spanish_repaints_the_mixed_tile() -> None:
    """The reported symptom: eleven Spanish tiles under an English first one."""
    tiles = _run()["spanish"]
    assert tiles[0] == {"name": "Mixto", "count": "Todos los paquetes"}


@_NEEDS_NODE
def test_switching_again_repaints_it_again() -> None:
    """It is the *previous* language that sticks, so one switch is not a test —
    a tile that had been fixed to say Spanish would fail here."""
    tiles = _run()["german"]
    assert tiles[0] == {"name": "Gemischt", "count": "Alle Packs"}


@_NEEDS_NODE
def test_the_pack_tiles_are_not_given_a_bundle_key() -> None:
    """A pack name is data, not a bundle string. Handing those tiles a key
    would be the mirror-image bug: the sweep would overwrite "Ciencia" with
    whatever the key resolved to, or warn about a key that does not exist."""
    tiles = _run()["spanish"]
    names = [tile["name"] for tile in tiles[1:]]
    assert names == ["Ciencia", "Música"], names
