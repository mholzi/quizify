"""#698 — the television has to show the Hot Seat, including its outcome.

Two faults, both seen on a real 720p television on 03.09.2026.

The round indicator read "QUESTION UNDEFINED / UNDEFINED" for the whole answer
window: ``handleHotSeatQuestion`` forwards ``msg.round_num`` and
``msg.total_rounds`` into the normal question renderer, and the live payload
carried neither, so ``i18n.js`` interpolated the literal ``undefined``. Only a
television that *reconnected* showed the right numbers, because the snapshot
path passes them.

And ``hot_seat_result`` had no consumer anywhere: not on the phone, not on the
board, not on the admin page. After the seat holder answered, the television
stayed on the question with the timer at zero until the host advanced — so the
room never learned whether the chair had paid off, which is the one thing
everybody was watching for.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
WEBSOCKET = REPO / "custom_components" / "quizify" / "server" / "websocket.py"
DASHBOARD = REPO / "custom_components" / "quizify" / "www" / "dashboard.html"
I18N = REPO / "custom_components" / "quizify" / "www" / "i18n"


def _payload_block(source: str, message_type: str) -> str:
    """The dict literal that carries ``"type": "<message_type>"``."""
    marker = f'"type": "{message_type}"'
    start = source.index(marker)
    open_brace = source.rindex("{", 0, start)
    depth, j = 0, open_brace
    while True:
        if source[j] == "{":
            depth += 1
        elif source[j] == "}":
            depth -= 1
            if depth == 0:
                return source[open_brace : j + 1]
        j += 1


def test_every_hot_seat_broadcast_carries_the_round_numbers() -> None:
    """Written against all three, not just the one that was reported.

    The question broadcast is what printed "undefined"; the auction silently
    kept the previous round's number, and the result would have inherited
    whatever the frame had. A television is a single header — if any of the
    three omits them, the detour lies about where the game is.
    """
    source = WEBSOCKET.read_text(encoding="utf-8")
    for message_type in ("hot_seat_auction", "hot_seat_question", "hot_seat_result"):
        block = _payload_block(source, message_type)
        assert '"round_num"' in block, f"{message_type} has no round_num: {block}"
        assert '"total_rounds"' in block, f"{message_type} has no total_rounds"


def test_the_television_renders_the_hot_seat_outcome() -> None:
    html = DASHBOARD.read_text(encoding="utf-8")
    assert "case 'hot_seat_result':" in html, (
        "the settlement still has no case in the dashboard switch"
    )
    assert "function handleHotSeatResult(" in html


def test_the_outcome_distinguishes_a_wrong_answer_from_a_timeout() -> None:
    """They cost the same points and mean opposite things about the room.

    #653 made an unanswered chair cost its stake, which is exactly why the
    board may not report silence as a wrong answer.
    """
    html = DASHBOARD.read_text(encoding="utf-8")
    start = html.index("function handleHotSeatResult(")
    body = html[start : html.index("\n        }", start)]
    for key in ("hotSeat.resultRight", "hotSeat.resultWrong", "hotSeat.resultTimeout"):
        assert key in body, f"{key} is not used in the result renderer"

    for locale in ("de", "en", "es"):
        strings = json.loads((I18N / f"{locale}.json").read_text(encoding="utf-8"))
        hot_seat = strings["hotSeat"]
        for key in ("resultRight", "resultWrong", "resultTimeout"):
            assert key in hot_seat, f"{locale}.json is missing hotSeat.{key}"
            assert re.search(r"\{name\}", hot_seat[key]), (
                f"{locale}.json hotSeat.{key} does not name the player"
            )
