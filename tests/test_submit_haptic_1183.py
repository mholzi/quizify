"""A 10 ms tick when the phone's own move leaves — Android only (#1183).

Decision 4 of the interaction review: ``navigator.vibrate(10)`` on submit,
feature-detected, because iOS Safari has no vibrate at all. Nothing else on
the phone vibrates (no host steps, no reactions), and a send that never left
the phone does not pretend it did.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

_JS = Path(__file__).resolve().parent.parent / "custom_components" / "quizify" / "www" / "js"
_STUB = Path(__file__).resolve().parent / "fixtures" / "dom_stub.js"
_NEEDS_NODE = pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")

_SCRIPT = """
require(%(stub)s);
QZ.load(%(utils)s);
var pu = window.QuizifyPlayerUtils;
var out = {};
window.navigator = {};
out.iosNoop = pu.submitHaptic() === false;
var calls = [];
window.navigator = { vibrate: function (ms) { calls.push(ms); return true; } };
out.android = pu.submitHaptic() === true;
window.navigator = { vibrate: function () { throw new Error('blocked'); } };
out.throwSurvives = pu.submitHaptic() === false;
out.calls = calls;
console.log(JSON.stringify(out));
"""


@_NEEDS_NODE
def test_vibrate_is_feature_detected_and_10_ms() -> None:
    script = _SCRIPT % {
        "stub": json.dumps(str(_STUB)),
        "utils": json.dumps(str(_JS / "player-utils.js")),
    }
    run = subprocess.run(["node", "-e", script], capture_output=True, text=True, check=True)
    out = json.loads(run.stdout.strip().splitlines()[-1])
    assert out == {"iosNoop": True, "android": True, "throwSurvives": True, "calls": [10]}


def test_only_sent_submits_tick() -> None:
    core = (_JS / "player-core.js").read_text("utf-8")
    send = core[core.index("    function send(type, payload) {") :]
    send = send[: send.index("\n    }\n")]
    tick = send.index("pu.submitHaptic()")
    assert send.index("state.ws.send(") < tick < send.index("return true;"), (
        "the tick belongs after the frame left, inside the open-socket branch"
    )
    types = send[send.index("var SUBMIT_TYPES = ") :]
    types = types[: types.index(";")]
    for t in ("submit_answer", "submit_wager", "hot_seat_bid", "hot_seat_bet",
              "lightning_answer", "use_powerup"):
        assert t in types
    for t in ("admin_skip", "next_round", "reaction", "end_game"):
        assert t not in types
    assert core.count("vibrate") == 0, "player-utils.js owns the one vibrate call"
