"""The Freeze picker lists the other team, and does not outlive the question (#952).

Found in the v1.19.0-RC1 live test: in a team game, Anna tapped Freeze on round
1 and the picker read "No other players to target" — then sat on top of the
reveal, because nothing closed it.

``openTargetPicker`` built its opponents from ``_latestPlayers``, the cache
``renderSubmissionTracker`` fills. That list is empty until the round's first
``answer_progress``, and in team mode its rows are teams (#835) — names the
server cannot freeze. The per-player roster ``player-core.js`` already keeps
(``_lastRoster``) is the list the picker needs; teammates are left out of it,
and the server now refuses them too.

The JS halves run the real ``player-game.js`` (and the real
``handleRoundSummary`` out of ``player-core.js``) against the DOM stub.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from custom_components.quizify.game.powerups import PowerUpType
from custom_components.quizify.game.state import QuizifyGameState
from custom_components.quizify.const import ERR_INVALID_ACTION

_REPO = Path(__file__).resolve().parent.parent
_WWW = _REPO / "custom_components" / "quizify" / "www"
_JS = _WWW / "js"
_I18N = _WWW / "i18n"
_CORE = _JS / "player-core.js"
_STUB = Path(__file__).resolve().parent / "fixtures" / "dom_stub.js"

_NEEDS_NODE = pytest.mark.skipif(
    shutil.which("node") is None, reason="node not installed"
)

_REVEAL_SLICE = ("    function handleRoundSummary(msg) {", "    // ============")


def _slice(path: Path, start: str, end: str) -> str:
    source = path.read_text("utf-8")
    a = source.index(start)
    b = source.index(end, a)
    return source[a:b]


_SCRIPT = """
require({stub});
QZ.serveI18n({i18n});
QZ.load({i18njs});

QZ.els(['powerup-target-modal', 'powerup-target-title', 'powerup-target-hint',
        'powerup-target-list', 'powerup-target-cancel', 'submission-tracker',
        'submitted-players', 'reaction-bar', 'admin-control-bar',
        'next-round-admin-btn', 'skip-question-btn']);
document.getElementById('powerup-target-modal').classList.add('hidden');

QZ.load({utils});
QZ.load({render_shared});
QZ.load({player_utils});
QZ.load({player_game});
QZ.load({player_team});

var pu = window.QuizifyPlayerUtils;
var game = window.QuizifyPlayerGame;
var state = pu.state;
state.playerName = 'Anna';

// The roster frames carry players, in team mode as in solo.
var ROSTER = [
    {{ name: 'Anna', connected: true, color: '#E88A7F' }},
    {{ name: 'Bert', connected: true, color: '#7FA8C4' }},
    {{ name: 'Cleo', connected: true, color: '#7FA897' }}
];

function options() {{
    return document.getElementById('powerup-target-list')
        .querySelectorAll('.powerup-target-option')
        .map(function (b) {{ return b.dataset.player; }});
}}
function modalOpen() {{
    return !document.getElementById('powerup-target-modal').classList.contains('hidden');
}}

// handleRoundSummary's surroundings, as test_final_round_timer_drift_941 does.
var currentQuestion = null;
var myPowerUp = 'freeze';
var reveal = {{ updateRevealView: function () {{}} }};
function _clearFinaleCountdown() {{}}

{reveal_core}

