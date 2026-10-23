"""The phone reveal card shows the settled round score, Steal included (#1118).

``player-core.js`` enriched the player's own ``all_answers`` row with the
submit-time ``answer_result`` and overwrote ``round_score`` with that frame's
``points_earned``. A Steal lands after (thief) or before (victim) that frame,
so the card read +20 / +21 while the server said 30 / 11. The server's row
already carries the settled figure; the card must keep it.

The real enrichment block is lifted out of ``player-core.js`` and run in node.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

_JS = Path(__file__).parent.parent / "custom_components/quizify/www/js"

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="node missing")


def _block() -> str:
    source = (_JS / "player-core.js").read_text("utf-8")
    start = source.index("var lastResult = game.getLastAnswerResult")
    end = source.index("reveal.updateRevealView(msg);", start)
    return source[start:end]


def _run(rows: list[dict], last_result: dict | None) -> list[dict]:
    script = f"""
var state = {{ playerName: 'Me' }};
var last = {json.dumps(last_result)};
var game = {{ getLastAnswerResult: function () {{ return last; }} }};
var msg = {{ all_answers: {json.dumps(rows)} }};
{_block()}
console.log(JSON.stringify(msg.all_answers));
"""
    out = subprocess.run(["node", "-e", script], capture_output=True, text=True)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout.strip().splitlines()[-1])


_SUBMIT = {"points_earned": 20, "speed_bonus": 3, "new_streak": 1}


def test_thief_card_keeps_the_stolen_points() -> None:
    rows = _run([{"player_name": "Me", "points_earned": 30}], _SUBMIT)
    assert rows[0]["round_score"] == 30
    assert rows[0]["speed_bonus"] == 3  # breakdown still comes from the frame


def test_victim_card_keeps_the_loss() -> None:
    frame = {**_SUBMIT, "points_earned": 21}
    rows = _run([{"player_name": "Me", "points_earned": 11}], frame)
    assert rows[0]["round_score"] == 11


def test_a_settled_zero_is_not_replaced_by_the_submit_frame() -> None:
    rows = _run([{"player_name": "Me", "points_earned": 0}], _SUBMIT)
    assert rows[0]["round_score"] == 0


def test_falls_back_to_the_submit_frame_without_a_server_score() -> None:
    rows = _run([{"player_name": "Me"}], _SUBMIT)
    assert rows[0]["round_score"] == 20
