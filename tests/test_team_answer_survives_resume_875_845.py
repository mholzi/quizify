"""The standing team answer has to survive a pause (#875), and the tracker has
to say which row is yours (#845).

**#875.** The dots on the answer row, the "set by Anna" chip and the change lock
all arrive in one live ``team_answer`` frame, sent the moment a member taps.
Nothing re-sends it. The projected snapshot — the thing a phone rebuilds its
whole round from — carried nothing about ``Team.current_answer`` at all, and
``handleQuestionStarted`` opens with ``team.resetRound()``.

That is worse than a reload, because ``resume_game`` and the admin auto-resume
fan a projected snapshot out to *every* live phone at once. Anna taps B, the
host pauses, the host resumes: both phones now show an unanswered question, and
Ben overwrites B believing nothing was set. The same hole is why
``_broadcast_team_answer`` may skip a member with no shuffle — its comment says
"their client re-reads the answer from the next projected snapshot", which
until now never happened.

**#875, solo half.** A resume snapshot also lost the ``is-selected`` mark on a
solo phone: ``handleQuestionStarted`` calls ``renderQuestion`` (which re-applies
the mark) and then ``resetSubmissionState`` (which strips it), and the
``lockSubmitted`` that follows only disabled the buttons. The player came back
to three dead buttons and no visible pick.

**#845.** ``serialize_answer_progress`` sends teams in team mode since #835,
which is what the television needed. The phone renders the same payload and
marks "this one is you" by comparing the viewer's name against the row's name —
and a player's name never equals a team name, so in team mode no row lit up.
The rows now carry the ``entrant_id`` the leaderboard has had since #814.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from custom_components.quizify.game.state import QuizifyGameState
from custom_components.quizify.game.team import ANSWER_CHANGE_LOCK_SECONDS
from custom_components.quizify.server.round_message_builder import RoundMessageBuilder
from custom_components.quizify.server.serializers import (
    serialize_answer_progress,
    serialize_state_snapshot,
)

REPO = Path(__file__).resolve().parent.parent
JS = REPO / "custom_components" / "quizify" / "www" / "js"
PLAYER_CORE = JS / "player-core.js"
PLAYER_GAME = JS / "player-game.js"
PLAYER_TEAM = JS / "player-team.js"
RENDER_SHARED = JS / "render-shared.js"

needs_node = pytest.mark.skipif(
    shutil.which("node") is None, reason="node not installed"
)


# ===========================================================================
# The server: what the snapshot carries
# ===========================================================================


def _ws() -> MagicMock:
    ws = MagicMock()
    ws.closed = False
    return ws


class _Runtime:
    def __init__(self, tmp_path: Path) -> None:
        self.data_dir = tmp_path


def _team_game(tmp_path: Path) -> QuizifyGameState:
    """Anna and Jan on the sofa, Mira on the balcony. One live question."""
    st = QuizifyGameState(runtime=_Runtime(tmp_path), entry_id="test")
    for name in ("Anna", "Jan", "Mira"):
        st.add_player(name, _ws())
    st.create_team("Sofa", "Anna")
    st.join_team(st.get_team_of("Anna")["team_id"], "Jan")
    st.create_team("Balkon", "Mira")
    # Pinned to a multiple-choice category: the mixed pool includes estimate
    # packs (#275), which have no answers and therefore no shuffle.
    st.start_game(
        category="geographie", language="de", num_rounds=5, difficulty="easy"
    )
    assert st.start_next_question() is not None
    # Two members who see the same three answers in different orders — the
    # whole reason the index cannot be sent as one number (#253).
    st.set_player_shuffle("Anna", [0, 1, 2])
    st.set_player_shuffle("Jan", [2, 1, 0])
    st.set_player_shuffle("Mira", [1, 0, 2])
    return st


def _solo_game(tmp_path: Path) -> QuizifyGameState:
    st = QuizifyGameState(runtime=_Runtime(tmp_path), entry_id="test")
    for name in ("Anna", "Ben"):
        st.add_player(name, _ws())
    st.start_game(
        category="geographie", language="de", num_rounds=5, difficulty="easy"
    )
    assert st.start_next_question() is not None
    st.set_player_shuffle("Anna", [2, 0, 1])
    return st


def _team_of(st: QuizifyGameState, name: str):  # noqa: ANN202
    return next(t for t in st.team_registry.all_teams() if t.name == name)


def _project(st: QuizifyGameState, name: str) -> dict:
    return RoundMessageBuilder().project_snapshot_for_player(
        st,
        snapshot=serialize_state_snapshot(st),
        player=st.get_player(name),
    )


def test_the_projected_snapshot_carries_the_standing_team_answer(
    tmp_path: Path,
) -> None:
    """#875 itself: pause + resume must not erase what the team decided."""
    st = _team_game(tmp_path)
    _team_of(st, "Sofa").set_answer(1, "Jan")

    block = _project(st, "Anna").get("team_answer")

    assert block is not None, (
        "the projected snapshot carries nothing about the standing answer, so "
        "every resume wipes the dots off every phone in the team (#875)"
    )
    assert block["set_by"] == "Jan"
    assert block["members"] == ["Anna", "Jan"]
    assert block["team_id"] == _team_of(st, "Sofa").team_id


