"""The last three frames the host page had no case for (#830).

#830 counted ten frames the server sends ``/quizify/admin`` and the host page
dropped. Seven were the ``hot_seat_*`` broadcasts and went with #832. These are
the other three, and they are the same omission the issue named: the host page
was treated as a control panel rather than as a surface, so frames that carry
state to *look at* were never wired.

* ``answer_progress`` — #619 wired the submission tracker to the phone and the
  television. The host is the one who decides to move on early, and was the
  only one who could not see who the room was waiting for.
* ``evening_tally`` (#612) and ``head_to_head`` (#613) — both go out over
  ``broadcast_to_admins_and_dashboards``, and both were rendered on the
  dashboard alone.

The renderers here build text rather than HTML, which is a decision worth
pinning: the host holds this page in one hand, and a name a guest typed can
never become markup if it is never parsed as markup.

The tests run the real ``admin.js`` router and the real renderers against the
real bundles under node (``tests/fixtures/dom_stub.js``), so they assert what
the host reads off the screen rather than what the source looks like.
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
_ADMIN_JS = _JS / "admin.js"
_ADMIN_HTML = _WWW / "admin.html"
_I18N = _WWW / "i18n"
_STUB = Path(__file__).resolve().parent / "fixtures" / "dom_stub.js"

_NEEDS_NODE = pytest.mark.skipif(
    shutil.which("node") is None, reason="node not installed"
)

#: The three, by name. Counted in the issue; named here, because a count says
#: nothing about which one somebody deleted.
DROPPED = ("answer_progress", "evening_tally", "head_to_head")

ELEMENT_IDS = (
    "setup-screen",
    "lobby-screen",
    "game-view",
    "admin-finale-view",
    "admin-lightning-view",
    "admin-lightning-recap-view",
    "admin-round",
    "admin-question",
    "admin-detour-note",
    "admin-correct",
    "game-leaderboard",
    "next-question-btn",
    "end-game-btn",
    "reset-game-btn",
    "admin-answer-progress",
    "admin-lobby-h2h",
    "admin-finale-h2h",
    "admin-evening-tally",
)

#: The elements this change adds. Not demanding them lets the harness run
#: against a host page that has none of it, so the tests fail on what the host
#: reads instead of on the fixture.
ADDED_IDS = (
    "admin-answer-progress",
    "admin-lobby-h2h",
    "admin-finale-h2h",
    "admin-evening-tally",
)


def _admin() -> str:
    return _ADMIN_JS.read_text(encoding="utf-8")


def _js_block(source: str, signature: str, required: bool = True) -> str:
    """One brace-balanced declaration — a function or an object literal."""
    if signature not in source:
        if required:
            raise AssertionError(f"admin.js no longer has `{signature}`")
        return ""
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


def _tag(element_id: str) -> str:
    """The whole opening tag carrying this id, newlines and all.

    Read rather than assumed, and read as a tag rather than as a line: the
    markup wraps its attributes, and a line-wise scan would have called an
    element visible purely because its ``style`` sat one line below its id.
    """
    html = _ADMIN_HTML.read_text(encoding="utf-8")
    m = re.search(rf'<[^<>]*\bid="{element_id}"[^<>]*>', html)
    return m.group(0) if m else ""


def _element_classes() -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for element_id in ELEMENT_IDS:
        tag = _tag(element_id)
        assert tag or element_id in ADDED_IDS, (
            f"#{element_id} is missing from admin.html"
        )
        m = re.search(r'class="([^"]*)"', tag)
        out[element_id] = (m.group(1) or "").split() if m else []
    return out


def _element_hidden() -> dict[str, bool]:
    """Which elements ship hidden, so the harness starts where the page does."""
    return {
        element_id: "display:none" in _tag(element_id).replace(" ", "")
        for element_id in ELEMENT_IDS
    }


# ---------------------------------------------------------------------------
# The router has a case at all
# ---------------------------------------------------------------------------


def test_all_three_frames_reach_the_host_router() -> None:
    """The shape of the omission, in one assertion."""
    router = _js_block(_admin(), "function handleMessage(msg) {")

    missing = [f for f in DROPPED if f"case '{f}':" not in router]
    assert not missing, f"the host page still drops {missing}"


def test_the_three_lines_start_hidden() -> None:
    """Each is empty until its frame arrives, and an empty box on the setup
    screen or under every question is the cost of getting this wrong."""
    for element_id in ADDED_IDS:
        tag = _tag(element_id)
        assert tag, f"admin.html has no #{element_id}"
        assert "display:none" in tag.replace(" ", ""), element_id


# ---------------------------------------------------------------------------
# …and what the host actually reads
# ---------------------------------------------------------------------------


_SCRIPT = """
require({stub});
QZ.serveI18n({i18n});

