"""The television says which kind of pause the room is looking at (#892).

The snapshot has carried ``pause_reason`` since #703, and the phones act on it:
``updatePausedView`` swaps to ``admin.pausedHostDisconnected`` /
``admin.pausedHostHint`` on ``admin_disconnected`` and, sixty seconds later,
reveals a reset button because the server authorizes ``reset_game`` from any
client by then (#207 / #299).

The television read none of that. ``setPaused(on)`` took one argument, so a
host who tapped pause and a host whose phone had dropped off the network
produced the same card on the big screen — *"The game will resume when the host
returns"* — and the room sat waiting for a resume that no longer had a device
to come from, with the way out sitting unannounced in their own pockets.

The tests run the real ``setPaused`` out of ``js/dashboard.js`` against the DOM
stub and the shipped ``en.json``, with the page's own ``t()`` shape, so what is
asserted is the sentence the room reads rather than the branch that picks it.
The sixty-second clock is a captured ``setTimeout``: the harness shadows it,
which lets a test both name the delay and fire it.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parent.parent
_WWW = _REPO / "custom_components" / "quizify" / "www"
# #829: the television's script is its own file now; the markup that carries
# the three scrim lines is still in the page.
_DASHBOARD = _WWW / "js" / "dashboard.js"
_DASHBOARD_HTML = _WWW / "dashboard.html"
_I18N = _WWW / "i18n"
_STUB = Path(__file__).resolve().parent / "fixtures" / "dom_stub.js"

_NEEDS_NODE = pytest.mark.skipif(
    shutil.which("node") is None, reason="node not installed"
)

LANGUAGES = ("en", "de", "es")


def _pause_source() -> str:
    """The pause block of the television's script, verbatim."""
    source = _DASHBOARD.read_text("utf-8")
    start = source.index("    var PAUSED_ESCAPE_DELAY_MS")
    end = source.index("    function setImageLayout(", start)
    return source[start:end]


_HARNESS = """
require({stub});
const fs = require('fs');

QZ.els(['paused-overlay', 'paused-title', 'paused-hint', 'paused-escape']);
const els = {{
    pausedOverlay: document.getElementById('paused-overlay'),
    pausedTitle: document.getElementById('paused-title'),
    pausedHint: document.getElementById('paused-hint'),
    pausedEscape: document.getElementById('paused-escape')
}};
// The shipped markup ships the escape line with `hidden`; the stub's elements
// start visible, so mirror the page before anything runs.
els.pausedEscape.hidden = true;

// The page's own i18n helper, over the real English bundle.
const BUNDLE = JSON.parse(fs.readFileSync({en}, 'utf8'));
function t(key, fallback) {{
    let node = BUNDLE;
    key.split('.').forEach(function (part) {{ node = node ? node[part] : null; }});
    if (typeof node === 'string') return node;
    return fallback != null ? fallback : key;
}}

// A clock the test drives. Shadows the global inside this scope, which is the
// scope the pause block is spliced into below.
let pending = [];
function setTimeout(fn, ms) {{
    pending.push({{fn: fn, ms: ms}});
    return pending.length;
}}
function clearTimeout(id) {{ if (id) pending[id - 1] = null; }}
function fireClock() {{
    const due = pending;
    pending = [];
    due.forEach(function (entry) {{ if (entry) entry.fn(); }});
}}
function armed() {{
    return pending.filter(Boolean).map(function (e) {{ return e.ms; }});
}}

let isPaused = false;

{pause}

function card() {{
    return {{
        title: els.pausedTitle.textContent,
        titleKey: els.pausedTitle.getAttribute('data-i18n'),
        hint: els.pausedHint.textContent,
        hintKey: els.pausedHint.getAttribute('data-i18n'),
        escapeHidden: els.pausedEscape.hidden,
        escape: els.pausedEscape.textContent,
        overlay: els.pausedOverlay.classList.contains('active'),
        armed: armed()
    }};
}}

const out = {{}};
{body}
console.log(JSON.stringify(out));
"""


def _run(body: str) -> dict:
    script = _HARNESS.format(
        stub=json.dumps(str(_STUB)),
        en=json.dumps(str(_I18N / "en.json")),
        pause=_pause_source(),
        body=body,
    )
    proc = subprocess.run(
        ["node", "-e", script], capture_output=True, text=True, check=True
    )
    return json.loads(proc.stdout)


# ---------------------------------------------------------------------------
# The two pauses read differently
# ---------------------------------------------------------------------------


@_NEEDS_NODE
def test_a_pause_the_host_chose_still_reads_as_before() -> None:
    """The deliberate pause is the sentence that was always right for it."""
    result = _run("out.deliberate = (setPaused(true), card());")

    assert result["deliberate"]["overlay"] is True
    assert result["deliberate"]["title"] == "Game Paused"
    assert (
        result["deliberate"]["hint"]
        == "The game will resume when the host returns"
    )
    assert result["deliberate"]["escapeHidden"] is True
    # Nothing counts down: this host is coming back.
    assert result["deliberate"]["armed"] == []