def test_each_member_gets_the_answer_in_their_own_order(tmp_path: Path) -> None:
    """One canonical number would put the dot on the wrong row for everybody
    but the member who tapped — the same reason the live broadcast remaps it."""
    st = _team_game(tmp_path)
    _team_of(st, "Sofa").set_answer(1, "Jan")

    # Canonical answer 1 sits at position 1 in Anna's order and at position 1
    # in Jan's too, so pick an answer where the two orders disagree.
    _team_of(st, "Sofa").answered_at = None  # release the change lock
    _team_of(st, "Sofa").set_answer(0, "Jan")

    anna = _project(st, "Anna")["team_answer"]
    jan = _project(st, "Jan")["team_answer"]

    assert anna["answer_index"] == 0, "Anna sees the answers unshuffled"
    assert jan["answer_index"] == 2, "Jan sees the same answer in third place"


def test_the_lock_is_what_is_left_of_it_not_a_fresh_two_seconds(
    tmp_path: Path,
) -> None:
    """A phone coming back from a pause must not be braked again from zero."""
    st = _team_game(tmp_path)
    team = _team_of(st, "Sofa")
    team.set_answer(1, "Jan")
    # The tap was long enough ago that the lock has run out.
    team.answered_at -= ANSWER_CHANGE_LOCK_SECONDS + 5

    assert _project(st, "Anna")["team_answer"]["lock_seconds"] == 0.0


def test_no_block_before_the_team_has_answered(tmp_path: Path) -> None:
    """Nothing to repaint is not the same as an answer of zero."""
    st = _team_game(tmp_path)

    assert "team_answer" not in _project(st, "Anna")
    # Mira's team has not answered either, and neither has anybody else's.
    _team_of(st, "Sofa").set_answer(1, "Jan")
    assert "team_answer" not in _project(st, "Mira")


def test_a_solo_game_never_grows_a_team_answer_block(tmp_path: Path) -> None:
    st = _solo_game(tmp_path)

    assert "team_answer" not in _project(st, "Anna")


# ---------------------------------------------------------------------------
# #845 — the tracker row that is yours
# ---------------------------------------------------------------------------


def test_the_progress_rows_carry_an_entrant_id_in_team_mode(
    tmp_path: Path,
) -> None:
    """The identity the phone needs to mark its own row (#845)."""
    st = _team_game(tmp_path)
    rows = serialize_answer_progress(st.get_players(), st.get_ranked_participants())

    by_name = {e["name"]: e for e in rows["players"]}
    assert by_name["Sofa"]["entrant_id"] == _team_of(st, "Sofa").team_id, (
        "a team row carries only a name, which no player's name can equal — "
        "so nobody's row is marked in team mode (#845)"
    )
    assert by_name["Balkon"]["entrant_id"] == _team_of(st, "Balkon").team_id


def test_a_solo_progress_row_is_identified_by_the_players_own_name(
    tmp_path: Path,
) -> None:
    """The fallback that keeps solo games rendering exactly as before."""
    st = _solo_game(tmp_path)
    rows = serialize_answer_progress(st.get_players())

    assert {e["entrant_id"] for e in rows["players"]} == {"Anna", "Ben"}
    assert all(e["entrant_id"] == e["name"] for e in rows["players"])


# ===========================================================================
# The phone: what it does with the snapshot
# ===========================================================================


