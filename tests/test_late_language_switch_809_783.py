"""Three lines on the phone stayed in the language they were first painted in.

Same root cause, three elements, two issues.

**#809 — the lobby's companion and meta lines.** ``_renderHeroAndOrbits``
wrote ``#pl-also`` ("Waiting on your own…", "+ 1 more player") and ``#pl-meta``
(difficulty · "10 Rounds", else "Game Lobby") with ``textContent``, which
destroys the ``data-i18n`` spans the markup ships with. The first
``game_state`` after ``joined`` starts the async ``setLanguage`` and then,
*synchronously*, calls ``handlePlayerJoined`` and ``renderLobby`` — both paint
from the bundle that is still loaded. When the new bundle lands,
``initPageTranslations()`` finds no attribute on either element and leaves
them. A guest whose phone is English joining a German game reads "DU BIST DRIN"
and "So wird gespielt" over "Waiting on your own…" and "Game Lobby" until
somebody else joins; the last guest to arrive keeps the mismatch until the
game starts.

**#783 — the screen-reader connection status.** ``#conn-status-announce``
carried no ``data-i18n`` either. Measured live on v1.15.0-RC2: an English game
with ``document.documentElement.lang === "en"`` and every visible label in
English, and the live region reading "Verbunden". It is ``.sr-only``, so the
only people it reached were the ones who depend on it.

Both are the ``renderAllTime`` half of #776, one element further on. The tests
below run the real modules against the real bundles: paint in one language,
switch, sweep, and read the line back.
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
_I18N = _WWW / "i18n"
_STUB = Path(__file__).resolve().parent / "fixtures" / "dom_stub.js"

_NEEDS_NODE = pytest.mark.skipif(
    shutil.which("node") is None, reason="node not installed"
)


def _without_comments(source: str) -> str:
    source = re.sub(r"/\*.*?\*/", "", source, flags=re.S)
    return re.sub(r"^\s*//.*$", "", source, flags=re.M)


def _js_function(source: str, signature: str) -> str:
    start = source.index(signature)
    depth = 0
    seen = False
    for i in range(start, len(source)):
        if source[i] == "{":
            depth += 1
            seen = True
        elif source[i] == "}":
            depth -= 1
            if seen and depth == 0:
                return source[start : i + 1]
    raise AssertionError(f"unbalanced braces after {signature!r}")


def _node(script: str) -> dict:
    out = subprocess.run(
        ["node", "-e", script], capture_output=True, text=True, check=True
    )
    return json.loads(out.stdout)


_PRELUDE = """
require({stub});
QZ.serveI18n({i18n});
QZ.load({i18njs});
"""


# ---------------------------------------------------------------------------
# #809 — the lobby lines
# ---------------------------------------------------------------------------


_LOBBY_SCRIPT = _PRELUDE + """
QZ.els([
    'pl-mega-avatar', 'pl-mega-name', 'pl-also', 'pl-meta', 'player-list',
    'pl-alltime', 'player-count-badge', 'players-summary', 'players-empty',
    'lobby-difficulty-badge', 'start-game-btn', 'admin-controls',
    'waiting-message'
]);

window.QuizifyPlayerUtils = {{
    state: {{ playerName: 'Anna', playerColor: '#A89E89', isAdmin: false }},
    escapeHtml: function (s) {{ return String(s); }},
    generateQR: function () {{}},
    showToast: function () {{}}
}};
window.QuizifyPlayer = {{ send: function () {{}} }};
QZ.load({lobby});

var ROOM = {{
    players: [
        {{ name: 'Anna', color: '#A89E89' }},
        {{ name: 'Bea', color: '#7FA897' }},
        {{ name: 'Cem', color: '#E8C47F' }}
    ],
    difficulty: 'easy',
    total_rounds: 10
}};

function snap() {{
    return {{
        also: document.getElementById('pl-also').textContent,
        meta: document.getElementById('pl-meta').textContent
    }};
}}

