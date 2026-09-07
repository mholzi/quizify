"""Both post-game buttons died in the second game (#877).

Two buttons are disabled on tap so they cannot double-fire, and neither was
ever put back:

* ``player-lightning.js`` — the lightning recap's **Continue** sets
  ``this.disabled = true`` and sends ``next_question``.
* ``player-end.js`` — the finale's **Play again** sets ``disabled = true`` and
  replaces its label with an hourglass, then sends ``play_again``.

That is correct for one game and wrong for every game after it, because a
rematch never takes the phone off the page: ``play_again`` broadcasts a
``game_state`` and nobody navigates. The server clears ``_lightning_fired``
(``game/state.py``), so game two has a lightning round of its own — and its
recap arrived carrying game one's disabled button. Same story at the second
finale, where **Play again** was a spent hourglass and the only way into a
third game was "New game", which throws the room back to the admin setup
screen.

Who is hit is the point: the admin tab redirects to the phone on start, so the
host plays along by default. The person who has to move the game on is the one
looking at the dead button, and the only way out is a reload nobody thinks to
try.

The tests below run the real modules against ``tests/fixtures/dom_stub.js`` and
press the buttons for real: render, tap, render again, tap again. The second
tap is the whole test — before the fix it sends nothing.
"""

from __future__ import annotations

import json
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


def _node(script: str) -> dict:
    out = subprocess.run(
        ["node", "-e", script], capture_output=True, text=True, check=True
    )
    return json.loads(out.stdout)


# ---------------------------------------------------------------------------
# The lightning recap's Continue button
# ---------------------------------------------------------------------------

_LIGHTNING = """
require({stub});

QZ.els([
    'lightning-recap-admin',
    'lightning-recap-player-msg',
    'lightning-recap-continue-btn',
    'lightning-recap-leaderboard',
    'lightning-recap-grid'
]);

window.QuizifyPlayerUtils = {{
    state: {{ isAdmin: true, playerName: 'Markus' }},
    showView: function () {{}},
    escapeHtml: function (s) {{ return String(s); }},
    renderMedalStandings: function () {{}}
}};

QZ.load({lightning});

var sent = [];
var L = window.QuizifyPlayerLightning;
L.setSend(function (type) {{ sent.push(type); }});
L.init();

var btn = document.getElementById('lightning-recap-continue-btn');
var recap = {{ recap: {{ questions: [], leaderboard: [] }} }};

// Game one: the recap arrives, the host taps Continue, the main game resumes.
L.handleLightningRecap(recap);
var armedFirst = !btn.disabled;
btn.click();
var sentFirst = sent.length;
var disabledAfterTap = btn.disabled;

// Rematch. Nobody navigates; the phone is still on this page. Game two runs
// its own lightning round and the recap renders again.
L.handleLightningRecap(recap);
var armedSecond = !btn.disabled;
btn.click();
var sentSecond = sent.length;

console.log(JSON.stringify({{
    armedFirst: armedFirst,
    disabledAfterTap: disabledAfterTap,
    armedSecond: armedSecond,
    sent: sent,
    sentFirst: sentFirst,
    sentSecond: sentSecond
}}));
"""


def _run_lightning() -> dict:
    return _node(
        _LIGHTNING.format(
            stub=json.dumps(str(_STUB)),
            lightning=json.dumps(str(_JS / "player-lightning.js")),
        )
    )


@_NEEDS_NODE
def test_continue_still_fires_in_the_second_game() -> None:
    """The regression itself: two lightning rounds, two taps, two advances."""
    result = _run_lightning()

    assert result["sentFirst"] == 1
    assert result["sentSecond"] == 2, (
        "the second lightning recap's Continue did nothing — the host is stuck "
        "on the recap with no way to resume the game"
    )
    assert result["sent"] == ["next_question", "next_question"]


@_NEEDS_NODE
def test_the_recap_hands_over_an_armed_continue_button() -> None:
    result = _run_lightning()

    assert result["armedFirst"] is True
    assert result["armedSecond"] is True


@_NEEDS_NODE
def test_one_tap_still_only_counts_once() -> None:
    """The re-arm must not cost what the disable bought: within one recap the
    button is still spent after the tap, so a double-tap cannot skip a
    question."""
    result = _run_lightning()

    assert result["disabledAfterTap"] is True