def _js_function(path: Path, name: str) -> str:
    """The full source of a top-level ``function <name>(...) { ... }``.

    Brace-matched rather than line-counted, so the extraction survives the
    function moving or growing. Same helper as
    tests/test_snapshot_restore_parity_730_731.py.
    """
    source = path.read_text(encoding="utf-8")
    marker = f"function {name}("
    assert marker in source, f"{path.name} has no {marker[:-1]}"
    start = source.index(marker)
    depth, j = 0, source.index("{", start)
    while True:
        if source[j] == "{":
            depth += 1
        elif source[j] == "}":
            depth -= 1
            if depth == 0:
                return source[start : j + 1]
        j += 1


def _restore_branch() -> str:
    """The shipped ``case 'QUESTION_ACTIVE':`` arm of ``handleGameState``.

    Lifted rather than re-typed for the same reason the #834 tests lift the
    reset affordance: a test that re-implements the branch it is testing pins
    nothing. It is a ``case``, so the harness wraps it back into a switch.
    """
    source = _js_function(PLAYER_CORE, "handleGameState")
    start = source.index("case 'QUESTION_ACTIVE':")
    end = source.index("case 'ANSWER_REVEAL':", start)
    return source[start:end]


_HARNESS = r"""
const fs = require('fs');

function makeEl(id) {
  const e = {
    id, textContent: '', innerHTML: '', value: '', disabled: false,
    dataset: {}, children: [], _attrs: {}, _classes: new Set(),
    style: { cssText: '', setProperty() {}, removeProperty() {} },
    _handlers: {},
    classList: {
      add(c) { e._classes.add(c); },
      remove(...cs) { cs.forEach((c) => e._classes.delete(c)); },
      contains(c) { return e._classes.has(c); },
      toggle(c, on) {
        const want = (on === undefined) ? !e._classes.has(c) : !!on;
        if (want) { e._classes.add(c); } else { e._classes.delete(c); }
      },
    },
    addEventListener(kind, fn) {
      (e._handlers[kind] = e._handlers[kind] || []).push(fn);
    },
    click() {
      (e._handlers.click || []).forEach((fn) => fn({ target: e, preventDefault() {} }));
    },
    appendChild(child) { e.children.push(child); return child; },
    remove() {},
    setAttribute(k, v) { e._attrs[k] = v; },
    getAttribute(k) { return e._attrs[k] === undefined ? null : e._attrs[k]; },
    removeAttribute(k) { delete e._attrs[k]; },
    querySelector() { return null; },
    querySelectorAll() { return []; },
    focus() {},
    closest() { return null; },
  };
  return e;
}

// Elements the shipped markup ships hidden. `frozen-overlay` without the class
// reads as a live freeze lockout and swallows every tap.
const HIDDEN_AT_REST = [
  'team-pick', 'team-mine', 'team-note', 'team-name-row', 'team-name-hint',
  'team-empty', 'team-chip', 'frozen-overlay', 'submitted-confirmation',
  'estimate-submitted-confirmation', 'estimate-container',
];

const nodes = {};
function get(id) {
  if (!nodes[id]) {
    nodes[id] = makeEl(id);
    if (HIDDEN_AT_REST.indexOf(id) !== -1) nodes[id]._classes.add('hidden');
  }
  return nodes[id];
}

const answerButtons = [0, 1, 2].map((i) => {
  const b = makeEl('answer-' + i);
  b.dataset.index = String(i);
  b.querySelector = () => null;
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
  readyState: 'complete',
};
const S = { playerName: 'Anna', isAdmin: false, currentPhase: null };
global.window = {
  addEventListener() {},
  QuizifyPlayerUtils: {
    state: S,
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
global.__timers = [];
global.setTimeout = (f, ms) => {
  global.__timers.push({ fn: f, ms: ms });
  return global.__timers.length;
};
global.clearTimeout = () => {};
global.setInterval = () => 0;
global.clearInterval = () => {};

eval(fs.readFileSync(process.argv[2], 'utf8'));   // render-shared.js
eval(fs.readFileSync(process.argv[3], 'utf8'));   // player-team.js
eval(fs.readFileSync(process.argv[4], 'utf8'));   // player-game.js

const team = global.window.QuizifyPlayerTeam;
const game = global.window.QuizifyPlayerGame;
const pu = global.window.QuizifyPlayerUtils;
const state = S;

// The collaborators the lifted player-core code reaches for. Only these are
// stubs; everything the assertions look at is shipped code.
let currentQuestion = null;
let myPowerUp = null;
function isFinalRound() { return false; }
function _myScore() { return 0; }

__CORE__

function restore(msg) {
  if (team && msg.teams) team.handleTeamsUpdate(msg);
  switch (msg.phase) {
    __BRANCH__
    default:
      break;
  }
}

const SNAPSHOT_QUESTION = {
  text: 'Which city is this?',
  answers: ['Lisbon', 'Porto', 'Faro'],
  category: 'Geography',
  question_type: 'multiple_choice',
  time_limit: 30,
  time_remaining: 12.5,
};

function live(roundNum) {
  return {
    question_text: SNAPSHOT_QUESTION.text,
    answers: SNAPSHOT_QUESTION.answers,
    category: SNAPSHOT_QUESTION.category,
    question_type: 'multiple_choice',
    timer_duration: 30,
    round_num: roundNum,
    total_rounds: 5,
  };
}

function snapshot(roundNum, extra) {
  const msg = {
    phase: 'QUESTION_ACTIVE',
    round: roundNum,
    total_rounds: 5,
    leaderboard: [],
    question: JSON.parse(JSON.stringify(SNAPSHOT_QUESTION)),
  };
  Object.keys(extra || {}).forEach((k) => { msg[k] = extra[k]; });
  return msg;
}

const SOFA = { team_id: 't1', name: 'Sofa', color: 'sage', members: ['Anna', 'Jan'] };
const BALKON = { team_id: 't2', name: 'Balkon', color: 'coral', members: ['Mira'] };

const out = { threw: null };
try {
  const scenario = process.argv[5];

  if (scenario === 'team-resume') {
    // The round as the team lived it: Jan taps, both phones light up.
    team.handleTeamsUpdate({ teams: [SOFA] });
    handleQuestionStarted(live(2));
    team.handleTeamAnswer({
      team_id: 't1', answer_index: 1, set_by: 'Jan',
      lock_seconds: 2, members: ['Anna', 'Jan'],
    });
    out.beforeMarked = answerButtons.map(
      (b) => b.classList.contains('has-team-answer'));
    out.beforeChip = get('team-chip-set').textContent;

    // The host pauses and resumes: a projected snapshot lands on every phone.
    restore(snapshot(2, {
      teams: [SOFA],
      team_answer: {
        team_id: 't1', answer_index: 1, set_by: 'Jan',
        members: ['Anna', 'Jan'], lock_seconds: 1.2,
      },
    }));
    out.afterMarked = answerButtons.map((b) => b.classList.contains('has-team-answer'));
    out.afterChip = get('team-chip-set').textContent;
    out.afterLocked = answerButtons.map((b) => b.disabled);
    out.dotHtml = answerButtons[1].children.map((c) => c.innerHTML).join('');
  }

  if (scenario === 'team-resume-unanswered') {
    // Same resume, but nothing was ever tapped: no block, no dots.
    team.handleTeamsUpdate({ teams: [SOFA] });
    handleQuestionStarted(live(2));
    restore(snapshot(2, { teams: [SOFA] }));
    out.marked = answerButtons.map((b) => b.classList.contains('has-team-answer'));
    out.locked = answerButtons.map((b) => b.disabled);
  }

  if (scenario === 'solo-resume') {
    handleQuestionStarted(live(2));
    game.handleAnswerClick(2, () => {});
    out.beforeSelected = answerButtons.map((b) => b.classList.contains('is-selected'));

    restore(snapshot(2, { leaderboard: [{ name: 'Anna', submitted: true }] }));
    out.afterSelected = answerButtons.map((b) => b.classList.contains('is-selected'));
    out.afterDisabled = answerButtons.map((b) => b.disabled);
    out.confirmationHidden = get('submitted-confirmation').classList.contains('hidden');
  }

  if (scenario === 'later-round') {
    // The phone last saw round 2 and picked C; the snapshot it comes back to
    // is round 3. Nothing here knows what was picked in round 3.
    handleQuestionStarted(live(2));
    game.handleAnswerClick(2, () => {});
    restore(snapshot(3, { leaderboard: [{ name: 'Anna', submitted: true }] }));
    out.selected = answerButtons.map((b) => b.classList.contains('is-selected'));
    out.disabled = answerButtons.map((b) => b.disabled);
  }

  if (scenario === 'tracker-team') {
    team.handleTeamsUpdate({ teams: [SOFA, BALKON] });
    game.renderSubmissionTracker([
      { entrant_id: 't1', name: 'Sofa', submitted: false, connected: true },
      { entrant_id: 't2', name: 'Balkon', submitted: true, connected: true },
    ]);
    out.html = get('submitted-players').innerHTML;
  }

  if (scenario === 'tracker-solo') {
    game.renderSubmissionTracker([
      { entrant_id: 'Anna', name: 'Anna', submitted: false, connected: true },
      { entrant_id: 'Ben', name: 'Ben', submitted: true, connected: true },
    ]);
    out.html = get('submitted-players').innerHTML;
  }
} catch (e) {
  out.threw = String((e && e.stack) || e);
}
process.stdout.write(JSON.stringify(out));
"""


