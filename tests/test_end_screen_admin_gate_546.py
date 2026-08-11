"""The end screen must not throw on its admin gate (#546).

``player-end.js`` read a free ``currentPlayer`` when deciding whether to show
the host's "Start New Game" block. That identifier only exists as a local
inside ``player-reveal.js`` / ``player-lobby.js``, and every player module is
its own IIFE — so the read threw a ReferenceError in the middle of
``updateEndView``. Everything before it (winner hero, highlights, scoreboard,
share card) had already rendered, which is why the screen looked healthy;
everything from that line on did not run, so ``end-admin-controls`` and
``end-player-message`` both kept their ``hidden`` class. The host lost the
button, the player lost the explanation, and the message handler swallowed the
error.

Two tests, because either alone would be weak:

* the shipped module is executed under node against a DOM stub and must
  un-hide the right block — that is the behaviour that broke;
* no player module may read an identifier that another module declares
  locally — that is the shape of the bug, and it would come back the next
  time someone copies a line between modules.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
JS_DIR = REPO / "custom_components" / "quizify" / "www" / "js"
END_JS = JS_DIR / "player-end.js"
BUNDLE_JS = JS_DIR / "player.bundle.js"

# Modules that declare `currentPlayer` as their own local. A reader outside
# these files is looking at a variable that does not exist for it.
_OWNERS = {"player-reveal.js", "player-lobby.js"}

_HARNESS = r"""
const fs = require('fs');

// --- minimal DOM ---------------------------------------------------------
function el(id) {
  return {
    id, textContent: '', innerHTML: '', hidden: false, style: {},
    _classes: new Set(id === 'end-admin-controls' || id === 'end-player-message' ? ['hidden'] : []),
    classList: {
      add(c) { this._o._classes.add(c); },
      remove(c) { this._o._classes.delete(c); },
      contains(c) { return this._o._classes.has(c); },
      toggle(c, on) { on ? this._o._classes.add(c) : this._o._classes.delete(c); },
    },
    appendChild() {}, removeChild() {}, setAttribute() {}, removeAttribute() {},
    getAttribute() { return null; }, addEventListener() {},
    querySelector() { return null; }, querySelectorAll() { return []; },
    insertAdjacentHTML() {}, focus() {}, cloneNode() { return el(id); },
  };
}
const nodes = {};
function get(id) {
  if (!nodes[id]) { const e = el(id); e.classList._o = e; nodes[id] = e; }
  return nodes[id];
}
global.document = {
  getElementById: get,
  createElement: (t) => get('created-' + t),
  querySelector: () => null,
  querySelectorAll: () => [],
  addEventListener() {},
  body: get('body'),
};
global.window = {
  scrollTo() {},
  addEventListener() {},
  setTimeout: (f) => f && 0,
  QuizifyPlayerUtils: {
    state: { playerName: 'Host', isAdmin: false },
    escapeHtml: (s) => String(s == null ? '' : s),
    formatPoints: (n) => String(n),
    showView() {},
    paintUiIcons() {},
    feedbackIconHtml: () => '',
    animateValue() {},
    renderLeaderboard() {},
    setupCollapsibles() {},
  },
  QuizifyUtils: { escapeHtml: (s) => String(s == null ? '' : s) },
};
global.requestAnimationFrame = (f) => f && 0;
global.setTimeout = (f) => f && 0;

eval(fs.readFileSync(process.argv[2], 'utf8'));

const api = global.window.QuizifyPlayerEnd;
const payload = JSON.parse(process.argv[3]);
global.window.QuizifyPlayerUtils.state.isAdmin = payload.stateIsAdmin;

let threw = null;
try {
  (api.updateEndView || api.render || api.update)(payload.data);
} catch (e) {
  threw = String(e && e.message || e);
}
process.stdout.write(JSON.stringify({
  threw,
  adminHidden: get('end-admin-controls').classList.contains('hidden'),
  messageHidden: get('end-player-message').classList.contains('hidden'),
}));
"""


def _require_node() -> None:
    if shutil.which("node") is not None:
        return
    msg = "node not available — the end-screen render check cannot run"
    if os.environ.get("QUIZIFY_REQUIRE_NODE") == "1":
        pytest.fail(msg)
    pytest.skip(msg)


def _strip_comments(src: str) -> str:
    """Drop // and /* */ comments so prose about a bug isn't mistaken for it."""
    src = re.sub(r"/\*.*?\*/", "", src, flags=re.S)
    return re.sub(r"(?m)^\s*//.*$|(?<=[;\s)])//.*$", "", src)


