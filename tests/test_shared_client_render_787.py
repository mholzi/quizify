"""The shared core and renderers behave the way the three copies did (#787).

``client-core.js`` and ``render-shared.js`` replaced code that existed two or
three times over. A refactor like that is only safe if "the same" can be
demonstrated, and the source no longer contains the thing to compare against —
so what is pinned here is the *output*, taken from the implementations that
were merged: the exact markup each surface used to build, the exact blur value
each used to write, the exact retry curve, and the one non-obvious rule about
the message handler running inside the parse ``try``.

These call the real modules under node, through ``tests/fixtures/dom_stub.js``
(#826), rather than reading the source for shapes. A test that greps for
``new Image()`` cannot tell you the leaderboard still renders the same row.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parent.parent
_JS = _REPO / "custom_components" / "quizify" / "www" / "js"
_STUB = _REPO / "tests" / "fixtures" / "dom_stub.js"

CLIENT_CORE = _JS / "client-core.js"
RENDER_SHARED = _JS / "render-shared.js"
UTILS = _JS / "utils.js"


def _require_node() -> None:
    if shutil.which("node") is None:
        pytest.skip("node is not installed")


def _run(script: str, tmp_path: Path) -> dict:
    """Run a snippet with the stub DOM installed; it prints one JSON blob."""
    _require_node()
    harness = tmp_path / "shared-harness.js"
    harness.write_text(
        f"require({json.dumps(str(_STUB))});\n"
        f"QZ.load({json.dumps(str(UTILS))});\n"
        f"QZ.load({json.dumps(str(CLIENT_CORE))});\n"
        f"QZ.load({json.dumps(str(RENDER_SHARED))});\n" + script,
        encoding="utf-8",
    )
    result = subprocess.run(
        ["node", str(harness)], capture_output=True, text=True, check=False
    )
    assert result.returncode == 0, f"node harness failed:\n{result.stderr}"
    return json.loads(result.stdout)


# ---------------------------------------------------------------------------
# render-shared.js — the leaderboard row
# ---------------------------------------------------------------------------

# The three surfaces built these strings. Written out in full rather than
# assembled from the same pieces the implementation uses, because a helper
# shared between the code and its test proves nothing about either.
_ROWS = [
    {"name": "Anna", "score": 12, "streak": 3},
    {"name": "B<b>", "score": 7},
]

_BOARD_ROW_HTML = (
    '<div class="leaderboard-row">'
    '<span class="leaderboard-rank rank-1">1</span>'
    '<span class="leaderboard-name">Anna</span>'
    '<span class="leaderboard-score">12</span>'
    '<span class="leaderboard-delta is-up">+5</span>'
    '<span class="leaderboard-streak">3x</span>'
    "</div>"
    '<div class="leaderboard-row">'
    '<span class="leaderboard-rank rank-2">2</span>'
    '<span class="leaderboard-name">B&lt;b&gt;</span>'
    '<span class="leaderboard-score">7</span>'
    "</div>"
)

_PHONE_ROW_HTML = (
    '<div class="leaderboard-row">'
    '<span class="leaderboard-rank rank-1">1</span>'
    '<span class="leaderboard-name">Anna<span class="you-badge">(you)</span></span>'
    '<span class="leaderboard-score">12</span>'
    '<span class="leaderboard-streak">3x</span>'
    "</div>"
    '<div class="leaderboard-row">'
    '<span class="leaderboard-rank rank-2">2</span>'
    '<span class="leaderboard-name">B&lt;b&gt;</span>'
    '<span class="leaderboard-score">7</span>'
    "</div>"
)


def test_the_board_row_is_the_row_the_two_boards_built(tmp_path: Path) -> None:
    """Television and host page: name, score, steal chip, streak — in that order."""
    out = _run(
        f"""
const R = window.QuizifyRenderShared;
const rows = {json.dumps(_ROWS)};
console.log(JSON.stringify({{
  html: R.leaderboardRowsHtml(rows, {{
    afterScore: function (p) {{
      return p.name === 'Anna'
        ? '<span class="leaderboard-delta is-up">+5</span>' : '';
    }}
  }})
}}));
""",
        tmp_path,
    )
    assert out["html"] == _BOARD_ROW_HTML


def test_the_phone_row_still_carries_its_you_badge(tmp_path: Path) -> None:
    """#625: the badge sits inside the name span, not after it."""
    out = _run(
        f"""
const R = window.QuizifyRenderShared;
const rows = {json.dumps(_ROWS)};
console.log(JSON.stringify({{
  html: R.leaderboardRowsHtml(rows, {{
    nameSuffix: function (p) {{
      return p.name === 'Anna'
        ? '<span class="you-badge">(you)</span>' : '';
    }}
  }})
}}));
""",
        tmp_path,
    )
    assert out["html"] == _PHONE_ROW_HTML