(async function () {{
    await window.QuizifyI18n.init('en');
    var out = {{}};

    // Team mode, round 1, before any answer_progress: the tracker is empty.
    window.QuizifyPlayerTeam.handleTeamsUpdate({{ teams: [
        {{ team_id: 't-sofa', name: 'Sofa', members: ['Anna', 'Bert'] }},
        {{ team_id: 't-couch', name: 'Couch', members: ['Cleo'] }}
    ] }});
    game.openTargetPicker('freeze', function () {{}}, ROSTER);
    out.teamRound1 = options();
    out.teamRound1Hint = document.getElementById('powerup-target-hint').textContent;

    // Team mode after the tracker has team rows: still players, never teams.
    game.renderSubmissionTracker([
        {{ entrant_id: 't-sofa', name: 'Sofa', submitted: false, connected: true }},
        {{ entrant_id: 't-couch', name: 'Couch', submitted: true, connected: true }}
    ]);
    game.openTargetPicker('freeze', function () {{}}, ROSTER);
    out.teamAfterTracker = options();

    // The round ends with the picker open.
    out.openBeforeReveal = modalOpen();
    handleRoundSummary({{ type: 'round_summary', all_answers: [] }});
    out.openAfterReveal = modalOpen();

    // Solo: everyone but me, and the steal badge still reads the tracker.
    window.QuizifyPlayerTeam.handleTeamsUpdate({{ teams: [] }});
    game.renderSubmissionTracker([
        {{ entrant_id: 'Anna', name: 'Anna', submitted: false, connected: true }},
        {{ entrant_id: 'Bert', name: 'Bert', submitted: true, connected: true }},
        {{ entrant_id: 'Cleo', name: 'Cleo', submitted: false, connected: true }}
    ]);
    game.openTargetPicker('steal', function () {{}}, ROSTER);
    out.solo = options();
    out.soloStealHtml = document.getElementById('powerup-target-list').innerHTML;
    var badges = document.getElementById('powerup-target-list')
        .querySelectorAll('.powerup-target-option')
        .map(function (b) {{ return b.innerHTML.indexOf('powerup-target-submitted') !== -1; }});
    out.soloBadges = badges;

    console.log(JSON.stringify(out));
}})().catch(function (e) {{ console.error(e); process.exit(1); }});
"""


def _run() -> dict:
    script = _SCRIPT.format(
        stub=json.dumps(str(_STUB)),
        i18n=json.dumps(str(_I18N)),
        i18njs=json.dumps(str(_JS / "i18n.js")),
        utils=json.dumps(str(_JS / "utils.js")),
        render_shared=json.dumps(str(_JS / "render-shared.js")),
        player_utils=json.dumps(str(_JS / "player-utils.js")),
        player_game=json.dumps(str(_JS / "player-game.js")),
        player_team=json.dumps(str(_JS / "player-team.js")),
        reveal_core=_slice(_CORE, *_REVEAL_SLICE),
    )
    out = subprocess.run(
        ["node", "-e", script], capture_output=True, text=True, check=True
    )
    return json.loads(out.stdout.strip().splitlines()[-1])


@pytest.fixture(scope="module")
def result() -> dict:
    return _run()


@_NEEDS_NODE
def test_round_one_in_team_mode_lists_the_other_team(result: dict) -> None:
    """The reported screen: "No other players to target" on round 1."""
    assert result["teamRound1"] == ["Cleo"], result
    assert "No other players" not in result["teamRound1Hint"]


@_NEEDS_NODE
def test_the_picker_offers_players_not_team_names(result: dict) -> None:
    """The tracker's rows are teams; the server freezes players."""
    assert result["teamAfterTracker"] == ["Cleo"]


@_NEEDS_NODE
def test_the_picker_does_not_survive_into_the_reveal(result: dict) -> None:
    assert result["openBeforeReveal"] is True, "premise: the picker was open"
    assert result["openAfterReveal"] is False, (
        "the Freeze picker was still on top of the reveal screen"
    )


@_NEEDS_NODE
def test_a_solo_game_is_unchanged(result: dict) -> None:
    assert result["solo"] == ["Bert", "Cleo"]
    # Steal's "answered" badge keeps reading who has submitted.
    assert result["soloBadges"] == [True, False]


# ---------------------------------------------------------------------------
# The server: a teammate is not a target
# ---------------------------------------------------------------------------


class _Runtime:
    def __init__(self, tmp_path: Path) -> None:
        self.data_dir = tmp_path

    async def run_in_executor(self, func, *args):  # noqa: ANN001, ANN002
        return func(*args)

    def create_task(self, coro):  # noqa: ANN001
        import asyncio

        return asyncio.ensure_future(coro)


@pytest.fixture
def team_game(tmp_path: Path) -> QuizifyGameState:
    st = QuizifyGameState(runtime=_Runtime(tmp_path), entry_id="test")
    for name in ("Anna", "Bert", "Cleo"):
        st.add_player(name)
    st.create_team("Sofa", "Anna")
    st.join_team(st.get_team_of("Anna")["team_id"], "Bert")
    st.create_team("Couch", "Cleo")
    st.start_game(
        category="picture-round-en", difficulty="easy", num_rounds=3, language="en"
    )
    assert st.start_next_question() is not None
    return st


def _hold_freeze(game: QuizifyGameState, name: str) -> None:
    game._powerup_manager._inventory[name] = PowerUpType.FREEZE  # noqa: SLF001


def test_freezing_a_teammate_is_refused_and_keeps_the_power_up(
    team_game: QuizifyGameState,
) -> None:
    _hold_freeze(team_game, "Anna")
    assert team_game.use_powerup("Anna", "Bert") == ERR_INVALID_ACTION
    assert team_game._powerup_manager.get_powerup("Anna") is PowerUpType.FREEZE  # noqa: SLF001


def test_freezing_the_other_team_works(team_game: QuizifyGameState) -> None:
    _hold_freeze(team_game, "Anna")
    effect = team_game.use_powerup("Anna", "Cleo")
    assert not isinstance(effect, str), effect
    assert effect.target_player == "Cleo"


def test_the_random_fallback_never_picks_a_teammate(
    team_game: QuizifyGameState,
) -> None:
    for _ in range(25):
        _hold_freeze(team_game, "Anna")
        effect = team_game.use_powerup("Anna", None)
        assert not isinstance(effect, str), effect
        assert effect.target_player == "Cleo"
