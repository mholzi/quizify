"""The leaderboard line under the question spoke the browser's language.

#1055, found in a live test of v1.23.0-RC1: an English game, a host whose
browser is German. When the game starts the admin tab navigates to
``/quizify/player``, so the player page is loaded fresh at the very moment the
first question arrives. ``handleGameState`` in ``player-core.js`` paints the
leaderboard *before* ``_syncServerLanguage`` has loaded the game's bundle, and
``updateLeaderboardSummary`` wrote the collapsed line with one ``textContent``
call — so the page read "Probe1234: 0  + 1 weiterer" while every label around
it was English, through the whole first question and its reveal, until round 2
repainted it. The row's "(Du)" badge was written in the same call and froze
the same way.

It is the #776 / #809 problem one element further on, and it gets the same
answer: the translated words live in their own ``data-i18n`` span (the count
rides along as ``data-i18n-params``), so the sweep that follows a language
switch can find them. Any fresh page load mid-game takes this path — a phone
reloading or reconnecting as much as the host's tab — and so does the
language picker on the phone (#492).

Reproducing it in a browser against the dev server turned up a second, worse
form of the same moment. ``i18n.js`` let two overlapping loads finish in either
order: the game's English (already held as the fallback) was installed at once,
and the browser-language fetch that was still on the wire then wrote German
over it. ``getLanguage()`` said ``en``, every label on the page said German,
and nothing ever corrected it, because each later ``game_state`` saw the
language "matching" and skipped the switch. A load now only installs its bundle
if its language is still the one asked for.

The tests run the real ``player-game.js`` and ``i18n.js`` against the real
bundles: paint in one language, switch, sweep, read the line back.
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

_SCRIPT = """
require({stub});
QZ.serveI18n({i18n});
QZ.load({i18njs});

QZ.els(['leaderboard-list', 'reveal-leaderboard-list', 'leaderboard-summary',
        'reveal-leaderboard-summary', 'timer-sr-announce']);
QZ.load({utils_js});
QZ.load({render_shared});
QZ.load({player_utils});
QZ.load({player_game});

var G = window.QuizifyPlayerGame;
window.QuizifyPlayerUtils.state.playerName = 'Probe1234';

function board(n) {{
    var rows = [{{ rank: 1, name: 'Probe1234', score: 0, entrant_id: 'Probe1234' }}];
    for (var i = 1; i < n; i++) {{
        var name = 'Phone' + i;
        rows.push({{ rank: i + 1, name: name, score: 0, entrant_id: name }});
    }}
    return rows;
}}

function snap() {{
    var badges = document.getElementById('leaderboard-list')
        .querySelectorAll('.you-badge');
    return {{
        summary: document.getElementById('leaderboard-summary').textContent,
        reveal: document.getElementById('reveal-leaderboard-summary').textContent,
        you: badges.length ? badges[0].textContent : null
    }};
}}

// What the first game_state after a page load does: the board is painted in
// whatever the browser said, and only then does the game language land.
async function lateSwitch(from, to, players) {{
    await window.QuizifyI18n.setLanguage(from);
    G.updateLeaderboard({{ leaderboard: board(players) }}, 'leaderboard-list');
    var before = snap();
    await window.QuizifyI18n.setLanguage(to);
    window.QuizifyI18n.initPageTranslations();
    return {{ before: before, after: snap() }};
}}

