"""Three measured hot-path findings from the 2026-09-07 review (#896).

a) ``QuizifyAnalytics.get_head_to_head`` recomputed the lobby duel with an
   O(pairs x games) scan on the event loop at every roster flush. It is a pure
   optimisation, so what is asserted here is *equivalence*: the reference
   implementation below is the loop that shipped, copied verbatim, and the
   shipping one has to agree with it record for record. A faster head-to-head
   that ranks differently is worse than a slow one, so the timing check at the
   bottom is deliberately the least of these assertions.

b) ``_broadcast_team_answer`` and ``_broadcast_lightning_team_answer`` awaited
   ``ConnectionManager.send`` once per member. ``send`` wraps the write in a
   2 s timeout, so one half-dead phone held the frame — and the handler's
   ``answer_accepted`` reply — for its whole team.

c) The reaction flush emitted one broadcast per distinct ``(player, emoji)``
   instead of one batched frame per window, while ``reaction_bonus`` in the
   same function had batched since #416.
"""

from __future__ import annotations

import asyncio
import random
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.quizify.analytics import QuizifyAnalytics
from custom_components.quizify.game.state import QuizifyGameState, TeamAnswerAck
from custom_components.quizify.server.websocket import QuizifyWebSocketHandler

# ---------------------------------------------------------------------------
# (a) head-to-head: the shipped-before implementation, kept as the oracle
# ---------------------------------------------------------------------------


def _reference_head_to_head(games: list[dict], present: list[str]):
    """The pair-major loop from before #896, character for character.

    Kept in the test rather than in the module so the fast path has something
    independent to be compared against. If this ever has to change, the change
    is a behaviour change and belongs in a separate commit with its own reason.
    """
    names = [n for n in dict.fromkeys(present) if n]
    if len(names) < 2:
        return None

    best = None
    for i, left in enumerate(names):
        for right in names[i + 1 :]:
            left_wins = right_wins = met = 0
            for game in games:
                scores = game.get("player_scores") or {}
                if left not in scores or right not in scores:
                    continue
                met += 1
                if scores[left] > scores[right]:
                    left_wins += 1
                elif scores[right] > scores[left]:
                    right_wins += 1
            if met < 2:
                continue
            if best is None or met > best["games"]:
                best = {
                    "left": left,
                    "right": right,
                    "left_wins": left_wins,
                    "right_wins": right_wins,
                    "games": met,
                }
    return best


def _analytics_over(games: list[dict]) -> QuizifyAnalytics:
    """An analytics object holding *games* and nothing else.

    ``__init__`` wants a runtime for the file it will never open here, so the
    instance is built without it: this is a pure read over ``_data``.
    """
    analytics = object.__new__(QuizifyAnalytics)
    analytics._data = {"version": 2, "games": games, "all_time_players": {}}
    return analytics


def _history(
    n_games: int, pool: list[str], per_game: int, seed: int = 7
) -> list[dict]:
    """*n_games* records, each a random sitting drawn from *pool*."""
    rng = random.Random(seed)
    return [
        {
            "ended_at": index,
            "player_scores": {
                name: rng.randrange(0, 200)
                for name in rng.sample(pool, min(per_game, len(pool)))
            },
        }
        for index in range(n_games)
    ]


POOL = [f"Player{i:02d}" for i in range(20)]


@pytest.mark.parametrize(
    ("present_count", "per_game", "pool_size"),
    [
        (2, 4, 12),  # a couple, against a history of parties
        (4, 4, 12),
        (8, 4, 12),  # the reported case
        (8, 8, 8),  # every present player in every recorded game
        (12, 5, 16),
        (20, 6, 20),  # the player cap
        (20, 20, 20),  # the cap, densest possible history
    ],
)
def test_the_fast_head_to_head_agrees_with_the_old_one(
    present_count: int, per_game: int, pool_size: int
) -> None:
    """1000 games, every roster shape: identical result, field for field."""
    games = _history(1000, POOL[:pool_size], per_game)
    present = POOL[:present_count]

    assert _analytics_over(games).get_head_to_head(present) == (
        _reference_head_to_head(games, present)
    )


def test_the_two_agree_across_a_hundred_random_histories() -> None:
    """Short histories, so draws and two-game ties actually occur.

    The 1000-game case above almost never produces a tie on ``met``; these do,
    which is where a rewrite would quietly pick the other pair.
    """
    for seed in range(100):
        rng = random.Random(seed)
        pool = POOL[: rng.randrange(2, 8)]
        # A small score range makes draws — a meeting that goes to nobody —
        # common rather than theoretical.
        games = [
            {
                "ended_at": i,
                "player_scores": {
                    name: rng.randrange(0, 3)
                    for name in rng.sample(pool, rng.randrange(1, len(pool) + 1))
                },
            }
            for i in range(rng.randrange(0, 12))
        ]
        present = rng.sample(pool, rng.randrange(0, len(pool) + 1))
        assert _analytics_over(games).get_head_to_head(present) == (
            _reference_head_to_head(games, present)
        ), f"seed {seed}"