@_NEEDS_NODE
def test_the_host_dropping_is_not_told_as_a_deliberate_pause() -> None:
    """THE #892 case. The phones already said this; the television did not."""
    result = _run("out.gone = (setPaused(true, 'admin_disconnected'), card());")
    gone = result["gone"]

    assert gone["title"] == "Lost connection to the host"
    assert gone["hint"] == "Trying to reconnect the host — hang tight"
    assert gone["title"] != "Game Paused"
    assert gone["hint"] != "The game will resume when the host returns"


@_NEEDS_NODE
def test_the_line_that_moves_is_the_key_not_only_the_text() -> None:
    """``initPageTranslations`` repaints every ``[data-i18n]`` element from its
    attribute, and the TV sweeps on every language change (#733). Writing only
    ``textContent`` would put the generic pause sentence straight back."""
    result = _run("out.gone = (setPaused(true, 'admin_disconnected'), card());")

    assert result["gone"]["titleKey"] == "admin.pausedHostDisconnected"
    assert result["gone"]["hintKey"] == "admin.pausedHostHint"


@_NEEDS_NODE
def test_the_card_goes_back_to_the_ordinary_wording() -> None:
    """A disconnect pause followed by a deliberate one — the second must not
    inherit the first one's sentences."""
    result = _run(
        "setPaused(true, 'admin_disconnected');\n"
        "setPaused(false);\n"
        "out.after = (setPaused(true), card());"
    )

    assert result["after"]["titleKey"] == "game.paused"
    assert result["after"]["hintKey"] == "game.pausedHint"


# ---------------------------------------------------------------------------
# The sixtieth second
# ---------------------------------------------------------------------------


@_NEEDS_NODE
def test_the_room_is_told_about_the_way_out_after_a_minute() -> None:
    """Same window the phones use, so the sofa and the screen agree."""
    result = _run(
        "setPaused(true, 'admin_disconnected');\n"
        "out.waiting = card();\n"
        "fireClock();\n"
        "out.after = card();"
    )

    # Not on the first second: a host ten seconds from being back should not
    # find the room has been invited to reset the game.
    assert result["waiting"]["escapeHidden"] is True
    assert result["waiting"]["armed"] == [60000]

    assert result["after"]["escapeHidden"] is False
    assert result["after"]["escape"] == "Any phone in the room can reset the game now"


@_NEEDS_NODE
def test_a_repeated_paused_snapshot_does_not_restart_the_wait() -> None:
    """PAUSED snapshots arrive on every (re)connect. A clock that restarts on
    each of them is a clock that never reaches sixty."""
    result = _run(
        "setPaused(true, 'admin_disconnected');\n"
        "setPaused(true, 'admin_disconnected');\n"
        "setPaused(true, 'admin_disconnected');\n"
        "out.armed = armed();\n"
        "fireClock();\n"
        "out.after = card();"
    )

    assert result["armed"] == [60000]
    assert result["after"]["escapeHidden"] is False


@_NEEDS_NODE
def test_the_host_coming_back_takes_the_invitation_down() -> None:
    """Resumed mid-wait: no reset line, then or later."""
    result = _run(
        "setPaused(true, 'admin_disconnected');\n"
        "setPaused(false);\n"
        "out.resumed = card();\n"
        "fireClock();\n"
        "out.later = card();"
    )

    assert result["resumed"]["escapeHidden"] is True
    assert result["later"]["escapeHidden"] is True


@_NEEDS_NODE
def test_a_deliberate_pause_never_offers_the_reset_line() -> None:
    result = _run(
        "setPaused(true);\nfireClock();\nout.card = card();"
    )
    assert result["card"]["escapeHidden"] is True


# ---------------------------------------------------------------------------
# The wiring and the strings
# ---------------------------------------------------------------------------


def test_the_snapshots_reason_reaches_setpaused() -> None:
    """``handleGameState`` is the only caller that has the reason to pass; the
    branch above is unreachable without it."""
    source = _DASHBOARD.read_text("utf-8")
    assert "setPaused(msg.phase === 'PAUSED', msg.pause_reason)" in source


def test_the_escape_line_ships_hidden() -> None:
    html = _DASHBOARD_HTML.read_text("utf-8")
    line = next(
        line for line in html.splitlines() if 'id="paused-escape"' in line
    )
    assert "hidden" in line
    assert 'data-i18n="game.pausedResetHint"' in line


def test_the_new_line_exists_in_every_shipped_language() -> None:
    """``test_i18n_hardcoded_strings_625`` pins key parity; this pins that the
    three strings are three languages rather than one copied twice."""
    texts = {
        code: json.loads((_I18N / f"{code}.json").read_text("utf-8"))["game"][
            "pausedResetHint"
        ]
        for code in LANGUAGES
    }
    assert len(set(texts.values())) == 3, texts
    for code, text in texts.items():
        assert text.strip(), code