(async function () {{
    await window.QuizifyI18n.init('de');
    var out = {{}};
    out.oneDeEn = await lateSwitch('de', 'en', 2);
    out.manyDeEn = await lateSwitch('de', 'en', 4);
    out.oneEnEs = await lateSwitch('en', 'es', 2);
    out.manyEnEs = await lateSwitch('en', 'es', 4);
    out.manyEsDe = await lateSwitch('es', 'de', 3);
    out.oneEsDe = await lateSwitch('es', 'de', 2);

    // A board that shrinks to one entrant must drop the hint, not keep the
    // span from the render before it.
    out.alone = await lateSwitch('de', 'en', 1);

    // And a later render in the right language is still one clean line.
    G.updateLeaderboard({{ leaderboard: board(3) }}, 'leaderboard-list');
    out.rerender = snap();

    console.log(JSON.stringify(out));
}})();
"""


def _run() -> dict:
    script = _SCRIPT.format(
        stub=json.dumps(str(_STUB)),
        i18n=json.dumps(str(_I18N)),
        i18njs=json.dumps(str(_JS / "i18n.js")),
        utils_js=json.dumps(str(_JS / "utils.js")),
        render_shared=json.dumps(str(_JS / "render-shared.js")),
        player_utils=json.dumps(str(_JS / "player-utils.js")),
        player_game=json.dumps(str(_JS / "player-game.js")),
    )
    out = subprocess.run(
        ["node", "-e", script], capture_output=True, text=True, check=True
    )
    return json.loads(out.stdout)


@pytest.fixture(scope="module")
def result() -> dict:
    return _run()


@_NEEDS_NODE
def test_the_line_is_painted_in_the_language_it_has(result: dict) -> None:
    """Guards the guard: the line as the issue quotes it, before any switch."""
    assert result["oneDeEn"]["before"]["summary"] == "Probe1234: 0  + 1 weiterer"
    assert result["manyDeEn"]["before"]["summary"] == "Probe1234: 0  + 3 weitere"
    assert result["oneDeEn"]["before"]["you"] == "(Du)"


@_NEEDS_NODE
def test_a_late_switch_reaches_the_singular_hint(result: dict) -> None:
    """The reported symptom: "+ 1 weiterer" in an English game."""
    assert result["oneDeEn"]["after"]["summary"] == "Probe1234: 0  + 1 more"


@_NEEDS_NODE
def test_a_late_switch_reaches_the_plural_hint(result: dict) -> None:
    """The count has to survive the switch — it rides on data-i18n-params."""
    assert result["manyDeEn"]["after"]["summary"] == "Probe1234: 0  + 3 more"


@_NEEDS_NODE
def test_the_hidden_reveal_mirror_follows_too(result: dict) -> None:
    """Same function, second element; screen readers read this one."""
    assert result["oneDeEn"]["after"]["reveal"] == "Probe1234: 0  + 1 more"
    assert result["manyDeEn"]["after"]["reveal"] == "Probe1234: 0  + 3 more"


@_NEEDS_NODE
def test_it_is_not_a_german_english_special_case(result: dict) -> None:
    """Spanish in both directions, singular and plural."""
    assert result["oneEnEs"]["before"]["summary"] == "Probe1234: 0  + 1 more"
    assert result["oneEnEs"]["after"]["summary"] == "Probe1234: 0  + 1 más"
    assert result["manyEnEs"]["after"]["summary"] == "Probe1234: 0  + 3 más"
    assert result["manyEsDe"]["before"]["summary"] == "Probe1234: 0  + 2 más"
    assert result["manyEsDe"]["after"]["summary"] == "Probe1234: 0  + 2 weitere"
    assert result["oneEsDe"]["after"]["summary"] == "Probe1234: 0  + 1 weiterer"


@_NEEDS_NODE
def test_the_you_badge_follows_the_switch(result: dict) -> None:
    """Painted by the same call, frozen by the same write."""
    assert result["oneDeEn"]["after"]["you"] == "(You)"
    assert result["oneEnEs"]["after"]["you"] == "(Tú)"
    assert result["manyEsDe"]["after"]["you"] == "(Du)"


@_NEEDS_NODE
def test_a_board_of_one_carries_no_hint(result: dict) -> None:
    """No leftover span from the render before it, in either language."""
    assert result["alone"]["before"]["summary"] == "Probe1234: 0"
    assert result["alone"]["after"]["summary"] == "Probe1234: 0"


@_NEEDS_NODE
def test_the_next_render_does_not_stack_hints(result: dict) -> None:
    """Each render replaces the hint; it never appends a second one."""
    assert result["rerender"]["summary"] == "Probe1234: 0  + 2 more"
    assert result["rerender"]["you"] == "(You)"


# ---------------------------------------------------------------------------
# The overlapping loads
# ---------------------------------------------------------------------------

_RACE_SCRIPT = """
require({stub});
var fs = require('fs');
var DIR = {i18n};

// The real bundles, served with a delay per language so the test decides
// which fetch lands last.
var DELAY = {delays};
global.fetch = function (url) {{
    var code = /([a-z]{{2}})\\.json/.exec(String(url))[1];
    var body = JSON.parse(fs.readFileSync(DIR + '/' + code + '.json', 'utf8'));
    return new Promise(function (resolve) {{
        setTimeout(function () {{
            resolve({{
                ok: true,
                status: 200,
                json: async function () {{ return body; }}
            }});
        }}, DELAY[code] || 0);
    }});
}};
QZ.load({i18njs});

var I = window.QuizifyI18n;
var line = QZ.el('line');
line.setAttribute('data-i18n', 'leaderboard.title');

