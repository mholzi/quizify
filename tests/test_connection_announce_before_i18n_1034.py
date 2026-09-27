"""The connection live region recovers when the socket opens before i18n (#1034).

Found in the v1.21.0-RC4 live test: ``#conn-status-announce`` sometimes read
the raw status ``connected``, with no ``data-i18n``, on a freshly loaded phone
and on the admin page after a reload.

``announceConnection`` treated "t() returned the key" as "unknown status" and
removed ``data-i18n``. When the socket opens before the bundle has loaded, t()
returns the key for every status, so the line lost the attribute the sweep
after i18n init would have re-rendered it from, and stayed raw for good.

The test runs the real ``client-core.js`` and ``i18n.js`` against the DOM stub:
paint before init, init, sweep, and read the line back.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parent.parent
_WWW = _REPO / "custom_components" / "quizify" / "www"
_JS = _WWW / "js"
_I18N = _WWW / "i18n"
_STUB = Path(__file__).resolve().parent / "fixtures" / "dom_stub.js"

_NEEDS_NODE = pytest.mark.skipif(
    shutil.which("node") is None, reason="node not installed"
)

_SCRIPT = """
require({stub});
QZ.serveI18n({i18n});
QZ.load({i18njs});
QZ.els(['conn-status', 'conn-status-announce']);
QZ.load({core});

var core = window.QuizifyClientCore;

function announced() {{
    var el = document.getElementById('conn-status-announce');
    return {{ text: el.textContent, key: el.getAttribute('data-i18n') }};
}}

(async function () {{
    var out = {{}};

    // The socket opens first: no bundle loaded yet.
    core.updateConnectionIndicator('connected');
    out.beforeInit = announced();

    // The bundle lands; the page sweeps like player-core and admin do.
    await window.QuizifyI18n.init('en');
    window.QuizifyI18n.initPageTranslations();
    out.afterInit = announced();

    await window.QuizifyI18n.setLanguage('de');
    window.QuizifyI18n.initPageTranslations();
    out.afterSwitch = announced();

    // An unknown status still leaves no key behind (#783).
    core.updateConnectionIndicator('wobbly');
    window.QuizifyI18n.initPageTranslations();
    out.unknown = announced();

    console.log(JSON.stringify(out));
}})().catch(function (e) {{ console.error(e); process.exit(1); }});
"""


def _run() -> dict:
    script = _SCRIPT.format(
        stub=json.dumps(str(_STUB)),
        i18n=json.dumps(str(_I18N)),
        i18njs=json.dumps(str(_JS / "i18n.js")),
        core=json.dumps(str(_JS / "client-core.js")),
    )
    out = subprocess.run(
        ["node", "-e", script], capture_output=True, text=True, check=True
    )
    return json.loads(out.stdout.strip().splitlines()[-1])


@pytest.fixture(scope="module")
def result() -> dict:
    return _run()


@_NEEDS_NODE
def test_a_known_status_keeps_its_key_before_i18n_loads(result: dict) -> None:
    assert result["beforeInit"]["key"] == "connection.connected"


@_NEEDS_NODE
def test_the_sweep_after_init_translates_the_line(result: dict) -> None:
    """The reported DOM: the line still read ``connected`` after i18n loaded."""
    assert result["afterInit"]["text"] == "Connected"
    assert result["afterInit"]["key"] == "connection.connected"


@_NEEDS_NODE
def test_a_later_language_switch_still_reaches_it(result: dict) -> None:
    assert result["afterSwitch"]["text"] == "Verbunden"


@_NEEDS_NODE
def test_an_unknown_status_still_leaves_no_key(result: dict) -> None:
    assert result["unknown"]["key"] is None
    assert result["unknown"]["text"] == "wobbly"