def _harness() -> str:
    core = "\n\n".join(
        _js_function(PLAYER_CORE, name)
        for name in ("questionStartedFromSnapshot", "handleQuestionStarted")
    )
    return _HARNESS.replace("__CORE__", core).replace(
        "__BRANCH__", _restore_branch()
    )


def _run(scenario: str, tmp_path: Path) -> dict:
    script = tmp_path / "harness.js"
    script.write_text(_harness(), encoding="utf-8")
    proc = subprocess.run(
        [
            "node",
            str(script),
            str(RENDER_SHARED),
            str(PLAYER_TEAM),
            str(PLAYER_GAME),
            scenario,
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    out = json.loads(proc.stdout)
    assert out["threw"] is None, out["threw"]
    return out


@needs_node
def test_the_resumed_phone_still_shows_the_standing_team_answer(
    tmp_path: Path,
) -> None:
    """#875 on the phone: the dots, the chip and the lock come back."""
    out = _run("team-resume", tmp_path)

    assert out["beforeMarked"] == [False, True, False], "the live frame paints"
    assert out["afterMarked"] == [False, True, False], (
        "the resume snapshot leaves the question looking unanswered, so a "
        "teammate overwrites an answer they cannot see (#875)"
    )
    assert "Jan" in out["afterChip"], "the 'set by Jan' chip went with the dots"
    assert out["afterLocked"] == [True, True, True], "the change lock is back"
    assert "A" in out["dotHtml"] and "J" in out["dotHtml"], "both members' dots"


@needs_node
def test_a_resume_with_no_standing_answer_leaves_the_row_clear(
    tmp_path: Path,
) -> None:
    """The absent block must not be read as an answer of zero."""
    out = _run("team-resume-unanswered", tmp_path)

    assert out["marked"] == [False, False, False]
    assert out["locked"] == [False, False, False]


@needs_node
def test_the_resumed_solo_phone_still_shows_which_answer_it_picked(
    tmp_path: Path,
) -> None:
    """The solo half of #875: the mark, not just the grey."""
    out = _run("solo-resume", tmp_path)

    assert out["beforeSelected"] == [False, False, True]
    assert out["afterSelected"] == [False, False, True], (
        "the restore renders, then resets, then only disables — so a resumed "
        "phone shows three dead buttons and no pick (#875)"
    )
    assert out["afterDisabled"] == [True, True, True]
    assert out["confirmationHidden"] is False


@needs_node
def test_a_snapshot_from_a_later_round_marks_nothing(tmp_path: Path) -> None:
    """The remembered index belongs to the round it was tapped in. Painting it
    onto the next round's buttons would be a confident lie."""
    out = _run("later-round", tmp_path)

    assert out["selected"] == [False, False, False]
    assert out["disabled"] == [True, True, True], "still locked, just unmarked"


@needs_node
def test_the_tracker_marks_your_team_in_team_mode(tmp_path: Path) -> None:
    """#845: the row that is you, when the rows are teams."""
    out = _run("tracker-team", tmp_path)

    rows = re.findall(r'<div class="([^"]*player-indicator[^"]*)"', out["html"])
    assert len(rows) == 2, out["html"]
    assert "is-current-player" in rows[0], (
        "the tracker matches the viewer's name against the row's name, and a "
        "player's name never equals a team's — so no row is marked (#845)"
    )
    assert "is-current-player" not in rows[1], "only one row is yours"


@needs_node
def test_the_tracker_still_marks_your_own_row_in_a_solo_game(
    tmp_path: Path,
) -> None:
    """The fallback: unchanged behaviour where the rows are people."""
    out = _run("tracker-solo", tmp_path)

    rows = re.findall(r'<div class="([^"]*player-indicator[^"]*)"', out["html"])
    assert len(rows) == 2, out["html"]
    assert "is-current-player" in rows[0]
    assert "is-current-player" not in rows[1]
