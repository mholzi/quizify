"""One podium, one roster, one Hot Seat sentence — for all three screens.

#883: `renderPodium` was written twice by hand — `dashboard.html` with a medal
`.podium-avatar` and a `#podium-others` panel, `admin.js` with a `.podium-title`
and no avatar — and the two had drifted far enough that `05-finale.css` carried
`.podium-bar` **and** `.podium-stand`, plus a "legacy" avatar rule whose comment
pointed at the page that had stopped rendering it. One stylesheet styling two
DOM shapes for one feature is what the bleed in #880 rode on.

#787 (part d): the same story for `renderLobbyPlayers` and the Hot Seat
handlers. `admin.js` even carried a comment admitting it — "same grouping the
television has used since #365" — a duplicate documenting itself instead of
being removed. And the settlement had already drifted where drift costs
something: the host console had dropped the `deltas[winner]` fallback the other
two screens kept, so a frame carrying `deltas` without `winner_delta` told the
host the chair settled for 0 while the room read the real number off the
television.

These run the real modules under node against the stub DOM, so they assert on
what the functions *return*, not on the shape of their source.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
WWW = REPO / "custom_components" / "quizify" / "www"
JS = WWW / "js"
STUB = REPO / "tests" / "fixtures" / "dom_stub.js"

RENDER_SHARED = JS / "render-shared.js"
UTILS = JS / "utils.js"


def _run(script: str, tmp_path: Path) -> dict:
    if shutil.which("node") is None:
        pytest.skip("node is not installed")
    harness = tmp_path / "harness.js"
    harness.write_text(
        f"require({json.dumps(str(STUB))});\n"
        f"QZ.load({json.dumps(str(UTILS))});\n"
        f"QZ.load({json.dumps(str(RENDER_SHARED))});\n" + script,
        encoding="utf-8",
    )
    result = subprocess.run(
        ["node", str(harness)], capture_output=True, text=True, check=False
    )
    assert result.returncode == 0, f"node harness failed:\n{result.stderr}"
    return json.loads(result.stdout)


PODIUM = [
    {"name": "Anna", "score": 90},
    {"name": "Bo", "score": 70},
    {"name": "Cy", "score": 50},
]


# ---------------------------------------------------------------------------
# #883 — the podium
# ---------------------------------------------------------------------------


def test_the_podium_renderer_exists_and_both_pages_use_it() -> None:
    """The duplicate is gone, not merely wrapped.

    Both copies built the planks with their own `'<div class="podium-place">'`
    string. If either one is still doing that, the pair is back.
    """
    shared = RENDER_SHARED.read_text("utf-8")
    assert "function podiumHtml(" in shared

    for name in ("dashboard.js", "admin.js"):
        source = (JS / name).read_text("utf-8")
        assert "QuizifyRenderShared.podiumHtml(" in source, (
            f"{name} does not render the podium through the shared renderer"
        )
        assert '"podium-place"' not in source, (
            f"{name} still writes the plank markup by hand (#883)"
        )
        assert '"podium-bar ' not in source, (
            f"{name} still writes the plank class by hand (#883)"
        )


def test_the_champion_stands_in_the_middle(tmp_path: Path) -> None:
    """2 — 1 — 3, which is what makes the winner the middle, tallest plank.

    Not an option either surface gets to have: it is the shape of a podium.
    """
    out = _run(
        "var html = window.QuizifyRenderShared.podiumHtml(%s, { pointsLabel: 'pts' });\n"
        "console.log(JSON.stringify({ html: html }));\n" % json.dumps(PODIUM),
        tmp_path,
    )
    html = out["html"]
    assert html.index("Bo") < html.index("Anna") < html.index("Cy")
    assert html.count('class="podium-place"') == 3
    for place, cls in ((2, "second"), (1, "first"), (3, "third")):
        assert f'<div class="podium-bar {cls}">{place}</div>' in html


def test_the_television_asks_for_the_medal_and_the_host_page_does_not(
    tmp_path: Path,
) -> None:
    """The one real difference between the two DOM shapes, now an argument.

    The medal and its `.podium-label` wrapper are the television's; the champion
    title is the host page's. Everything between them is the same markup.
    """
    out = _run(
        "var S = window.QuizifyRenderShared;\n"
        "console.log(JSON.stringify({\n"
        "  tv: S.podiumHtml(%s, { pointsLabel: 'pts', medals: true }),\n"
        "  host: S.podiumHtml(%s, { pointsLabel: 'pts', championLabel: 'Champion', wrap: true })\n"
        "}));\n" % (json.dumps(PODIUM), json.dumps(PODIUM)),
        tmp_path,
    )
    assert 'class="podium-avatar">\U0001f947' in out["tv"]
    assert 'class="podium-label"' in out["tv"]
    assert "podium-title" not in out["tv"], "the television has no champion title"

    assert "podium-avatar" not in out["host"], (
        "the host page grew a medal it never rendered — this is a visible change"
    )
    assert '<div class="podium-title">Champion</div>' in out["host"]
    assert '<div class="podium-champion-name">Anna</div>' in out["host"]
    assert out["host"].startswith("<div class=\"podium-title\"")
    assert '<div class="podium">' in out["host"]


def test_a_name_cannot_carry_markup_onto_either_screen(tmp_path: Path) -> None:
    """Both hand-written copies escaped; the shared one has to as well."""
    out = _run(
        "var html = window.QuizifyRenderShared.podiumHtml("
        "[{ name: '<img src=x onerror=alert(1)>', score: 1 }], "
        "{ pointsLabel: '<b>pts</b>', medals: true });\n"
        "console.log(JSON.stringify({ html: html }));\n",
        tmp_path,
    )
    assert "<img" not in out["html"]
    assert "&lt;img" in out["html"]
    assert "<b>pts</b>" not in out["html"]


def test_an_unfinished_podium_still_renders(tmp_path: Path) -> None:
    """Two players is a whole evening; three is not a precondition."""
    out = _run(
        "var S = window.QuizifyRenderShared;\n"
        "console.log(JSON.stringify({\n"
        "  two: S.podiumHtml([{name:'A',score:2},{name:'B',score:1}], { pointsLabel: 'pts' }),\n"
        "  none: S.podiumHtml([], { pointsLabel: 'pts', championLabel: 'Champion' })\n"
        "}));\n",
        tmp_path,
    )
    assert out["two"].count('class="podium-place"') == 2
    assert out["none"] == "", "an empty podium must not print a champion title"


# ---------------------------------------------------------------------------
# #787 part d — the lobby roster
# ---------------------------------------------------------------------------


def test_the_roster_is_read_the_same_way_however_the_frame_carried_it(
    tmp_path: Path,
) -> None:
    """A list from `player_joined`, a dict keyed by name from a snapshot."""
    out = _run(
        "var S = window.QuizifyRenderShared;\n"
        "console.log(JSON.stringify({\n"
        "  list: S.rosterList([{name:'A'}]).length,\n"
        "  dict: S.rosterList({ A: {name:'A'}, B: {name:'B'} }).length,\n"
        "  none: S.rosterList(null).length,\n"
        "  junk: S.rosterList(7).length\n"
        "}));\n",
        tmp_path,
    )
    assert out == {"list": 1, "dict": 2, "none": 0, "junk": 0}


def test_a_player_in_no_team_is_a_team_of_one(tmp_path: Path) -> None:
    """Solo players keep their own entry below the groups, not a leftover bucket.

    And the index runs on across the groups AND the tail, because the host
    page's colour fallback is palette-indexed and must not restart per team.
    """
    out = _run(
        "var S = window.QuizifyRenderShared;\n"
        "var html = S.teamGroupedRosterHtml(\n"
        "  [{name:'A'}, {name:'B'}, {name:'C'}],\n"
        "  [{ name: 'Reds', members: ['A', 'B'] }],\n"
        "  { entry: function (p, i) { return '<i>' + p.name + i + '</i>'; },\n"
        "    groupClass: 'g', nameClass: 'n', membersClass: 'm' });\n"
        "console.log(JSON.stringify({ html: html }));\n",
        tmp_path,
    )
    html = out["html"]
    assert '<div class="g"><div class="n">Reds</div><div class="m">' in html
    assert "<i>A0</i><i>B1</i>" in html
    assert html.endswith("<i>C2</i>"), (
        "the solo player is missing, or the index restarted for the tail"
    )


def test_a_team_member_the_roster_has_not_caught_up_with_still_renders(
    tmp_path: Path,
) -> None:
    """A roster frame and a `teams_update` can arrive in either order."""
    out = _run(
        "var S = window.QuizifyRenderShared;\n"
        "var html = S.teamGroupedRosterHtml([], [{ name: 'Reds', members: ['Ghost'] }],\n"
        "  { entry: function (p) { return '<i>' + p.name + '</i>'; },\n"
        "    groupClass: 'g', nameClass: 'n' });\n"
        "console.log(JSON.stringify({ html: html }));\n",
        tmp_path,
    )
    assert "<i>Ghost</i>" in out["html"]


def test_with_no_teams_the_caller_keeps_its_own_flat_branch(tmp_path: Path) -> None:
    out = _run(
        "var S = window.QuizifyRenderShared;\n"
        "console.log(JSON.stringify({ html: S.teamGroupedRosterHtml([{name:'A'}], [], "
        "{ entry: function () { return 'x'; }, groupClass: 'g', nameClass: 'n' }) }));\n",
        tmp_path,
    )
    assert out["html"] == ""


def test_both_boards_group_the_lobby_through_the_shared_renderer() -> None:
    for name in ("dashboard.js", "admin.js"):
        source = (JS / name).read_text("utf-8")
        assert "QuizifyRenderShared.teamGroupedRosterHtml(" in source, (
            f"{name} still groups the lobby by hand (#787)"
        )
        assert "QuizifyRenderShared.rosterList(" in source


# ---------------------------------------------------------------------------
# #787 part d — the Hot Seat
# ---------------------------------------------------------------------------


def test_the_bid_count_is_the_count_and_never_the_amounts(tmp_path: Path) -> None:
    out = _run(
        "var S = window.QuizifyRenderShared;\n"
        "console.log(JSON.stringify({\n"
        "  some: S.hotSeatBidCountText({ count: 3, total: 7 }),\n"
        "  none: S.hotSeatBidCountText({})\n"
        "}));\n",
        tmp_path,
    )
    assert out["some"] == "3 / 7"
    assert out["none"] == "0 / 0"


def test_the_award_names_the_payer_not_the_person_in_the_chair(
    tmp_path: Path,
) -> None:
    """#804: in team mode the chair is won by a person and paid for by a team.

    The points move on the entrant's row, so that is the name every screen
    prints. All three used to read this chain themselves.
    """
    out = _run(
        "var S = window.QuizifyRenderShared;\n"
        "console.log(JSON.stringify({\n"
        "  team: S.hotSeatAward({ winner: 'Anna', entrant: 'Reds', pct: 40, stake: 80 }),\n"
        "  solo: S.hotSeatAward({ winner: 'Anna', pct: 40, stake: 80 })\n"
        "}));\n",
        tmp_path,
    )
    assert out["team"]["name"] == "Reds"
    assert out["team"]["vars"] == {"name": "Reds", "pct": 40, "pts": 80}
    assert out["team"]["key"] == "hotSeat.lost"
    assert out["solo"]["name"] == "Anna"


def test_the_settlement_tells_the_three_outcomes_apart(tmp_path: Path) -> None:
    """`answered` is tri-state, and since #653 two of the three cost the same.

    A bare falsy check collapses "got it wrong" into "ran out of time", which is
    the one distinction these three keys exist to make.
    """
    out = _run(
        "var S = window.QuizifyRenderShared;\n"
        "console.log(JSON.stringify({\n"
        "  right: S.hotSeatSettlement({ answered: true, winner: 'A', winner_delta: 60 }).key,\n"
        "  wrong: S.hotSeatSettlement({ answered: false, winner: 'A', winner_delta: -60 }).key,\n"
        "  timeout: S.hotSeatSettlement({ answered: null, winner: 'A', winner_delta: -60 }).key,\n"
        "  missing: S.hotSeatSettlement({ winner: 'A' }).key\n"
        "}));\n",
        tmp_path,
    )
    assert out == {
        "right": "hotSeat.resultRight",
        "wrong": "hotSeat.resultWrong",
        "timeout": "hotSeat.resultTimeout",
        "missing": "hotSeat.resultTimeout",
    }


def test_the_settlement_still_falls_back_to_the_deltas_map(tmp_path: Path) -> None:
    """The drift this extraction closes.

    `winner_delta` is preferred — a team's key in `deltas` is its id, which no
    screen can construct (#804). But `admin.js` had dropped the older
    `deltas[winner]` read that `dashboard.js` and `player-hotseat.js` both kept,
    so on a frame that carries only `deltas` the host console printed 0 while
    the room read the real number off the television.
    """
    out = _run(
        "var S = window.QuizifyRenderShared;\n"
        "console.log(JSON.stringify({\n"
        "  preferred: S.hotSeatSettlement({ answered: true, winner: 'A', entrant: 'Reds',\n"
        "      winner_delta: 60, deltas: { A: 5 } }).delta,\n"
        "  fallback: S.hotSeatSettlement({ answered: true, winner: 'A', deltas: { A: 42 } }).delta,\n"
        "  neither: S.hotSeatSettlement({ answered: true, winner: 'A' }).delta\n"
        "}));\n",
        tmp_path,
    )
    assert out == {"preferred": 60, "fallback": 42, "neither": 0}


def test_every_surface_reads_the_hot_seat_through_the_shared_helpers() -> None:
    """The three sentences the room has to hear identically on three screens."""
    for name in ("dashboard.js", "admin.js", "player-hotseat.js"):
        source = (JS / name).read_text("utf-8")
        assert "QuizifyRenderShared.hotSeatBidCountText(" in source, name
        assert "QuizifyRenderShared.hotSeatAward(" in source, name
        assert "QuizifyRenderShared.hotSeatSettlement(" in source, name
        assert "hotSeat.resultTimeout" not in source, (
            f"{name} still picks the settlement key itself (#787)"
        )


def test_the_finale_frames_two_collections_are_read_in_one_place(
    tmp_path: Path,
) -> None:
    """`all_players` is the older field name and is still on the wire.

    Both boards carried the same `||` chain: wire-format knowledge, which is
    exactly the sort that goes stale on one surface and not the other.
    """
    out = _run(
        "var S = window.QuizifyRenderShared;\n"
        "console.log(JSON.stringify({\n"
        "  modern: S.finaleStandings({ podium: [1], leaderboard: [1, 2] }),\n"
        "  legacy: S.finaleStandings({ all_players: [1, 2, 3] }),\n"
        "  empty: S.finaleStandings(null)\n"
        "}));\n",
        tmp_path,
    )
    assert out["modern"] == {"podium": [1], "leaderboard": [1, 2]}
    assert out["legacy"] == {"podium": [], "leaderboard": [1, 2, 3]}
    assert out["empty"] == {"podium": [], "leaderboard": []}

    for name in ("dashboard.js", "admin.js"):
        source = (JS / name).read_text("utf-8")
        assert "QuizifyRenderShared.finaleStandings(" in source, name
        assert "msg.all_players" not in source, (
            f"{name} still reads the legacy field name itself (#787)"
        )
