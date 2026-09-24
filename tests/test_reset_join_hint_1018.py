"""#1018 — a phone put back on the join form says why, and keeps the name.

``game_reset`` (the host started over) and ``reconnect_failed`` (the server
would not take the stored session back) both dropped the guest onto the bare
"Enter your name to play" form — the same screen a fresh scan shows. Every
guest had to be told out loud to type their name again.

The behaviour tests run the real ``game_reset`` / ``reconnect_failed`` cases
lifted out of player-core.js, together with the real ``forgetIdentity`` and
the join-form helpers, against the real player-utils.js and client-core.js
over the node DOM stub. Where the helpers do not exist (the pre-#1018 source)
the harness runs without them, so the old code reaches an assertion about the
missing hint instead of a ReferenceError.
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
_CORE = _JS / "player-core.js"
_UTILS = _JS / "player-utils.js"
_CLIENT_CORE = _JS / "client-core.js"
_HTML = _WWW / "player.html"
_I18N = _WWW / "i18n"
_STUB = Path(__file__).resolve().parent / "fixtures" / "dom_stub.js"

_NEEDS_NODE = pytest.mark.skipif(
    shutil.which("node") is None, reason="node not installed"
)

_KEYS = ("resetHint", "reconnectFailedHint")


def _slice(start: str, end: str, *, required: bool = True) -> str:
    source = _CORE.read_text("utf-8")
    a = source.find(start)
    if a == -1:
        if required:
            raise AssertionError(f"player-core.js lost {start.strip()!r}")
        return ""
    return source[a : source.index(end, a)]


_SCRIPT = """
require({stub});

QZ.els(['join-view', 'lobby-view', 'game-view', 'end-view', 'join-hint',
        'name-input', 'join-btn']);
QZ.el('join-hint').classList.add('hidden');
QZ.el('join-btn').disabled = true;
QZ.el('name-input').focus = function () {{}};

global.sessionStorage = {{
    _v: Object.create(null),
    getItem: function (k) {{ return this._v[k] === undefined ? null : this._v[k]; }},
    setItem: function (k, v) {{ this._v[k] = String(v); }},
    removeItem: function (k) {{ delete this._v[k]; }}
}};
global.location = window.location =
    {{ protocol: 'http:', host: 'ha:8123', search: '' }};
global.QuizifyUtils = window.QuizifyUtils = {{
    MAX_NAME_LENGTH: 20,
    readAdminToken: function () {{ return null; }},
    formatPoints: function (n) {{ return String(n); }},
    formatTime: function (n) {{ return String(n); }},
    escapeHtml: function (s) {{ return String(s); }}
}};
QZ.load({client_core});
QZ.load({utils});

var pu = window.QuizifyPlayerUtils;
var state = pu.state;
var els = {{
    nameInput: document.getElementById('name-input'),
    joinBtn: document.getElementById('join-btn')
}};
var game = null, reveal = null, lobby = null;
function endJoinPending() {{ state.joinPending = false; }}
function clearGameChrome() {{}}
function _t() {{ return function (k) {{ return 'T(' + k + ')'; }}; }}

{forget}

{helpers}

function handleMessage(msg) {{
    switch (msg.type || msg.event) {{
{cases}
        default:
            break;
    }}
}}

var scenario = {scenario};
if (scenario !== 'fresh') {{
    // A guest in a game; the join form still says "Joining…" from their join,
    // and a reload may already have emptied the input (reconnect_failed).
    state.playerName = 'Anna';
    state.playerId = 'Anna';
    state.sessionToken = 'tok';
    pu.saveSession('tok', 'Anna');
    els.joinBtn.textContent = 'T(join.joining)';
    els.nameInput.value = '';
    pu.showView('end-view');
    handleMessage({{ type: scenario }});
}} else {{
    pu.showView('join-view');
}}

