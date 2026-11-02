"""The hot-seat bet is chosen in two steps, then placed (#1183, draft ④).

"They'll get it" and "They won't" used to pick the side AND place the bet in
one tap, at the slider's preset 20 %. A spectator who only wanted to read which
side was which had already bet, the two opposite buttons sat about 4 px apart,
and nothing could be taken back. Now a side button only chooses, the slider
sets the amount, and "Place bet" (approved new text) sends. Run for real.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

_WWW = Path(__file__).resolve().parent.parent / "custom_components" / "quizify" / "www"
_JS = _WWW / "js"
_STUB = Path(__file__).resolve().parent / "fixtures" / "dom_stub.js"
_NEEDS_NODE = pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")

_SCRIPT = """
require(%(stub)s);
QZ.serveI18n(%(i18n)s);
QZ.load(%(i18njs)s);
QZ.els(['hotseat-panel', 'hotseat-title', 'hotseat-hint', 'hotseat-bid-stage',
        'hotseat-bet-stage', 'hotseat-result-stage', 'hotseat-bet-slider',
        'hotseat-bet-value', 'hotseat-bet-will', 'hotseat-bet-wont', 'hotseat-bet-place',
        'hotseat-slider', 'hotseat-value', 'hotseat-bank', 'hotseat-bid-btn',
        'hotseat-bid-count', 'question-text', 'question-category',
        'answer-buttons', 'answers-container', 'estimate-container']);
QZ.load(%(utils)s);
QZ.load(%(render_shared)s);
QZ.load(%(player_utils)s);
QZ.load(%(player_game)s);
QZ.load(%(hotseat)s);
var sent = [];
window.QuizifyPlayer = { send: function (type, p) { sent.push([type, p]); return true; } };
var H = window.QuizifyPlayerHotSeat;
var $ = function (id) { return document.getElementById(id); };

(async function () {
    await window.QuizifyI18n.init('en');
    window.QuizifyPlayerUtils.state.playerName = 'Cleo';
    H.reset();
    H.handleQuestion({ winner: 'Ben', you_are_seated: false, score: 200,
        question: { text: 'Q', answers: ['a', 'b'] } });
    var out = {};
    out.placeOffAtFirst = !!$('hotseat-bet-place').disabled;
    $('hotseat-bet-will').click();
    out.sideTapSendsNothing = sent.length === 0;
    out.willPressed = $('hotseat-bet-will').getAttribute('aria-pressed') === 'true';
    $('hotseat-bet-wont').click();
    out.switchedSide = $('hotseat-bet-wont').getAttribute('aria-pressed') === 'true'
        && $('hotseat-bet-will').getAttribute('aria-pressed') === 'false';
    out.placeOn = !$('hotseat-bet-place').disabled;
    $('hotseat-bet-slider').value = '40';
    $('hotseat-bet-place').click();
    out.sent = sent;
    out.locked = !!$('hotseat-bet-place').disabled && !!$('hotseat-bet-will').disabled
        && !!$('hotseat-bet-slider').disabled;
    $('hotseat-bet-place').click();
    out.sentOnce = sent.length === 1;
    console.log(JSON.stringify(out));
})();
"""


@_NEEDS_NODE
def test_a_side_tap_chooses_and_only_place_bet_sends() -> None:
    script = _SCRIPT % {
        "stub": json.dumps(str(_STUB)),
        "i18n": json.dumps(str(_WWW / "i18n")),
        "i18njs": json.dumps(str(_JS / "i18n.js")),
        "utils": json.dumps(str(_JS / "utils.js")),
        "render_shared": json.dumps(str(_JS / "render-shared.js")),
        "player_utils": json.dumps(str(_JS / "player-utils.js")),
        "player_game": json.dumps(str(_JS / "player-game.js")),
        "hotseat": json.dumps(str(_JS / "player-hotseat.js")),
    }
    run = subprocess.run(["node", "-e", script], capture_output=True, text=True, check=True)
    out = json.loads(run.stdout.strip().splitlines()[-1])
    assert out["placeOffAtFirst"], "nothing to place before a side is chosen"
    assert out["sideTapSendsNothing"], "a side tap is a choice, not a bet"
    assert out["willPressed"] and out["switchedSide"]
    assert out["placeOn"]
    assert out["sent"] == [["hot_seat_bet", {"side": "wont", "bet": 40}]]
    assert out["locked"] and out["sentOnce"]


def test_the_markup_orders_side_amount_place() -> None:
    html = (_WWW / "player.html").read_text("utf-8")
    stage = html[html.index('id="hotseat-bet-stage"') :]
    stage = stage[: stage.index("</div>\n\n") if "</div>\n\n" in stage else 2000]
    assert stage.index('id="hotseat-bet-will"') < stage.index('id="hotseat-bet-slider"')
    assert stage.index('id="hotseat-bet-slider"') < stage.index('id="hotseat-bet-place"')
    assert 'aria-pressed="false"' in stage
    de = json.loads((_WWW / "i18n" / "de.json").read_text("utf-8"))
    assert de["hotSeat"]["betPlace"] == "Wette setzen"


def test_every_slider_gets_the_shared_thumb() -> None:
    css = (_WWW / "css" / "src" / "07-player.css").read_text("utf-8")
    for slider in ("#wager-slider", "#hotseat-slider", "#hotseat-bet-slider"):
        assert f"{slider}::-webkit-slider-thumb" in css
        assert f"{slider}::-moz-range-thumb" in css
    block = css[css.index(".estimate-slider,\n#wager-slider,") :][:300]
    assert "height: 44px" in block, "44 px to hit, 34 px to see"
