"""Phone signals: tick only for who still answers, one toast at a time (#1185).

P4: the last-five-seconds tick ran on every phone, answered or not — up to 30
offset ticks in five seconds at people who could no longer do anything. It is
gated on "this phone has not answered" now, and the spoken countdown ships off.

P5: every toast wrote into one element and a second toast inherited the first
one's fade-out timer, so it vanished early. Now there is a short queue: one
toast at a time, each with its whole duration, a repeat only restarts the clock.
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
_JS = _WWW / "js"
_STUB = Path(__file__).resolve().parent / "fixtures" / "dom_stub.js"

_NEEDS_NODE = pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")


def test_every_tick_goes_through_the_answered_gate() -> None:
    source = (_JS / "player-game.js").read_text("utf-8")
    calls = re.findall(r"tickFromRemaining\(", source)
    assert len(calls) == 1, "one call site, inside _tick"
    body = source[source.index("function _tick(remaining) {") :]
    body = body[: body.index("}") + 1]
    assert "_hasAnswered()" in body and "tickFromRemaining" in body


def test_the_spoken_countdown_ships_off() -> None:
    admin = (_JS / "admin.js").read_text("utf-8")
    assert re.search(r"announce_countdown:\s*false", admin)
    html = (_WWW / "admin.html").read_text("utf-8")
    assert '<input type="checkbox" id="tts-announce-countdown">' in html


_TOAST = """
require({stub});
QZ.load({utils});
QZ.load({client});
QZ.load({pu});
var toast = QZ.el('error-toast');
var pu = window.QuizifyPlayerUtils;
var seen = [];
function look(tag) {{ seen.push([tag, toast.style.opacity === '1' ? String(toast.textContent) : '']); }}
pu.showToast('A', 120);
pu.showToast('B', 120);
pu.showToast('B', 120);  // queued once, not twice
look('t0');
setTimeout(function () {{ look('t80'); }}, 80);
setTimeout(function () {{ look('t300'); }}, 300);   // A fading
setTimeout(function () {{ look('t500'); }}, 500);   // B up, full duration
setTimeout(function () {{ look('t1100'); pu.showToast('C', 120); pu.clearToast(); look('cleared');
    console.log(JSON.stringify(seen)); }}, 1100);
"""


@_NEEDS_NODE
def test_toasts_queue_and_each_gets_its_time() -> None:
    script = _TOAST.format(
        stub=json.dumps(str(_STUB)),
        utils=json.dumps(str(_JS / "utils.js")),
        client=json.dumps(str(_JS / "client-core.js")),
        pu=json.dumps(str(_JS / "player-utils.js")),
    )
    out = subprocess.run(["node", "-e", script], capture_output=True, text=True, check=False)
    assert out.returncode == 0, out.stderr
    seen = dict(json.loads(out.stdout.strip().splitlines()[-1]))
    assert seen["t0"] == "A"
    assert seen["t80"] == "A"
    assert seen["t300"] == ""
    assert seen["t500"] == "B"
    assert seen["t1100"] == ""  # nothing left: B was queued once
    assert seen["cleared"] == ""
