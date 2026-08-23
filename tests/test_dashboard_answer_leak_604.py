"""The unauthenticated TV role must not learn the answer mid-question (#604).

``/api/quizify/ws?role=dashboard`` takes **no token of any kind** — the
handshake reads the role straight off the query string. Until this fix the
question fan-out sent that role the *admin* payload, which carries the answer
three separate ways: ``correct_answer``, a ``correct`` flag on every option, and
the true value inside ``estimate`` on estimate rounds.

That mattered because the per-player answer shuffle exists precisely to make
copying hard. One query parameter walked around all of it: a guest on the LAN,
or anyone holding the Nabu Casa URL, could open a second tab and win every
round.

Distinct from the ``is_admin: true`` join trust documented in DESIGN.md, which
deliberately grants *control* on a trusted LAN. This one leaked *answers* to an
unauthenticated role, undocumented, and strictly more than the big screen ever
renders before the reveal.

The tests below cover each of the three carriers plus the split itself, because
plugging two of three still leaves the round readable.

A note on how these were verified, since the usual "run them against the unfixed
code" step does not work here: the fix adds a new function, so on the old code
the module fails to *import* rather than failing an assertion, and an ImportError
proves nothing about the leak. The last test therefore reproduces the old call
site directly — one payload for both groups, which is exactly what
``_emit_question`` used to do — and asserts that the dashboard receives the
answer. That test passes on both sides of the fix by design: it documents the
vulnerability and pins why the call site had to change, rather than pretending
to be a regression guard.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from custom_components.quizify.server.connection import (  # noqa: E402
    ConnectionManager,
)
from custom_components.quizify.server.serializers import (  # noqa: E402
    strip_answer_for_dashboard,
)

ADMIN_QUESTION = {
    "type": "question_started",
    "question_text": "Which planet is closest to the sun?",
    "correct_answer": "Mercury",
    "answers": [
        {"text": "Venus", "correct": False},
        {"text": "Mercury", "correct": True},
        {"text": "Mars", "correct": False},
    ],
    "round_num": 1,
    "total_rounds": 5,
}

ESTIMATE_QUESTION = {
    "type": "question_started",
    "question_text": "How tall is the Eiffel Tower in metres?",
    "correct_answer": "",
    "answers": [],
    "estimate": {"min": 100, "max": 500, "unit": "m", "step": 1, "answer": 330},
}


def _fake_ws() -> MagicMock:
    ws = MagicMock()
    ws.closed = False
    ws.send_str = AsyncMock()
    return ws


def _sent_payloads(ws: MagicMock) -> list[dict]:
    return [json.loads(call.args[0]) for call in ws.send_str.call_args_list]


def test_the_stripper_removes_all_three_carriers() -> None:
    """Text, per-option flag and estimate value all have to go.

    Asserted separately rather than by comparing whole dicts: a future payload
    key would silently pass a dict comparison written today, while these three
    assertions keep naming the thing that must not be there.
    """
    stripped = strip_answer_for_dashboard(ADMIN_QUESTION)

    assert "correct_answer" not in stripped
    assert all("correct" not in a for a in stripped["answers"])
    assert strip_answer_for_dashboard(ESTIMATE_QUESTION)["estimate"] == {
        "min": 100,
        "max": 500,
        "unit": "m",
        "step": 1,
    }


def test_the_stripper_keeps_what_the_tv_renders() -> None:
    """Option texts and their canonical order survive untouched.

    The order is the #521 shuffle. If stripping reordered the tiles, the big
    screen would stop lining up with ``correct_answer_index`` at the reveal and
    with the letters the TTS narrator speaks.
    """
    stripped = strip_answer_for_dashboard(ADMIN_QUESTION)

    assert [a["text"] for a in stripped["answers"]] == ["Venus", "Mercury", "Mars"]
    assert stripped["question_text"] == ADMIN_QUESTION["question_text"]
    assert stripped["round_num"] == 1
    # The source payload must not be mutated — admins still need the full one.
    assert ADMIN_QUESTION["correct_answer"] == "Mercury"


async def test_dashboards_and_admins_receive_different_payloads() -> None:
    """The split itself: same broadcast, two different messages on the wire."""
    conn = ConnectionManager(runtime=MagicMock(), game_state_provider=lambda: None)
    admin_ws, dash_ws = _fake_ws(), _fake_ws()
    conn.add_connection(admin_ws, is_admin=True, is_dashboard=False)
    conn.add_connection(dash_ws, is_admin=False, is_dashboard=True)

    await conn.broadcast_to_admins_and_dashboards(
        ADMIN_QUESTION, dashboard_message=strip_answer_for_dashboard(ADMIN_QUESTION)
    )

    (admin_msg,) = _sent_payloads(admin_ws)
    (dash_msg,) = _sent_payloads(dash_ws)

    assert admin_msg["correct_answer"] == "Mercury"
    assert "correct_answer" not in dash_msg
    assert all("correct" not in a for a in dash_msg["answers"])
    # "Mercury" still appears as an option on the TV — it must, it is one of the
    # tiles. What must not appear is anything marking it as the right one.
    assert "Mercury" in [a["text"] for a in dash_msg["answers"]]


async def test_a_socket_registered_as_both_gets_the_full_payload_once() -> None:
    """An admin-authenticated socket that also registered as a dashboard.

    It holds a token, so it is entitled to the answer; and it must receive
    exactly one message, or the stripped copy would land second and blank the
    grid it just rendered.
    """
    conn = ConnectionManager(runtime=MagicMock(), game_state_provider=lambda: None)
    both_ws = _fake_ws()
    conn.add_connection(both_ws, is_admin=True, is_dashboard=True)

    await conn.broadcast_to_admins_and_dashboards(
        ADMIN_QUESTION, dashboard_message=strip_answer_for_dashboard(ADMIN_QUESTION)
    )

    sent = _sent_payloads(both_ws)
    assert len(sent) == 1
    assert sent[0]["correct_answer"] == "Mercury"


async def test_callers_without_a_dashboard_message_still_send_one_payload() -> None:
    """Timer ticks and the lightning question carry no answer and keep sharing.

    Guards the back-compatible default: those call sites were left untouched,
    so the single-message path has to keep behaving exactly as before.
    """
    conn = ConnectionManager(runtime=MagicMock(), game_state_provider=lambda: None)
    admin_ws, dash_ws = _fake_ws(), _fake_ws()
    conn.add_connection(admin_ws, is_admin=True, is_dashboard=False)
    conn.add_connection(dash_ws, is_admin=False, is_dashboard=True)

    tick = {"type": "timer_tick", "remaining": 12.0}
    await conn.broadcast_to_admins_and_dashboards(tick)

    assert _sent_payloads(admin_ws) == [tick]
    assert _sent_payloads(dash_ws) == [tick]


async def test_the_old_single_payload_path_is_what_leaked() -> None:
    """Reproduces the pre-fix behaviour, deliberately.

    ``_emit_question`` used to call this with the admin payload only. Feeding it
    that way still does what it always did: both groups get the same message, so
    the tokenless dashboard receives ``correct_answer`` and the ``correct`` flag
    while the question is live.

    Keeping the leak reproducible is the point. If someone later "simplifies"
    the question fan-out back to a single argument, the fix is undone silently —
    the three tests above would still pass, because they exercise the helper and
    the split rather than the call site. This one names what that call site must
    never go back to.
    """
    conn = ConnectionManager(runtime=MagicMock(), game_state_provider=lambda: None)
    dash_ws = _fake_ws()
    conn.add_connection(dash_ws, is_admin=False, is_dashboard=True)

    await conn.broadcast_to_admins_and_dashboards(ADMIN_QUESTION)

    (leaked,) = _sent_payloads(dash_ws)
    assert leaked["correct_answer"] == "Mercury"
    assert [a["correct"] for a in leaked["answers"]] == [False, True, False]