var hint = document.getElementById('join-hint');
console.log(JSON.stringify({{
    onJoinView: !document.getElementById('join-view').classList.contains('hidden'),
    hintVisible: !hint.classList.contains('hidden'),
    hintText: hint.textContent,
    hintKey: hint.getAttribute('data-i18n'),
    nameValue: els.nameInput.value,
    joinDisabled: !!els.joinBtn.disabled,
    joinText: els.joinBtn.textContent,
    stateName: state.playerName
}}));
"""


def _run(scenario: str) -> dict:
    cases = _slice(
        "            case 'game_reset':", "            case 'kicked':"
    ) + _slice(
        "            case 'reconnect_failed':", "            case 'host_presence':"
    )
    script = _SCRIPT.format(
        stub=json.dumps(str(_STUB)),
        client_core=json.dumps(str(_CLIENT_CORE)),
        utils=json.dumps(str(_UTILS)),
        forget=_slice("    function forgetIdentity() {", "\n    }\n") + "\n    }",
        helpers=_slice(
            "    function lastPlayerName() {",
            "    // Put the join form back in a usable state",
            required=False,
        ),
        cases=cases,
        scenario=json.dumps(scenario),
    )
    out = subprocess.run(
        ["node", "-e", script], capture_output=True, text=True, check=True
    )
    return json.loads(out.stdout.strip().splitlines()[-1])


@_NEEDS_NODE
def test_game_reset_explains_itself_on_the_join_form() -> None:
    result = _run("game_reset")

    assert result["onJoinView"] is True
    assert result["hintVisible"] is True, "the join form gave no reason"
    assert result["hintText"] == "T(join.resetHint)"
    # Re-translated on a language switch like every other string on the form.
    assert result["hintKey"] == "join.resetHint"


@_NEEDS_NODE
def test_reconnect_failed_explains_itself_differently() -> None:
    result = _run("reconnect_failed")

    assert result["onJoinView"] is True
    assert result["hintVisible"] is True, "the join form gave no reason"
    assert result["hintText"] == "T(join.reconnectFailedHint)"
    assert result["hintKey"] == "join.reconnectFailedHint"


@_NEEDS_NODE
@pytest.mark.parametrize("scenario", ["game_reset", "reconnect_failed"])
def test_the_last_name_is_handed_back(scenario: str) -> None:
    result = _run(scenario)

    assert result["nameValue"] == "Anna"
    # Ready to tap, and no longer stuck on the previous "Joining…".
    assert result["joinDisabled"] is False
    assert result["joinText"] == "T(join.joinButton)"
    # Handing the name to the input is not re-joining under it (#981).
    assert result["stateName"] is None


@_NEEDS_NODE
def test_a_fresh_load_shows_no_hint() -> None:
    result = _run("fresh")

    assert result["onJoinView"] is True
    assert result["hintVisible"] is False
    assert result["nameValue"] == ""


def test_the_hint_is_on_the_join_form_and_starts_hidden() -> None:
    html = _HTML.read_text("utf-8")
    join = html[html.index('<div id="join-view"') : html.index('<div id="lobby-view"')]
    tag = re.search(r"<p[^>]*\bid=\"join-hint\"[^>]*>", join)

    assert tag, "no #join-hint inside #join-view"
    assert re.search(r'class="[^"]*\bhidden\b', tag.group(0))
    assert 'role="status"' in tag.group(0)


def test_a_join_hides_the_hint_again() -> None:
    joined = _slice(
        "            case 'joined':", "            case 'reconnect_failed':"
    )
    assert "hideJoinHint();" in joined


@pytest.mark.parametrize("lang", ["de", "en", "es"])
def test_both_reasons_are_translated(lang: str) -> None:
    join = json.loads((_I18N / f"{lang}.json").read_text("utf-8"))["join"]
    for key in _KEYS:
        assert join.get(key), f"{lang}.json is missing join.{key}"
    assert join["resetHint"] != join["reconnectFailedHint"]
