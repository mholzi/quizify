"""A tie at the top shares the top step and the crown (#1078).

Found in the v1.23.0-RC6 live test: an estimation game ended 225 : 225. The
standings beside the TV podium said "1" twice — ``serialize_finale`` has given
equal scores a shared rank since #308 — but the podium drew the two players
on steps 1 and 2 at different heights, and the phone's winner banner named
only whoever the sort put first.

The podium now stands each entry on its shared rank, and every winner line
names everyone on step 1 ("Anna & Bert") without a new string. These run the
real modules under node against the stub DOM.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from custom_components.quizify.game.player import PlayerSession
from custom_components.quizify.server.serializers import serialize_finale

REPO = Path(__file__).resolve().parent.parent
JS = REPO / "custom_components" / "quizify" / "www" / "js"
STUB = REPO / "tests" / "fixtures" / "dom_stub.js"


def _run(script: str, tmp_path: Path, *modules: str) -> dict:
    if shutil.which("node") is None:
        pytest.skip("node is not installed")
    loads = "".join(
        f"QZ.load({json.dumps(str(JS / m))});\n"
        for m in ("utils.js", "render-shared.js", *modules)
    )
    harness = tmp_path / "harness.js"
    harness.write_text(
        f"require({json.dumps(str(STUB))});\n" + loads + script, encoding="utf-8"
    )
    result = subprocess.run(
        ["node", str(harness)], capture_output=True, text=True, check=False
    )
    assert result.returncode == 0, f"node harness failed:\n{result.stderr}"
    return json.loads(result.stdout)


def _podium(*scores: int) -> list[dict]:
    """The podium exactly as the server sends it, shared ranks included."""
    players = []
    for name, score in zip(("Anna", "Bert", "Cleo", "Dora"), scores, strict=False):
        p = PlayerSession(name=name)
        p.score = score
        players.append(p)
    return serialize_finale(players, players)["podium"]


def test_the_server_gives_a_tie_one_rank() -> None:
    """Premise: the wire already carries the shared rank."""
    assert [e["rank"] for e in _podium(225, 225)] == [1, 1]
    assert [e["rank"] for e in _podium(300, 200, 200)] == [1, 2, 2]


def _planks(podium: list[dict], tmp_path: Path) -> dict:
    host_opts = "{ pointsLabel: 'pts', championLabel: 'Champion', wrap: true }"
    return _run(
        "var S = window.QuizifyRenderShared;\n"
        "var P = " + json.dumps(podium) + ";\n"
        "console.log(JSON.stringify({\n"
        "  tv: S.podiumHtml(P, { pointsLabel: 'pts', medals: true }),\n"
        "  host: S.podiumHtml(P, " + host_opts + "),\n"
        "  winners: S.winnerNames(P)\n"
        "}));\n",
        tmp_path,
    )


def test_two_tied_winners_share_the_top_step(tmp_path: Path) -> None:
    out = _planks(_podium(225, 225), tmp_path)
    assert out["tv"].count('<div class="podium-bar first">1</div>') == 2
    assert "podium-bar second" not in out["tv"]
    assert out["tv"].count("\U0001f947") == 2, "both get the gold medal"
    assert '<div class="podium-champion-name">Anna &amp; Bert</div>' in out["host"]
    assert out["winners"] == "Anna & Bert"


def test_a_tie_for_second_shares_the_second_step(tmp_path: Path) -> None:
    out = _planks(_podium(300, 200, 200), tmp_path)
    assert out["tv"].count('<div class="podium-bar first">1</div>') == 1
    assert out["tv"].count('<div class="podium-bar second">2</div>') == 2
    assert "podium-bar third" not in out["tv"]
    assert out["winners"] == "Anna"


def test_no_tie_is_unchanged(tmp_path: Path) -> None:
    out = _planks(_podium(300, 200, 100), tmp_path)
    for place, cls in ((2, "second"), (1, "first"), (3, "third")):
        assert f'<div class="podium-bar {cls}">{place}</div>' in out["tv"]
    assert '<div class="podium-champion-name">Anna</div>' in out["host"]


def test_a_podium_without_ranks_falls_back_to_position(tmp_path: Path) -> None:
    podium = [{"name": "Anna", "score": 9}, {"name": "Bert", "score": 9}]
    out = _planks(podium, tmp_path)
    assert '<div class="podium-bar first">1</div>' in out["tv"]
    assert '<div class="podium-bar second">2</div>' in out["tv"]
    assert out["winners"] == "Anna"


def _hero(leaderboard: list[dict], tmp_path: Path) -> dict:
    return _run(
        "QZ.els(['end-hero-name', 'end-hero-score']);\n"
        "QZ.load(" + json.dumps(str(JS / "player-end.js")) + ");\n"
        "window.QuizifyPlayerEnd.renderWinnerHero(" + json.dumps(leaderboard) + ");\n"
        "console.log(JSON.stringify({\n"
        "  name: document.getElementById('end-hero-name').textContent\n"
        "}));\n",
        tmp_path,
        "player-utils.js",
    )


def test_the_phone_crowns_both_tied_winners(tmp_path: Path) -> None:
    lb = [
        {"name": "Anna", "score": 225, "rank": 1},
        {"name": "Bert", "score": 225, "rank": 1},
        {"name": "Cleo", "score": 100, "rank": 3},
    ]
    assert _hero(lb, tmp_path)["name"] == "Anna & Bert"


def test_the_phone_crowns_a_sole_winner_alone(tmp_path: Path) -> None:
    lb = [
        {"name": "Anna", "score": 300, "rank": 1},
        {"name": "Bert", "score": 225, "rank": 2},
    ]
    assert _hero(lb, tmp_path)["name"] == "Anna"
