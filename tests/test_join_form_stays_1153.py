"""A phone that has not joined stays on the join form (#1153).

Every broadcast frame (round summary, snapshot, finale, lightning) used to
switch the view regardless of whether the phone had a name, so a guest who
scanned the QR mid-round was pulled off the form. ``showView`` now ignores the
in-game screens while ``state.playerName`` is unset. Runs the real
player-utils.js under node.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parent.parent
_JS = _REPO / "custom_components" / "quizify" / "www" / "js"
_STUB = Path(__file__).resolve().parent / "fixtures" / "dom_stub.js"

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="node missing")

_SCRIPT = """
require({stub});
var VIEWS = ['loading-view','not-found-view','ended-view','in-progress-view',
  'join-view','lobby-view','game-view','reveal-view','paused-view','end-view',
  'connection-lost-view','kicked-view','lightning-splash-view','lightning-view',
  'lightning-recap-view'];
VIEWS.forEach(function (id) {{ QZ.el(id).classList.add('hidden'); }});
QZ.load({utils});
var pu = window.QuizifyPlayerUtils;
var GAME = ['lobby-view','game-view','reveal-view','paused-view','end-view',
  'lightning-splash-view','lightning-view','lightning-recap-view'];
var out = {{}};

function active() {{
  return VIEWS.filter(function (id) {{
    return QZ.el(id).classList.contains('active');
  }});
}}

// 1. Nameless phone on the join form: every game screen is ignored.
pu.state.playerName = null;
pu.showView('join-view');
out.ignored = GAME.map(function (id) {{ pu.showView(id); return active().join(); }});

// 2. Pending join / restored session: the name is set, so frames land.
pu.state.playerName = 'Anna';
out.joined = GAME.map(function (id) {{ pu.showView(id); return active().join(); }});

// 3. Kick: the name is cleared first, kicked-view must still show.
pu.state.playerName = null;
pu.showView('kicked-view');
out.kicked = active().join();

// 4. Rejoin path: back to the join form works nameless.
pu.showView('join-view');
out.rejoin = active().join();
console.log(JSON.stringify(out));
"""


def _run() -> dict:
    script = _SCRIPT.format(
        stub=json.dumps(str(_STUB)), utils=json.dumps(str(_JS / "player-utils.js"))
    )
    out = subprocess.run(
        ["node", "-e", script], capture_output=True, text=True, check=True
    )
    return json.loads(out.stdout)


def test_nameless_phone_stays_on_join_form() -> None:
    assert set(_run()["ignored"]) == {"join-view"}


def test_joined_or_pending_phone_follows_the_game() -> None:
    result = _run()["joined"]
    assert result == [
        "lobby-view", "game-view", "reveal-view", "paused-view", "end-view",
        "lightning-splash-view", "lightning-view", "lightning-recap-view",
    ]


def test_kick_and_rejoin_screens_still_show_without_a_name() -> None:
    result = _run()
    assert result["kicked"] == "kicked-view"
    assert result["rejoin"] == "join-view"
