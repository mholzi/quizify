"""The power-up button keeps its name through the view's i18n sweep (#961).

Found in the v1.20.0-RC2 live test: in every round but the last, the phone's
power-up button read a generic "⚡ Power-Up" while the hint underneath said
"Doubles your points for this round". Instrumented on the real page, the order
on a round change was: ``renderPowerUp('double_points')`` wrote "✨ Double",
then ``showView()`` re-ran ``initPageTranslations`` on the view it revealed —
and the label still carried ``data-i18n="game.powerup"``, so the sweep put
"Power-Up" back. The final round only escaped because its flourish delays the
render past the sweep.

The test runs the real ``player-game.js`` and ``i18n.js`` against the DOM stub,
with the button markup read out of ``player.html`` so a change to the page is a
change to what is tested.
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


def _label_markup() -> str:
    html = (_WWW / "player.html").read_text("utf-8")
    match = re.search(r'<span class="powerup-label"[^>]*>[^<]*</span>', html)
    assert match, "player.html no longer has the .powerup-label span"
    return match.group(0)


_SCRIPT = """
require({stub});
QZ.serveI18n({i18n});
QZ.load({i18njs});

QZ.els(['powerup-btn', 'powerup-hint']);
var btn = document.getElementById('powerup-btn');
btn.innerHTML = {label};
btn.classList.add('hidden');

QZ.load({utils});
QZ.load({render_shared});
QZ.load({player_utils});
QZ.load({player_game});

var game = window.QuizifyPlayerGame;
var i18n = window.QuizifyI18n;

function label() {{
    return btn.querySelector('.powerup-label').textContent;
}}

(async function () {{
    var out = {{}};
    await i18n.init('en');

    // Before a power-up is dealt the sweep still translates the placeholder.
    i18n.initPageTranslations(btn);
    out.placeholder = label();

    // powerup_assigned, then showView()'s sweep a millisecond later.
    game.renderPowerUp('double_points');
    out.rendered = label();
    i18n.initPageTranslations(btn);
    out.afterSweep = label();

    // The next round deals a different one; the sweep runs again.
    game.renderPowerUp('freeze');
    i18n.initPageTranslations(btn);
    out.nextRound = label();

    // A German game names it in German, and the sweep leaves that alone too.
    await i18n.init('de');
    game.renderPowerUp('joker');
    i18n.initPageTranslations(btn);
    out.german = label();

    // No power-up: the button is hidden, as before.
    game.renderPowerUp(null);
    out.hiddenWithout = btn.classList.contains('hidden');

    console.log(JSON.stringify(out));
}})().catch(function (e) {{ console.error(e); process.exit(1); }});
"""


def _run() -> dict:
    script = _SCRIPT.format(
        stub=json.dumps(str(_STUB)),
        i18n=json.dumps(str(_I18N)),
        i18njs=json.dumps(str(_JS / "i18n.js")),
        label=json.dumps(_label_markup()),
        utils=json.dumps(str(_JS / "utils.js")),
        render_shared=json.dumps(str(_JS / "render-shared.js")),
        player_utils=json.dumps(str(_JS / "player-utils.js")),
        player_game=json.dumps(str(_JS / "player-game.js")),
    )
    out = subprocess.run(
        ["node", "-e", script], capture_output=True, text=True, check=True
    )
    return json.loads(out.stdout.strip().splitlines()[-1])


@pytest.fixture(scope="module")
def result() -> dict:
    return _run()


def _i18n(lang: str, dotted: str) -> str:
    node = json.loads((_I18N / f"{lang}.json").read_text("utf-8"))
    for part in dotted.split("."):
        node = node[part]
    return node


@_NEEDS_NODE
def test_the_sweep_does_not_overwrite_the_power_up_name(result: dict) -> None:
    """The reported screen: "Power-Up" on the button, "Double" in the hint."""
    name = _i18n("en", "powerups.double_points")
    assert name in result["rendered"], "premise: renderPowerUp names it"
    assert result["afterSweep"] == result["rendered"], result


@_NEEDS_NODE
def test_a_later_round_keeps_its_own_name(result: dict) -> None:
    assert _i18n("en", "powerups.freeze") in result["nextRound"], result
    assert result["nextRound"] != _i18n("en", "game.powerup")


@_NEEDS_NODE
def test_the_name_follows_the_game_language(result: dict) -> None:
    assert _i18n("de", "powerups.joker") in result["german"], result


@_NEEDS_NODE
def test_the_placeholder_is_still_translated_before_a_deal(result: dict) -> None:
    assert result["placeholder"] == _i18n("en", "game.powerup")


@_NEEDS_NODE
def test_no_power_up_still_hides_the_button(result: dict) -> None:
    assert result["hiddenWithout"] is True
