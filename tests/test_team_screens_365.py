"""The team screens, executed rather than read (#365, part 2).

The lobby flow was designed from both sides — she opens the team while he
still sees nothing, and his joining has to land on her screen. What the server
sends is pinned in ``test_team_wire_365.py``; what the phone *does* with it is
pinned here, by running the shipped module under node against a DOM stub.

Three things are worth this much machinery:

* route C only works if answering "alone" actually ends the question. A block
  that keeps asking is worse than no question at all;
* the last member leaving dissolves the team, and that arrives as a roster
  broadcast rather than as an answer to anything the player did. The screen has
  to notice and say so;
* a tap in team mode must not lock the phone. That is the whole re-decision
  feature, and it lives in one early return in ``handleAnswerClick``.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
JS_DIR = REPO / "custom_components" / "quizify" / "www" / "js"
TEAM_JS = JS_DIR / "player-team.js"
GAME_JS = JS_DIR / "player-game.js"
BUNDLE_JS = JS_DIR / "player.bundle.js"

_HARNESS = r"""
const fs = require('fs');

function makeEl(id) {
  const e = {
    id, textContent: '', innerHTML: '', value: '', disabled: false,
    dataset: {}, style: {}, children: [], _attrs: {},
    _classes: new Set(),
    _handlers: {},
    classList: {
      add(c) { e._classes.add(c); },
      remove(c) { e._classes.delete(c); },
      contains(c) { return e._classes.has(c); },
      toggle(c, on) { if (on === undefined) { e._classes.has(c) ? e._classes.delete(c) : e._classes.add(c); } else if (on) { e._classes.add(c); } else { e._classes.delete(c); } },
    },
    addEventListener(kind, fn) { (e._handlers[kind] = e._handlers[kind] || []).push(fn); },
    click() { (e._handlers.click || []).forEach((fn) => fn({ target: e, preventDefault() {} })); },
    appendChild(child) { e.children.push(child); },
    remove() {},
    setAttribute(k, v) { e._attrs[k] = v; },
    getAttribute(k) { return e._attrs[k] === undefined ? null : e._attrs[k]; },
    querySelector() { return null; },
    querySelectorAll() { return []; },
    focus() {},
    closest() { return null; },
  };
  return e;
}

// Elements the shipped markup ships with a `hidden` class. Seeding them is
// not cosmetic: `frozen-overlay` without it reads as a live freeze lockout,
// and every answer tap would be swallowed before it reached the server.
const HIDDEN_AT_REST = [
  'team-pick', 'team-mine', 'team-note', 'team-name-row', 'team-name-hint',
  'team-empty', 'team-chip', 'frozen-overlay', 'submitted-confirmation',
  'estimate-submitted-confirmation',
];

const nodes = {};
function get(id) {
  if (!nodes[id]) {
    nodes[id] = makeEl(id);
    if (HIDDEN_AT_REST.indexOf(id) !== -1) nodes[id]._classes.add('hidden');
  }
  return nodes[id];
}

// The answer buttons are the only collection the modules query by selector.
const answerButtons = [0, 1, 2].map((i) => {
  const b = makeEl('answer-' + i);
  b.dataset.index = String(i);
  return b;
});

get('answer-buttons').querySelectorAll = (sel) =>
  (sel.indexOf('.answer-btn') !== -1 ? answerButtons : []);

global.document = {
  getElementById: get,
  createElement: (tag) => makeEl('created-' + tag),
  querySelector: () => null,
  querySelectorAll: (sel) => (sel.indexOf('.answer-btn') !== -1 ? answerButtons : []),
  addEventListener() {},
  body: get('body'),
};
global.window = {
  addEventListener() {},
  QuizifyPlayerUtils: {
    state: { playerName: 'Anna', isAdmin: false },
    escapeHtml: (s) => String(s == null ? '' : s),
    showToast() {},
    showView() {},
    setupCollapsibles() {},
    formatPoints: (n) => String(n),
  },
  QuizifyI18n: { t: (k, p) => (p && p.name ? k + ':' + p.name : k) },
};
global.WebSocket = { OPEN: 1 };
global.requestAnimationFrame = (f) => f && 0;
global.setTimeout = (f, ms) => { global.__timers.push({ fn: f, ms: ms }); return global.__timers.length; };
global.clearTimeout = () => {};
global.__timers = [];

eval(fs.readFileSync(process.argv[2], 'utf8'));   // player-team.js
eval(fs.readFileSync(process.argv[3], 'utf8'));   // player-game.js

const team = global.window.QuizifyPlayerTeam;
const game = global.window.QuizifyPlayerGame;
const scenario = process.argv[4];
const sent = [];
team.setupLobby((type, data) => sent.push({ type, data }));