def test_a_surface_that_hangs_nothing_off_the_row_gets_the_bare_row(
    tmp_path: Path,
) -> None:
    """The default has to be *nothing*, or one surface grows the other's badge."""
    out = _run(
        f"""
const R = window.QuizifyRenderShared;
console.log(JSON.stringify({{ html: R.leaderboardRowsHtml({json.dumps(_ROWS)}) }}));
""",
        tmp_path,
    )
    assert "you-badge" not in out["html"]
    assert "leaderboard-delta" not in out["html"]
    assert out["html"].count('class="leaderboard-row"') == 2


def test_the_name_is_escaped_and_the_score_is_not_quoted(tmp_path: Path) -> None:
    """The row takes a player-chosen name straight off the wire."""
    out = _run(
        """
const R = window.QuizifyRenderShared;
console.log(JSON.stringify({
  html: R.leaderboardRowsHtml([{ name: '<img src=x onerror=alert(1)>', score: 1 }])
}));
""",
        tmp_path,
    )
    assert "<img" not in out["html"]
    assert "&lt;img" in out["html"]


# ---------------------------------------------------------------------------
# render-shared.js — the steal chip
# ---------------------------------------------------------------------------


def test_the_steal_chip_reads_the_way_both_boards_read_it(tmp_path: Path) -> None:
    """Up is a plus, down is a real minus sign, and the sign is not doubled."""
    out = _run(
        """
const R = window.QuizifyRenderShared;
let repaints = 0;
const d = R.createScoreDeltas({ repaint: function () { repaints++; } });
d.show([{ name: 'Anna', points: 40 }, { name: 'Ben', points: -40 }]);
console.log(JSON.stringify({
  up: d.html('Anna'),
  down: d.html('Ben'),
  absent: d.html('Cara'),
  repaints: repaints
}));
""",
        tmp_path,
    )
    assert out["up"] == '<span class="leaderboard-delta is-up">+40</span>'
    assert out["down"] == '<span class="leaderboard-delta is-down">−40</span>'
    assert out["absent"] == ""
    assert out["repaints"] == 1, "showing the chips must repaint the rows at once"


def test_the_chips_come_off_after_the_hold_and_repaint_again(tmp_path: Path) -> None:
    """game_state repaints every few seconds and would otherwise wipe them."""
    out = _run(
        """
const R = window.QuizifyRenderShared;
const timers = [];
global.setTimeout = function (fn, ms) {
  timers.push({ fn: fn, ms: ms });
  return timers.length;
};
global.clearTimeout = function () {};
let repaints = 0;
const d = R.createScoreDeltas({ repaint: function () { repaints++; } });
d.show([{ name: 'Anna', points: 40 }]);
const held = d.html('Anna');
const ms = timers[0].ms;
timers[0].fn();
console.log(JSON.stringify({
  held: held, after: d.html('Anna'), ms: ms, repaints: repaints
}));
""",
        tmp_path,
    )
    assert out["held"] != ""
    assert out["after"] == "", "the chip outlived its hold"
    assert out["ms"] == 4000, "the hold changed length"
    assert out["repaints"] == 2, "the expiry must repaint too, or the chip stays drawn"


# ---------------------------------------------------------------------------
# render-shared.js — the power-up sentence
# ---------------------------------------------------------------------------


def test_the_sentence_is_one_unit_with_the_callers_class_names(
    tmp_path: Path,
) -> None:
    """The board and the host page style the two spans differently."""
    out = _run(
        """
const R = window.QuizifyRenderShared;
const spec = R.POWERUP_SPECS.steal;
const vars = { source: 'Anna', target: 'Ben', points: 40 };
console.log(JSON.stringify({
  tv: R.powerUpSentenceHtml(spec, vars, {
    name: 'dashboard-powerup-name', points: 'dashboard-powerup-points'
  }),
  host: R.powerUpSentenceHtml(spec, vars, {
    name: 'powerup-banner-name', points: 'powerup-banner-points'
  })
}));
""",
        tmp_path,
    )
    assert out["tv"] == (
        '<span class="dashboard-powerup-name">Anna</span> stole '
        '<span class="dashboard-powerup-points">40</span> points from '
        '<span class="dashboard-powerup-name">Ben</span>'
    )
    assert out["host"] == (
        '<span class="powerup-banner-name">Anna</span> stole '
        '<span class="powerup-banner-points">40</span> points from '
        '<span class="powerup-banner-name">Ben</span>'
    )


