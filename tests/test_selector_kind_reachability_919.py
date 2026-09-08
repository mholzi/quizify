"""A class selector may not name something the markup only carries as an id (#919).

``test_dead_frontend_weight_797`` asks whether a selector's class name appears
*anywhere* in the front-end corpus. That question cannot tell ``#foo`` from
``.foo``, so it has one blind spot with a very specific shape:

    /* CSS */   .next-round-btn { background: var(--color-accent-blue); }
    <!-- HTML -->  <button id="next-round-btn" class="pl-result-bar-btn">

The token ``next-round-btn`` is all over the corpus — as an id, in the markup
and in ``getElementById``. #797 therefore sees a live rule, while the browser
sees a rule that can never match. This is exactly what a rename produces: a
redesign moves the styling onto new classes (``pl-result-*``), the element keeps
its id for the JS, and the old rule stays behind looking referenced forever.

So this test asks the *kind* question instead: for every class selector in
``www/css/src/*.css``, is that name ever set as a class — in a ``class="…"``
attribute, ``classList.add``, ``className =`` or ``setAttribute('class', …)``?
If it is not, but the same name **is** an element id, the rule is dead and the
name proves it was written before a rename rather than for markup that never
existed.

The check is deliberately narrow, because the fix for a hit is to delete
styling:

* only names that are *both* never-a-class *and* somewhere-an-id are reported.
  A class nothing sets at all is #797's business, not this test's — that keeps
  this test from re-litigating rules whose name simply went away.
* ``DYNAMIC_PREFIXES`` covers the class stems JS assembles by concatenation, so
  a runtime-built class is never mistaken for a dead one.

When this fails, the rule matches nothing today, so **deleting it changes no
pixel**. Rewriting ``.foo`` to ``#foo`` is a different act: it makes a rule
apply that has not applied for as long as the id has been there, and whatever
it then paints is a new appearance that needs a design decision. Delete, or
open an issue for the appearance — do not quietly promote the selector.
"""

from __future__ import annotations

import re
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
_PKG = _REPO / "custom_components" / "quizify"
_WWW = _PKG / "www"
_SRC = _WWW / "css" / "src"

#: Class stems assembled at runtime, so the literal never appears in source.
#: Kept in step with ``test_dead_frontend_weight_797.DYNAMIC_PREFIXES``.
DYNAMIC_PREFIXES = ("hpt-", "is-score-")

_TOKEN = re.compile(r"[A-Za-z_][A-Za-z0-9_-]*")
_CLASS_SELECTOR = re.compile(r"\.(-?[A-Za-z_][A-Za-z0-9_-]*)")

# --- how a name gets to be a class -------------------------------------------
_CLASS_ATTR = re.compile(r"""\bclass\s*=\s*["']([^"']*)["']""")
_CLASS_LIST = re.compile(
    r"""classList\s*\.\s*(?:add|remove|toggle|contains|replace)\s*\(([^)]*)\)"""
)
_CLASS_NAME = re.compile(r"""\bclassName\s*\+?=\s*([^;\n]*)""")
_SET_ATTR_CLASS = re.compile(
    r"""setAttribute\s*\(\s*["']class["']\s*,\s*([^)]*)\)"""
)
_STRING = re.compile(r"""["'`]([^"'`]*)["'`]""")

# --- how a name gets to be an id ---------------------------------------------
_ID_ATTR = re.compile(r"""\bid\s*=\s*["']\s*([^"'\s]+)\s*["']""")
_GET_BY_ID = re.compile(r"""getElementById\s*\(\s*["']([^"']+)["']""")
_HASH_IN_SELECTOR = re.compile(
    r"""querySelector(?:All)?\s*\(\s*["'][^"']*#([A-Za-z_][A-Za-z0-9_-]*)"""
)

_COMMENT_OR_STRING = re.compile(r"/\*.*?\*/|\"[^\"]*\"|'[^']*'", re.S)
_PRELUDE = re.compile(r"([^{}]*)\{")


def _corpus_files() -> list[Path]:
    """Every file that can put a class or an id on an element.

    ``player.bundle.js`` is generated from the ``js/`` modules, so it adds no
    name the modules do not already carry; it is read anyway because a name it
    alone contained would still be a real class at runtime, and a false
    *negative* here only costs a missed offence.
    """
    files = [
        path
        for path in sorted(_WWW.rglob("*"))
        if path.is_file() and path.suffix in (".html", ".js")
    ]
    files.extend(sorted(_PKG.rglob("*.py")))
    return files


def collect_class_names(texts: list[str]) -> set[str]:
    """Names the front end ever puts in an element's class list."""
    names: set[str] = set()
    for text in texts:
        for value in _CLASS_ATTR.findall(text):
            names.update(_TOKEN.findall(value))
        for args in _CLASS_LIST.findall(text):
            for literal in _STRING.findall(args):
                names.update(_TOKEN.findall(literal))
        for rhs in _CLASS_NAME.findall(text):
            for literal in _STRING.findall(rhs):
                names.update(_TOKEN.findall(literal))
        for args in _SET_ATTR_CLASS.findall(text):
            for literal in _STRING.findall(args):
                names.update(_TOKEN.findall(literal))
    return names


