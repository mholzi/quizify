"""The estimate reveal speaks about teams in team mode (#962).

Found in the live test of v1.20.0-RC2: two teams of two, one member per team
set the guess. Every phone read "Anna wins the round", listed Anna and Cleo
instead of Sofa and Couch, and counted "2 without a guess" — Bert and Dora,
whose teams had both guessed. The scores were right; the reveal block was
built per player while the entrant is the team (the rule #923 applied to
podium, sensors and events).

The backend tests drive the real game through its entry points. The phone
tests run the real ``player-reveal.js`` against the DOM stub.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from custom_components.quizify.game.state import QuizifyGameState


class _Runtime:
    def __init__(self, tmp_path: Path) -> None:
        self.data_dir = tmp_path

    async def run_in_executor(self, func, *args):  # noqa: ANN001, ANN002
        return func(*args)

    def create_task(self, coro):  # noqa: ANN001
        import asyncio

        return asyncio.ensure_future(coro)


def _team_game(tmp_path: Path, solo: tuple[str, ...] = ()) -> QuizifyGameState:
    st = QuizifyGameState(runtime=_Runtime(tmp_path), entry_id="test")
    for name in ("Anna", "Bert", "Cleo", "Dora", *solo):
        st.add_player(name)
    st.create_team("Sofa", "Anna")
    st.join_team(st.get_team_of("Anna")["team_id"], "Bert")
    st.create_team("Couch", "Cleo")
    st.join_team(st.get_team_of("Cleo")["team_id"], "Dora")
    st.start_game(
        category="estimation-en", difficulty="easy", num_rounds=3, language="en"
    )
    st.start_next_question()
    return st


def _solo_game(tmp_path: Path) -> QuizifyGameState:
    st = QuizifyGameState(runtime=_Runtime(tmp_path), entry_id="test")
    for name in ("Anna", "Bert", "Cleo"):
        st.add_player(name)
    st.start_game(
        category="estimation-en", difficulty="easy", num_rounds=3, language="en"
    )
    st.start_next_question()
    return st


def _estimate(game: QuizifyGameState) -> dict:
    summary = game.get_round_summary()
    assert summary is not None and summary.estimate is not None
    return summary.estimate


def _rows(est: dict) -> dict[str, dict]:
    return {g["player_name"]: g for g in est["guesses"]}


def test_the_winner_is_the_team(tmp_path: Path) -> None:
    game = _team_game(tmp_path)
    answer = game.get_current_question().estimate_answer
    game.submit_guess("Anna", answer)
    game.submit_guess("Cleo", answer * 0.5)
    game.evaluate_round()

    assert _estimate(game)["winner"] == "Sofa"


def test_one_row_per_team_and_none_per_member(tmp_path: Path) -> None:
    game = _team_game(tmp_path)
    answer = game.get_current_question().estimate_answer
    game.submit_guess("Anna", answer)
    game.submit_guess("Cleo", answer * 0.5)
    game.evaluate_round()

    rows = _rows(_estimate(game))
    assert set(rows) == {"Sofa", "Couch"}
    assert sorted(rows["Sofa"]["members"]) == ["Anna", "Bert"]
    assert rows["Sofa"]["team_id"] == game.get_team_of("Anna")["team_id"]
    assert rows["Sofa"]["guess"] == answer
    assert rows["Sofa"]["rank"] == 1
    assert rows["Couch"]["rank"] == 2


def test_a_teammate_who_did_not_move_the_slider_is_not_without_a_guess(
    tmp_path: Path,
) -> None:
    game = _team_game(tmp_path)
    answer = game.get_current_question().estimate_answer
    game.submit_guess("Anna", answer)
    game.submit_guess("Cleo", answer * 0.5)
    game.evaluate_round()

    assert not any(g["no_guess"] for g in _estimate(game)["guesses"])


def test_a_team_that_never_guessed_is_without_a_guess(tmp_path: Path) -> None:
    game = _team_game(tmp_path)
    game.submit_guess("Anna", game.get_current_question().estimate_answer)
    game.evaluate_round()

    rows = _rows(_estimate(game))
    assert rows["Couch"]["no_guess"] is True
    assert rows["Couch"]["points"] == 0
    assert rows["Sofa"]["no_guess"] is False


def test_the_reveal_points_match_the_team_score(tmp_path: Path) -> None:
    """The wording changes, the scoring does not."""
    game = _team_game(tmp_path)
    answer = game.get_current_question().estimate_answer
    game.submit_guess("Anna", answer)
    game.submit_guess("Cleo", answer * 0.5)
    game.evaluate_round()

    rows = _rows(_estimate(game))
    for team in game.team_registry.all_teams():
        assert rows[team.name]["points"] == team.score


def test_a_guest_in_no_team_keeps_a_row_of_their_own(tmp_path: Path) -> None:
    game = _team_game(tmp_path, solo=("Mira",))
    answer = game.get_current_question().estimate_answer
    game.submit_guess("Anna", answer * 0.5)
    game.submit_guess("Mira", answer)
    game.evaluate_round()

    est = _estimate(game)
    rows = _rows(est)
    assert set(rows) == {"Sofa", "Couch", "Mira"}
    assert "members" not in rows["Mira"]
    assert est["winner"] == "Mira"


def test_solo_rows_are_unchanged(tmp_path: Path) -> None:
    game = _solo_game(tmp_path)
    answer = game.get_current_question().estimate_answer
    game.submit_guess("Anna", answer)
    game.submit_guess("Cleo", answer * 0.5)
    game.evaluate_round()

    est = _estimate(game)
    rows = _rows(est)
    assert set(rows) == {"Anna", "Bert", "Cleo"}
    assert est["winner"] == "Anna"
    assert rows["Bert"]["no_guess"] is True
    assert all("members" not in g and "team_id" not in g for g in est["guesses"])


# --------------------------------------------------------------------------
# The phone
# --------------------------------------------------------------------------

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

QZ.els([
    'reveal-hero', 'reveal-estimate', 'reveal-answer-strip', 'reveal-standings',
    'header-round-num', 'header-round-total', 'fun-fact-container', 'fun-fact',
    'reveal-admin-controls', 'next-round-btn', 'flag-question-btn'
]);
QZ.load({utils_js});
QZ.load({render_shared});
QZ.load({player_utils});
QZ.load({player_game});
QZ.load({player_team});
QZ.load({player_reveal});

var pu = window.QuizifyPlayerUtils;
window.QuizifyPlayerSound = {{
    playCorrect: function () {{}},
    playWrong: function () {{}}
}};

var TEAM_ROWS = [
    {{ player_name: 'Sofa', team_id: 't-sofa', members: ['Anna', 'Bert'], guess: 1850,
       distance: 47, points: 75, rank: 1, exact: false, no_guess: false }},
    {{ player_name: 'Couch', team_id: 't-couch', members: ['Cleo', 'Dora'], guess: 1840,
       distance: 57, points: 52, rank: 2, exact: false, no_guess: false }}
];

function reveal() {{
    return {{
        question_type: 'estimate', question_id: 'dracula', round: 2, total_rounds: 10,
        correct_answer: '1897 year', all_answers: [],
        players: [
            {{ name: 'Anna', score: 0, streak: 0, connected: true }},
            {{ name: 'Bert', score: 0, streak: 0, connected: true }},
            {{ name: 'Cleo', score: 0, streak: 0, connected: true }},
            {{ name: 'Dora', score: 0, streak: 0, connected: true }}
        ],
        leaderboard: [],
        estimate: {{ answer: 1897, answer_text: '1897 year', min: 1700, max: 2000,
                     unit: 'year', step: 1, winner: 'Sofa', guesses: TEAM_ROWS }}
    }};
}}

function as(name) {{
    pu.state.playerName = name;
    window.QuizifyPlayerReveal.updateRevealView(reveal());
    return {{
        hero: document.getElementById('reveal-hero').textContent,
        estimate: document.getElementById('reveal-estimate').innerHTML
    }};
}}

(async function () {{
    await window.QuizifyI18n.init('en');
    window.QuizifyPlayerTeam.handleTeamsUpdate({{ teams: [
        {{ team_id: 't-sofa', name: 'Sofa', members: ['Anna', 'Bert'] }},
        {{ team_id: 't-couch', name: 'Couch', members: ['Cleo', 'Dora'] }}
    ] }});
    var out = {{}};
    out.bert = as('Bert');
    out.dora = as('Dora');
    console.log(JSON.stringify(out));
}})().catch(function (e) {{ console.error(e); process.exit(1); }});
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
        player_team=json.dumps(str(_JS / "player-team.js")),
        player_reveal=json.dumps(str(_JS / "player-reveal.js")),
    )
    out = subprocess.run(
        ["node", "-e", script], capture_output=True, text=True, check=True
    )
    return json.loads(out.stdout.strip().splitlines()[-1])


@pytest.fixture(scope="module")
def phones() -> dict:
    return _run()


def _en(*path: str) -> str:
    node = json.loads((_I18N / "en.json").read_text("utf-8"))
    for key in path:
        node = node[key]
    return node


@_NEEDS_NODE
def test_the_winning_team_s_member_is_told_they_won(phones: dict) -> None:
    """Bert set no guess himself; his team won, so the round is his too."""
    bert = phones["bert"]
    assert _en("estimate", "youWon") in bert["estimate"]
    assert "+75" in bert["hero"]


@_NEEDS_NODE
def test_the_other_team_s_member_reads_the_team_name(phones: dict) -> None:
    dora = phones["dora"]
    won = _en("estimate", "playerWon").replace("{name}", "Sofa")
    assert won in dora["estimate"]
    assert "Anna" not in dora["estimate"]
    assert "+52" in dora["hero"]


@_NEEDS_NODE
def test_no_teammate_is_counted_without_a_guess(phones: dict) -> None:
    note = _en("estimate", "noGuessNote").split("{")[0].strip()
    for phone in phones.values():
        assert "est-noguess" not in phone["estimate"]
        if note:
            assert note not in phone["estimate"]