var CLASSES = {classes};
var HIDDEN = {hidden};
Object.keys(CLASSES).forEach(function (id) {{
    var el = QZ.el(id);
    CLASSES[id].forEach(function (c) {{ el.classList.add(c); }});
    if (HIDDEN[id]) el.style.display = 'none';
}});

QZ.load({i18njs});

var _redirecting = false;
var currentPhase = 'LOBBY';
var adminTimer = {{
    start: function () {{}}, update: function () {{}}, stop: function () {{}}
}};
function renderLeaderboard() {{}}

{admin}

function line(id) {{
    var el = document.getElementById(id);
    return {{
        text: el.textContent,
        shown: el.style.display !== 'none'
    }};
}}

function snap() {{
    return {{
        progress: line('admin-answer-progress'),
        lobbyH2h: line('admin-lobby-h2h'),
        finaleH2h: line('admin-finale-h2h'),
        tally: line('admin-evening-tally')
    }};
}}

function progress(entries) {{
    return {{
        type: 'answer_progress',
        players: entries,
        submitted: entries.filter(function (e) {{ return e.submitted; }}).length,
        total: entries.length
    }};
}}
function row(name, submitted, connected) {{
    return {{ name: name, submitted: submitted, connected: connected !== false }};
}}

(async function () {{
    await window.QuizifyI18n.init('en');
    var out = {{}};

    // Mid-question: two in, one gone home, two the room is waiting for.
    handleMessage(progress([
        row('Anna', true), row('Bea', true), row('Chris', false, false),
        row('Dora', false), row('Emil', false)
    ]));
    out.waiting = snap();

    // Everybody is in — the host can move on without asking the room.
    handleMessage(progress([row('Anna', true), row('Bea', true)]));
    out.complete = snap();

    // A big table: three names and a count, not a roll call.
    handleMessage(progress([
        row('Anna', true), row('Bea', false), row('Chris', false),
        row('Dora', false), row('Emil', false), row('Fritz', false)
    ]));
    out.many = snap();

    // The tracker belongs to one question.
    handleMessage({{
        type: 'round_summary', correct_answer: 'Mercury', last_round: false
    }});
    out.afterReveal = snap();
    handleMessage(progress([row('Anna', false), row('Bea', false)]));
    handleMessage({{
        type: 'question_started', question_text: 'Round five',
        round_num: 5, total_rounds: 10, timer_duration: 30
    }});
    out.afterNext = snap();

    // A name is text, never markup.
    handleMessage(progress([row('<b>Bea</b>', false)]));
    out.markup = snap();

    // The two analytics lines. The duel comes twice, at two placements.
    handleMessage({{
        type: 'head_to_head', at: 'lobby', left: 'Anna', right: 'Ben',
        left_wins: 3, right_wins: 2
    }});
    out.lobbyDuel = snap();
    handleMessage({{
        type: 'head_to_head', at: 'finale', left: 'Anna', right: 'Ben',
        left_wins: 4, right_wins: 2
    }});
    out.finaleDuel = snap();
    handleMessage({{
        type: 'evening_tally',
        leaders: [
            {{ name: 'Anna', wins: 2 }}, {{ name: 'Ben', wins: 1 }},
            {{ name: 'Cara', wins: 1 }}, {{ name: 'Dora', wins: 1 }}
        ]
    }});
    out.tally = snap();

    // Nothing to say is said by saying nothing.
    handleMessage({{ type: 'evening_tally', leaders: [] }});
    handleMessage({{ type: 'head_to_head', at: 'lobby' }});
    out.empty = snap();

    // German, because the host reads the room in their own language.
    await window.QuizifyI18n.setLanguage('de');
    handleMessage(progress([row('Anna', true), row('Bea', false)]));
    out.progressDe = snap();

    console.log(JSON.stringify(out));
}})();
"""


def _run() -> dict:
    source = _admin()
    parts = [
        _js_block(source, "const views = {"),
        _js_block(source, "const els = {"),
        _js_block(source, "function showView(name) {"),
        _js_block(source, "function _t(key, params) {"),
        _js_block(source, "function _tOr(key, params, fallback) {"),
        _js_block(source, "function setDetourDetail(text) {"),
        _js_block(source, "function handleQuestionStarted(msg) {"),
        _js_block(source, "function handleRoundSummary(msg) {"),
        _js_block(source, "function handleMessage(msg) {"),
    ]
    parts += [
        _js_block(source, signature, required=False)
        for signature in (
            "function handleAnswerProgress(msg) {",
            "function clearAnswerProgress() {",
            "function handleEveningTally(msg) {",
            "function handleHeadToHead(msg) {",
        )
    ]
    script = _SCRIPT.format(
        stub=json.dumps(str(_STUB)),
        i18n=json.dumps(str(_I18N)),
        i18njs=json.dumps(str(_JS / "i18n.js")),
        classes=json.dumps(_element_classes()),
        hidden=json.dumps(_element_hidden()),
        admin="\n\n".join(p for p in parts if p),
    )
    out = subprocess.run(
        ["node", "-e", script], capture_output=True, text=True, check=True
    )
    return json.loads(out.stdout)


@_NEEDS_NODE
def test_the_host_can_see_who_the_room_is_waiting_for() -> None:
    """The complaint in #830, exactly: the count is the television's half of
    this frame. The host is the one deciding whether to move on, and "2 of 5"
    does not say whether the missing three are thinking or in the kitchen."""
    waiting = _run()["waiting"]["progress"]

    assert waiting["shown"] is True
    assert waiting["text"] == "2 of 5 answered — waiting for Dora, Emil"


@_NEEDS_NODE
def test_a_phone_that_left_is_not_somebody_to_wait_for() -> None:
    """Chris is disconnected and unsubmitted. The other two surfaces grey that
    row out; naming him would send the host looking for a guest who has gone
    home."""
    assert "Chris" not in _run()["waiting"]["progress"]["text"]


@_NEEDS_NODE
def test_a_full_room_is_not_a_list_of_names() -> None:
    out = _run()

    assert out["complete"]["progress"]["text"] == "2 of 2 answered"
    # Three names is a glance; a fourth is a list the host reads instead of
    # reading the room.
    assert out["many"]["progress"]["text"] == (
        "1 of 6 answered — waiting for Bea, Chris, Dora +2"
    )


@_NEEDS_NODE
def test_the_tracker_belongs_to_the_question_it_counts() -> None:
    """A stale "3 of 6" under the next question is worse than none: it is a
    number the host would act on."""
    out = _run()

    assert out["afterReveal"]["progress"]["shown"] is False
    assert out["afterNext"]["progress"]["shown"] is False
    assert out["afterNext"]["progress"]["text"] == ""


@_NEEDS_NODE
def test_a_guest_typed_name_stays_text() -> None:
    """The host page renders these lines as text, so a name cannot become
    markup on the one screen that also holds the game's controls."""
    assert _run()["markup"]["progress"]["text"] == (
        "0 of 1 answered — waiting for <b>Bea</b>"
    )