def test_half_a_sentence_is_not_shown_at_all(tmp_path: Path) -> None:
    """A half sentence is worse than silence — the rule both boards had."""
    out = _run(
        """
const R = window.QuizifyRenderShared;
const banners = [];
const deltas = [];
const handle = R.createPowerUpApplied({
  showBanner: function (spec, vars) { banners.push(vars); },
  showScoreDeltas: function (list) { deltas.push(list); }
});
handle({ powerup_type: 'freeze', source_player: 'Anna' });      // no target
handle({ powerup_type: 'time_boost', source_player: 'A', target_player: 'B' });
handle({ powerup_type: 'freeze', source_player: 'Anna', target_player: 'Ben' });
handle({ powerup_type: 'steal', source_player: 'Anna', target_player: 'Ben',
         stolen_points: -40 });
console.log(JSON.stringify({ banners: banners, deltas: deltas }));
""",
        tmp_path,
    )
    assert len(out["banners"]) == 2, (
        "a half sentence, or a power-up that only changes the user's own turn, "
        "reached the screen"
    )
    assert out["deltas"] == [
        [{"name": "Anna", "points": 40}, {"name": "Ben", "points": -40}]
    ], "only a steal moves rows, and the magnitude is taken absolute"


# ---------------------------------------------------------------------------
# render-shared.js — the progressive reveal
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "max_blur,ids",
    [
        (28, ["question-image"]),  # the television
        (14, ["question-image", "image-zoom-img"]),  # the phone, plus its zoom
    ],
)
def test_the_blur_follows_the_clock_on_both_canvases(
    tmp_path: Path, max_blur: int, ids: list[str]
) -> None:
    """Full at the start, zero when the clock runs out, two decimals throughout.

    The zoom overlay carries the blur too, or one tap on the magnifier defeats
    the whole round.
    """
    out = _run(
        f"""
const R = window.QuizifyRenderShared;
const ids = {json.dumps(ids)};
const seen = {{}};
function bin(id) {{ return (seen[id] = seen[id] || {{}}); }}
const els = ids.map(function (id) {{
  const el = QZ.el(id);
  el.style.setProperty = function (n, v) {{ bin(id)[n] = v; }};
  el.style.removeProperty = function (n) {{ bin(id)[n] = null; }};
  return el;
}});
const rev = R.createProgressiveReveal({{
  maxBlurPx: {max_blur},
  targets: function () {{
    return ids.map(function (id) {{ return document.getElementById(id); }});
  }}
}});
const before = JSON.parse(JSON.stringify(seen));
rev.set(10, 20);                       // not armed yet: nothing must move
const stillBefore = JSON.parse(JSON.stringify(seen));
rev.arm(20);
const armed = JSON.parse(JSON.stringify(seen));
rev.set(10, 20);
const half = JSON.parse(JSON.stringify(seen));
rev.set(0, 20);
const done = JSON.parse(JSON.stringify(seen));
rev.clear();
const cleared = JSON.parse(JSON.stringify(seen));
console.log(JSON.stringify({{
  before: before, stillBefore: stillBefore, armed: armed, half: half,
  done: done, cleared: cleared,
  classes: els.map(function (el) {{
    return el.classList.contains('progressive-reveal');
  }}),
  active: rev.isActive()
}}));
""",
        tmp_path,
    )
    assert out["stillBefore"] == out["before"], (
        "a tick before the picture was armed wrote a blur onto a sharp image"
    )
    for element_id in ids:
        assert out["armed"][element_id]["--reveal-blur"] == f"{max_blur}px"
        assert out["half"][element_id]["--reveal-blur"] == f"{max_blur / 2:.2f}px"
        assert out["done"][element_id]["--reveal-blur"] == "0.00px"
        assert out["cleared"][element_id]["--reveal-blur"] is None
    assert out["classes"] == [False] * len(ids), "the class survived the clear"
    assert out["active"] is False


def test_the_phone_can_leave_the_duration_where_it_armed_it(tmp_path: Path) -> None:
    """The television passes the duration on every tick; the phone does not."""
    out = _run(
        """
const R = window.QuizifyRenderShared;
const seen = {};
const el = QZ.el('question-image');
el.style.setProperty = function (name, value) { seen[name] = value; };
const rev = R.createProgressiveReveal({
  maxBlurPx: 14,
  targets: function () { return [document.getElementById('question-image')]; }
});
rev.arm(20);
rev.set(5);
console.log(JSON.stringify({ blur: seen['--reveal-blur'] }));
""",
        tmp_path,
    )
    assert out["blur"] == "3.50px", "the armed duration was not remembered"


