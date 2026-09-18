"""The "New in this update" banner names only the host's language (#968).

``/api/quizify/packs/news`` reports every shipped pack of every language, and
the seasonal packs share a display name across languages. ``showPackNews``
joined all of them, so the RC3 live test read "Halloween (101), Halloween
(101), Halloween (101)" and an English host was told about Navidad, Silvester
and Weihnachten — packs the category list below hides from them.

These tests run the real top-level banner code from ``admin.js`` under node
with a fetch that answers exactly what the RC3 host's endpoint answered, and
read what the host would see: the banner's text, or nothing if it is hidden.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent
ADMIN_JS = _REPO_ROOT / "custom_components" / "quizify" / "www" / "js" / "admin.js"

pytestmark = pytest.mark.skipif(
    shutil.which("node") is None, reason="node not installed"
)

#: What ``pack_news_view`` returned on the RC3 host (issue #968).
RC3_NEWS = [
    {"slug": slug, "name": name, "question_count": count, "language": lang}
    for slug, name, count, lang in (
        ("christmas-en", "Christmas", 101, "en"),
        ("halloween-de", "Halloween", 101, "de"),
        ("halloween-en", "Halloween", 101, "en"),
        ("halloween-es", "Halloween", 101, "es"),
        ("navidad-es", "Navidad", 101, "es"),
        ("nye-en", "New Year's Eve", 100, "en"),
        ("nochevieja-es", "Nochevieja", 100, "es"),
        ("silvester-de", "Silvester", 100, "de"),
        ("weihnachten-de", "Weihnachten", 101, "de"),
    )
]


def _top_level_code() -> str:
    """Everything in admin.js after the admin IIFE: the banner code."""
    src = ADMIN_JS.read_text(encoding="utf-8")
    end = src.rfind("\n})();\n")
    assert end != -1, "admin IIFE end not found"
    return src[end + len("\n})();\n") :]


# A DOM just big enough for the banner: an element whose innerHTML is a string,
# a names line the banner can look up by class, and a style object. "Visible
# text" is the banner markup with the names line filled in and tags stripped —
# the same thing whether the names were baked into the markup or set later.
_HARNESS = r"""
function makeEl(tag) {
    const el = {
        tagName: tag, id: '', style: {}, children: [], _html: '', listeners: {},
        addEventListener(t, fn) {
            (this.listeners[t] = this.listeners[t] || []).push(fn);
        },
        remove() { this.removed = true; },
        insertBefore(child) { this.children.unshift(child); },
    };
    Object.defineProperty(el, 'innerHTML', {
        get() { return this._html; },
        set(v) {
            this._html = String(v);
            const hasNames = /class="pack-news-names"/.test(this._html);
            this._names = hasNames ? makeEl('div') : null;
            this._dismiss = makeEl('button');
        },
    });
    el.querySelector = function (sel) {
        if (sel === '.pack-news-names') return this._names;
        if (sel === '#pack-news-dismiss') return this._dismiss;
        return null;
    };
    return el;
}
const setup = makeEl('div');
global.window = {
    t: () => '',
    QuizifyUtils: { escapeHtml: (s) => String(s) },
};
global.document = {
    createElement: makeEl,
    getElementById: (id) => (id === 'setup-screen' ? setup : null),
};
global.fetch = async () => ({ ok: true, json: async () => ({ new_packs: NEWS }) });

function visible() {
    const banner = setup.children[0];
    if (!banner || banner.removed || banner.style.display === 'none') return '';
    let html = banner.innerHTML;
    if (banner._names) {
        html = html.replace(/(class="pack-news-names"[^>]*>)(<\/div>)/,
            '$1' + banner._names.innerHTML + '$2');
    }
    return html.replace(/<[^>]*>/g, ' ').replace(/\s+/g, ' ').trim();
}
"""


def _run(news: list[dict], steps: str) -> dict:
    script = (
        f"const NEWS = {json.dumps(news)};\n"
        + _HARNESS
        + _top_level_code()
        + "\n(async () => {\n  const out = {};\n"
        + steps
        + "\n  process.stdout.write(JSON.stringify(out));\n})()"
        + ".catch(e => { console.error(e); process.exit(1); });\n"
    )
    res = subprocess.run(
        ["node", "-e", script], capture_output=True, text=True, check=False
    )
    assert res.returncode == 0, res.stderr
    return json.loads(res.stdout)


def _names(text: str) -> list[str]:
    return re.findall(r"([A-Z][\w' ]*?) \(\d+\)", text)


@pytest.mark.parametrize(
    ("lang", "expected"),
    [
        ("en", ["Christmas", "Halloween", "New Year's Eve"]),
        ("de", ["Halloween", "Silvester", "Weihnachten"]),
        ("es", ["Halloween", "Navidad", "Nochevieja"]),
    ],
)
def test_banner_names_only_the_game_language(lang: str, expected: list[str]) -> None:
    out = _run(
        RC3_NEWS,
        f"  await showPackNews(() => {json.dumps(lang)});\n  out.text = visible();",
    )
    assert _names(out["text"]) == expected, out["text"]


def test_halloween_appears_once() -> None:
    out = _run(RC3_NEWS, "  await showPackNews(() => 'en');\n  out.text = visible();")
    assert out["text"].count("Halloween") == 1, out["text"]


def test_language_switch_rerenders_the_names() -> None:
    out = _run(
        RC3_NEWS,
        "  let lang = 'en';\n"
        "  await showPackNews(() => lang);\n"
        "  out.before = visible();\n"
        "  renderPackNewsNames('es');\n"
        "  out.after = visible();",
    )
    assert "Christmas" in out["before"] and "Navidad" not in out["before"]
    assert _names(out["after"]) == ["Halloween", "Navidad", "Nochevieja"]


def test_banner_hidden_when_the_language_has_no_new_pack() -> None:
    news = [p for p in RC3_NEWS if p["language"] == "de"]
    out = _run(
        news,
        "  await showPackNews(() => 'en');\n"
        "  out.en = visible();\n"
        "  renderPackNewsNames('de');\n"
        "  out.de = visible();",
    )
    assert out["en"] == ""
    assert _names(out["de"]) == ["Halloween", "Silvester", "Weihnachten"]


def test_untagged_pack_is_still_announced() -> None:
    news = [{"slug": "legacy", "name": "Legacy", "question_count": 5, "language": ""}]
    out = _run(news, "  await showPackNews(() => 'en');\n  out.text = visible();")
    assert _names(out["text"]) == ["Legacy"]


def test_language_pick_refilters_the_banner() -> None:
    """``_applyLanguage`` is the one path every language change goes through."""
    src = ADMIN_JS.read_text(encoding="utf-8")
    body = src[src.index("function _applyLanguage(v)") :]
    body = body[: body.index("\n    }\n")]
    assert "renderPackNewsNames(v)" in body
    assert "showPackNews(function () { return selectedLanguage; })" in src
