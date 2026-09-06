"""No CSS rule may style only classes that no page ever sets (issue #797).

``styles.css`` is 9.2k source lines concatenated from ``www/css/src/*.css`` and
shipped whole to every phone and the TV. Nothing in the build prunes it, so a
redesign that stops emitting ``.player-chip`` leaves the rule behind forever —
by the time this test was written about 1,000 lines styled classes that had not
appeared in any ``.html``/``.js``/``.py`` since the lobby, reveal and finale
redesigns.

The rule this pins is deliberately conservative, because a false positive here
deletes visible styling:

* a rule counts as dead only when **every** comma-separated alternative of its
  selector names at least one unreferenced class. ``.podium-title, .end-title``
  survives on ``.podium-title`` alone.
* a selector with no class at all (``#end-view``, ``body``, ``a:hover``) is
  never dead — this test says nothing about element or id selectors.
* classes built by concatenation are whitelisted by prefix
  (``DYNAMIC_PREFIXES``), and a ``base--modifier`` counts as referenced when the
  base is, because the modifier half is routinely appended in JS.

The reference corpus is every ``.html`` and ``.js`` under ``www/`` (minus the
generated ``player.bundle.js``, which would make every player module look used
twice) plus every ``.py`` in the integration, since the server renders some
markup itself.

When this fails: either delete the rule, or — if the class is genuinely set
somewhere this scan cannot see — add its prefix to ``DYNAMIC_PREFIXES`` with a
comment saying where.
"""

from __future__ import annotations

import re
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
_PKG = _REPO / "custom_components" / "quizify"
_WWW = _PKG / "www"
_SRC = _WWW / "css" / "src"

#: Class-name stems assembled at runtime, so the literal never appears in source.
#: ``hpt-*``   — hot-seat phase tokens, ``'hpt-' + phase`` (player-hotseat.js)
#: ``is-score-`` — score-band tint, ``'is-score-' + band`` (player-end.js)
DYNAMIC_PREFIXES = ("hpt-", "is-score-")

_CLASS_RE = re.compile(r"\.(-?[A-Za-z_][A-Za-z0-9_-]*)")
_TOKEN_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_-]*")
_NESTING_AT_RULE = re.compile(r"@(media|supports|layer|container|scope)\b")


def _reference_corpus() -> str:
    parts: list[str] = []
    for path in sorted(_WWW.rglob("*")):
        if not path.is_file() or path.suffix not in (".html", ".js"):
            continue
        if path.name == "player.bundle.js":  # generated from the js/ modules
            continue
        parts.append(path.read_text("utf-8", errors="replace"))
    for path in sorted(_PKG.rglob("*.py")):
        parts.append(path.read_text("utf-8", errors="replace"))
    return "\n".join(parts)


class _Node:
    __slots__ = ("kind", "prelude", "sel_start", "end", "children")

    def __init__(self, kind: str, prelude: str, sel_start: int, end: int, children):
        self.kind = kind  # "rule" | "at"
        self.prelude = prelude
        self.sel_start = sel_start
        self.end = end
        self.children = children


def _skip_comment_or_string(text: str, i: int) -> int | None:
    """Advance past a /* */ comment or a quoted string starting at ``i``."""
    n = len(text)
    if text.startswith("/*", i):
        end = text.find("*/", i + 2)
        return n if end == -1 else end + 2
    if text[i] in "\"'":
        quote = text[i]
        i += 1
        while i < n and text[i] != quote:
            i += 2 if text[i] == "\\" else 1
        return i + 1
    return None


def _parse(text: str, pos: int = 0) -> tuple[list[_Node], int]:
    """Split CSS into top-level rules and nesting at-rules. Not a full parser —
    it only needs selector text, brace spans and comment/string skipping."""
    nodes: list[_Node] = []
    n = len(text)
    i = pos
    prelude_start = i
    while i < n:
        jumped = _skip_comment_or_string(text, i)
        if jumped is not None:
            i = jumped
            continue
        char = text[i]
        if char == "}":
            return nodes, i
        if char == ";":  # @import, @charset — no block
            i += 1
            prelude_start = i
            continue
        if char == "{":
            raw = text[prelude_start:i]
            prelude = _strip_comments(raw).strip()
            sel_start = prelude_start + _first_selector_offset(raw)
            if prelude.startswith("@") and _NESTING_AT_RULE.match(prelude):
                children, close = _parse(text, i + 1)
                i = close + 1
                nodes.append(_Node("at", prelude, sel_start, i, children))
            else:
                i = _skip_block(text, i + 1)
                kind = "at" if prelude.startswith("@") else "rule"
                nodes.append(_Node(kind, prelude, sel_start, i, []))
            prelude_start = i
            continue
        i += 1
    return nodes, i