@_NEEDS_NODE
def test_the_duel_lands_on_the_placement_the_frame_asks_for() -> None:
    """``at`` is the whole difference between the two sends (#613): the lobby
    before the game, the finale after it, where the record already includes
    the game just played."""
    out = _run()

    assert out["lobbyDuel"]["lobbyH2h"]["text"] == (
        "Head to head: Anna 3 – 2 Ben (last 90 days)"
    )
    assert out["lobbyDuel"]["lobbyH2h"]["shown"] is True
    assert out["lobbyDuel"]["finaleH2h"]["shown"] is False

    assert out["finaleDuel"]["finaleH2h"]["text"] == (
        "Head to head: Anna 4 – 2 Ben (last 90 days)"
    )


@_NEEDS_NODE
def test_the_sitting_is_summed_up_on_the_end_screen() -> None:
    """#612 sends this to admins and dashboards once the finished game is
    recorded. Three names is the cut the television makes; a fourth turns the
    line into a table nobody reads mid-celebration."""
    tally = _run()["tally"]["tally"]

    assert tally["text"] == "Tonight: Anna 2 wins · Ben 1 win · Cara 1 win"
    assert tally["shown"] is True


@_NEEDS_NODE
def test_nothing_to_report_is_reported_as_nothing() -> None:
    """A duel needs two regulars who have met twice, and the tally needs a
    second game. Both are routinely absent, and an empty label would be worse
    than the line not being there."""
    empty = _run()["empty"]

    assert empty["tally"]["shown"] is False
    assert empty["lobbyH2h"]["shown"] is False


@_NEEDS_NODE
def test_the_host_reads_the_room_in_their_own_language() -> None:
    """New strings, so they go through the bundles like everything else."""
    assert _run()["progressDe"]["progress"]["text"] == (
        "1 von 2 haben geantwortet — es fehlen noch Bea"
    )