def collect_id_names(texts: list[str]) -> set[str]:
    """Names the front end ever uses as an element id."""
    names: set[str] = set()
    for text in texts:
        names.update(_ID_ATTR.findall(text))
        names.update(_GET_BY_ID.findall(text))
        names.update(_HASH_IN_SELECTOR.findall(text))
    return names


def selector_preludes(css: str) -> list[tuple[int, str]]:
    """``(line, selector)`` for every rule in ``css``.

    Comments and quoted strings go first so a ``/* .foo */`` note or a
    ``content: "…"`` value cannot be read as a selector; neither the modules nor
    CSS itself put a brace inside a string, so what is left before each ``{`` is
    a prelude. At-rules are returned too and filtered by the caller.
    """
    def blank(match: re.Match[str]) -> str:
        # Same length and the same newlines, so offsets and line numbers hold.
        return re.sub(r"[^\n]", " ", match.group(0))

    masked = _COMMENT_OR_STRING.sub(blank, css)
    out: list[tuple[int, str]] = []
    for match in _PRELUDE.finditer(masked):
        selector = " ".join(match.group(1).split())
        if not selector or selector.startswith("@"):
            continue
        out.append((masked.count("\n", 0, match.start(1)) + 1, selector))
    return out


def unreachable_class_selectors(
    css: str, class_names: set[str], id_names: set[str]
) -> list[tuple[int, str, str]]:
    """``(line, selector, name)`` for class selectors that name an id."""
    offences: list[tuple[int, str, str]] = []
    for line, selector in selector_preludes(css):
        for name in dict.fromkeys(_CLASS_SELECTOR.findall(selector)):
            if name in class_names or name not in id_names:
                continue
            if any(name.startswith(prefix) for prefix in DYNAMIC_PREFIXES):
                continue
            offences.append((line, selector, name))
    return offences


def _scan() -> list[str]:
    texts = [path.read_text("utf-8", errors="replace") for path in _corpus_files()]
    class_names = collect_class_names(texts)
    id_names = collect_id_names(texts)

    offences: list[str] = []
    for path in sorted(_SRC.glob("*.css")):
        css = path.read_text("utf-8")
        for line, selector, name in unreachable_class_selectors(
            css, class_names, id_names
        ):
            offences.append(f"{path.name}:{line}  {selector[:80]}  (.{name} is an id)")
    return offences


def test_no_class_selector_targets_an_id_only_name() -> None:
    offences = _scan()
    assert not offences, (
        f"{len(offences)} CSS rules style a class the markup only ever sets as "
        "an id, so they match nothing and #797 cannot see it. Delete them — "
        "changing `.foo` to `#foo` would make a rule apply that never has, "
        f"which is a new appearance, not a fix (#919):\n  " + "\n  ".join(offences)
    )


def test_the_scan_can_tell_a_class_from_an_id() -> None:
    """A corpus that failed to load would make the scan above vacuously green."""
    texts = [path.read_text("utf-8", errors="replace") for path in _corpus_files()]
    class_names = collect_class_names(texts)
    id_names = collect_id_names(texts)

    # The reveal admin bar of #919: styling on the classes, wiring on the ids.
    assert "pl-result-admin-bar" in class_names
    assert "pl-result-bar-btn" in class_names
    assert "reveal-admin-controls" in id_names
    assert "next-round-btn" in id_names
    assert "reveal-admin-controls" not in class_names
    assert "next-round-btn" not in class_names


def test_the_check_flags_the_shape_it_is_meant_to_catch() -> None:
    """The #919 rules themselves, as a fixture, against a fixture corpus."""
    markup = ['<button id="next-round-btn" class="pl-result-bar-btn">Next</button>']
    class_names = collect_class_names(markup)
    id_names = collect_id_names(markup)

    css = (
        ".next-round-btn { background: blue; }\n"
        ".next-round-btn.is-final { color: red; }\n"
    )
    offences = unreachable_class_selectors(css, class_names, id_names)
    assert [name for _, _, name in offences] == ["next-round-btn", "next-round-btn"]

    # The live selector, and an id selector, are both left alone.
    alive = ".pl-result-bar-btn { color: red; }\n#next-round-btn { color: red; }\n"
    assert unreachable_class_selectors(alive, class_names, id_names) == []


def test_comments_and_content_strings_are_not_read_as_selectors() -> None:
    markup = ['<div id="ghost" class="real"></div>']
    class_names = collect_class_names(markup)
    id_names = collect_id_names(markup)

    css = '/* .ghost was here */\n.real::after { content: ".ghost"; }\n'
    assert unreachable_class_selectors(css, class_names, id_names) == []