(async function () {{
    // The phone joins in English, which is what its browser says.
    await window.QuizifyI18n.init('en');
    window.QuizifyPlayerLobby.handlePlayerJoined(ROOM);
    var english = snap();

    // One heartbeat later the first game_state says the room is German.
    await window.QuizifyI18n.setLanguage('de');
    window.QuizifyI18n.initPageTranslations();
    var afterSwitch = snap();

    // A room of one: the other branch of the companion line.
    window.QuizifyPlayerLobby.handlePlayerJoined({{
        players: [{{ name: 'Anna', color: '#A89E89' }}], difficulty: 'easy',
        total_rounds: 10
    }});
    var aloneDe = snap();
    await window.QuizifyI18n.setLanguage('es');
    window.QuizifyI18n.initPageTranslations();
    var aloneEs = snap();

    // And the roster frame that carries no game data at all, which is what
    // player_joined / player_left look like.
    window.QuizifyPlayerLobby.handlePlayerJoined({{
        players: [
            {{ name: 'Anna' }}, {{ name: 'Bea' }}
        ]
    }});
    var rosterOnlyEs = snap();
    await window.QuizifyI18n.setLanguage('de');
    window.QuizifyI18n.initPageTranslations();
    var rosterOnlyDe = snap();

    console.log(JSON.stringify({{
        english: english,
        afterSwitch: afterSwitch,
        aloneDe: aloneDe,
        aloneEs: aloneEs,
        rosterOnlyEs: rosterOnlyEs,
        rosterOnlyDe: rosterOnlyDe
    }}));
}})();
"""


def _lobby() -> dict:
    return _node(
        _LOBBY_SCRIPT.format(
            stub=json.dumps(str(_STUB)),
            i18n=json.dumps(str(_I18N)),
            i18njs=json.dumps(str(_JS / "i18n.js")),
            lobby=json.dumps(str(_JS / "player-lobby.js")),
        )
    )


@_NEEDS_NODE
def test_the_lobby_paints_in_the_language_it_has() -> None:
    """Guards the guard: if the English half were broken the switch would
    prove nothing."""
    result = _lobby()

    assert result["english"]["also"] == "+ 2 more players"
    assert result["english"]["meta"] == "🌱 Easy · 10 Rounds"


@_NEEDS_NODE
def test_a_late_switch_reaches_the_companion_line() -> None:
    """The reported symptom. Before the fix this stayed "+ 2 more players"
    under a German hero."""
    result = _lobby()

    assert result["afterSwitch"]["also"] == "+ 2 weitere Spieler"


@_NEEDS_NODE
def test_a_late_switch_reaches_the_meta_line() -> None:
    """Both halves of it: the difficulty word and the unit after the number."""
    result = _lobby()

    assert result["afterSwitch"]["meta"] == "🌱 Einfach · 10 Runden"


@_NEEDS_NODE
def test_the_lone_guest_s_line_switches_too() -> None:
    """"Du wartest noch allein…" is the line the LAST guest to join keeps
    longest — nobody arrives after them to trigger a repaint."""
    result = _lobby()

    assert result["aloneDe"]["also"] == "Du wartest noch allein…"
    assert result["aloneEs"]["also"] == "Esperando tú solo…"


@_NEEDS_NODE
def test_a_roster_only_frame_still_re_translates() -> None:
    """`player_joined` / `player_left` carry players and teams and nothing
    else, so the meta line falls back to "Game Lobby" — which has to switch
    with everything else rather than freezing in whichever language the last
    person to arrive happened to trigger."""
    result = _lobby()

    assert result["rosterOnlyEs"]["meta"] == "Sala de juego"
    assert result["rosterOnlyDe"]["meta"] == "Spiel-Lobby"
    assert result["rosterOnlyDe"]["also"] == "+ 1 weiterer Spieler"


def test_the_companion_line_carries_its_count_as_params() -> None:
    """The #776 mechanism, not a re-implementation: the sweep re-interpolates
    from data-i18n-params, so the count must be there and not baked into the
    text."""
    source = _without_comments((_JS / "player-lobby.js").read_text("utf-8"))
    body = _js_function(source, "function _renderHeroAndOrbits(players, data)")

    assert "alsoEl.setAttribute('data-i18n', alsoKey)" in body
    assert "data-i18n-params', JSON.stringify(alsoParams)" in body
    # …and dropped again when the chosen sentence takes none, or the sweep
    # would keep interpolating a count into a line that has no {count}.
    assert "alsoEl.removeAttribute('data-i18n-params')" in body


def test_the_meta_line_keeps_its_words_in_keys_not_in_params() -> None:
    """A composed line CAN carry params, but pre-translated pieces handed to
    the sweep as params would just freeze one level down — the sweep would
    re-render the template around the same German words. So each word gets its
    own key on its own span, and only the round number is written as text."""
    source = _without_comments((_JS / "player-lobby.js").read_text("utf-8"))
    body = _js_function(source, "function _renderHeroAndOrbits(players, data)")

    assert "_i18nSpan('difficulties.' + diff, t)" in body
    assert "_i18nSpan('admin.summaryRoundsUnit', t)" in body
    assert "_i18nSpan('lobby.gameLobby', t)" in body
    assert "metaEl.textContent" not in body, (
        "a textContent write is exactly what the sweep cannot follow"
    )


def test_the_markup_puts_the_key_where_the_renderer_writes() -> None:
    """#pl-also ships with a key so a phone that never repaints is translated
    too — on the <p> itself, because an inner span would be destroyed by the
    first repaint."""
    html = (_WWW / "player.html").read_text("utf-8")

    assert 'id="pl-also" data-i18n="lobby.aloneWaiting"' in html


# ---------------------------------------------------------------------------
# #783 — the screen-reader connection status
# ---------------------------------------------------------------------------


_CONN_SCRIPT = _PRELUDE + """
QZ.els(['conn-status', 'conn-status-announce', 'reconnecting-overlay']);
window.QuizifyUtils = {{}};
QZ.load({utils});