const out = { sent, threw: null };
try {
  if (scenario === 'alone') {
    get('team-alone-btn').click();
    out.sectionHidden = get('team-section').classList.contains('hidden');
    out.pickHidden = get('team-pick').classList.contains('hidden');
  }

  if (scenario === 'together') {
    get('team-together-btn').click();
    out.askHidden = get('team-ask').classList.contains('hidden');
    out.pickHidden = get('team-pick').classList.contains('hidden');
    out.emptyHidden = get('team-empty').classList.contains('hidden');
    // He sees her team the moment the broadcast lands.
    team.handleTeamsUpdate({ teams: [{ team_id: 't1', name: 'Sofa', color: 'coral', members: ['Berta'] }] });
    out.listHtml = get('team-list').innerHTML;
    out.emptyHiddenAfter = get('team-empty').classList.contains('hidden');
  }

  if (scenario === 'open-team') {
    get('team-together-btn').click();
    get('team-open-btn').click();
    out.nameRowHidden = get('team-name-row').classList.contains('hidden');
    get('team-name-input').value = '   ';
    get('team-name-confirm').click();
  }

  if (scenario === 'joined') {
    team.handleTeamJoined({ team: { team_id: 't1', name: 'Sofa', color: 'sage', members: ['Anna'] } });
    out.mineHidden = get('team-mine').classList.contains('hidden');
    out.mineName = get('team-mine-name').textContent;
    out.mineMembers = get('team-mine-members').textContent;
    out.chipHidden = get('team-chip').classList.contains('hidden');
    out.isTeamMode = team.isTeamMode();
    // Then Jan joins — her screen has to show him.
    team.handleTeamsUpdate({ teams: [{ team_id: 't1', name: 'Sofa', color: 'sage', members: ['Anna', 'Jan'] }] });
    out.membersAfter = get('team-mine-members').textContent;
  }

  if (scenario === 'dissolved') {
    team.handleTeamJoined({ team: { team_id: 't1', name: 'Sofa', color: 'sage', members: ['Anna'] } });
    team.handleTeamsUpdate({ teams: [] });
    out.isTeamMode = team.isTeamMode();
    out.noteHidden = get('team-note').classList.contains('hidden');
    out.note = get('team-note').textContent;
    out.pickHidden = get('team-pick').classList.contains('hidden');
    out.chipHidden = get('team-chip').classList.contains('hidden');
  }

  if (scenario === 'answer-dots') {
    team.handleTeamJoined({ team: { team_id: 't1', name: 'Sofa', color: 'sage', members: ['Anna', 'Jan'] } });
    team.handleTeamAnswer({ team_id: 't1', answer_index: 1, set_by: 'Jan', lock_seconds: 2, members: ['Anna', 'Jan'] });
    out.marked = answerButtons.map((b) => b.classList.contains('has-team-answer'));
    out.dotHtml = answerButtons[1].children.map((c) => c.innerHTML).join('');
    out.locked = answerButtons.map((b) => b.disabled);
    out.chipSet = get('team-chip-set').textContent;
    // The lock expires and hands the buttons back.
    global.__timers.forEach((t) => t.fn());
    out.unlocked = answerButtons.map((b) => b.disabled);
    // A new round clears the dots.
    team.resetRound();
    out.markedAfterReset = answerButtons.map((b) => b.classList.contains('has-team-answer'));
  }

  if (scenario === 'tap-solo' || scenario === 'tap-team') {
    if (scenario === 'tap-team') {
      team.handleTeamJoined({ team: { team_id: 't1', name: 'Sofa', color: 'sage', members: ['Anna', 'Jan'] } });
    }
    game.resetSubmissionState();
    game.handleAnswerClick(2, (type, data) => sent.push({ type, data }));
    out.disabledAfterTap = answerButtons.map((b) => b.disabled);
    out.confirmationHidden = get('submitted-confirmation').classList.contains('hidden');
    // A second tap: in team mode it must still go through (re-decide), solo
    // it must not.
    game.handleAnswerClick(0, (type, data) => sent.push({ type, data }));
  }
} catch (e) {
  out.threw = String((e && e.stack) || e);
}
process.stdout.write(JSON.stringify(out));
"""


def _require_node() -> None:
    if shutil.which("node") is not None:
        return
    msg = "node not available — the team-screen checks cannot run"
    if os.environ.get("QUIZIFY_REQUIRE_NODE") == "1":
        pytest.fail(msg)
    pytest.skip(msg)


def _run(tmp_path: Path, scenario: str) -> dict:
    _require_node()
    harness = tmp_path / "team-harness.js"
    harness.write_text(_HARNESS, encoding="utf-8")
    result = subprocess.run(
        ["node", str(harness), str(TEAM_JS), str(GAME_JS), scenario],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, f"node harness failed:\n{result.stderr}"
    out = json.loads(result.stdout)
    assert out["threw"] is None, out["threw"]
    return out


# ----------------------------------------------------------------------
# The lobby, route C
# ----------------------------------------------------------------------


def test_answering_alone_ends_the_question(tmp_path: Path) -> None:
    """A block that keeps asking is worse than never asking."""
    out = _run(tmp_path, "alone")

    assert out["sectionHidden"] is True, "the team block must go away entirely"
    assert out["pickHidden"] is True, "and it must not have opened the list"
    assert out["sent"] == [], "saying 'alone' is not a server action"


def test_only_saying_with_someone_reveals_the_list(tmp_path: Path) -> None:
    out = _run(tmp_path, "together")

    assert out["askHidden"] is True
    assert out["pickHidden"] is False
    assert out["emptyHidden"] is False, "an empty list has to say it is empty"
    assert "Sofa" in out["listHtml"], "her team must appear on his screen"
    assert out["emptyHiddenAfter"] is True


def test_an_empty_name_is_sent_as_empty_not_refused(tmp_path: Path) -> None:
    """The name is a suggestion. Whitespace is the same as no answer, and the
    server picks one — a required field is the hurdle route C avoids."""
    out = _run(tmp_path, "open-team")

    assert out["nameRowHidden"] is False
    assert out["sent"] == [{"type": "create_team", "data": {"name": ""}}]


def test_the_team_screen_replaces_the_list_and_shows_who_is_in_it(
    tmp_path: Path,
) -> None:
    out = _run(tmp_path, "joined")

    assert out["mineHidden"] is False
    assert out["mineName"] == "Sofa"
    assert out["isTeamMode"] is True
    assert out["chipHidden"] is False, "the indicator says which entry is yours"
    assert out["mineMembers"] == "teams.waitingForTeammate"
    assert out["membersAfter"] == "Jan", "his joining lands on her screen"


def test_a_dissolved_team_is_noticed_and_explained(tmp_path: Path) -> None:
    """It arrives as a roster broadcast, not as an answer to anything she did."""
    out = _run(tmp_path, "dissolved")

    assert out["isTeamMode"] is False
    assert out["noteHidden"] is False
    assert out["note"] == "teams.dissolved"
    assert out["pickHidden"] is False, "she is put back in front of the list"
    assert out["chipHidden"] is True, "and the indicator goes with the team"


# ----------------------------------------------------------------------
# The question screen
# ----------------------------------------------------------------------


def test_dots_land_on_the_standing_answer_and_the_lock_runs_out(
    tmp_path: Path,
) -> None:
    out = _run(tmp_path, "answer-dots")

    assert out["marked"] == [False, True, False], "dots sit on one row only"
    assert "A" in out["dotHtml"] and "J" in out["dotHtml"], "every member shows"
    assert "is-setter" in out["dotHtml"], "the one who set it is the solid dot"
    assert out["chipSet"] == "teams.standingAnswer:Jan"
    assert out["locked"] == [True, True, True], "the brake is the team's"
    assert out["unlocked"] == [False, False, False], "and it lets go again"
    assert out["markedAfterReset"] == [False, False, False]


def test_a_solo_tap_still_locks_the_phone(tmp_path: Path) -> None:
    """The base game is untouched: a solo answer is final the moment it lands."""
    out = _run(tmp_path, "tap-solo")

    assert out["disabledAfterTap"] == [True, True, True]
    assert out["confirmationHidden"] is False
    assert [m["data"]["answer_index"] for m in out["sent"]] == [2], (
        "the second tap must not reach the server"
    )


def test_a_team_tap_leaves_the_phone_open_for_a_re_decision(
    tmp_path: Path,
) -> None:
    """This early return is the whole re-decision feature."""
    out = _run(tmp_path, "tap-team")

    assert out["disabledAfterTap"] == [False, False, False]
    assert out["confirmationHidden"] is True, "nothing was confirmed yet"
    assert [m["data"]["answer_index"] for m in out["sent"]] == [2, 0], (
        "a teammate changing their mind has to reach the server"
    )


def test_the_shipped_bundle_carries_the_team_module() -> None:
    """The page loads the bundle, not the modules — a forgotten rebuild ships
    a lobby whose buttons do nothing."""
    bundle = BUNDLE_JS.read_text("utf-8")

    assert "QuizifyPlayerTeam" in bundle
    assert "player-team.js" in bundle
