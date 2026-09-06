"""The reveal reaction bonus credits the participant the room can see (#800).

``_handle_reaction`` awarded its +1 to ``game_state.get_player(result.player_id)``.
In team mode the ``RoundSummary`` results are still per player — the member
whose tap carried the team's answer is the "correct" one — so the point landed
on that member's ``player.score``, the shadow value #668/#669 established is
meaningless in team mode. ``Team.score`` never moved, and the ``reaction_bonus``
broadcast serializes ``get_ranked_participants()``, i.e. the teams: the
recipient's phone toasted "+1 from Mira" while the TV, the reveal leaderboard
and the podium all stayed exactly where they were.

The bonus now goes to the ranked participant — the team when there is one, the
player when they are solo — and the toast list stays a list of player names so
every member of a credited team hears about it.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.quizify.game.state import GamePhase, QuizifyGameState
from custom_components.quizify.server.websocket import QuizifyWebSocketHandler


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


def _revealed_team_game(tmp_path: Path) -> QuizifyGameState:
    """Team Sofa (Anna + Jan) answered correctly; Mira is solo and watching."""
    gs = QuizifyGameState(runtime=_Runtime(tmp_path), entry_id="t")
    for name in ("Anna", "Jan", "Mira"):
        gs.add_player(name, _ws())
    gs.create_team("Sofa", "Anna")
    gs.join_team(gs.get_team_of("Anna")["team_id"], "Jan")
    gs.start_game(
        category="picture-round-en",
        difficulty="easy",
        num_rounds=3,
        language="en",
        lightning_enabled=False,
        hot_seat_enabled=False,
    )
    gs.start_next_question()
    q = gs.get_current_question()
    correct = next(i for i, a in enumerate(q.answers) if a.correct)
    gs.submit_answer("Anna", correct)
    gs.evaluate_round()
    assert gs.phase == GamePhase.ANSWER_REVEAL
    return gs


def _sofa(gs: QuizifyGameState):  # noqa: ANN202
    return next(t for t in gs.team_registry.all_teams() if t.name == "Sofa")


def _row(msg: dict, name: str) -> dict:
    return next(e for e in msg["leaderboard"] if e["name"] == name)


# --------------------------------------------------------------------------
# The bug: the point landed on a score nobody can see
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_the_bonus_lands_on_the_team_not_the_carrier(
    tmp_path: Path,
) -> None:
    gs = _revealed_team_game(tmp_path)
    team = _sofa(gs)
    team_before = team.score
    carrier_before = gs.get_player("Anna").score

    h = _handler(tmp_path, gs)
    h._conn.broadcast = lambda m: asyncio.sleep(0)  # type: ignore[assignment,return-value]
    await h._handle_reaction(gs.get_player("Mira").ws, {"emoji": "🎉"}, gs)

    assert team.score == team_before + 1, "the team's score never moved"
    assert gs.get_player("Anna").score == carrier_before, (
        "the point went to the carrier's shadow score again"
    )


@pytest.mark.asyncio
async def test_the_broadcast_leaderboard_shows_the_point(
    tmp_path: Path,
) -> None:
    """The frame that carries the toast has to carry the changed board too."""
    gs = _revealed_team_game(tmp_path)
    before = _sofa(gs).score

    h = _handler(tmp_path, gs)
    sent: list[dict] = []
    h._conn.broadcast = lambda m: sent.append(m) or asyncio.sleep(0)  # type: ignore[assignment,return-value]
    await h._handle_reaction(gs.get_player("Mira").ws, {"emoji": "🎉"}, gs)
    if h._reaction_flush_task is not None:
        await h._reaction_flush_task

    frame = next(m for m in sent if m.get("type") == "reaction_bonus")
    assert _row(frame, "Sofa")["score"] == before + 1


@pytest.mark.asyncio
async def test_every_member_of_the_credited_team_is_toasted(
    tmp_path: Path,
) -> None:
    """``to_players`` stays player names — it is what each phone matches on."""
    gs = _revealed_team_game(tmp_path)

    h = _handler(tmp_path, gs)
    sent: list[dict] = []
    h._conn.broadcast = lambda m: sent.append(m) or asyncio.sleep(0)  # type: ignore[assignment,return-value]
    await h._handle_reaction(gs.get_player("Mira").ws, {"emoji": "🎉"}, gs)
    if h._reaction_flush_task is not None:
        await h._reaction_flush_task

    frame = next(m for m in sent if m.get("type") == "reaction_bonus")
    assert set(frame["to_players"]) == {"Anna", "Jan"}


@pytest.mark.asyncio
async def test_you_cannot_tip_your_own_team(tmp_path: Path) -> None:
    """The 'no tipping your own hat' rule follows the participant, not the name."""
    gs = _revealed_team_game(tmp_path)
    team = _sofa(gs)
    before = team.score

    h = _handler(tmp_path, gs)
    h._conn.broadcast = lambda m: asyncio.sleep(0)  # type: ignore[assignment,return-value]
    # Jan is on Sofa but is not the carrier, so by player name he looked like
    # an unrelated tipper.
    await h._handle_reaction(gs.get_player("Jan").ws, {"emoji": "🎉"}, gs)

    assert team.score == before


@pytest.mark.asyncio
async def test_the_per_round_cap_applies_to_the_team(tmp_path: Path) -> None:
    """Four reactors, cap of three — the fourth point is refused."""
    gs = _revealed_team_game(tmp_path)
    for extra in ("Nils", "Ute", "Timo"):
        gs.add_player(extra, _ws())
    team = _sofa(gs)
    before = team.score

    h = _handler(tmp_path, gs)
    h._conn.broadcast = lambda m: asyncio.sleep(0)  # type: ignore[assignment,return-value]
    for reactor in ("Mira", "Nils", "Ute", "Timo"):
        await h._handle_reaction(gs.get_player(reactor).ws, {"emoji": "🎉"}, gs)

    assert team.score == before + h._REACTION_BONUS_CAP_PER_ROUND


@pytest.mark.asyncio
async def test_the_cap_counter_is_cleared_between_games(
    tmp_path: Path,
) -> None:
    """Round numbers restart at 1, so the received-count must too (#167)."""
    gs = _revealed_team_game(tmp_path)
    team = _sofa(gs)
    for extra in ("Nils", "Ute"):
        gs.add_player(extra, _ws())

    h = _handler(tmp_path, gs)
    h._conn.broadcast = lambda m: asyncio.sleep(0)  # type: ignore[assignment,return-value]
    for reactor in ("Mira", "Nils", "Ute"):
        await h._handle_reaction(gs.get_player(reactor).ws, {"emoji": "🎉"}, gs)
    assert team._reaction_bonuses_received

    team.reset_for_new_game()
    assert team._reaction_bonuses_received == {}


# --------------------------------------------------------------------------
# Solo play is untouched
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_game_without_teams_still_pays_the_player(
    tmp_path: Path,
) -> None:
    gs = QuizifyGameState(runtime=_Runtime(tmp_path), entry_id="t")
    for name in ("Bob", "Alice"):
        gs.add_player(name, _ws())
    gs.start_game(
        category="picture-round-en",
        difficulty="easy",
        num_rounds=3,
        language="en",
        lightning_enabled=False,
        hot_seat_enabled=False,
    )
    gs.start_next_question()
    q = gs.get_current_question()
    gs.submit_answer("Bob", next(i for i, a in enumerate(q.answers) if a.correct))
    gs.evaluate_round()
    before = gs.get_player("Bob").score

    h = _handler(tmp_path, gs)
    h._conn.broadcast = lambda m: asyncio.sleep(0)  # type: ignore[assignment,return-value]
    await h._handle_reaction(gs.get_player("Alice").ws, {"emoji": "🎉"}, gs)

    assert gs.get_player("Bob").score == before + 1


@pytest.mark.asyncio
async def test_a_solo_player_in_a_team_game_is_paid_directly(
    tmp_path: Path,
) -> None:
    """A player who joined no team keeps their own row, so they keep the point."""
    gs = QuizifyGameState(runtime=_Runtime(tmp_path), entry_id="t")
    for name in ("Anna", "Mira"):
        gs.add_player(name, _ws())
    gs.create_team("Sofa", "Anna")
    gs.start_game(
        category="picture-round-en",
        difficulty="easy",
        num_rounds=3,
        language="en",
        lightning_enabled=False,
        hot_seat_enabled=False,
    )
    gs.start_next_question()
    q = gs.get_current_question()
    gs.submit_answer("Mira", next(i for i, a in enumerate(q.answers) if a.correct))
    gs.evaluate_round()
    before = gs.get_player("Mira").score

    h = _handler(tmp_path, gs)
    h._conn.broadcast = lambda m: asyncio.sleep(0)  # type: ignore[assignment,return-value]
    await h._handle_reaction(gs.get_player("Anna").ws, {"emoji": "🎉"}, gs)

    assert gs.get_player("Mira").score == before + 1
