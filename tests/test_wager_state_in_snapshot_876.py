"""A phone that reloads inside the betting window (#876).

The final round's wager is the one bet in the game that is worth double, and
it is placed on a screen that carries a slider, a bank and nothing else. Two
things were wrong with the snapshot that rebuilds that screen after a reload,
and only one of them is visible to the player who suffers it.

* **The snapshot never said whether this phone had already bet.** So
  ``renderWagerWindow`` rebuilt a live 25% slider over a stake the server was
  already holding. A second submit is accepted (``record_wager`` just assigns),
  so the reload silently overwrites the bet the player made — and once the
  question starts ``_showWagerBadge(true)`` hides the badge, because the phone
  believes it never bet. Two players in the same room, one of whom reloaded,
  then see different amounts staked and only one of them sees "Wager: 50%".

* **In team mode the bank read zero.** The client took it from
  ``_myScore(msg)``, which looks the player's NAME up in the snapshot's
  leaderboard — and in team mode those rows are TEAMS (#365/#804). A member
  matched no row, took 0, and was offered "50% of nothing" while the live
  ``wager_window`` a moment earlier had priced the same slider against the
  team's real score (#804 fixed that half). Nothing errors: the player bets a
  number the screen agrees with and it settles as zero.

The fix projects both into the wager block per recipient — ``own_bank`` from
``get_ranked_participant_for``, the same row every payout lands in, and
``you_wagered`` from that participant's standing bet — and locks the slider on
restore, the shape the Hot Seat auction already uses (``lockBidUi``).

The node tests below run the real ``player-core.js`` snapshot mapper and the
real ``player-game.js`` panel against ``tests/fixtures/dom_stub.js``, fed with
snapshots this file builds out of a real game state. So what they assert is
what a guest's phone shows, not what the source says.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from custom_components.quizify.game.phase_controller import GamePhase
from custom_components.quizify.game.state import QuizifyGameState
from custom_components.quizify.server.round_message_builder import RoundMessageBuilder
from custom_components.quizify.server.serializers import serialize_state_snapshot

_REPO = Path(__file__).resolve().parent.parent
_WWW = _REPO / "custom_components" / "quizify" / "www"
_JS = _WWW / "js"
_I18N = _WWW / "i18n"
_CORE = _JS / "player-core.js"
_STUB = Path(__file__).resolve().parent / "fixtures" / "dom_stub.js"

_NEEDS_NODE = pytest.mark.skipif(
    shutil.which("node") is None, reason="node not installed"
)


class _Runtime:
    def __init__(self, tmp_path: Path) -> None:
        self.data_dir = tmp_path


def _ws() -> MagicMock:
    ws = MagicMock()
    ws.closed = False
    return ws


def _solo_game(tmp_path: Path) -> QuizifyGameState:
    """A one-round game parked in the betting window."""
    st = QuizifyGameState(runtime=_Runtime(tmp_path), entry_id="test")
    for name in ("Anna", "Tom"):
        st.add_player(name, _ws())
    st.start_game(language="en", num_rounds=1, timer_duration=30)
    st.start_next_question()
    assert st.phase is GamePhase.WAGER_ACTIVE
    return st


def _team_game(tmp_path: Path) -> QuizifyGameState:
    """Two teams of two, parked in the betting window.

    The scores are deliberately different on the team and on the member: the
    per-player number is the shadow score #668 was filed about, and it is what
    a name lookup in the leaderboard would have to find in order to be right.
    """
    st = QuizifyGameState(runtime=_Runtime(tmp_path), entry_id="test")
    for name in ("Anna", "Jan", "Mira", "Tom"):
        st.add_player(name, _ws())
    st.create_team("Sofa", "Anna")
    st.join_team(st.get_team_of("Anna")["team_id"], "Jan")
    st.create_team("Kitchen", "Mira")
    st.join_team(st.get_team_of("Mira")["team_id"], "Tom")
    st.start_game(language="en", num_rounds=1, timer_duration=30)
    st.start_next_question()
    assert st.phase is GamePhase.WAGER_ACTIVE
    _team(st, "Sofa").score = 240
    _team(st, "Kitchen").score = 120
    st.get_player("Anna").score = 7  # the number that must NOT reach the phone
    st.get_player("Jan").score = 0
    return st


def _team(st: QuizifyGameState, name: str):  # noqa: ANN202
    return next(t for t in st.team_registry.all_teams() if t.name == name)


def _project(st: QuizifyGameState, name: str) -> dict:
    return RoundMessageBuilder().project_snapshot_for_player(
        st,
        snapshot=serialize_state_snapshot(st),
        player=st.get_player(name),
    )


# ===========================================================================
# What the server projects
# ===========================================================================


def test_the_team_bank_is_the_teams_score_not_the_members(tmp_path: Path) -> None:
    """The half that silently produces a wrong number.

    240, not 7 and not 0 — the row the leaderboard shows and the settlement
    pays, resolved the same way every other payout resolves it.
    """
    st = _team_game(tmp_path)

    assert _project(st, "Anna")["wager"]["own_bank"] == 240
    assert _project(st, "Jan")["wager"]["own_bank"] == 240
    assert _project(st, "Mira")["wager"]["own_bank"] == 120


def test_the_solo_bank_is_the_players_own_score(tmp_path: Path) -> None:
    st = _solo_game(tmp_path)
    st.get_player("Anna").score = 180

    assert _project(st, "Anna")["wager"]["own_bank"] == 180


def test_the_snapshot_says_whether_this_phone_has_already_bet(
    tmp_path: Path,
) -> None:
    st = _solo_game(tmp_path)
    assert _project(st, "Anna")["wager"]["you_wagered"] is None

    assert st.record_wager("Anna", 50) is True

    assert _project(st, "Anna")["wager"]["you_wagered"] == 50
    # …and only for the phone that placed it.
    assert _project(st, "Tom")["wager"]["you_wagered"] is None


def test_a_teams_bet_is_the_standing_bet_on_every_members_phone(
    tmp_path: Path,
) -> None:
    """One bet per team (#804), so a member who reloads sees what their team
    staked — including when a teammate is the one who placed it."""
    st = _team_game(tmp_path)
    assert st.record_wager("Jan", 40) is True

    assert _project(st, "Anna")["wager"]["you_wagered"] == 40
    assert _project(st, "Jan")["wager"]["you_wagered"] == 40
    assert _project(st, "Mira")["wager"]["you_wagered"] is None


def test_the_amounts_stay_off_the_television(tmp_path: Path) -> None:
    """The canonical snapshot is what the host and the TV get. A bet either of
    them gave away would not be a bet (#656) — so both new fields exist only
    in a recipient's projection."""
    st = _solo_game(tmp_path)
    st.record_wager("Anna", 90)

    canonical = serialize_state_snapshot(st)["wager"]

    assert "you_wagered" not in canonical
    assert "own_bank" not in canonical
    # …and the tally the TV does show is untouched.
    assert canonical["locked_in"] == 1
    assert canonical["player_count"] == 2


# ===========================================================================
# …and what the phone does with it
# ===========================================================================

_SCRIPT = """
require({stub});

QZ.els({ids});
QZ.serveI18n({i18n});
QZ.load({i18njs});
QZ.load({utils_js});
QZ.load({render_shared});
QZ.load({player_utils});
QZ.load({player_game});

// The snapshot mapper, lifted whole out of player-core.js: `_myScore`, the
// question mapper beside it and the wager mapper under test.
var state = window.QuizifyPlayerUtils.state;
{mapper}

// The client from before #876 had no named mapper: the WAGER_ACTIVE case built
// the payload inline off the leaderboard. Reproduced here so a run against
// that client fails on the numbers a player reads off the panel rather than on
// a missing symbol — the bug was never a missing function.
if (typeof wagerWindowFromSnapshot !== 'function') {{
    wagerWindowFromSnapshot = function (msg) {{
        var w = msg.wager || {{}};
        return {{
            round_num: msg.round,
            total_rounds: msg.total_rounds,
            category: w.category,
            difficulty: w.difficulty,
            window_duration: w.window_remaining,
            player_score: _myScore(msg)
        }};
    }};
}}

var game = window.QuizifyPlayerGame;
var sent = [];
window.QuizifyPlayer = {{
    send: function (type, payload) {{ sent.push([type, payload]); }}
}};

var SNAPSHOTS = {snapshots};

function panel() {{
    function el(id) {{ return document.getElementById(id); }}
    return {{
        hidden: el('wager-panel').classList.contains('hidden'),
        collapsed: el('wager-panel').classList.contains('wager-panel--collapsed'),
        sliderDisabled: !!el('wager-slider').disabled,
        sliderValue: el('wager-slider').value,
        submitDisabled: !!el('wager-submit-btn').disabled,
        title: el('wager-panel-title').textContent,
        hint: el('wager-panel-hint').textContent,
        bank: el('wager-bank').textContent,
        value: el('wager-value').textContent
    }};
}}

// A fresh phone for each scenario: the module keeps one wager state, and the
// live-then-snapshot guard is keyed on the round number.
var round = 0;
function fresh(name) {{
    state.playerName = name;
    sent = [];
    ['wager-panel', 'wager-slider', 'wager-submit-btn'].forEach(function (id) {{
        var node = document.getElementById(id);
        node.classList.remove('hidden', 'wager-panel--collapsed');
        node.disabled = false;
    }});
    document.getElementById('wager-slider').value = '';
}}

function restore(name, snapshot) {{
    fresh(name);
    var payload = wagerWindowFromSnapshot(snapshot);
    payload.round_num = ++round;
    game.renderWagerWindow(payload);
    return payload;
}}

(async function () {{
    await window.QuizifyI18n.init('en');

    var out = {{}};

    // A team member who has not bet: a live slider, priced against the TEAM.
    out.teamOpen = {{ payload: restore('Anna', SNAPSHOTS.teamOpen), panel: panel() }};

    // …and one who has. The bet was placed by a teammate, which in team mode
    // is the same bet.
    out.teamBet = {{ payload: restore('Anna', SNAPSHOTS.teamBet), panel: panel() }};
    // A locked panel must refuse a second submit — the server accepts one and
    // overwrites, so the phone is where the first bet is defended.
    document.getElementById('wager-submit-btn').onclick();
    out.teamBetSent = sent.slice();

    // The two routes onto a placed bet, compared: the phone that tapped
    // Submit, and the phone that reloaded onto the same bet.
    fresh('Anna');
    game.renderWagerWindow({{
        round_num: ++round, total_rounds: 1, category: 'Science',
        window_duration: 20, player_score: 240
    }});
    document.getElementById('wager-slider').value = '50';
    document.getElementById('wager-submit-btn').onclick();
    out.tapped = panel();
    out.tappedSent = sent.slice();

    out.reloaded = restore('Anna', SNAPSHOTS.teamBet).round_num ? panel() : null;

    // The badge the question screen carries: a phone that reloaded mid-window
    // must still show "Wager: 50%" when the question arrives.
    restore('Anna', SNAPSHOTS.teamBet);
    game.renderQuestion({{
        question_text: 'Which planet is closest to the sun?',
        category: 'Science', answers: ['Mercury', 'Venus', 'Mars'],
        is_final_round: true
    }});
    out.badgeAfterReload = panel();

    // A solo game must be unaffected, including against a server too old to
    // send the new fields — there the leaderboard lookup is still right.
    out.solo = {{ payload: restore('Anna', SNAPSHOTS.solo), panel: panel() }};
    out.legacy = {{ payload: restore('Anna', SNAPSHOTS.legacy), panel: panel() }};

    console.log(JSON.stringify(out));
    // The countdown owns a live interval; node would otherwise never exit.
    process.exit(0);
}})();
"""

_IDS = [
    "answer-buttons",
    "answers-container",
    "estimate-container",
    "question-category",
    "question-image",
    "question-media",
    "question-text",
    "submission-tracker",
    "submitted-confirmation",
    "timer",
    "timer-sr-announce",
    "wager-bank",
    "wager-panel",
    "wager-panel-hint",
    "wager-panel-timeout-note",
    "wager-panel-title",
    "wager-slider",
    "wager-submit-btn",
    "wager-value",
]


def _mapper_source() -> str:
    source = _CORE.read_text("utf-8")
    start = source.index("    function _myScore(msg) {")
    end = source.index("    function handleWagerWindow(msg) {")
    return source[start:end]


def _snapshots(tmp_path: Path) -> dict:
    team = _team_game(tmp_path / "team")
    team_open = _project(team, "Anna")
    team.record_wager("Jan", 50)
    team_bet = _project(team, "Anna")

    solo_state = _solo_game(tmp_path / "solo")
    solo_state.get_player("Anna").score = 180
    solo = _project(solo_state, "Anna")

    # A server from before this fix: the block carries neither field, and the
    # leaderboard fallback is what the phone has to fall back on.
    legacy = json.loads(json.dumps(solo))
    legacy["wager"].pop("own_bank", None)
    legacy["wager"].pop("you_wagered", None)

    return {"teamOpen": team_open, "teamBet": team_bet, "solo": solo, "legacy": legacy}


def _run(tmp_path: Path) -> dict:
    script = _SCRIPT.format(
        stub=json.dumps(str(_STUB)),
        ids=json.dumps(_IDS),
        i18n=json.dumps(str(_I18N)),
        i18njs=json.dumps(str(_JS / "i18n.js")),
        utils_js=json.dumps(str(_JS / "utils.js")),
        render_shared=json.dumps(str(_JS / "render-shared.js")),
        player_utils=json.dumps(str(_JS / "player-utils.js")),
        player_game=json.dumps(str(_JS / "player-game.js")),
        mapper=_mapper_source(),
        snapshots=json.dumps(_snapshots(tmp_path)),
    )
    out = subprocess.run(
        ["node", "-e", script], capture_output=True, text=True, check=True
    )
    return json.loads(out.stdout)


@_NEEDS_NODE
def test_the_phone_prices_the_slider_against_the_team_bank(tmp_path: Path) -> None:
    """The team-mode reading, measured on the panel rather than in the wire:
    240 on the bank line, and 50% of it worth 120 points."""
    result = _run(tmp_path)

    assert result["teamOpen"]["payload"]["player_score"] == 240
    assert result["teamOpen"]["panel"]["bank"] == "240"
    # 25% of 240 — the default the slider opens on.
    assert "60" in result["teamOpen"]["panel"]["value"]


@_NEEDS_NODE
def test_a_placed_bet_comes_back_locked(tmp_path: Path) -> None:
    """The reload lands on the bet, not on a fresh slider over it."""
    result = _run(tmp_path)
    panel = result["teamBet"]["panel"]

    assert panel["sliderDisabled"] is True
    assert panel["submitDisabled"] is True
    assert panel["sliderValue"] == "50"
    assert panel["collapsed"] is True
    assert "50" in panel["title"]
    assert result["teamBet"]["payload"]["you_wagered"] == 50


@_NEEDS_NODE
def test_a_reloaded_phone_cannot_overwrite_its_own_bet(tmp_path: Path) -> None:
    """``record_wager`` accepts a second bet and assigns over the first, so a
    live-looking slider on a reloaded phone is a way to lose the stake the
    player chose without ever being told."""
    result = _run(tmp_path)

    assert result["teamBetSent"] == []


@_NEEDS_NODE
def test_both_routes_onto_a_bet_leave_the_phone_identical(tmp_path: Path) -> None:
    """The phone that tapped Submit and the phone that reloaded onto the same
    bet show the same panel — the #858 invariant on the screen where the two
    halves disagreeing costs points."""
    result = _run(tmp_path)

    assert result["tappedSent"] == [["submit_wager", {"wager": 50}]]
    assert result["reloaded"] == result["tapped"]


@_NEEDS_NODE
def test_the_badge_survives_the_reload(tmp_path: Path) -> None:
    """``_showWagerBadge(true)`` reads the phone's own wager state, so the
    reloaded phone used to lose the "Wager: 50%" reminder the connected one
    keeps for the whole final question."""
    result = _run(tmp_path)
    badge = result["badgeAfterReload"]

    assert badge["hidden"] is False
    assert badge["collapsed"] is True
    assert "50" in badge["title"]


@_NEEDS_NODE
def test_a_solo_game_is_unchanged_and_an_old_server_still_works(
    tmp_path: Path,
) -> None:
    """The leaderboard fallback stays, because in a solo game it is right —
    and a phone updated ahead of its server must not show a bank of zero."""
    result = _run(tmp_path)

    assert result["solo"]["payload"]["player_score"] == 180
    assert result["solo"]["panel"]["sliderDisabled"] is False
    assert result["legacy"]["payload"]["player_score"] == 180
    assert result["legacy"]["payload"]["you_wagered"] is None
