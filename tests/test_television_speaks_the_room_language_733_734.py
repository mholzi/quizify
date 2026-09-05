"""The television leaked English onto a localized room (issues #733, #734).

Two symptoms, one shape: a string that *has* a translation never reached the
screen, and nothing anywhere said so.

**#733 — the end screen's awards.** ``renderAwards`` in ``dashboard.html``
wrote ``s.award`` and ``s.detail`` straight into the DOM. Those two fields are
the English fallbacks built in ``game/highlights.py`` ("Fastest Finger", "avg
4.2s per correct answer"). The very same payload has always carried
``award_key``, ``detail_key`` and ``detail_params``, and the guests' phones
have always used them (``js/player-end.js``). So the room read a German
heading, "Auszeichnungen", with English cards underneath — while every phone
in the room showed the German wording for the same award.

**#734 — the "Reconnecting…" pill.** It was marked
``data-i18n="dashboard.reconnecting"``. No bundle has ever had that key; the
string lives under ``connection.reconnecting``. ``initPageTranslations``
leaves an element untouched when ``t(key) === key``, so the pill stayed
English on a German or Spanish television — at exactly the moment the room is
wondering whether something broke.

The second one is the more interesting bug, because it is not really about one
pill: **a ``data-i18n`` key that does not exist fails silently and ships.**
There is no build step, no linter and no runtime error between a typo'd key
and a living room. ``test_every_data_i18n_key_resolves_in_every_bundle`` below
is that missing step. Pointed at the tree as it stood, it found three keys:
the ``dashboard.reconnecting`` from #734 and two on the host's setup screen —
``setup.eigene.q7`` ("Hot Seat?") and ``setup.eigene.hotSeatHint``, which
arrived with the Hot Seat toggle in #616 and never got bundle entries. All
three are fixed here.

The award tests go one step further than "the key exists": they check that the
*placeholders* in all three languages match the ``detail_params`` the server
sends. German and Spanish reorder the numbers relative to English ("{points}
pts in round {round}" → "{points} Pkt. in Runde {round}"), and a translation
that quietly drops one would render a bare ``{round}`` on the television —
which is worse than the English it replaced.
"""

from __future__ import annotations

import ast
import json
import re
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
_WWW = _REPO_ROOT / "custom_components" / "quizify" / "www"
_I18N = _WWW / "i18n"
_JS = _WWW / "js"
_HIGHLIGHTS = _REPO_ROOT / "custom_components" / "quizify" / "game" / "highlights.py"

LANGUAGES = ("de", "en", "es")

# Every i18n attribute initPageTranslations() honours. A key under any of them
# is subject to the same silent-skip, so the guard has to cover all four.
_I18N_ATTRS = (
    "data-i18n",
    "data-i18n-placeholder",
    "data-i18n-title",
    "data-i18n-aria-label",
)
_ATTR_RE = re.compile(
    r'(data-i18n(?:-placeholder|-title|-aria-label)?)\s*=\s*"([^"]+)"'
)
_PLACEHOLDER_RE = re.compile(r"\{(\w+)\}")


def _leaves(node: dict, prefix: str = "") -> dict[str, str]:
    out: dict[str, str] = {}
    for key, value in node.items():
        name = f"{prefix}.{key}" if prefix else key
        if isinstance(value, dict):
            out.update(_leaves(value, name))
        else:
            out[name] = value
    return out


def _bundle(code: str) -> dict[str, str]:
    return _leaves(json.loads((_I18N / f"{code}.json").read_text("utf-8")))


def _bundles() -> dict[str, dict[str, str]]:
    return {code: _bundle(code) for code in LANGUAGES}


def _html_pages() -> list[Path]:
    pages = sorted(_WWW.glob("*.html"))
    assert pages, "no HTML pages found — the guard would pass vacuously"
    return pages


def _keys_in(page: Path) -> set[tuple[str, str]]:
    """Every (attribute, key) pair declared on a page."""
    text = page.read_text("utf-8")
    return {(attr, key) for attr, key in _ATTR_RE.findall(text)}