function announced() {{
    var el = document.getElementById('conn-status-announce');
    return {{ text: el.textContent, key: el.getAttribute('data-i18n') }};
}}

(async function () {{
    await window.QuizifyI18n.init('en');
    window.QuizifyPlayerUtils.updateConnectionIndicator('connected');
    var english = announced();

    await window.QuizifyI18n.setLanguage('de');
    window.QuizifyI18n.initPageTranslations();
    var afterSwitch = announced();

    window.QuizifyPlayerUtils.updateConnectionIndicator('reconnecting');
    var reconnectingDe = announced();
    await window.QuizifyI18n.setLanguage('es');
    window.QuizifyI18n.initPageTranslations();
    var reconnectingEs = announced();

    // A status the bundles have no word for must not leave a stale key
    // behind — the next sweep would announce the wrong state out loud.
    window.QuizifyPlayerUtils.updateConnectionIndicator('wobbly');
    var unknown = announced();
    window.QuizifyI18n.initPageTranslations();
    var unknownAfterSweep = announced();

    console.log(JSON.stringify({{
        english: english,
        afterSwitch: afterSwitch,
        reconnectingDe: reconnectingDe,
        reconnectingEs: reconnectingEs,
        unknown: unknown,
        unknownAfterSweep: unknownAfterSweep
    }}));
}})();
"""


def _conn() -> dict:
    return _node(
        _CONN_SCRIPT.format(
            stub=json.dumps(str(_STUB)),
            i18n=json.dumps(str(_I18N)),
            i18njs=json.dumps(str(_JS / "i18n.js")),
            utils=json.dumps(str(_JS / "player-utils.js")),
        )
    )


@_NEEDS_NODE
def test_the_live_region_starts_in_the_loaded_language() -> None:
    result = _conn()

    assert result["english"]["text"] == "Connected"


@_NEEDS_NODE
def test_a_late_switch_reaches_the_live_region() -> None:
    """The measured symptom: lang="en", every visible label English, and this
    element still reading "Verbunden" — heard only by the people who cannot
    see the rest of the screen."""
    result = _conn()

    assert result["afterSwitch"]["text"] == "Verbunden"
    assert result["afterSwitch"]["key"] == "connection.connected"


@_NEEDS_NODE
def test_it_holds_for_the_other_states_too() -> None:
    """Connected is the state a phone sits in; reconnecting and disconnected
    are the ones worth announcing correctly."""
    result = _conn()

    assert result["reconnectingDe"]["text"] == "Verbindung wird wiederhergestellt..."
    assert result["reconnectingEs"]["text"] == "Reconectando..."
    assert result["reconnectingEs"]["key"] == "connection.reconnecting"


@_NEEDS_NODE
def test_an_unknown_status_leaves_no_key_to_re_render_from() -> None:
    """The fallback path prints the raw status. Leaving the previous key on the
    element would let the next sweep overwrite it with a state the connection
    is no longer in."""
    result = _conn()

    assert result["unknown"]["key"] is None
    assert result["unknown"]["text"] == "wobbly"
    assert result["unknownAfterSweep"]["text"] == "wobbly"
