"""The FINALE snapshot must carry what the live ``finale`` frame carries (#878).

Both paths end in ``end.updateEndView(msg)``, and that function reads
``data.share`` — so whatever the server leaves out of one path simply is not on
the end screen for anyone who arrived by it.

The snapshot used to hand-list the finale fields, and the list was short by two:

* **no ``share``** — ``renderShareCard`` hides the section when the block is
  missing. The end screen stays up longest in an evening, so a reload or a wifi
  blip on it silently removed the result card the guest was about to paste into
  the group chat. A phone that joined late, straight into FINALE, never had one
  at all.
* **``rank: i + 1``** on the podium, while the live frame shares ranks on ties
  (#308) — so a reloaded phone could show two players who actually tied sitting
  on 1st and 2nd.

Same shape as #730/#731: the fix is not "add the two fields" but to build the
block from ``serialize_finale``, the function the live frame already goes
through. The parity test below is what keeps a third field from going missing.
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT))

from custom_components.quizify.game.state import (  # noqa: E402
    GamePhase,
    QuizifyGameState,
)
from custom_components.quizify.server.serializers import (  # noqa: E402
    resolve_pack_labels,
    serialize_finale,
    serialize_state_snapshot,
)


class _Runtime:
    def __init__(self, tmp_path: Path) -> None:
        self.data_dir = tmp_path

    async def run_in_executor(self, func, *args):  # noqa: ANN001, ANN002
        return func(*args)


def _fake_ws() -> MagicMock:
    ws = MagicMock()
    ws.closed = False
    ws.send_json = AsyncMock()
    return ws


def _finished_game(
    tmp_path: Path,
    scores: dict[str, int],
    histories: dict[str, list[str]] | None = None,
) -> QuizifyGameState:
    """A game sitting in FINALE with the given final scores.

    The rounds are stamped on rather than played: what is under test is the
    serialization of a finished game, and a hand-set ``round_history`` is the
    same input the real path arrives with.
    """
    gs = QuizifyGameState(runtime=_Runtime(tmp_path), entry_id="t")
    for name in scores:
        gs.add_player(name, _fake_ws())
    gs.start_game(language="de", num_rounds=2, lightning_enabled=False)
    for p in gs.get_players():
        p.score = scores[p.name]
        p.round_history = list((histories or {}).get(p.name, ["correct", "wrong"]))
    gs.end_game()
    assert gs.phase == GamePhase.FINALE
    return gs


# ---------------------------------------------------------------------------
# The share card
# ---------------------------------------------------------------------------


def test_finale_snapshot_carries_the_share_card(tmp_path: Path) -> None:
    """A phone that reloads on the end screen still gets something to paste."""
    gs = _finished_game(tmp_path, {"Alice": 30, "Bob": 10})

    snapshot = serialize_state_snapshot(gs)

    assert "share" in snapshot, "the end screen has nothing to share after a reload"
    names = [e["name"] for e in snapshot["share"]["players"]]
    assert names == ["Alice", "Bob"]
    alice = snapshot["share"]["players"][0]
    assert alice["rank"] == 1
    assert alice["total_players"] == 2
    assert alice["results"] == ["correct", "wrong"]


def test_snapshot_share_matches_the_live_frame(tmp_path: Path) -> None:
    """Byte-for-byte the same card, whichever path the phone arrived by."""
    gs = _finished_game(tmp_path, {"Alice": 30, "Bob": 10})
    live = serialize_finale(
        gs.get_finale_podium(),
        gs.get_ranked_participants(),
        superlatives=[s.to_dict() for s in (gs.get_finale_superlatives() or [])],
        packs=resolve_pack_labels(gs),
    )

    assert serialize_state_snapshot(gs)["share"] == live["share"]


def test_snapshot_share_carries_the_pack_labels(tmp_path: Path) -> None:
    """The card names the packs, so the line is not blank after a reload."""
    gs = _finished_game(tmp_path, {"Alice": 10})
    gs.categories = ["geographie", "musik"]

    packs = serialize_state_snapshot(gs)["share"]["packs"]

    assert len(packs) == 2
    # Display names when the bank knows the slug, the slug itself when it does
    # not — never an empty line and never a dropped pack.
    assert all(p for p in packs)


# ---------------------------------------------------------------------------
# Shared podium ranks (#308) on the restore path too
# ---------------------------------------------------------------------------


def test_snapshot_podium_shares_ranks_on_a_tie(tmp_path: Path) -> None:
    """Two players on the same score are both 1st — after a reload as well."""
    gs = _finished_game(tmp_path, {"Alice": 20, "Bob": 20, "Cara": 5})

    podium = serialize_state_snapshot(gs)["podium"]

    ranks = {e["name"]: e["rank"] for e in podium}
    assert ranks["Alice"] == 1
    assert ranks["Bob"] == 1, "a tie was printed as 1st/2nd by sort order"
    assert ranks["Cara"] == 3


def test_snapshot_podium_equals_the_live_podium(tmp_path: Path) -> None:
    gs = _finished_game(tmp_path, {"Alice": 20, "Bob": 20, "Cara": 5})
    live = serialize_finale(gs.get_finale_podium(), gs.get_ranked_participants())

    assert serialize_state_snapshot(gs)["podium"] == live["podium"]


# ---------------------------------------------------------------------------
# The guard against a third missing field
# ---------------------------------------------------------------------------


def test_snapshot_carries_every_finale_field(tmp_path: Path) -> None:
    """Whatever the live finale frame grows, the restore path grows with it."""
    gs = _finished_game(tmp_path, {"Alice": 30, "Bob": 10})
    live = serialize_finale(
        gs.get_finale_podium(),
        gs.get_ranked_participants(),
        superlatives=[s.to_dict() for s in (gs.get_finale_superlatives() or [])],
        packs=resolve_pack_labels(gs),
    )
    snapshot = serialize_state_snapshot(gs)

    # ``type`` names the live frame, not a phase — the snapshot says ``phase``.
    # ``all_players`` is the leaderboard under a second name; the snapshot
    # carries it once, as ``leaderboard``, and that one must agree.
    ignored = ("type", "all_players")
    missing = [k for k in live if k not in snapshot and k not in ignored]
    assert not missing, f"the FINALE snapshot drops {missing}"
    assert snapshot["leaderboard"] == live["all_players"]


def test_superlatives_stay_absent_when_there_are_none(tmp_path: Path) -> None:
    """An empty award list is no key, same as the live frame (#415 shape)."""
    gs = _finished_game(tmp_path, {"Alice": 0}, histories={"Alice": []})
    gs._finale_superlatives = []

    assert "superlatives" not in serialize_state_snapshot(gs)


@pytest.mark.parametrize("phase", [GamePhase.LOBBY, GamePhase.QUESTION_ACTIVE])
def test_share_rides_the_finale_only(tmp_path: Path, phase: GamePhase) -> None:
    """The card is a once-per-game payload — it must not ride every snapshot."""
    gs = _finished_game(tmp_path, {"Alice": 10, "Bob": 5})
    gs.phase = phase

    assert "share" not in serialize_state_snapshot(gs)
