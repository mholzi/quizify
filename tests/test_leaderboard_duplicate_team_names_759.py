"""Two teams of the same name are two rows on the normal leaderboard (#759).

``serialize_leaderboard`` identified a row by its name alone. Each ``Team``
object is scored and paid correctly in a normal round — the scoring half of
this was fixed for the lightning round in #728 — but two teams called "Sofa"
produced two rows the client could not tell apart: the name-based "you"
highlight lit both of them, the rank-delta memo and the FLIP animation shared
one slot between them.

The fix is the shape #728 introduced in ``game/lightning.py``: a stable
``entrant_id`` beside the ``name``, with the name still the label. A player's
name is already unique per game, so for a player the entrant id is their name
and nothing about solo play changes.
"""

from __future__ import annotations

import asyncio
import re
from pathlib import Path
from unittest.mock import MagicMock

from custom_components.quizify.game.state import QuizifyGameState
from custom_components.quizify.server.serializers import serialize_leaderboard

_JS_DIR = (
    Path(__file__).resolve().parent.parent
    / "custom_components"
    / "quizify"
    / "www"
    / "js"
)


class _Runtime:
    def __init__(self, tmp_path: Path) -> None:
        self.data_dir = tmp_path

    async def run_in_executor(self, func, *args):  # noqa: ANN001, ANN002
        return func(*args)

    def create_task(self, coro):  # noqa: ANN001
        return asyncio.ensure_future(coro)


def _ws() -> MagicMock:
    ws = MagicMock()
    ws.closed = False
    return ws


def _two_sofas(tmp_path: Path) -> QuizifyGameState:
    """Two teams called "Sofa", plus one solo player."""
    gs = QuizifyGameState(runtime=_Runtime(tmp_path), entry_id="t")
    for name in ("Anna", "Jan", "Mira"):
        gs.add_player(name, _ws())
    gs.create_team("Sofa", "Anna")
    gs.create_team("Sofa", "Jan")
    return gs


# --------------------------------------------------------------------------
# The wire: two rows, and they can be told apart
# --------------------------------------------------------------------------


def test_two_same_named_teams_are_two_distinguishable_rows(
    tmp_path: Path,
) -> None:
    gs = _two_sofas(tmp_path)
    teams = gs.team_registry.all_teams()
    teams[0].score = 30
    teams[1].score = 10

    rows = serialize_leaderboard(gs.get_ranked_participants())
    sofas = [r for r in rows if r["name"] == "Sofa"]

    assert len(sofas) == 2
    ids = {r["entrant_id"] for r in sofas}
    assert len(ids) == 2, f"the two Sofas share one identity: {ids}"
    assert ids == {t.team_id for t in teams}


def test_the_row_still_prints_the_team_name(tmp_path: Path) -> None:
    """The id is for matching; the name is still the label the TV shows."""
    gs = _two_sofas(tmp_path)
    rows = serialize_leaderboard(gs.get_ranked_participants())
    assert [r["name"] for r in rows if r["entrant_id"] != r["name"]] == [
        "Sofa",
        "Sofa",
    ]


def test_a_players_entrant_id_is_their_name(tmp_path: Path) -> None:
    """Names are unique per game, so solo play needs no second identifier."""
    gs = QuizifyGameState(runtime=_Runtime(tmp_path), entry_id="t")
    for name in ("Bob", "Alice"):
        gs.add_player(name, _ws())

    rows = serialize_leaderboard(gs.get_ranked_participants())
    assert {r["entrant_id"] for r in rows} == {"Bob", "Alice"}
    assert all(r["entrant_id"] == r["name"] for r in rows)


def test_a_solo_player_in_a_team_game_keeps_their_own_id(
    tmp_path: Path,
) -> None:
    gs = _two_sofas(tmp_path)
    rows = serialize_leaderboard(gs.get_ranked_participants())
    mira = next(r for r in rows if r["name"] == "Mira")
    assert mira["entrant_id"] == "Mira"


# --------------------------------------------------------------------------
# The client: nothing matches a leaderboard row by its printed name any more
# --------------------------------------------------------------------------


def _src(name: str) -> str:
    return (_JS_DIR / name).read_text("utf-8")


def test_the_player_leaderboard_matches_on_the_entrant_id() -> None:
    src = _src("player-game.js")
    assert "function entrantKey(" in src
    assert "function myEntrant(" in src
    assert "entry.is_current = (entrantKey(entry) === me)" in src
    assert "entry.name === state.playerName" not in src, (
        "a leaderboard row is still identified by its printed name"
    )


def test_the_flip_animation_and_rank_memo_key_on_the_entrant_id() -> None:
    src = _src("player-game.js")
    assert "data-entrant=" in src
    assert "el.dataset.entrant" in src
    assert "_prevLeaderboardRanks[entrantKey(entry)]" in src
    assert "dataset.name" not in src


def test_the_finale_you_badge_matches_on_the_entrant_id() -> None:
    src = _src("player-end.js")
    assert "(entry.entrant_id || entry.name) === me" in src
    assert "entry.is_current = (entry.name === state.playerName)" not in src


def test_the_shipped_bundle_carries_the_change() -> None:
    """The bundle is committed; a stale one ships the old matching to phones."""
    bundle = _src("player.bundle.js")
    for needle in (
        "function entrantKey(",
        "data-entrant=",
        "(entry.entrant_id || entry.name) === me",
    ):
        assert needle in bundle, (
            f"{needle!r} missing — run: python3 scripts/build_bundle.py"
        )
    assert not re.search(
        r"entry\.is_current = \(entry\.name === state\.playerName\)", bundle
    )