def test_a_duplicate_or_blank_name_is_still_dropped() -> None:
    """The roster arrives from ``get_players``; the guards stay guards."""
    games = _history(40, POOL[:4], 3)
    analytics = _analytics_over(games)

    with_noise = ["", POOL[0], POOL[1], POOL[0], "", POOL[2]]
    assert analytics.get_head_to_head(with_noise) == (
        _reference_head_to_head(games, with_noise)
    )
    assert analytics.get_head_to_head([""]) is None
    assert analytics.get_head_to_head([POOL[0], POOL[0]]) is None


def test_head_to_head_is_faster_than_the_scan_it_replaced() -> None:
    """The point of the change, measured — 1000 games and eight players.

    A wall-clock assertion in a test suite has to survive a loaded CI runner,
    so the bar is deliberately low: merely "not slower". The margin measured on
    an M-series Mac is 2.9x for a sparse history and 3.9x when all eight
    present players appear in every record; at the twenty-player cap it is 14x.
    """
    games = _history(1000, POOL[:8], 8)
    present = POOL[:8]
    analytics = _analytics_over(games)

    def _best_of(call, rounds: int = 5) -> float:
        return min(_time_one(call) for _ in range(rounds))

    def _time_one(call) -> float:
        start = time.perf_counter()
        for _ in range(5):
            call()
        return time.perf_counter() - start

    fast = _best_of(lambda: analytics.get_head_to_head(present))
    slow = _best_of(lambda: _reference_head_to_head(games, present))
    assert fast < slow, f"new {fast * 1000:.2f} ms vs old {slow * 1000:.2f} ms"


# ---------------------------------------------------------------------------
# (b) team-answer fan-out
# ---------------------------------------------------------------------------


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
    ws.send_json = AsyncMock()
    return ws


def _handler(tmp_path: Path, game: QuizifyGameState) -> QuizifyWebSocketHandler:
    return QuizifyWebSocketHandler(
        runtime=_Runtime(tmp_path), game_state_provider=lambda: game
    )


def _team_game(tmp_path: Path) -> QuizifyGameState:
    """Anna, Ben and Cem on one sofa, mid-question."""
    gs = QuizifyGameState(runtime=_Runtime(tmp_path), entry_id="t")
    for name in ("Anna", "Ben", "Cem"):
        gs.add_player(name, _ws())
    gs.create_team("Sofa", "Anna")
    team_id = gs.get_team_of("Anna")["team_id"]
    gs.join_team(team_id, "Ben")
    gs.join_team(team_id, "Cem")
    gs.start_game(
        category="picture-round-en",
        difficulty="easy",
        num_rounds=3,
        language="en",
        lightning_enabled=False,
        hot_seat_enabled=False,
    )
    gs.start_next_question()
    # Every member holds a shuffle containing the tapped index, so nothing is
    # skipped by the ValueError guard and the fan-out really is three sends.
    gs.player_shuffles = {name: [0, 1, 2, 3] for name in ("Anna", "Ben", "Cem")}
    return gs


class _BlockingSend:
    """Stands in for ``ConnectionManager.send``: one member never returns.

    Records which member each call was for as it *starts*, which is the whole
    question — sequentially awaited sends only ever start the second one after
    the first has finished.
    """

    def __init__(self, stalled) -> None:
        self.stalled = stalled
        self.started: list[str] = []
        self.release = asyncio.Event()

    async def __call__(self, ws, message: dict) -> None:  # noqa: ANN001
        self.started.append(message["set_by"] + ":" + str(id(ws)))
        if ws is self.stalled:
            await self.release.wait()


async def _wait_for_sends(send: _BlockingSend, count: int, timeout: float = 1.0):
    """Give the loop every chance to start *count* sends, then stop waiting.

    Polling rather than a fixed number of ``sleep(0)`` turns: a loaded machine
    can take more turns than a hand-picked constant allows, and a test that
    goes red because a benchmark was running next door teaches nobody
    anything. With the gather in place this returns on the first check; without
    it the remaining sends never start, so the wait runs out and the assertion
    that follows fails for the right reason.
    """
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while len(send.started) < count and loop.time() < deadline:
        await asyncio.sleep(0.005)


