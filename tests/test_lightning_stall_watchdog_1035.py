"""Lightning stall: fan-out diagnostics and a client watchdog (#1035).

In a live test the host page and the television froze on Lightning question 1
while the phones, served by the same broadcast, reached the recap. The cause is
unproven (half-open sockets or frozen background tabs are the candidates), so
this pins two things down rather than a fix:

* the server logs, per Lightning frame, how many admin / dashboard / player
  sockets it went to and how many sends failed, and names the role on a failed
  send;
* ``createSocket`` drops a socket that goes silent for ``STALL_TIMEOUT_MS``
  while the page says a steady stream is due, and hands over to the page's own
  reconnect path. The admin and dashboard pages arm it between a Lightning
  question and its recap.
"""

from __future__ import annotations

import json
import logging
import shutil
import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO))

from custom_components.quizify.server.connection import ConnectionManager  # noqa: E402

_JS = _REPO / "custom_components" / "quizify" / "www" / "js"
_STUB = Path(__file__).resolve().parent / "fixtures" / "dom_stub.js"
_CONN_LOGGER = "custom_components.quizify.server.connection"

_NEEDS_NODE = pytest.mark.skipif(
    shutil.which("node") is None, reason="node not installed"
)


# ---------------------------------------------------------------------------
# Server: per-role fan-out line
# ---------------------------------------------------------------------------


class _FakeRuntime:
    def __init__(self, tmp_path: Path) -> None:
        self.data_dir = tmp_path


def _socket(*, closed: bool = False, fails: bool = False) -> MagicMock:
    ws = MagicMock()
    ws.closed = closed

    async def _send(_payload: str) -> None:
        if fails:
            raise ConnectionResetError("peer gone")

    ws.send_str = _send
    return ws


@pytest.fixture
def conn(tmp_path: Path) -> ConnectionManager:
    return ConnectionManager(_FakeRuntime(tmp_path), lambda: None)


def _fanout_lines(caplog: pytest.LogCaptureFixture) -> list[str]:
    return [
        r.getMessage() for r in caplog.records if r.getMessage().startswith("Fan-out")
    ]


@pytest.mark.asyncio
async def test_lightning_tick_logs_targets_per_role(
    conn: ConnectionManager, caplog: pytest.LogCaptureFixture
) -> None:
    admin = _socket(fails=True)
    tv = _socket()
    phone = _socket()
    gone = _socket(closed=True)
    conn.add_connection(admin, is_admin=True, is_dashboard=False)
    conn.add_connection(tv, is_admin=False, is_dashboard=True)
    conn.add_connection(phone, is_admin=False, is_dashboard=False)
    conn.add_connection(gone, is_admin=False, is_dashboard=True)

    with caplog.at_level(logging.DEBUG, logger=_CONN_LOGGER):
        await conn.broadcast({"type": "lightning_tick"}, diagnose=True)

    assert _fanout_lines(caplog) == [
        "Fan-out lightning_tick: admin=1 dashboard=1 player=1; "
        "failed admin=1 dashboard=0 player=0; "
        "skipped closed admin=0 dashboard=1 player=0"
    ]


@pytest.mark.asyncio
async def test_failed_send_to_the_host_page_names_its_role(
    conn: ConnectionManager, caplog: pytest.LogCaptureFixture
) -> None:
    admin = _socket(fails=True)
    conn.add_connection(admin, is_admin=True, is_dashboard=False)

    with caplog.at_level(logging.WARNING, logger=_CONN_LOGGER):
        await conn.broadcast({"type": "lightning_tick"})

    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1
    assert "role=admin" in warnings[0].getMessage()


@pytest.mark.asyncio
async def test_admin_dashboard_fanout_logs_when_asked(
    conn: ConnectionManager, caplog: pytest.LogCaptureFixture
) -> None:
    admin = _socket()
    tv = _socket(fails=True)
    conn.add_connection(admin, is_admin=True, is_dashboard=False)
    conn.add_connection(tv, is_admin=False, is_dashboard=True)

    with caplog.at_level(logging.DEBUG, logger=_CONN_LOGGER):
        await conn.broadcast_to_admins_and_dashboards(
            {"type": "lightning_question"}, diagnose=True
        )

    assert _fanout_lines(caplog) == [
        "Fan-out lightning_question: admin=1 dashboard=1 player=0; "
        "failed admin=0 dashboard=1 player=0; "
        "skipped closed admin=0 dashboard=0 player=0"
    ]
    assert any("role=dashboard" in r.getMessage() for r in caplog.records)


@pytest.mark.asyncio
async def test_ordinary_broadcasts_stay_quiet(
    conn: ConnectionManager, caplog: pytest.LogCaptureFixture
) -> None:
    """Only the Lightning frames ask for the line; a normal game's fan-outs
    do not grow a DEBUG line each."""
    conn.add_connection(_socket(), is_admin=False, is_dashboard=False)
    with caplog.at_level(logging.DEBUG, logger=_CONN_LOGGER):
        await conn.broadcast({"type": "timer_tick"})
    assert _fanout_lines(caplog) == []