def _skip_block(text: str, i: int) -> int:
    """Return the offset just past the ``}`` closing a block opened before ``i``."""
    depth = 1
    n = len(text)
    while i < n and depth:
        jumped = _skip_comment_or_string(text, i)
        if jumped is not None:
            i = jumped
            continue
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
        i += 1
    return i


def _strip_comments(raw: str) -> str:
    return re.sub(r"/\*.*?\*/", " ", raw, flags=re.S)


def _first_selector_offset(raw: str) -> int:
    """Offset within ``raw`` of the selector itself, past leading comments."""
    i = 0
    n = len(raw)
    while i < n:
        if raw.startswith("/*", i):
            end = raw.find("*/", i + 2)
            i = n if end == -1 else end + 2
            continue
        if raw[i].isspace():
            i += 1
            continue
        return i
    return 0


def _alternatives(prelude: str) -> list[str]:
    out: list[str] = []
    depth = 0
    current: list[str] = []
    for char in prelude:
        if char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
        if char == "," and depth == 0:
            out.append("".join(current))
            current = []
        else:
            current.append(char)
    out.append("".join(current))
    return [alt.strip() for alt in out if alt.strip()]


def _dead_selector(prelude: str, referenced: set[str]) -> set[str]:
    """Unreferenced classes, or an empty set when the rule is alive."""

    def is_referenced(cls: str) -> bool:
        if cls in referenced:
            return True
        if any(cls.startswith(prefix) for prefix in DYNAMIC_PREFIXES):
            return True
        return "--" in cls and cls.split("--", 1)[0] in referenced

    alternatives = _alternatives(prelude)
    if not alternatives:
        return set()
    unreferenced: set[str] = set()
    for alt in alternatives:
        classes = _CLASS_RE.findall(alt)
        if not classes:
            return set()  # element/id selector — out of scope
        dead_here = {cls for cls in classes if not is_referenced(cls)}
        if not dead_here:
            return set()  # this alternative keeps the whole rule alive
        unreferenced |= dead_here
    return unreferenced


def _collect_dead(nodes: list[_Node], referenced: set[str]) -> list[_Node]:
    found: list[_Node] = []
    for node in nodes:
        if node.kind == "rule":
            if _dead_selector(node.prelude, referenced):
                found.append(node)
        elif node.children:
            inner = _collect_dead(node.children, referenced)
            if len(inner) == len(node.children):
                found.append(node)  # whole @media block is dead
            else:
                found.extend(inner)
    return found


def _scan() -> list[str]:
    referenced = set(_TOKEN_RE.findall(_reference_corpus()))
    offences: list[str] = []
    for path in sorted(_SRC.glob("*.css")):
        text = path.read_text("utf-8")
        nodes, _ = _parse(text)
        for node in _collect_dead(nodes, referenced):
            line = text.count("\n", 0, node.sel_start) + 1
            selector = " ".join(node.prelude.split())[:90]
            offences.append(f"{path.name}:{line}  {selector}")
    return offences


def test_no_css_rule_styles_only_unreferenced_classes() -> None:
    offences = _scan()
    assert not offences, (
        f"{len(offences)} CSS rules style classes nothing sets — they ship to "
        "every phone and the TV inside styles.css and can never match "
        f"(#797):\n  " + "\n  ".join(offences)
    )


def test_the_scan_can_actually_see_the_classes_pages_use() -> None:
    """A corpus that failed to load would make the scan above vacuously green."""
    referenced = set(_TOKEN_RE.findall(_reference_corpus()))
    for known_live in ("pl-result", "answer-btn", "dashboard-answer", "view"):
        assert known_live in referenced, (
            f"{known_live!r} is on live markup but the reference corpus missed "
            "it — the scan is not reading www/ properly"
        )
