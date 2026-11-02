"""Render real bundle strings through the real ``i18n.js`` in node (#1177).

Since #1177 a count lives inside the string as an ICU plural
(``{count, plural, one{…} other{…}}``), so "does German say 1 Runde" is no
longer a dictionary lookup — it is what ``t()`` makes of the template. Tests
that used to read a flat ``…One`` key ask this helper instead.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
_WWW = _REPO / "custom_components" / "quizify" / "www"
_STUB = Path(__file__).resolve().parent / "fixtures" / "dom_stub.js"

HAVE_NODE = shutil.which("node") is not None

_SCRIPT = """
require({stub});
QZ.serveI18n({i18n});
QZ.load({i18njs});
(async function () {{
    var calls = {calls};
    var out = [];
    for (var i = 0; i < calls.length; i++) {{
        await window.QuizifyI18n.setLanguage(calls[i][0]);
        out.push(window.QuizifyI18n.t(calls[i][1], calls[i][2]));
    }}
    console.log(JSON.stringify(out));
}})();
"""


def render(calls: list[tuple[str, str, dict | None]]) -> list[str]:
    """``[(lang, key, params), …]`` -> the strings ``t()`` returns."""
    script = _SCRIPT.format(
        stub=json.dumps(str(_STUB)),
        i18n=json.dumps(str(_WWW / "i18n")),
        i18njs=json.dumps(str(_WWW / "js" / "i18n.js")),
        calls=json.dumps([list(c) for c in calls]),
    )
    out = subprocess.run(
        ["node", "-e", script], capture_output=True, text=True, check=True
    )
    return json.loads(out.stdout)


def render_one(lang: str, key: str, params: dict | None = None) -> str:
    return render([(lang, key, params)])[0]