def test_a_missing_element_is_a_no_op_not_a_crash(tmp_path: Path) -> None:
    """The television has one image; the zoom overlay only exists on the phone."""
    out = _run(
        """
const R = window.QuizifyRenderShared;
const rev = R.createProgressiveReveal({
  maxBlurPx: 28,
  targets: function () { return [null, undefined]; }
});
rev.arm(10); rev.set(5, 10); rev.clear();
console.log(JSON.stringify({ ok: true }));
""",
        tmp_path,
    )
    assert out["ok"] is True


# ---------------------------------------------------------------------------
# render-shared.js — the preload
# ---------------------------------------------------------------------------


def test_the_preload_is_detached_and_sanitised(tmp_path: Path) -> None:
    """#736 with #540's sanitizer: a hint off the wire is not more trusted."""
    out = _run(
        """
const R = window.QuizifyRenderShared;
const made = [];
global.Image = function () {
  const self = { decoding: null, onerror: null, _src: null };
  Object.defineProperty(self, 'src', {
    set: function (v) { self._src = v; made.push(v); },
    get: function () { return self._src; }
  });
  return self;
};
const good = R.preloadNextImage('/quizify/static/img/q.png');
const absolute = R.preloadNextImage('https://example.com/q.png');
const evil = R.preloadNextImage('javascript:alert(1)');
const missing = R.preloadNextImage('');
console.log(JSON.stringify({
  made: made,
  decoding: good ? good.decoding : null,
  absoluteOk: absolute !== null,
  evil: evil, missing: missing
}));
""",
        tmp_path,
    )
    assert out["made"] == [
        "/quizify/static/img/q.png",
        "https://example.com/q.png",
    ]
    assert out["decoding"] == "async"
    assert out["evil"] is None, "a javascript: hint was fetched"
    assert out["missing"] is None


# ---------------------------------------------------------------------------
# client-core.js
# ---------------------------------------------------------------------------


def test_the_socket_url_follows_the_page_scheme(tmp_path: Path) -> None:
    """A ws:// socket on an https page is blocked by the browser, silently."""
    out = _run(
        """
const C = window.QuizifyClientCore;
global.location = { protocol: 'http:', host: 'ha.local:8123' };
const plain = C.socketUrl('/api/quizify/ws?role=dashboard');
global.location = { protocol: 'https:', host: 'ha.example.com' };
const secure = C.socketUrl('/api/quizify/ws?role=admin');
console.log(JSON.stringify({ plain: plain, secure: secure }));
""",
        tmp_path,
    )
    assert out["plain"] == "ws://ha.local:8123/api/quizify/ws?role=dashboard"
    assert out["secure"] == "wss://ha.example.com/api/quizify/ws?role=admin"


def test_the_backoff_is_the_curve_the_host_and_the_phone_had(
    tmp_path: Path,
) -> None:
    """1s, 2s, 4s, 8s, 16s, then the cap — long enough for an HA restart (#290)."""
    out = _run(
        """
const C = window.QuizifyClientCore;
const curve = [];
for (let i = 0; i < 8; i++) curve.push(C.backoffDelay(i, 30000));
console.log(JSON.stringify({ curve: curve }));
""",
        tmp_path,
    )
    assert out["curve"] == [1000, 2000, 4000, 8000, 16000, 30000, 30000, 30000]


def _socket_harness(body: str) -> str:
    """A fake WebSocket that records what the module wired onto it."""
    return (
        """
global.location = { protocol: 'http:', host: 'ha.local:8123' };
let made = null;
global.WebSocket = function (url) {
  made = this;
  this.url = url;
  this.closed = 0;
  this.close = function () { this.closed++; };
};
const C = window.QuizifyClientCore;
"""
        + body
    )


