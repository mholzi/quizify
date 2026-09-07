"""Each surface reads its own stylesheet, and nothing bleeds across (#880).

The television used to load `css/styles.css` whole — 8,347 lines, of which nine
of the ten modules address the phone or the host console — and then re-declare
the same class names in a 1,900-line inline `<style>` block to out-cascade it.
That is the structural cause behind #775, #836, #865 and the podium fix in
v1.16.0: four incidents of a rule written for a 390 px phone landing on a
1280 px television. A fifth was live in the tree when this was filed —
`05-finale.css` hid `.podium-avatar` with a comment saying the slot belonged to
`admin.js`, which had stopped rendering it; the television renders it, and the
shared `display: none` won there.

Every width query in the tree is `min-width`, so by construction the un-queried
base rule **is** the phone rule and it reaches 1280 px unless something
overrides it. Nothing scoped by surface. The fix is physical: `build_css.py`
emits one sheet per surface, so a rule cannot reach a page it was not written
for, and each page carries a surface root class (`surface-tv` / `surface-host`
/ `surface-player`) for the rules that want to say so explicitly.

The load-bearing test in here is
``test_no_rule_dropped_from_the_tv_sheet_could_have_matched``. #880 is a
refactor: the television has to render identically before and after. That holds
only if every rule the TV sheet leaves out was already dead on the television,
so this asserts it rule by rule rather than asking anyone to take it on trust.
The check is a *necessary* condition — a selector that requires a class or id
token which appears nowhere in the page's markup or its script cannot match —
which is the right direction: it over-reports possible matches and can only
fail when a rule might genuinely have applied.
"""

from __future__ import annotations

import importlib.util
import re
import sys
from pathlib import Path
from types import ModuleType

import pytest

from tests.conftest import without_comments

REPO = Path(__file__).resolve().parent.parent
WWW = REPO / "custom_components" / "quizify" / "www"
CSS = WWW / "css"
SRC = CSS / "src"
JS = WWW / "js"