# ---------------------------------------------------------------------------
# The guard (#734's real finding)
# ---------------------------------------------------------------------------


def test_every_data_i18n_key_resolves_in_every_bundle() -> None:
    """No page may point at a key the bundles do not carry.

    This is the check that did not exist. ``initPageTranslations`` treats an
    unknown key as "leave it alone", which is indistinguishable from "this
    element has no translation on purpose" — so a typo survives review, CI and
    release, and only a German living room ever notices.
    """
    bundles = _bundles()
    missing: list[str] = []

    for page in _html_pages():
        for attr, key in sorted(_keys_in(page)):
            absent = [code for code in LANGUAGES if key not in bundles[code]]
            if absent:
                missing.append(
                    f"{page.name}: {attr}=\"{key}\" missing in {', '.join(absent)}"
                )

    assert not missing, "data-i18n keys with no translation:\n  " + "\n  ".join(
        missing
    )


def test_the_guard_actually_looks_at_every_i18n_attribute() -> None:
    """Guard the guard: the regex must cover all four attribute spellings.

    If ``initPageTranslations`` ever grows a fifth attribute, the coverage test
    above would keep passing while silently ignoring it.
    """
    source = (_JS / "i18n.js").read_text("utf-8")
    used = set(re.findall(r"querySelectorAll\('\[(data-i18n[^\]]*)\]'\)", source))
    assert used == set(_I18N_ATTRS), (
        f"i18n.js translates {sorted(used)}, but the guard checks "
        f"{sorted(_I18N_ATTRS)} — teach _ATTR_RE the difference"
    )


def test_a_missing_key_is_loud_at_runtime_too() -> None:
    """The silent skip gets a console warning (#734).

    Tests catch this before release; the warning catches whatever a test cannot
    see, e.g. a key rendered into the DOM at runtime.
    """
    source = (_JS / "i18n.js").read_text("utf-8")
    call_sites = source.count("warnMissingKey(")
    # One call per data-i18n* branch, plus the helper's own definition.
    assert call_sites >= len(_I18N_ATTRS) + 1, (
        f"i18n.js mentions warnMissingKey() {call_sites}x — every "
        f"data-i18n* branch in initPageTranslations should report a miss"
    )


# ---------------------------------------------------------------------------
# #734 — the pill
# ---------------------------------------------------------------------------


def test_the_reconnect_pill_points_at_the_key_that_exists() -> None:
    dashboard = (_WWW / "dashboard.html").read_text("utf-8")
    stale = 'data-i18n="dashboard.reconnecting"' in dashboard
    assert not stale, "dashboard.reconnecting has never existed in any bundle"
    assert 'data-i18n="connection.reconnecting"' in dashboard, (
        "the pill no longer points at connection.reconnecting"
    )

    absent = [c for c in LANGUAGES if "connection.reconnecting" not in _bundle(c)]
    assert not absent, f"connection.reconnecting missing in {absent}"


def test_the_pill_reads_differently_in_each_language() -> None:
    """A translation that is identical in all three is not a translation."""
    rendered = {code: _bundle(code)["connection.reconnecting"] for code in LANGUAGES}
    assert len(set(rendered.values())) == len(LANGUAGES), rendered


# ---------------------------------------------------------------------------
# #733 — the awards
# ---------------------------------------------------------------------------


