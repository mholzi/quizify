"""The GitHub star hint under "Start New Game" on the host finale.

One muted line below ``#new-game-btn`` in ``#admin-finale-view``: shown from
the third finished game on this device, hidden for good once its × is tapped,
and never shown when localStorage is unavailable. A game counts once — the
live ``finale`` frame and the FINALE snapshot both carry ``game_id``, and a
reload of the same finale must not count it twice.

The counting runs the real ``_updateStarHint`` / ``_dismissStarHint`` from
``admin.js`` under node (``tests/fixtures/dom_stub.js``).
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from custom_components.quizify.server.serializers import serialize_finale
from tests.test_admin_live_controls_1017 import _block

_REPO = Path(__file__).resolve().parent.parent
_WWW = _REPO / "custom_components" / "quizify" / "www"
_ADMIN_JS = _WWW / "js" / "admin.js"
_ADMIN_HTML = _WWW / "admin.html"
_STUB = Path(__file__).resolve().parent / "fixtures" / "dom_stub.js"
_KEYS = ("starHintBefore", "starHintLink", "starHintAfter", "starHintDismiss")

_NEEDS_NODE = pytest.mark.skipif(
    shutil.which("node") is None, reason="node not installed"
)


def _finale_markup() -> str:
    html = _ADMIN_HTML.read_text("utf-8")
    start = html.index('id="admin-finale-view"')
    nxt = re.search(r'<div id="[^"]+" class="view"', html[start + 1 :])
    assert nxt
    return html[start : start + 1 + nxt.start()]


def test_hint_sits_below_new_game_inside_the_finale_actions() -> None:
    view = _finale_markup()
    actions = view[view.index('class="admin-actions') :]
    assert actions.index('id="new-game-btn"') < actions.index('id="admin-star-hint"')
    hint = re.search(r'<p id="admin-star-hint"[^>]*>', view)
    assert hint and " hidden" in hint.group(0), "must start hidden"
    link = re.search(r'<a [^>]*data-i18n="admin.starHintLink"[^>]*>', view)
    assert link
    assert 'href="https://github.com/mholzi/quizify"' in link.group(0)
    assert 'target="_blank"' in link.group(0)
    assert 'rel="noopener"' in link.group(0)
    assert 'data-i18n-aria-label="admin.starHintDismiss"' in view


def test_hint_is_host_only() -> None:
    for page in ("player.html", "dashboard.html"):
        assert "starHint" not in (_WWW / page).read_text("utf-8"), page


@pytest.mark.parametrize("lang", ["de", "en", "es"])
def test_every_bundle_carries_the_hint(lang: str) -> None:
    admin = json.loads((_WWW / "i18n" / f"{lang}.json").read_text("utf-8"))["admin"]
    for key in _KEYS:
        assert admin.get(key), f"{lang}: admin.{key}"
    assert "⭐" in admin["starHintBefore"]
    assert "GitHub" in admin["starHintLink"]


def test_live_finale_frame_carries_the_game_id() -> None:
    assert serialize_finale([], [], game_id="abc123")["game_id"] == "abc123"
    assert "game_id" not in serialize_finale([], [])


_SCRIPT = r"""
require(%(stub)s);
var localStorage = QZ.window.localStorage;
var hint = QZ.el('admin-star-hint');
hint.hidden = true;
var els = { starHint: hint };

%(admin)s

var R = [];
function fin(id) { _updateStarHint(id); R.push(hint.hidden ? 0 : 1); }
fin('g1'); fin('g1'); fin('g2'); fin('g2');   // reloads of the same finale
fin('g3'); fin('g3'); fin('g4');
_dismissStarHint();
R.push(hint.hidden ? 0 : 1);
fin('g5');
var dismissed = localStorage.getItem('quizify_star_hint_dismissed');

// Storage that throws: no hint, and no exception escapes.
localStorage = { getItem: function () { throw new Error('denied'); },
                 setItem: function () { throw new Error('denied'); } };
hint.hidden = false;
_updateStarHint('g6');
var broken = hint.hidden;
_dismissStarHint();
console.log(JSON.stringify({ seq: R, dismissed: dismissed, broken: broken }));
"""


def _run() -> dict:
    source = _ADMIN_JS.read_text("utf-8")
    consts = "\n".join(
        re.search(rf"    var {name} = [^;]+;", source).group(0)
        for name in (
            "STAR_HINT_GAMES_KEY",
            "STAR_HINT_DISMISSED_KEY",
            "STAR_HINT_MIN_GAMES",
        )
    )
    parts = [
        consts,
        _block(source, "function _updateStarHint(gameId) {"),
        _block(source, "function _dismissStarHint() {"),
    ]
    script = _SCRIPT % {"stub": json.dumps(str(_STUB)), "admin": "\n\n".join(parts)}
    out = subprocess.run(
        ["node", "-e", script], capture_output=True, text=True, check=True
    )
    return json.loads(out.stdout)


@_NEEDS_NODE
def test_shown_from_the_third_game_counted_once_each_until_dismissed() -> None:
    r = _run()
    # g1, g1, g2, g2 hidden; g3 (third game) shown, reload still shown, g4
    # shown; dismiss hides it, and g5 keeps it hidden.
    assert r["seq"] == [0, 0, 0, 0, 1, 1, 1, 0, 0]
    assert r["dismissed"] == "1"


@_NEEDS_NODE
def test_unavailable_storage_never_shows_the_hint() -> None:
    assert _run()["broken"] is True


def test_finale_handler_feeds_the_game_id_and_the_x_is_wired() -> None:
    source = _ADMIN_JS.read_text("utf-8")
    assert "_updateStarHint(msg && msg.game_id);" in _block(
        source, "function handleFinale(msg) {"
    )
    assert "on(els.starHintDismiss, 'click', _dismissStarHint);" in source