# ---------------------------------------------------------------------------
# The finale's Play again button
# ---------------------------------------------------------------------------

_END = """
require({stub});
QZ.serveI18n({i18n});
QZ.load({i18njs});

QZ.els([
    'end-admin-controls',
    'end-player-message',
    'end-meta-line',
    'end-hero-name',
    'end-hero-score',
    'end-chiprow',
    'end-highlights-section',
    'end-scoreboard',
    'end-share-section',
    'end-share-strip',
    'end-share-title',
    'end-share-facts',
    'end-share-btn',
    'end-share-status',
    'play-again-same-btn',
    'new-game-btn'
]);

var playAgain = document.getElementById('play-again-same-btn');
// The page ships the button with its i18n key and its English label, and the
// startup sweep fills it in — reproduce that starting point.
playAgain.setAttribute('data-i18n', 'admin.playAgainSame');
playAgain.textContent = 'Play again';

var sent = [];
window.WebSocket = {{ OPEN: 1 }};
window.QuizifyPlayerUtils = {{
    state: {{
        isAdmin: true,
        playerName: 'Markus',
        ws: {{
            readyState: 1,
            send: function (raw) {{ sent.push(JSON.parse(raw).type); }}
        }}
    }},
    showView: function () {{}},
    escapeHtml: function (s) {{ return String(s); }}
}};
window.QuizifyUtils = {{}};

QZ.load({end});

var E = window.QuizifyPlayerEnd;
var finale = {{
    leaderboard: [
        {{ name: 'Markus', entrant_id: 'Markus', rank: 1, score: 42, is_admin: true }},
        {{ name: 'Anna', entrant_id: 'Anna', rank: 2, score: 30 }}
    ],
    superlatives: [],
    total_rounds: 5
}};

(async function () {{
    await window.QuizifyI18n.init('de');

    // Game one ends. handleFinale renders the screen and wires the CTAs.
    E.updateEndView(finale);
    E.setupNewGameButton();
    var labelFirst = playAgain.textContent;
    var armedFirst = !playAgain.disabled;

    playAgain.click();
    var sentFirst = sent.length;
    var labelWhileWaiting = playAgain.textContent;
    var disabledAfterTap = playAgain.disabled;

    // Rematch: the phone stays on this page and the next finale renders here.
    E.updateEndView(finale);
    E.setupNewGameButton();
    var labelSecond = playAgain.textContent;
    var armedSecond = !playAgain.disabled;

    playAgain.click();
    var sentSecond = sent.length;

    console.log(JSON.stringify({{
        labelFirst: labelFirst,
        armedFirst: armedFirst,
        labelWhileWaiting: labelWhileWaiting,
        disabledAfterTap: disabledAfterTap,
        labelSecond: labelSecond,
        armedSecond: armedSecond,
        sent: sent,
        sentFirst: sentFirst,
        sentSecond: sentSecond
    }}));
}})();
"""


def _run_end() -> dict:
    return _node(
        _END.format(
            stub=json.dumps(str(_STUB)),
            i18n=json.dumps(str(_I18N)),
            i18njs=json.dumps(str(_JS / "i18n.js")),
            end=json.dumps(str(_JS / "player-end.js")),
        )
    )


@_NEEDS_NODE
def test_play_again_still_fires_at_the_second_finale() -> None:
    result = _run_end()

    assert result["sentFirst"] == 1
    assert result["sentSecond"] == 2, (
        "the second finale's Play again did nothing — a third game was only "
        "reachable through New game, which resets to the admin setup screen"
    )
    assert result["sent"] == ["play_again", "play_again"]


@_NEEDS_NODE
def test_the_second_finale_shows_a_label_not_an_hourglass() -> None:
    """And in the room's language, because the label is re-read from i18n
    rather than hard-coded back."""
    result = _run_end()

    assert result["labelFirst"] == "Nochmal spielen"
    assert "⏳" in result["labelWhileWaiting"]
    assert result["labelSecond"] == "Nochmal spielen"


@_NEEDS_NODE
def test_the_finale_hands_over_an_armed_play_again_button() -> None:
    result = _run_end()

    assert result["armedFirst"] is True
    assert result["armedSecond"] is True


@_NEEDS_NODE
def test_one_rematch_per_tap() -> None:
    """The hourglass state still holds within a single finale."""
    result = _run_end()

    assert result["disabledAfterTap"] is True
