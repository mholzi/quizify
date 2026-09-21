"""One escape, one meaning (#982).

Five ``escapeHtml`` implementations shipped side by side, with three different
meanings: the canonical one in ``utils.js``, a DOM ``textContent`` trick in
``dashboard.js`` that leaves ``"`` and ``'`` unescaped, and a ``String(str)``
copy in ``pack-submit.js`` that renders a missing field as the word ``null``.
``admin.js`` added two wrappers and a third inline ``esc`` on top.

The two copies that differed are the ones that matter. The television's copy
cannot escape a quote, so the first ``title="…"`` written against it would
inherit the gap in silence; the submission form's copy already printed the
literal text ``null`` wherever a pack left a field out.

These tests take every escape a surface actually calls, run it under node, and
hold it to the canonical meaning: a name carrying ``"`` and ``'`` comes back
escaped, and a missing field comes back empty rather than as ``null``.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent
_JS = _REPO_ROOT / "custom_components" / "quizify" / "www" / "js"
_UTILS = _JS / "utils.js"

pytestmark = pytest.mark.skipif(
    shutil.which("node") is None, reason="node not installed"
)

#: The surfaces that render user- or pack-supplied text into ``innerHTML``.
#: ``render-shared.js`` is not here on purpose: its copy is a documented,
#: deliberately identical fallback for a page that has not loaded utils.js.
SURFACES = ("dashboard.js", "admin.js", "pack-submit.js")

#: A hostile display name: every character the canonical escape covers.
HOSTILE = "Ann \"The Hammer\" O'Neill <b> & co"

#: What the canonical escape makes of it.
HOSTILE_ESCAPED = (
    "Ann &quot;The Hammer&quot; O&#39;Neill &lt;b&gt; &amp; co"
)

#: Any named binding in the file: ``function NAME(`` or ``var NAME =``.
_DEFINITION = re.compile(
    r"(?:function\s+([\w$]+)\s*\(|(?:var|let|const)\s+([\w$]+)\s*=)"
)


def _is_escape_helper(name: str) -> bool:
    """``esc``, or any name that says both "escape" and "html"."""
    lowered = name.lower()
    return name == "esc" or ("esc" in lowered and "html" in lowered)


def _node(script: str) -> dict:
    res = subprocess.run(
        ["node", "-e", script], capture_output=True, text=True, check=False
    )
    assert res.returncode == 0, res.stderr
    return json.loads(res.stdout)


def _canonical_harness() -> str:
    """Load the real ``utils.js`` under node and expose ``escape``."""
    return (
        "global.window = global.window || {};\n"
        f"const src = require('fs').readFileSync({json.dumps(str(_UTILS))}, 'utf8');\n"
        "(0, eval)(src);\n"
        "const escape = window.QuizifyUtils.escapeHtml;\n"
    )


#: ``el.textContent = x; el.innerHTML`` in a real browser: ``&``, ``<`` and
#: ``>`` are escaped and the two quote characters are not. That asymmetry is
#: the whole of the dashboard copy's bug, so the stub has to reproduce it.
_DOM_STUB = """
const document = { createElement: () => ({
    set textContent(v) {
        this._html = String(v)
            .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
    },
    get innerHTML() { return this._html; },
}) };
const QuizifyUtils = window.QuizifyUtils;
"""


def _helper_source(source: str, name: str) -> str:
    """Lift a local escape helper out of the file, verbatim."""
    for opener in (f"function {name}(", f"const {name} =", f"var {name} ="):
        at = source.find(opener)
        if at == -1:
            continue
        depth = 0
        for i in range(at, len(source)):
            char = source[i]
            if char in "({[":
                depth += 1
            elif char in ")}]":
                depth -= 1
            elif char == ";" and depth == 0 and not opener.startswith("function"):
                return source[at : i + 1]
            if depth == 0 and char == "}" and opener.startswith("function"):
                return source[at : i + 1]
    raise AssertionError(f"no definition of {name} found")


def _calls(source: str) -> set[str]:
    """Every distinct escape callee spelled out in ``source``."""
    return set(re.findall(r"([\w.$]*escapeHtml|(?<![\w.])esc)\s*\(", source))


def test_the_canonical_escape_covers_quotes_and_missing_fields() -> None:
    """``QuizifyUtils.escapeHtml`` is the meaning the surfaces must inherit."""
    out = _node(
        _canonical_harness()
        + "process.stdout.write(JSON.stringify({\n"
        f"  hostile: escape({json.dumps(HOSTILE)}),\n"
        "  missing: escape(null),\n"
        "  undef: escape(undefined),\n"
        "  zero: escape(0),\n"
        "}));"
    )
    assert out["hostile"] == HOSTILE_ESCAPED
    assert out["missing"] == ""
    assert out["undef"] == ""
    # A real value of 0 is still a value, not a missing field.
    assert out["zero"] == "0"


@pytest.mark.parametrize("name", SURFACES)
def test_the_surface_escapes_through_the_one_implementation(name: str) -> None:
    """Every escaping call on the surface names ``QuizifyUtils.escapeHtml``."""
    source = (_JS / name).read_text(encoding="utf-8")
    callees = _calls(source)
    assert callees, f"{name} renders no escaped text at all — did the parse break?"
    stray = {c for c in callees if not c.endswith("QuizifyUtils.escapeHtml")}
    assert not stray, (
        f"{name} still escapes through {sorted(stray)}. #982: there is one "
        "escape in the front end — call QuizifyUtils.escapeHtml."
    )


@pytest.mark.parametrize("name", SURFACES)
def test_the_surface_defines_no_escape_of_its_own(name: str) -> None:
    """A local copy is how the three meanings came about in the first place."""
    source = (_JS / name).read_text(encoding="utf-8")
    defined = sorted(
        {
            name
            for m in _DEFINITION.finditer(source)
            for name in [m.group(1) or m.group(2)]
            if _is_escape_helper(name)
        }
    )
    assert not defined, (
        f"{name} defines its own escape ({defined}). Even a wrapper is a name "
        "that can drift — call QuizifyUtils.escapeHtml at the call site."
    )


@pytest.mark.parametrize("name", SURFACES)
def test_a_hostile_name_comes_back_escaped_from_the_surface(name: str) -> None:
    """The escape each surface actually calls, run against a hostile name.

    A callee spelled ``QuizifyUtils.escapeHtml`` resolves to the real thing out
    of ``utils.js`` — which is how the browser resolves it too, since both
    pages load ``utils.js`` first (``dashboard.html:249``, ``admin.html:1121``).
    Anything else is a local helper, and is lifted out of the file and run as
    written, so a copy that cannot escape a quote says so in its own words.
    """
    source = (_JS / name).read_text(encoding="utf-8")
    for callee in sorted(_calls(source)):
        if callee.endswith("QuizifyUtils.escapeHtml"):
            prelude = f"const surfaceEscape = {callee.removeprefix('window.')};\n"
        else:
            prelude = _helper_source(source, callee) + (
                f"\nconst surfaceEscape = {callee};\n"
            )
        out = _node(
            _canonical_harness()
            + _DOM_STUB
            + prelude
            + "process.stdout.write(JSON.stringify({\n"
            f"  hostile: surfaceEscape({json.dumps(HOSTILE)}),\n"
            "  missing: surfaceEscape(null),\n"
            "}));"
        )
        assert out["hostile"] == HOSTILE_ESCAPED, (
            f"{name} renders a display name through {callee}() without "
            f"escaping every quote: {out['hostile']!r}. An attribute written "
            "against it (title=, alt=) would break out of its own quotes."
        )
        assert out["missing"] == "", (
            f"{name} renders a missing field through {callee}() as "
            f"{out['missing']!r} — a pack that leaves the field out would put "
            "that word on the screen."
        )
