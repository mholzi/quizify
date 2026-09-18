"""The end screen counts the people who played, not the rows (#969).

Observed live on v1.20.0-RC3, team mode, four people in two teams (Sofa =
Anna + Bert, Couch = Cleo + Dora)::

    game-over subtitle on every phone -> "10 rounds · 2 players"

``renderEndHeader`` counted ``leaderboard.length``. Since #365/#923 a team is
one leaderboard row, so in team mode that is the number of teams. The fix
gives every leaderboard row the people behind it (``members``) and the
subtitle sums them. The subtitle keeps its existing ``playersCount`` string.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from custom_components.quizify.game.team import Team
from custom_components.quizify.server.serializers import (
    serialize_finale,
    serialize_leaderboard,
)

_REPO = Path(__file__).resolve().parent.parent
_WWW = _REPO / "custom_components" / "quizify" / "www"
_JS = _WWW / "js"
_I18N = _WWW / "i18n"
_STUB = Path(__file__).resolve().parent / "fixtures" / "dom_stub.js"
_END = _JS / "player-end.js"

_NEEDS_NODE = pytest.mark.skipif(
    shutil.which("node") is None, reason="node not installed"
)


class _Player:
    """Just the attributes ``serialize_leaderboard`` reads off a player."""

    def __init__(self, name: str, score: int) -> None:
        self.name = name
        self.score = score
        self.streak = 0
        self.round_score = 0
        self.round_history = ["correct"] * 10
        self.color = "#fff"
        self.is_admin = False
        self.submitted = False
        self.max_streak = 0
        self.powerups_used = 0


def _live_test_teams() -> list[Team]:
    sofa = Team(name="Sofa", members=["Anna", "Bert"], score=60)
    couch = Team(name="Couch", members=["Cleo", "Dora"], score=40)
    for team in (sofa, couch):
        team.round_history = ["correct"] * 10
    return [sofa, couch]


# ---------------------------------------------------------------------------
# The wire: every row says who is behind it
# ---------------------------------------------------------------------------


def test_a_team_row_carries_its_members() -> None:
    rows = serialize_leaderboard(_live_test_teams())

    assert rows[0]["members"] == ["Anna", "Bert"]
    assert rows[1]["members"] == ["Cleo", "Dora"]


def test_a_player_row_stands_for_one_person() -> None:
    rows = serialize_leaderboard([_Player("Mira", 30)])

    assert rows[0]["members"] == ["Mira"]


def test_the_finale_payload_carries_them_too() -> None:
    """The live finale event is one of the two routes to the end screen."""
    teams = _live_test_teams()
    payload = serialize_finale(teams, teams)

    assert [len(r["members"]) for r in payload["leaderboard"]] == [2, 2]


# ---------------------------------------------------------------------------
# The subtitle
# ---------------------------------------------------------------------------


def _js_function(source: str, signature: str) -> str:
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


_SCRIPT = """
require({stub});
QZ.serveI18n({i18n});
QZ.load({i18njs});
QZ.el('end-meta-line');

function _t(key, params) {{ return window.QuizifyI18n.t(key, params); }}
function _tf(key, fallback, params) {{
    var v = _t(key, params);
    return (v === key) ? fallback : v;
}}

{render_end_header}

var cases = {cases};

(async function () {{
    var out = {{}};
    for (var lang of ['en', 'de']) {{
        await window.QuizifyI18n.init(lang);
        await window.QuizifyI18n.setLanguage(lang);
        out[lang] = {{}};
        Object.keys(cases).forEach(function (name) {{
            renderEndHeader(cases[name], {{ total_rounds: 10 }});
            out[lang][name] = document.getElementById('end-meta-line').textContent;
        }});
    }}
    console.log(JSON.stringify(out));
}})();
"""


def _render() -> dict:
    teams = _live_test_teams()
    mixed = [*_live_test_teams()[:1], _Player("Mira", 20)]
    solo = [_Player(n, 10) for n in ("Anna", "Bert", "Cleo")]
    cases = {
        "teams": serialize_finale(teams, teams)["leaderboard"],
        "mixed": serialize_leaderboard(mixed),
        "solo": serialize_leaderboard(solo),
        # A row from before the key existed (a stale snapshot) still counts
        # as one person rather than as nobody.
        "legacy": [{"name": "Anna", "score": 1}, {"name": "Bert", "score": 0}],
    }
    source = _END.read_text("utf-8")
    script = _SCRIPT.format(
        stub=json.dumps(str(_STUB)),
        i18n=json.dumps(str(_I18N)),
        i18njs=json.dumps(str(_JS / "i18n.js")),
        render_end_header=_js_function(
            source, "function renderEndHeader(leaderboard, data)"
        ),
        cases=json.dumps(cases),
    )
    out = subprocess.run(
        ["node", "-e", script], capture_output=True, text=True, check=True
    )
    return json.loads(out.stdout)


@_NEEDS_NODE
def test_two_teams_of_two_read_as_four_players() -> None:
    """The live-test room, exactly."""
    result = _render()

    assert result["en"]["teams"] == "10 rounds · 4 players"
    assert result["de"]["teams"] == "10 Runden · 4 Spieler"


@_NEEDS_NODE
def test_a_lone_player_beside_a_team_counts_once() -> None:
    result = _render()

    assert result["en"]["mixed"] == "10 rounds · 3 players"


@_NEEDS_NODE
def test_a_game_without_teams_is_unchanged() -> None:
    result = _render()

    assert result["en"]["solo"] == "10 rounds · 3 players"
    assert result["en"]["legacy"] == "10 rounds · 2 players"