@pytest.mark.asyncio
async def test_lightning_broadcasters_ask_for_the_line() -> None:
    """The three Lightning frames the admin and TV missed in #1035."""
    from custom_components.quizify.server.broadcasters import LightningBroadcaster

    calls: list[tuple[str, dict]] = []

    class _Conn:
        async def broadcast(self, message: dict, **kw: object) -> None:
            calls.append((message["type"], kw))

        async def broadcast_to_admins_and_dashboards(
            self, message: dict, **kw: object
        ) -> None:
            calls.append((message["type"], kw))

        async def send_to_player(self, player: object, message: dict) -> bool:
            return True

    lr = MagicMock()
    lr.time_remaining.return_value = 3.0
    lr.index = 0
    lr.num_questions = 5
    lr.seconds_per_question = 15
    lr.build_recap.return_value = {}
    lr.current_question.question = "Q"
    lr.current_question.answers = []
    gs = MagicMock()
    gs.lightning = lr
    gs.get_players.return_value = []

    b = LightningBroadcaster(_Conn(), MagicMock())  # type: ignore[arg-type]
    await b.send_lightning_question(gs, lr)
    await b.send_lightning_tick(gs, lr)
    await b.send_lightning_recap(gs)

    assert calls == [
        ("lightning_question", {"diagnose": True}),
        ("lightning_tick", {"diagnose": True}),
        ("lightning_recap", {"diagnose": True}),
    ]


# ---------------------------------------------------------------------------
# Client: the stall watchdog in createSocket
# ---------------------------------------------------------------------------

_SCRIPT = """
require({stub});
var now = 0;
Date.now = function () {{ return now; }};
var intervals = [];
global.setInterval = function (fn) {{ intervals.push(fn); return intervals.length; }};
global.clearInterval = function (id) {{ intervals[id - 1] = null; }};
global.location = {{ protocol: 'http:', host: 'ha.local' }};
var sockets = [];
global.WebSocket = function (url) {{
    this.url = url; this.readyState = 1; this.closed = false;
    sockets.push(this);
}};
global.WebSocket.prototype.close = function () {{ this.closed = true; }};
QZ.load({core});

var core = window.QuizifyClientCore;
function tick(ms) {{
    now += ms;
    intervals.forEach(function (fn) {{ if (fn) fn(); }});
}}

function run(guarded, silentMs, frameEvery) {{
    intervals = []; sockets = []; now = 0;
    var closes = 0;
    core.createSocket('/api/quizify/ws?role=admin', {{
        stallGuard: function () {{ return guarded; }},
        onClose: function () {{ closes++; }}
    }});
    var ws = sockets[0];
    ws.onopen();
    for (var t = 0; t < silentMs; t += 1000) {{
        tick(1000);
        if (frameEvery && ws.onmessage) {{
            ws.onmessage({{ data: '{{"type":"lightning_tick"}}' }});
        }}
    }}
    // A late close from the dead socket must not start a second reconnect.
    if (ws.onclose) ws.onclose();
    return {{ closes: closes, socketClosed: ws.closed }};
}}

console.log(JSON.stringify({{
    timeout: core.STALL_TIMEOUT_MS,
    stalled: run(true, 6000, false),
    before: run(true, 4000, false),
    ticking: run(true, 10000, true),
    unguarded: run(false, 10000, false)
}}));
"""


@pytest.fixture(scope="module")
def watchdog() -> dict:
    script = _SCRIPT.format(
        stub=json.dumps(str(_STUB)),
        core=json.dumps(str(_JS / "client-core.js")),
    )
    out = subprocess.run(
        ["node", "-e", script], capture_output=True, text=True, check=True
    )
    return json.loads(out.stdout.strip().splitlines()[-1])


@_NEEDS_NODE
def test_window_is_five_ticks(watchdog: dict) -> None:
    assert watchdog["timeout"] == 5000


@_NEEDS_NODE
def test_silent_live_round_reconnects_once(watchdog: dict) -> None:
    assert watchdog["stalled"] == {"closes": 1, "socketClosed": True}


@_NEEDS_NODE
def test_silence_shorter_than_the_window_is_left_alone(watchdog: dict) -> None:
    # Only the explicit late onclose at the end counts.
    assert watchdog["before"] == {"closes": 1, "socketClosed": False}


@_NEEDS_NODE
def test_a_ticking_round_is_left_alone(watchdog: dict) -> None:
    assert watchdog["ticking"] == {"closes": 1, "socketClosed": False}


@_NEEDS_NODE
def test_an_unguarded_page_is_left_alone(watchdog: dict) -> None:
    assert watchdog["unguarded"] == {"closes": 1, "socketClosed": False}


# ---------------------------------------------------------------------------
# Pages: admin and dashboard arm the watchdog for the live Lightning stretch
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("page", "flag"),
    [("admin.js", "_lightningLive"), ("dashboard.js", "lightningLive")],
)
def test_pages_arm_the_watchdog_for_lightning(page: str, flag: str) -> None:
    src = (_JS / page).read_text(encoding="utf-8")
    assert "stallGuard: function () {" in src
    assert f"return {flag} && currentPhase === 'LIGHTNING';" in src
    # Armed by the question and the tick, disarmed by the silent splash.
    assert src.count(f"{flag} = true;") >= 2
    assert f"{flag} = false;" in src