def test_a_bad_frame_is_logged_and_the_socket_keeps_going(tmp_path: Path) -> None:
    out = _run(
        _socket_harness(
            """
const seen = [];
const errors = [];
console.error = function () { errors.push(Array.prototype.slice.call(arguments)[0]); };
const ws = C.createSocket('/api/quizify/ws', {
  logPrefix: '[Dashboard]',
  onMessage: function (msg) { seen.push(msg); }
});
ws.onmessage({ data: '{"type":"timer_tick","remaining":9}' });
ws.onmessage({ data: 'not json at all' });
ws.onmessage({ data: '{"type":"finale"}' });
console.log(JSON.stringify({ seen: seen, errors: errors, url: ws.url }));
"""
        ),
        tmp_path,
    )
    assert out["seen"] == [
        {"type": "timer_tick", "remaining": 9},
        {"type": "finale"},
    ], "a bad frame took the next one down with it"
    assert out["errors"] == ["[Dashboard] Bad message:"]
    assert out["url"] == "ws://ha.local:8123/api/quizify/ws"


def test_a_renderer_that_throws_does_not_kill_the_socket(tmp_path: Path) -> None:
    """The handler runs INSIDE the parse try, exactly as all three already had.

    This is the one part of the merge that was not obvious, and it is
    load-bearing: one odd frame that breaks a renderer must cost that frame,
    not the rest of the round.
    """
    out = _run(
        _socket_harness(
            """
const seen = [];
const errors = [];
console.error = function () { errors.push(Array.prototype.slice.call(arguments)[0]); };
const ws = C.createSocket('/api/quizify/ws', {
  onMessage: function (msg) {
    seen.push(msg.type);
    if (msg.type === 'boom') throw new Error('renderer blew up');
  }
});
ws.onmessage({ data: '{"type":"boom"}' });
ws.onmessage({ data: '{"type":"after"}' });
console.log(JSON.stringify({ seen: seen, errors: errors }));
"""
        ),
        tmp_path,
    )
    assert out["seen"] == ["boom", "after"]
    assert out["errors"] == ["[Quizify] Bad message:"]


def test_an_error_closes_the_socket_so_one_path_reaches_the_retry(
    tmp_path: Path,
) -> None:
    """Every surface's retry policy hangs off onclose; onerror must funnel into it."""
    out = _run(
        _socket_harness(
            """
let closes = 0;
const ws = C.createSocket('/api/quizify/ws', {
  onClose: function () { closes++; }
});
ws.onerror();
ws.onclose();
console.log(JSON.stringify({ closed: ws.closed, closes: closes }));
"""
        ),
        tmp_path,
    )
    assert out["closed"] == 1, "onerror did not close the socket"
    assert out["closes"] == 1


def test_the_open_callback_gets_the_socket_it_opened(tmp_path: Path) -> None:
    """The television sends get_state from inside onOpen, on that same socket."""
    out = _run(
        _socket_harness(
            """
let same = false;
const ws = C.createSocket('/api/quizify/ws', {
  onOpen: function (opened) { same = (opened === ws); }
});
ws.onopen();
console.log(JSON.stringify({ same: same }));
"""
        ),
        tmp_path,
    )
    assert out["same"] is True


def test_the_player_session_is_one_pair_of_keys(tmp_path: Path) -> None:
    """The host page writes what the phone reads; two spellings would drift."""
    out = _run(
        """
const C = window.QuizifyClientCore;
const store = {};
global.sessionStorage = {
  setItem: function (k, v) { store[k] = String(v); },
  getItem: function (k) { return store[k] === undefined ? null : store[k]; },
  removeItem: function (k) { delete store[k]; }
};
C.saveSession('tok-1', 'Anna');
const written = JSON.parse(JSON.stringify(store));
const read = C.getSession();
C.clearSession();
console.log(JSON.stringify({
  written: written, read: read, after: C.getSession(),
  keys: [C.SESSION_TOKEN_KEY, C.SESSION_NAME_KEY]
}));
""",
        tmp_path,
    )
    assert out["keys"] == ["quizify_session_token", "quizify_player_name"]
    assert out["written"] == {
        "quizify_session_token": "tok-1",
        "quizify_player_name": "Anna",
    }
    assert out["read"] == {"token": "tok-1", "name": "Anna"}
    assert out["after"] == {"token": None, "name": None}


def test_storage_that_throws_is_survivable(tmp_path: Path) -> None:
    """Private mode, or storage disabled: the socket still has to work."""
    out = _run(
        """
const C = window.QuizifyClientCore;
global.sessionStorage = {
  setItem: function () { throw new Error('denied'); },
  getItem: function () { throw new Error('denied'); },
  removeItem: function () { throw new Error('denied'); }
};
C.saveSession('t', 'n');
const read = C.getSession();
C.clearSession();
console.log(JSON.stringify({ read: read }));
""",
        tmp_path,
    )
    assert out["read"] == {"token": None, "name": None}
