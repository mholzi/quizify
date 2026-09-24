"""The reveal standings key on the entrant and print the server's rank (#1015).

``renderStandings`` in ``player-reveal.js`` was a third copy of the
leaderboard logic that never moved onto entrant keys (#759 fixed the in-game
leaderboard, #845 the submission tracker):

* "me" was matched by the printed name — in team mode the rows are teams, so
  no row ever carried the player's name and the reveal highlighted nothing;
* the rank-delta memo was keyed by the lowercased name — two teams called
  "Sofa" shared one slot and drew phantom arrows;
* rows were numbered ``i + 1`` — a tie read "1. / 2." on the reveal and
  "1 / 1" on the leaderboard one tap later.

These tests run the real ``player-reveal.js`` (with the real team module)
against the DOM stub and read the standings back.
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
var reveal = window.QuizifyPlayerReveal;

function show(leaderboard) {{
    reveal.updateRevealView({{
        question_id: 'q1', round: 2, total_rounds: 10,
        correct_answer: 'A', all_answers: [], players: [],
        leaderboard: leaderboard
    }});
    return document.getElementById('reveal-standings').innerHTML;
}}

(async function () {{
    await window.QuizifyI18n.init('en');
    var out = {{}};

    // --- Team mode: Bert plays for Sofa, rows are teams -------------------
    pu.state.playerName = 'Bert';
    window.QuizifyPlayerTeam.handleTeamsUpdate({{ teams: [
        {{ team_id: 't-sofa', name: 'Sofa', members: ['Anna', 'Bert'] }},
        {{ team_id: 't-couch', name: 'Couch', members: ['Cleo'] }}
    ] }});
    reveal.resetRankMemo();
    out.team = show([
        {{ name: 'Couch', entrant_id: 't-couch', score: 30, rank: 1 }},
        {{ name: 'Sofa', entrant_id: 't-sofa', score: 20, rank: 2 }}
    ]);

    // --- Two teams called Sofa (#759 on the reveal) -----------------------
    // Mira watches solo; the Sofas are told apart only by their id.
    pu.state.playerName = 'Mira';
    window.QuizifyPlayerTeam.handleTeamsUpdate({{ teams: [] }});
    reveal.resetRankMemo();
    show([
        {{ name: 'Sofa', entrant_id: 't-a', score: 30, rank: 1 }},
        {{ name: 'Sofa', entrant_id: 't-b', score: 20, rank: 2 }},
        {{ name: 'Mira', entrant_id: 'Mira', score: 10, rank: 3 }}
    ]);
    // Next round: the first Sofa holds 1st, Mira passes the second Sofa.
    out.sofas = show([
        {{ name: 'Sofa', entrant_id: 't-a', score: 40, rank: 1 }},
        {{ name: 'Mira', entrant_id: 'Mira', score: 35, rank: 2 }},
        {{ name: 'Sofa', entrant_id: 't-b', score: 30, rank: 3 }}
    ]);

    // --- A tie: the server's competition rank, not the row index ----------
    pu.state.playerName = 'Cleo';
    reveal.resetRankMemo();
    out.tie = show([
        {{ name: 'Anna', entrant_id: 'Anna', score: 50, rank: 1 }},
        {{ name: 'Bert', entrant_id: 'Bert', score: 50, rank: 1 }},
        {{ name: 'Cleo', entrant_id: 'Cleo', score: 10, rank: 3 }}
    ]);

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
def result() -> dict:
    return _run()


def _rows(html: str) -> list[dict]:
    """One dict per standings row: classes, place, name, delta."""
    rows = []
    for m in re.finditer(r'<div class="([^"]*)">(.*?)</div>', html):
        body = m.group(2)
        pos = re.search(r'pl-result-pos">([^<]*)<', body)
        nm = re.search(r'pl-result-nm">([^<]*)<', body)
        delta = re.search(r'pl-result-delta[^"]*">([^<]*)<', body)
        rows.append(
            {
                "cls": m.group(1),
                "pos": pos.group(1) if pos else None,
                "name": nm.group(1) if nm else None,
                "delta": delta.group(1) if delta else None,
            }
        )
    return rows


def _you() -> str:
    return json.loads((_I18N / "en.json").read_text("utf-8"))["lobby"]["you"]


@_NEEDS_NODE
def test_team_mode_highlights_my_team(result: dict) -> None:
    """The rows are teams; Bert's row is Sofa's, not a row named "Bert"."""
    rows = _rows(result["team"])
    assert len(rows) == 2, rows
    mine = [r for r in rows if "pl-result-srow--me" in r["cls"]]
    assert len(mine) == 1, f"no (or several) highlighted rows: {rows}"
    assert mine[0]["name"] == _you()
    assert "pl-result-srow--me" in rows[1]["cls"], (
        "the highlight belongs on Sofa, the second row"
    )


@_NEEDS_NODE
def test_same_named_teams_do_not_draw_phantom_deltas(result: dict) -> None:
    """Two Sofas shared one memo slot: the Sofa that held 1st drew ↓1."""
    rows = _rows(result["sofas"])
    assert [r["delta"] for r in rows] == ["—", "↑1", "↓1"], rows


@_NEEDS_NODE
def test_a_tie_shows_the_server_rank(result: dict) -> None:
    rows = _rows(result["tie"])
    assert [r["pos"] for r in rows] == ["1.", "1.", "3."], rows
    # Both tied leaders wear the gold tint the rank earns.
    assert all("pl-result-srow--gold" in r["cls"] for r in rows[:2]), rows
    assert "pl-result-srow--me" in rows[2]["cls"]


def test_the_shipped_bundle_carries_the_change() -> None:
    """The bundle is committed; a stale one ships the name-keyed reveal."""
    bundle = (_JS / "player.bundle.js").read_text("utf-8")
    assert "var isMe = r.key === me;" in bundle, "run: python3 scripts/build_bundle.py"
    assert "var isMe = r.name === myName;" not in bundle