def _awards_from_highlights() -> dict[str, tuple[str, set[str]]]:
    """Every award the server can emit, read out of ``highlights.py``.

    Parsed rather than executed: constructing a game that triggers all seven
    superlatives at once is impossible by design (one award per player), so a
    runtime sweep would silently cover only the awards the fixture happens to
    hit. The AST sees all of them.

    Returns ``{award_key: (detail_key, {detail_param names})}``.
    """
    tree = ast.parse(_HIGHLIGHTS.read_text("utf-8"))
    found: dict[str, tuple[str, set[str]]] = {}

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", "")
        if name != "_try_award":
            continue

        kwargs = {kw.arg: kw.value for kw in node.keywords if kw.arg}
        award_key = kwargs.get("award_key")
        detail_key = kwargs.get("detail_key")
        if not isinstance(award_key, ast.Constant) or not isinstance(
            detail_key, ast.Constant
        ):
            continue

        params: set[str] = set()
        detail_params = kwargs.get("detail_params")
        if isinstance(detail_params, ast.Dict):
            params = {
                k.value
                for k in detail_params.keys
                if isinstance(k, ast.Constant) and isinstance(k.value, str)
            }

        found[award_key.value] = (detail_key.value, params)

    assert len(found) >= 7, f"only found {len(found)} awards — did the parse break?"
    return found


def test_the_television_renders_awards_from_the_i18n_keys() -> None:
    """#733: the fix is that ``renderAwards`` stops printing the fallbacks."""
    dashboard = (_WWW / "dashboard.html").read_text("utf-8")
    start = dashboard.index("function renderAwards(")
    end = dashboard.index("function escapeHtml(", start)
    body = dashboard[start:end]

    ignored = [f for f in ("award_key", "detail_key", "detail_params") if f"s.{f}" not in body]
    assert not ignored, f"renderAwards ignores {', '.join(ignored)}"

    printed_raw = [f for f in ("award", "detail") if f"escapeHtml(s.{f})" in body]
    assert not printed_raw, (
        f"the English fallback s.{'/s.'.join(printed_raw)} still goes "
        f"straight into the DOM"
    )

    # The fallbacks must still be reachable: an older backend, or an award
    # added without bundle entries, should read English rather than blank.
    kept_fallback = "s.award)" in body and "s.detail)" in body
    assert kept_fallback, "the English fallback is no longer passed as a fallback"


def test_every_award_and_detail_key_exists_in_every_bundle() -> None:
    bundles = _bundles()
    missing: list[str] = []

    for award_key, (detail_key, _params) in sorted(_awards_from_highlights().items()):
        for key in (award_key, detail_key):
            for code in LANGUAGES:
                if key not in bundles[code]:
                    missing.append(f"{key} missing in {code}")

    assert not missing, "award keys with no translation:\n  " + "\n  ".join(missing)


def test_every_translated_detail_keeps_all_of_its_numbers() -> None:
    """The trap in translating an interpolated line.

    German and Spanish reorder the numbers ("{points} pts in round {round}" →
    "{points} Pkt. in Runde {round}"), and a translator who reorders can drop
    one. A dropped placeholder does not raise — ``t()`` just never substitutes
    it, and the television shows a literal ``{round}``. An *extra* placeholder
    is the same bug from the other side: the server never sends that param, so
    the braces survive to the screen.
    """
    bundles = _bundles()
    problems: list[str] = []

    for award_key, (detail_key, params) in sorted(_awards_from_highlights().items()):
        for code in LANGUAGES:
            text = bundles[code].get(detail_key)
            if text is None:
                continue  # covered by the previous test
            found = set(_PLACEHOLDER_RE.findall(text))
            if found != params:
                problems.append(
                    f"{code}: {detail_key} interpolates {sorted(found)}, "
                    f"server sends {sorted(params)} — {text!r}"
                )

    assert not problems, "detail placeholders out of sync:\n  " + "\n  ".join(problems)


def test_no_award_reads_the_same_in_german_as_in_english() -> None:
    """The symptom #733 reported, expressed as a property.

    If a German award title were byte-identical to the English one, the room
    would still read English — the fix would render, and change nothing.
    """
    bundles = _bundles()
    untranslated: list[str] = []

    for award_key, (detail_key, _params) in sorted(_awards_from_highlights().items()):
        for key in (award_key, detail_key):
            english = bundles["en"].get(key)
            german = bundles["de"].get(key)
            if english is not None and english == german:
                untranslated.append(f"{key}: {english!r}")

    assert not untranslated, "German still reads English:\n  " + "\n  ".join(
        untranslated
    )