def _build_css() -> ModuleType:
    path = REPO / "scripts" / "build_css.py"
    spec = importlib.util.spec_from_file_location("_qz_build_css_880", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


# --- a very small CSS reader: rule selectors, descending into @media ---------

_BLOCK_COMMENT = re.compile(r"/\*.*?\*/", re.S)


def selectors(css: str) -> list[str]:
    """Every style rule's selector list, `@media`/`@supports` included.

    Deliberately not a parser. It needs to answer one question — "which
    selectors are in this file" — and `@keyframes` / `@font-face` preludes are
    not selectors, so they are skipped rather than mis-read as one.
    """
    text = _BLOCK_COMMENT.sub("", css)
    out: list[str] = []

    def walk(start: int, end: int) -> None:
        i = start
        while i < end:
            brace = text.find("{", i)
            if brace == -1 or brace >= end:
                return
            prelude = text[i:brace].strip()
            depth, k = 1, brace + 1
            while k < end and depth:
                if text[k] == "{":
                    depth += 1
                elif text[k] == "}":
                    depth -= 1
                k += 1
            if prelude.startswith("@"):
                if prelude.split()[0] in ("@media", "@supports", "@layer", "@container"):
                    walk(brace + 1, k - 1)
            elif prelude:
                out.append(prelude)
            i = k

    walk(0, len(text))
    return out


_NAME = re.compile(r"([.#])([A-Za-z_][\w-]*)")


def required_names(selector: str) -> list[set[tuple[str, str]]]:
    """The class and id names each comma-separated part needs to match.

    A comma list matches when ANY part does, so this returns one set per part
    and the caller takes the union of possibilities. An empty set means the
    part is a bare element or universal selector, which can match anywhere. The
    sigil is kept: `#lightning-recap-grid` and `.lightning-recap-grid` are not
    the same requirement, and on the television exactly one of them is met.
    """
    return [set(_NAME.findall(part)) for part in selector.split(",") if part.strip()]


# Where a class or an id can enter the page. Markup attributes, the three DOM
# APIs that set a class, and the two that look one up — plus `querySelector`,
# which spells both sigils itself. Comments are stripped before any of this
# runs: prose names the very things it explains, and a comment about "the
# floating-reaction layer" must not vouch for a rule the page never renders.
_CLASS_SOURCES = (
    re.compile(r"""class\s*=\s*["']([^"']*)["']"""),
    re.compile(r"""className\s*(?:\+?=)\s*['"]([^'"]*)['"]"""),
    re.compile(r"""classList\.\w+\(\s*['"]([^'"]*)['"]"""),
    re.compile(r"""querySelector(?:All)?\(\s*['"][^'"]*\.([\w-]+)"""),
    re.compile(r"""getElementsByClassName\(\s*['"]([^'"]*)['"]"""),
)
_ID_SOURCES = (
    re.compile(r"""\bid\s*=\s*["']([^"']*)["']"""),
    re.compile(r"""getElementById\(\s*['"]([^'"]*)['"]"""),
    re.compile(r"""querySelector(?:All)?\(\s*['"][^'"]*#([\w-]+)"""),
)
# `'rank-' + rank` — a class assembled at runtime reaches the source only as its
# stem, so a literal ending in `-` stands in for whatever gets appended. Bounded
# to a short numeric or word suffix, because that is what such stems carry
# (`rank-1`, `is-score-high`); without the bound, `rank-` would vouch for every
# class in the tree that happens to start with it.
_STEM = re.compile(r"""['"]([\w-]*[-_])['"]""")
_STEM_SUFFIX = re.compile(r"[A-Za-z0-9]{1,6}$")

_SPLIT = re.compile(r"[\s]+")


def page_names(paths: list[Path]) -> tuple[set[str], set[str], list[str]]:
    """(classes, ids, class stems) a page can produce."""
    classes: set[str] = set()
    ids: set[str] = set()
    stems: set[str] = set()
    for path in paths:
        text = without_comments(path.read_text("utf-8"))
        for pattern in _CLASS_SOURCES:
            for hit in pattern.findall(text):
                classes |= {tok for tok in _SPLIT.split(hit) if tok}
        for pattern in _ID_SOURCES:
            for hit in pattern.findall(text):
                ids |= {tok for tok in _SPLIT.split(hit) if tok}
        stems |= set(_STEM.findall(text))
    return classes, ids, sorted(stems)


TV_SOURCES = [
    WWW / "dashboard.html",
    JS / "dashboard.js",
    JS / "render-shared.js",
    JS / "client-core.js",
    JS / "utils.js",
    JS / "i18n.js",
]

_TV_CLASSES, _TV_IDS, _TV_STEMS = page_names(TV_SOURCES)


def _tv_has(sigil: str, name: str) -> bool:
    if sigil == "#":
        return name in _TV_IDS
    if name in _TV_CLASSES:
        return True
    return any(
        name.startswith(stem) and _STEM_SUFFIX.fullmatch(name[len(stem) :])
        for stem in _TV_STEMS
    )


def can_match_tv(selector: str) -> bool:
    for part in required_names(selector):
        if not part:
            return True  # bare element / universal selector
        if all(_tv_has(sigil, name) for sigil, name in part):
            return True
    return False


# --- the split exists -------------------------------------------------------


def test_build_css_emits_one_sheet_per_surface() -> None:
    bc = _build_css()
    assert set(bc.SURFACES) == {"styles.css", "tv.css"}
    assert (CSS / "styles.css").is_file()
    assert (CSS / "tv.css").is_file()


def test_the_television_stops_loading_the_phones_stylesheet() -> None:
    dashboard = (WWW / "dashboard.html").read_text("utf-8")
    assert "/quizify/static/css/tv.css" in dashboard
    assert "/quizify/static/css/styles.css" not in dashboard, (
        "the television is loading the phone's sheet again — this is the whole "
        "of #880"
    )
    for page in ("player.html", "admin.html"):
        html = (WWW / page).read_text("utf-8")
        assert "/quizify/static/css/styles.css" in html
        assert "/quizify/static/css/tv.css" not in html, (
            f"{page} loads the television's sheet; the bleed now runs the other way"
        )


@pytest.mark.parametrize(
    "page,root",
    [
        ("dashboard.html", "surface-tv"),
        ("admin.html", "surface-host"),
        ("player.html", "surface-player"),
    ],
)
def test_every_page_declares_its_surface_root(page: str, root: str) -> None:
    """The hook a rule uses to say which screen it was written for.

    Before this, only the TV set a body class and no rule in `css/src/` used it;
    `admin.html` and `player.html` had a bare `<body>`, so there was nothing to
    scope by even if someone had wanted to.
    """
    html = without_comments((WWW / page).read_text("utf-8"))
    body = re.search(r"<body[^>]*>", html)
    assert body, f"{page} has no <body> tag"
    assert root in body.group(0), f"{page}'s <body> does not carry {root}"


# --- the split changes nothing on screen ------------------------------------


def test_no_rule_dropped_from_the_tv_sheet_could_have_matched() -> None:
    """The no-visual-change claim, checked rule by rule.

    `tv.css` leaves out `07-player.css`, `09-a11y.css` and the un-marked part of
    `08-responsive.css`. Dropping a rule is only safe when it was already dead
    on this surface, so every selector in those is required to name at least one
    class or id the television's markup and script never produce.
    """
    bc = _build_css()
    dropped_modules = [m for m in bc.CSS_MODULES if m not in bc.TV_MODULES]
    assert dropped_modules, "nothing is being dropped; the split buys nothing"

    reachable: list[str] = []
    for name in dropped_modules:
        for selector in selectors((SRC / name).read_text("utf-8")):
            if can_match_tv(selector):
                reachable.append(f"{name}: {selector}")

    # The sliced module: whatever build_css.py does NOT put in the TV sheet.
    sliced = "08-responsive.css"
    whole = (SRC / sliced).read_text("utf-8")
    kept = set(selectors(bc.slice_for_surface(whole, "tv")))
    for selector in selectors(whole):
        if selector not in kept and can_match_tv(selector):
            reachable.append(f"{sliced}: {selector}")

    assert not reachable, (
        "these rules are in the phone's sheet but not the television's, and "
        "their selectors name something the television does render — dropping "
        f"them changes how it looks: {reachable}"
    )


def test_the_tv_sheet_leaves_out_the_phones_views() -> None:
    bc = _build_css()
    assert "07-player.css" not in bc.TV_MODULES
    assert "09-a11y.css" not in bc.TV_MODULES
    assert "10-tv.css" not in bc.CSS_MODULES, (
        "the television's own module must not reach the phone's sheet either"
    )
    assert bc.TV_MODULES[-1] == "10-tv.css", (
        "the TV module has to be last: it is where the inline <style> block sat, "
        "after the stylesheet link, and its rules were written to win on order"
    )


def test_the_tv_sheet_is_substantially_smaller() -> None:
    styles = (CSS / "styles.css").stat().st_size
    tv = (CSS / "tv.css").stat().st_size
    assert tv < styles * 0.8, (
        f"tv.css is {tv:,} B against styles.css's {styles:,} B — the television "
        "is still carrying the phone's stylesheet in all but name"
    )


# --- the two rules #880 names by hand ---------------------------------------


def test_the_podium_avatar_is_no_longer_hidden_for_the_television() -> None:
    """The fifth instance, and the one that was live.

    `05-finale.css` hid `.podium-avatar` "rendered by admin.js renderPodium".
    admin.js had stopped rendering it; `dashboard.html` had started. The TV's
    own rule set font-size only, so the shared `display: none` won and the
    medal never appeared on the screen that shows one.
    """
    finale = without_comments((SRC / "05-finale.css").read_text("utf-8"))
    assert not re.search(r"\.podium-avatar\s*\{[^}]*display\s*:\s*none", finale), (
        "05-finale.css hides .podium-avatar again — that rule reaches the "
        "television, which is the one surface that renders the element"
    )
    tv = without_comments((SRC / "10-tv.css").read_text("utf-8"))
    assert ".podium-avatar" in tv, "the television still sizes its own medal"


def test_the_dashboard_team_rules_moved_to_the_tv_module() -> None:
    """The one thing the television genuinely needed from `04-lobby.css`.

    Named in #880 as the reason the TV could not simply drop the link. They
    render nowhere else — no `.dashboard-team-*` element is produced by
    `player.html`/`admin.html` or their scripts — so the phone's sheet was
    carrying them for nobody.
    """
    lobby = (SRC / "04-lobby.css").read_text("utf-8")
    tv = (SRC / "10-tv.css").read_text("utf-8")
    for rule in (
        ".dashboard-team-group",
        ".dashboard-team-name",
        ".dashboard-team-members",
    ):
        assert rule not in lobby, f"{rule} is back in the phone's sheet"
        assert rule in tv, f"{rule} is not in the television's module"