def _run(tmp_path: Path, *, state_is_admin: bool, row_is_admin: bool) -> dict:
    harness = tmp_path / "end-harness.js"
    harness.write_text(_HARNESS, encoding="utf-8")
    payload = {
        "stateIsAdmin": state_is_admin,
        "data": {
            "leaderboard": [
                {"name": "Host", "rank": 1, "score": 20, "is_admin": row_is_admin},
                {"name": "Guest", "rank": 2, "score": 10},
            ],
            "superlatives": [],
            "total_rounds": 5,
        },
    }
    result = subprocess.run(
        ["node", str(harness), str(END_JS), json.dumps(payload)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, f"node harness failed:\n{result.stderr}"
    return json.loads(result.stdout)


def test_end_screen_renders_without_throwing(tmp_path: Path) -> None:
    """The render completes — this is what the ReferenceError prevented.

    Deliberately with ``state.isAdmin`` false: the broken expression was
    ``!!state.isAdmin || !!(currentPlayer && …)``, so a true left side
    short-circuited past the bad read. The throw only ever happened when the
    fallback was actually consulted — a host whose page reloaded, and every
    non-host player.
    """
    _require_node()
    out = _run(tmp_path, state_is_admin=False, row_is_admin=False)
    assert out["threw"] is None, f"updateEndView threw: {out['threw']}"


def test_host_gets_the_controls(tmp_path: Path) -> None:
    """``state.isAdmin`` alone must reveal the host block and hide the note."""
    _require_node()
    out = _run(tmp_path, state_is_admin=True, row_is_admin=False)
    assert out["adminHidden"] is False, "host sees no Start New Game block"
    assert out["messageHidden"] is True


def test_leaderboard_flag_also_counts(tmp_path: Path) -> None:
    """The second signal still works: is_admin on the viewer's own row.

    Keeping it is the point — the fix removes a broken read, not a feature.
    """
    _require_node()
    out = _run(tmp_path, state_is_admin=False, row_is_admin=True)
    assert out["adminHidden"] is False
    assert out["messageHidden"] is True


def test_plain_player_gets_the_message(tmp_path: Path) -> None:
    """Neither signal → the waiting note shows and the host block stays away."""
    _require_node()
    out = _run(tmp_path, state_is_admin=False, row_is_admin=False)
    assert out["adminHidden"] is True
    assert out["messageHidden"] is False, "player sees neither controls nor note"


def test_no_module_reads_another_modules_local() -> None:
    """Guard the shape of the bug, not just this one line.

    ``currentPlayer`` is a local of player-reveal.js / player-lobby.js. Any
    other module reading it is reading a variable that does not exist there —
    which is a ReferenceError at runtime, not a quiet ``undefined``.
    """
    offenders = []
    for path in sorted(JS_DIR.glob("player-*.js")):
        if path.name in _OWNERS:
            continue
        code = _strip_comments(path.read_text(encoding="utf-8"))
        if re.search(r"\bcurrentPlayer\b", code):
            offenders.append(path.name)
    assert not offenders, (
        f"{offenders} read `currentPlayer`, which only exists inside "
        f"{sorted(_OWNERS)}. Each player module is its own IIFE, so this "
        "throws (#546) — resolve the viewer's row from the leaderboard instead."
    )


def test_bundle_matches_the_fixed_source() -> None:
    """The shipped bundle must carry the fix, not a stale copy."""
    code = _strip_comments(BUNDLE_JS.read_text(encoding="utf-8"))
    assert "!!(me && me.is_admin)" in code, (
        "player.bundle.js does not contain the fixed admin gate — "
        "run scripts/build_bundle.py"
    )