(async function () {{
    var out = {{}};
    // The page starts loading the browser's language...
    var boot = I.init({first}).then(function () {{
        // ...and sweeps once that resolves, as every page's init does.
        I.initPageTranslations();
        out.atBoot = line.textContent;
        out.readyAtBoot = I.isReady();
    }});
    // ...and the game language arrives {gap} ms in, while that is in flight.
    await new Promise(function (r) {{ setTimeout(r, {gap}); }});
    var sync = I.setLanguage({second}).then(function () {{
        I.initPageTranslations();
        out.atSync = line.textContent;
    }});
    await Promise.all([boot, sync]);
    await new Promise(function (r) {{ setTimeout(r, 80); }});
    I.initPageTranslations();
    out.settled = line.textContent;
    out.word = I.t('lobby.andOthersOne');
    out.language = I.getLanguage();
    console.log(JSON.stringify(out));
}})();
"""


def _race(first: str, second: str, delays: dict[str, int], gap: int) -> dict:
    script = _RACE_SCRIPT.format(
        stub=json.dumps(str(_STUB)),
        i18n=json.dumps(str(_I18N)),
        i18njs=json.dumps(str(_JS / "i18n.js")),
        delays=json.dumps(delays),
        first=json.dumps(first),
        second=json.dumps(second),
        gap=gap,
    )
    out = subprocess.run(
        ["node", "-e", script], capture_output=True, text=True, check=True
    )
    return json.loads(out.stdout)


@_NEEDS_NODE
def test_a_slow_browser_language_does_not_overwrite_the_game_language() -> None:
    """The host's tab: German is still on the wire when English is asked for.

    English is the fallback and already loaded, so it is installed at once;
    the German bundle arrives afterwards and used to win.
    """
    result = _race("de", "en", {"en": 5, "de": 60}, gap=20)

    assert result["language"] == "en"
    assert result["settled"] == "Leaderboard"
    assert result["word"] == "+ 1 more"
    assert result["atSync"] == "Leaderboard"


@_NEEDS_NODE
def test_the_page_boot_sweep_waits_for_the_language_that_won() -> None:
    """init() resolves into a page-wide sweep. Resolving it on the superseded
    load would paint from a bundle that is no longer the active one."""
    result = _race("de", "en", {"en": 5, "de": 60}, gap=20)

    assert result["readyAtBoot"] is True
    assert result["atBoot"] == "Leaderboard"


@_NEEDS_NODE
def test_the_same_holds_between_two_fetched_languages() -> None:
    """Neither side is the preloaded fallback: a Spanish game on a German
    browser, with the German file the slower of the two."""
    result = _race("de", "es", {"en": 5, "de": 80, "es": 10}, gap=20)

    assert result["language"] == "es"
    assert result["settled"] == "Clasificación"
    assert result["word"] == "+ 1 más"
    assert result["atBoot"] == "Clasificación"


@_NEEDS_NODE
def test_the_switch_can_arrive_before_the_fallback_has() -> None:
    """Earlier still: even the English fallback is not in yet."""
    result = _race("de", "en", {"en": 40, "de": 60}, gap=5)

    assert result["language"] == "en"
    assert result["settled"] == "Leaderboard"
    assert result["atBoot"] == "Leaderboard"


@_NEEDS_NODE
def test_an_undisturbed_load_is_unchanged() -> None:
    """Guards the guard: with the switch arriving after the first load is
    done, each language is simply shown in turn."""
    result = _race("de", "es", {"en": 1, "de": 2, "es": 2}, gap=40)

    assert result["atBoot"] == "Rangliste"
    assert result["settled"] == "Clasificación"


# ---------------------------------------------------------------------------
# The header toggles
# ---------------------------------------------------------------------------


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


def _without_comments(source: str) -> str:
    source = re.sub(r"/\*.*?\*/", "", source, flags=re.S)
    return re.sub(r"^\s*//.*$", "", source, flags=re.M)


def test_the_game_language_repaints_the_header_toggles() -> None:
    """The sound and large-text buttons write their own aria-label and title,
    so the sweep cannot reach them. The language picker has repainted them
    since #492; the game language arriving has to do the same, or a German
    browser keeps the German labels in an English game — measured in the
    browser while fixing #1055."""
    core = (_JS / "player-core.js").read_text("utf-8")
    sync = _without_comments(_js_function(core, "function _syncServerLanguage("))

    assert "_flushToggleLabels()" in sync
    assert sync.index("setLanguage(") < sync.index("_flushToggleLabels()")


def test_the_fix_reached_the_shipped_bundle() -> None:
    """player.html loads the bundle, not the modules — without a rebuild the
    change is a no-op on every real phone."""
    bundle = (_JS / "player.bundle.js").read_text("utf-8")

    assert "summary += '  ' + t('lobby.andOthersOne')" not in bundle
    assert "more.setAttribute('data-i18n', key)" in bundle
    assert '<span data-i18n="lobby.you">' in bundle