@pytest.mark.asyncio
async def test_a_stalled_member_does_not_hold_up_its_team(tmp_path: Path) -> None:
    """#896b: the three sends are in flight together, not queued behind one."""
    gs = _team_game(tmp_path)
    handler = _handler(tmp_path, gs)
    team = gs.get_team_of("Anna")
    stalled = gs.get_player("Anna").ws

    send = _BlockingSend(stalled)
    handler._conn.send = send  # type: ignore[assignment]

    ack = TeamAnswerAck(
        team_id=team["team_id"], answer_index=0, set_by="Anna", lock_seconds=2.0
    )
    task = asyncio.ensure_future(
        handler._broadcast_team_answer(gs, ack, setter="Anna")
    )
    await _wait_for_sends(send, 3)

    assert len(send.started) == 3, (
        "Ben and Cem are waiting behind Anna's 2 s send timeout"
    )
    assert not task.done()

    send.release.set()
    await asyncio.wait_for(task, timeout=1.0)


@pytest.mark.asyncio
async def test_a_stalled_member_does_not_hold_up_a_lightning_team(
    tmp_path: Path,
) -> None:
    """#896b again, for the Lightning round's own copy of the fan-out."""
    gs = _team_game(tmp_path)
    handler = _handler(tmp_path, gs)
    stalled = gs.get_player("Anna").ws

    standing = MagicMock()
    standing.answer_index = 0
    lightning = MagicMock()
    lightning.index = 0
    lightning.standing_answer.return_value = standing
    lightning.members_of.return_value = ["Anna", "Ben", "Cem"]
    lightning.ensure_shuffle.return_value = [0, 1, 2, 3]

    send = _BlockingSend(stalled)
    handler._conn.send = send  # type: ignore[assignment]

    task = asyncio.ensure_future(
        handler._broadcast_lightning_team_answer(gs, lightning, "Anna")
    )
    await _wait_for_sends(send, 3)

    assert len(send.started) == 3, (
        "Ben and Cem are waiting behind Anna's 2 s send timeout"
    )
    assert not task.done()

    send.release.set()
    await asyncio.wait_for(task, timeout=1.0)


# ---------------------------------------------------------------------------
# (c) one reactions frame per window
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_flush_window_produces_exactly_one_reactions_frame(
    tmp_path: Path,
) -> None:
    """#896c: six distinct reactions leave as one frame, not six."""
    gs = _team_game(tmp_path)
    handler = _handler(tmp_path, gs)
    handler._REACTION_FLUSH_WINDOW = 0.02

    frames: list[dict] = []

    async def _capture(message: dict) -> None:
        frames.append(message)

    handler._conn.broadcast = _capture  # type: ignore[assignment]

    for name in ("Anna", "Ben", "Cem"):
        handler._enqueue_reaction(name, "🎉")
        handler._enqueue_reaction(name, "👏")
    loop = asyncio.get_running_loop()
    deadline = loop.time() + 2.0
    while not frames and loop.time() < deadline:
        await asyncio.sleep(handler._REACTION_FLUSH_WINDOW / 2)

    assert len(frames) == 1
    assert frames[0]["type"] == "reactions"
    assert [(r["player_name"], r["emoji"]) for r in frames[0]["reactions"]] == [
        ("Anna", "🎉"),
        ("Anna", "👏"),
        ("Ben", "🎉"),
        ("Ben", "👏"),
        ("Cem", "🎉"),
        ("Cem", "👏"),
    ]


@pytest.mark.asyncio
async def test_an_empty_window_broadcasts_nothing(tmp_path: Path) -> None:
    """A window with nothing in it must not become an empty ``reactions``.

    The batched frame is built from a list, and a list is easy to send empty.
    Every client would then animate nothing, once per window, forever.
    """
    gs = _team_game(tmp_path)
    handler = _handler(tmp_path, gs)
    handler._REACTION_FLUSH_WINDOW = 0.02

    frames: list[dict] = []

    async def _capture(message: dict) -> None:
        frames.append(message)

    handler._conn.broadcast = _capture  # type: ignore[assignment]

    await handler._flush_reactions_after_window()

    assert frames == []


@pytest.mark.parametrize(
    "path",
    [
        "custom_components/quizify/www/js/player-core.js",
        "custom_components/quizify/www/js/admin.js",
        "custom_components/quizify/www/dashboard.html",
    ],
)
def test_the_clients_read_both_reaction_shapes(path: str) -> None:
    """One release of overlap (#896c).

    The batched frame is what the server sends now; the single-``reaction``
    branch stays so a phone holding a bundle from either side of this change
    still shows the reveal's reactions.
    """
    source = Path(path).read_text(encoding="utf-8")
    assert "case 'reactions':" in source, f"{path} ignores the batched frame"
    assert "case 'reaction':" in source, f"{path} dropped the old frame early"
