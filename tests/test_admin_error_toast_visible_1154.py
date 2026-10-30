"""The host page error toast must actually be visible (#1154).

``admin.html`` ships ``<div id="error-toast" class="toast hidden">``.
``showErrorToast`` only styled the element when it was missing, so on the real
page it set text and opacity and never took ``hidden`` off; ``.hidden`` is
``display: none !important``. Every refusal, the "Reconnecting" line and the
preset-save fallback were invisible.

These run the real ``showErrorToast`` and ``send`` from ``admin.js`` under node
against the ``error-toast`` element exactly as ``admin.html`` ships it.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parent.parent
_WWW = _REPO / "custom_components" / "quizify" / "www"
_ADMIN_JS = _WWW / "js" / "admin.js"
_ADMIN_HTML = _WWW / "admin.html"
_STUB = Path(__file__).resolve().parent / "fixtures" / "dom_stub.js"

_NEEDS_NODE = pytest.mark.skipif(
    shutil.which("node") is None, reason="node not installed"
)


def _block(source: str, signature: str) -> str:
    """One brace-balanced declaration, or '' when it is absent."""
    if signature not in source:
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


def _toast_classes() -> list[str]:
    html = _ADMIN_HTML.read_text("utf-8")
    tag = re.search(r'<[^>]*\bid="error-toast"[^>]*>', html)
    assert tag, "admin.html must still ship #error-toast"
    cls = re.search(r'class="([^"]*)"', tag.group(0))
    return cls.group(1).split() if cls else []


_SCRIPT = r"""
require(%(stub)s);
var toast = QZ.el('error-toast');
%(classes)s.forEach(function (c) { toast.classList.add(c); });

var timers = [];
global.setTimeout = function (fn) { timers.push(fn); return timers.length; };
global.clearTimeout = function () {};

// A refusing double: only a socket that is OPEN takes a frame.
var OPEN = 1;
var ws = { readyState: 3, sent: [], send: function (d) { this.sent.push(d); } };
global.WebSocket = { OPEN: OPEN };
var _notHost = false, NOT_HOST_SENDABLE = {};
function _t(key) { return key; }

%(code)s

function shown() {
    return !toast.classList.contains('hidden') && toast.style.opacity === '1';
}
var R = {};

showErrorToast('Admin only refused');
R.directShown = shown();
R.directText = toast.textContent;

toast.classList.add('hidden'); toast.style.opacity = '0'; toast.textContent = '';
send('start_game', {});
R.closedTap = { shown: shown(), text: toast.textContent, sent: ws.sent.length };

toast.classList.add('hidden'); toast.style.opacity = '0'; toast.textContent = '';
send('set_language', { language: 'de' }, { quiet: true });
R.closedPush = { shown: shown(), text: toast.textContent };

ws.readyState = OPEN;
send('start_game', {});
R.openTap = { shown: shown(), sent: ws.sent.length };
console.log(JSON.stringify(R));
"""


def _run() -> dict:
    source = _ADMIN_JS.read_text("utf-8")
    code = "\n".join(
        [
            _block(source, "function showErrorToast(message) {"),
            _block(source, "function send(type, payload, opts) {")
            or _block(source, "function send(type, payload) {"),
            "function _LOG_UNDELIVERED() {}",
        ]
    )
    # Module-level constants showErrorToast may use.
    consts = "\n".join(
        m.group(0)
        for m in re.finditer(
            r"^    var (?:ERROR_TOAST_STYLE|_errorToastTimer)\b[\s\S]*?;\n",
            source,
            re.M,
        )
    )
    script = _SCRIPT % {
        "stub": json.dumps(str(_STUB)),
        "classes": json.dumps(_toast_classes()),
        "code": consts + "\n" + code,
    }
    out = subprocess.run(
        ["node", "-e", script], capture_output=True, text=True, check=True
    )
    return json.loads(out.stdout)


@_NEEDS_NODE
def test_the_toast_is_visible_on_the_page_as_it_ships() -> None:
    assert "hidden" in _toast_classes(), "premise: the page ships it hidden"
    r = _run()
    assert r["directShown"] is True
    assert r["directText"] == "Admin only refused"


@_NEEDS_NODE
def test_a_tap_on_a_closed_socket_tells_the_host() -> None:
    r = _run()["closedTap"]
    assert r["sent"] == 0
    assert r["shown"] is True
    assert r["text"] == "connection.reconnecting"


@_NEEDS_NODE
def test_a_lobby_push_on_a_closed_socket_stays_quiet() -> None:
    """Pushes are replayed by onopen; they are not a failed host action."""
    r = _run()["closedPush"]
    assert r["shown"] is False
    assert r["text"] == ""


@_NEEDS_NODE
def test_a_delivered_send_shows_no_toast() -> None:
    r = _run()["openTap"]
    assert r["sent"] == 1
    assert r["shown"] is False
