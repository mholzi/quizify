"""Issue #1150: a guest who left can be taken out of the scoring.

Design Variant B, "Aus der Wertung". Since #1093 a disconnected guest keeps
seat and score for the whole game, so somebody who went home at round 8 stood
on the television for twelve rounds and on the podium at the end. The host now
takes such a row out with one tap: the points stay saved, a return undoes it,
and the room only sees it at the next normal reveal.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.quizify.const import ERR_INVALID_ACTION
from custom_components.quizify.game.state import GamePhase, QuizifyGameState
from custom_components.quizify.server.connection import ConnectionManager
from custom_components.quizify.server.serializers import (
    serialize_leaderboard,
    serialize_scoring_status,
)
from custom_components.quizify.server.websocket import QuizifyWebSocketHandler

WWW = Path(__file__).resolve().parent.parent / "custom_components/quizify/www"


class _Socket:
    """A socket that keeps what it got, and refuses once it is closed."""

    def __init__(self) -> None:
        self.closed = False
        self.frames: list[dict[str, Any]] = []

    async def send_json(self, message: dict[str, Any]) -> None:
        if self.closed:
            raise ConnectionResetError("socket is closed")
        self.frames.append(message)

    def of_type(self, frame_type: str) -> list[dict[str, Any]]:
        return [f for f in self.frames if f.get("type") == frame_type]


class _FakeRuntime:
    def __init__(self, tmp_path: Path) -> None:
        self.data_dir = tmp_path


def _game(*names: str) -> QuizifyGameState:
    game = QuizifyGameState(runtime=None, entry_id="test")
    for name in names:
        assert game.add_player(name, f"conn-{name}") == (True, None)
    return game


def _start(game: QuizifyGameState) -> None:
    game.start_game(language="en", num_rounds=20, difficulty="easy")


def _round(game: QuizifyGameState) -> None:
    """Play one normal round to its reveal; nobody answers."""
    assert game.start_next_question() is not None
    game._do_evaluate_round()
    assert game.phase == GamePhase.ANSWER_REVEAL


def _gone(game: QuizifyGameState, name: str) -> None:
    player = game.get_player(name)
    assert player is not None
    player.connected = False


def _ranked(game: QuizifyGameState) -> list[str]:
    return [p.name for p in game.get_standings()]


def _scores(game: QuizifyGameState, **scores: int) -> None:
    for name, score in scores.items():
        player = game.get_player(name)
        assert player is not None
        player.score = score


@pytest.fixture
def anna_left() -> QuizifyGameState:
    """Anna leads, her phone is gone, and a round has gone by without her."""
    game = _game("Anna", "Ben", "Clara")
    _start(game)
    _gone(game, "Anna")
    _round(game)
    _scores(game, Anna=1240, Ben=1180, Clara=1050)
    return game


# --- when the button appears --------------------------------------------------


def test_a_connected_guest_cannot_be_taken_out() -> None:
    game = _game("Anna", "Ben")
    _start(game)
    _round(game)
    assert game.take_out_of_scoring("Anna") is False


def test_a_disconnected_guest_needs_a_missed_round_first() -> None:
    game = _game("Anna", "Ben")
    _start(game)
    _round(game)
    _gone(game, "Anna")
    assert game.take_out_of_scoring("Anna") is False
    assert serialize_scoring_status(game)["flags"] == {}

    assert game.start_next_question() is not None
    game._do_evaluate_round()

    assert serialize_scoring_status(game)["flags"]["Anna"]["can_take_out"] is True
    assert game.take_out_of_scoring("Anna") is True


def test_an_answer_given_before_leaving_is_not_a_missed_round() -> None:
    game = _game("Anna", "Ben")
    _start(game)
    assert game.start_next_question() is not None
    anna = game.get_player("Anna")
    assert anna is not None
    anna.submitted = True
    anna.current_answer = 0
    _gone(game, "Anna")
    game._do_evaluate_round()
    assert anna.rounds_missed == 0


def test_the_host_is_not_this_features_business() -> None:
    game = _game("Host", "Ben")
    host = game.get_player("Host")
    assert host is not None
    host.is_admin = True
    _start(game)
    _gone(game, "Host")
    _round(game)
    assert host.rounds_missed == 1
    assert game.take_out_of_scoring("Host") is False
    assert "Host" not in serialize_scoring_status(game)["flags"]


def test_an_unknown_row_is_refused() -> None:
    game = _game("Anna")
    _start(game)
    assert game.take_out_of_scoring("Nobody") is False
    assert game.bring_back_to_scoring("Nobody") is False


# --- the room only sees it at the next normal reveal ---------------------------


def test_the_host_sees_it_at_once_and_the_room_at_the_next_reveal(
    anna_left: QuizifyGameState,
) -> None:
    game = anna_left
    assert game.take_out_of_scoring("Anna") is True

    status = serialize_scoring_status(game)
    assert status["out"] == [{"entrant_id": "Anna", "name": "Anna", "score": 1240}]
    assert "Anna" not in status["flags"]
    assert _ranked(game)[0] == "Anna", "the room saw a tap before the reveal"

    _round(game)

    assert "Anna" not in _ranked(game)
    board = serialize_leaderboard(game.get_standings())
    assert "Anna" not in [r["name"] for r in board]
    summary = game.get_round_summary()
    assert summary is not None
    assert "Anna" not in [p.name for p in summary.leaderboard]
    anna = game.get_player("Anna")
    assert anna is not None
    assert anna.score == 1240, "the points have to stay saved"


def test_bringing_back_before_the_reveal_leaves_no_trace(
    anna_left: QuizifyGameState,
) -> None:
    game = anna_left
    assert game.take_out_of_scoring("Anna") is True
    assert game.bring_back_to_scoring("Anna") is True
    _round(game)
    assert _ranked(game)[0] == "Anna"
    assert game.drain_scoring_notices() == []


def test_bringing_back_after_the_reveal_shows_the_host_first(
    anna_left: QuizifyGameState,
) -> None:
    game = anna_left
    game.take_out_of_scoring("Anna")
    _round(game)
    game.drain_scoring_notices()

    assert game.bring_back_to_scoring("Anna") is True

    status = serialize_scoring_status(game)
    assert status["out"] == []
    assert [r["name"] for r in status["back"]] == ["Anna"]
    assert status["back"][0]["rank"] == 1
    assert "Anna" not in _ranked(game), "the room must wait for the reveal"

    _round(game)
    assert _ranked(game)[0] == "Anna"
    assert serialize_scoring_status(game)["back"] == []


@pytest.mark.parametrize(
    "phase",
    [GamePhase.HOT_SEAT_AUCTION, GamePhase.HOT_SEAT, GamePhase.LIGHTNING],
)
def test_a_running_special_round_is_left_alone(
    anna_left: QuizifyGameState, phase: GamePhase
) -> None:
    game = anna_left
    game.phase = phase
    assert game.take_out_of_scoring("Anna") is True
    assert "Anna" in _ranked(game)
    game.phase = GamePhase.ANSWER_REVEAL
    assert "Anna" in _ranked(game)


def test_the_next_lightning_round_has_no_row_for_her(
    anna_left: QuizifyGameState,
) -> None:
    game = anna_left
    game.take_out_of_scoring("Anna")
    _round(game)

    assert game.start_lightning_round(auto=True) is True
    lightning = game.lightning
    assert lightning is not None
    assert "Anna" not in lightning.scores
    assert {"Ben", "Clara"} <= set(lightning.scores)


# --- a return ----------------------------------------------------------------


def test_a_returning_guest_is_back_in_with_their_points(
    anna_left: QuizifyGameState,
) -> None:
    game = anna_left
    game.take_out_of_scoring("Anna")
    _round(game)
    game.drain_scoring_notices()
    anna = game.get_player("Anna")
    assert anna is not None

    game.bind_player_connection(anna, "conn-new")
    anna.connected = True

    assert anna.rounds_missed == 0
    assert serialize_scoring_status(game)["out"] == []

    _round(game)
    assert _ranked(game)[0] == "Anna"
    assert anna.score == 1240
    assert game.drain_scoring_notices() == [{"kind": "back", "name": "Anna"}]


def test_a_flapping_phone_needs_another_tap(anna_left: QuizifyGameState) -> None:
    """A phone in the driveway fetches her back, as the design accepts."""
    game = anna_left
    game.take_out_of_scoring("Anna")
    anna = game.get_player("Anna")
    assert anna is not None
    game.bind_player_connection(anna, "conn-new")
    anna.connected = True
    anna.connected = False
    _round(game)
    assert "Anna" in _ranked(game)


@pytest.mark.asyncio
async def test_the_phone_is_told_only_when_she_was_out(
    anna_left: QuizifyGameState, tmp_path: Path
) -> None:
    game = anna_left
    handler = QuizifyWebSocketHandler(
        runtime=_FakeRuntime(tmp_path), game_state_provider=lambda: game
    )
    handler._conn = ConnectionManager(_FakeRuntime(tmp_path), lambda: game)
    handler._conn.broadcast = AsyncMock()

    first = _Socket()
    token = handler._conn.create_session_token("Clara")
    await handler._handle_reconnect(first, {"session_token": token}, game)
    assert first.of_type("reconnected")[0]["welcome_back_score"] is None

    game.take_out_of_scoring("Anna")
    second = _Socket()
    token = handler._conn.create_session_token("Anna")
    await handler._handle_reconnect(second, {"session_token": token}, game)
    assert second.of_type("reconnected")[0]["welcome_back_score"] == 1240
    assert serialize_scoring_status(game)["out"] == []
    handler._conn.cancel_pending_removal("Anna")
    if handler._roster_flush_task is not None:
        handler._roster_flush_task.cancel()


# --- the television -----------------------------------------------------------


def test_the_banner_only_names_somebody_the_board_was_showing() -> None:
    game = _game("A", "B", "C", "D", "E", "F", "Gone")
    _start(game)
    _gone(game, "Gone")
    _round(game)
    _scores(game, A=70, B=60, C=50, D=40, E=30, F=20, Gone=10)
    assert game.take_out_of_scoring("Gone") is True
    _round(game)
    assert "Gone" not in _ranked(game)
    assert game.drain_scoring_notices() == []


def test_the_banner_names_a_guest_in_the_top_five_once(
    anna_left: QuizifyGameState,
) -> None:
    game = anna_left
    game.take_out_of_scoring("Anna")
    _round(game)
    assert game.drain_scoring_notices() == [{"kind": "left", "name": "Anna"}]
    assert game.drain_scoring_notices() == []


def test_top_five_means_the_board_before_this_rounds_points() -> None:
    """The room was reading the old board when the reveal landed."""
    game = _game("A", "B", "C", "D", "E", "Gone")
    _start(game)
    _gone(game, "Gone")
    _round(game)
    _scores(game, A=50, B=40, C=30, D=20, E=5, Gone=10)
    assert game.take_out_of_scoring("Gone") is True

    assert game.start_next_question() is not None
    e = game.get_player("E")
    assert e is not None
    e.round_score = 100
    e.score += 100
    game._apply_out_of_scoring(announce=True)

    assert game.drain_scoring_notices() == [{"kind": "left", "name": "Gone"}]


@pytest.mark.asyncio
async def test_status_reaches_the_host_and_the_notice_only_the_television(
    anna_left: QuizifyGameState, tmp_path: Path
) -> None:
    game = anna_left
    handler = QuizifyWebSocketHandler(
        runtime=_FakeRuntime(tmp_path), game_state_provider=lambda: game
    )
    handler._conn = ConnectionManager(_FakeRuntime(tmp_path), lambda: game)
    handler._conn.broadcast = AsyncMock()
    tv, host_page, host_phone, guest = _Socket(), _Socket(), _Socket(), _Socket()
    handler._conn.add_connection(tv, False, True)
    handler._conn.add_connection(host_page, True, False)
    handler._conn.add_connection(guest, False, False)
    host = game.get_player("Ben")
    assert host is not None
    host.is_admin = True
    game.bind_player_connection(host, handler._conn.connection_id(host_phone))
    clara = game.get_player("Clara")
    assert clara is not None
    game.bind_player_connection(clara, handler._conn.connection_id(guest))

    await handler._handle_set_scoring(
        host_page, {"entrant_id": "Anna", "out": True}, game
    )

    for sock in (host_page, host_phone):
        assert sock.of_type("scoring_status")[-1]["out"][0]["name"] == "Anna"
    assert tv.frames == []
    assert guest.frames == []

    _round(game)
    await handler._dispatch_round_evaluated()

    assert [(f["kind"], f["name"]) for f in tv.of_type("scoring_notice")] == [
        ("left", "Anna")
    ]
    for sock in (host_page, host_phone, guest):
        assert sock.of_type("scoring_notice") == []
    assert tv.of_type("scoring_status") == []
    assert guest.of_type("scoring_status") == []


# --- the command ---------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_refused_tap_says_so_and_sends_the_current_status(
    tmp_path: Path,
) -> None:
    game = _game("Anna", "Ben")
    _start(game)
    handler = QuizifyWebSocketHandler(
        runtime=_FakeRuntime(tmp_path), game_state_provider=lambda: game
    )
    handler._conn = ConnectionManager(_FakeRuntime(tmp_path), lambda: game)
    host_page = _Socket()
    handler._conn.add_connection(host_page, True, False)

    await handler._handle_set_scoring(
        host_page, {"entrant_id": "Anna", "out": True}, game
    )

    errors = [f for f in host_page.frames if f.get("code") == ERR_INVALID_ACTION]
    assert errors, host_page.frames
    assert host_page.of_type("scoring_status")[-1]["out"] == []

    host_page.frames.clear()
    await handler._handle_set_scoring(host_page, {"entrant_id": "Anna"}, game)
    assert [f for f in host_page.frames if f.get("code") == ERR_INVALID_ACTION]


@pytest.mark.asyncio
async def test_kick_stays_lobby_only(
    anna_left: QuizifyGameState, tmp_path: Path
) -> None:
    game = anna_left
    handler = QuizifyWebSocketHandler(
        runtime=_FakeRuntime(tmp_path), game_state_provider=lambda: game
    )
    handler._conn = MagicMock()
    handler._conn.send_error = AsyncMock()
    await handler._handle_kick_player(MagicMock(), {"player_name": "Anna"}, game)
    handler._conn.send_error.assert_awaited_once()
    assert game.get_player("Anna") is not None


def test_nothing_changes_scoring_in_the_lobby_or_after_the_podium(
    anna_left: QuizifyGameState,
) -> None:
    game = anna_left
    game.take_out_of_scoring("Anna")
    game.end_game()
    assert game.bring_back_to_scoring("Anna") is False
    assert game.take_out_of_scoring("Ben") is False
    assert serialize_scoring_status(game)["out"] == []
    game.reset_to_lobby()
    assert game.phase == GamePhase.LOBBY
    assert game.take_out_of_scoring("Anna") is False


# --- the last reveal and everything after it ------------------------------------


def _last_reveal(game: QuizifyGameState) -> None:
    game.round = game.total_rounds - 1
    _round(game)
    assert game.round >= game.total_rounds


def test_the_last_reveal_warns_without_blocking(anna_left: QuizifyGameState) -> None:
    game = anna_left
    _last_reveal(game)
    _scores(game, Anna=1240, Ben=1180, Clara=1050)
    flag = serialize_scoring_status(game)["flags"]["Anna"]
    assert flag["on_podium"] is True
    assert flag["can_take_out"] is True

    podium = {p["name"] for p in game.end_game()["podium"]}
    assert "Anna" in podium, "a warning is not a block"


def test_no_warning_before_the_last_reveal(anna_left: QuizifyGameState) -> None:
    assert serialize_scoring_status(anna_left)["flags"]["Anna"]["on_podium"] is False


def test_no_warning_for_somebody_below_the_podium() -> None:
    game = _game("A", "B", "C", "Gone")
    _start(game)
    _gone(game, "Gone")
    _last_reveal(game)
    _scores(game, A=30, B=20, C=10, Gone=1)
    assert serialize_scoring_status(game)["flags"]["Gone"]["on_podium"] is False


def _awards_room() -> QuizifyGameState:
    """Anna would win the streak award, on a board big enough to give awards."""
    game = _game("Anna", "Ben", "Clara", "Deniz")
    _start(game)
    _gone(game, "Anna")
    for _ in range(3):
        _round(game)
    _scores(game, Anna=1240, Ben=1180, Clara=1050, Deniz=900)
    anna = game.get_player("Anna")
    assert anna is not None
    anna.max_streak = 99
    return game


def test_a_tap_on_the_last_reveal_reaches_podium_and_awards() -> None:
    game = _awards_room()
    assert game.take_out_of_scoring("Anna") is True

    finale = game.end_game()

    assert [p["name"] for p in finale["podium"]] == ["Ben", "Clara", "Deniz"]
    assert "Anna" not in [p.name for p in finale["leaderboard"]]
    awards = game.get_finale_superlatives() or []
    assert all(a.winner != "Anna" for a in awards), "she still won an award"
    assert game.leader.name == "Ben"  # type: ignore[union-attr]
    assert game.get_player("Anna").score == 1240  # type: ignore[union-attr]


def test_the_same_room_gives_her_the_award_when_she_stays_in() -> None:
    """Keeps the assertion above honest: this room can hand her an award."""
    game = _awards_room()
    game.end_game()
    awards = game.get_finale_superlatives() or []
    assert any(a.winner == "Anna" for a in awards)


@pytest.mark.asyncio
async def test_lasting_stats_record_the_podium_not_the_absent_guest(
    anna_left: QuizifyGameState,
) -> None:
    """Conservative choice: the game goes on record without her, win included."""
    game = anna_left
    recorded: list[dict[str, Any]] = []

    class _Stats:
        async def record_game(self, **kwargs: Any) -> None:
            recorded.append(kwargs)

    game.set_stats_services(_Stats(), None)  # type: ignore[arg-type]
    game.take_out_of_scoring("Anna")
    game.end_game()

    await asyncio.sleep(0)
    await asyncio.sleep(0)

    assert recorded, "nothing was recorded, so this proves nothing"
    assert set(recorded[0]["players"]) == {"Ben", "Clara"}
    assert set(recorded[0]["player_details"]) == {"Ben", "Clara"}


def test_a_new_game_starts_with_nobody_out(anna_left: QuizifyGameState) -> None:
    game = anna_left
    game.take_out_of_scoring("Anna")
    _round(game)
    game.end_game()
    game.reset_to_lobby()
    game.get_player("Anna").connected = True  # type: ignore[union-attr]
    _start(game)
    assert "Anna" in _ranked(game)
    assert game.get_player("Anna").rounds_missed == 0  # type: ignore[union-attr]


# --- the late joiner's seed ----------------------------------------------------


def test_a_late_joiner_is_not_seeded_from_somebody_out_of_the_scoring(
    anna_left: QuizifyGameState,
) -> None:
    game = anna_left
    for name in ("Anna", "Ben", "Clara"):
        game.get_player(name).rounds_played = 1  # type: ignore[union-attr]
    _scores(game, Anna=1240, Ben=800, Clara=600)
    game.take_out_of_scoring("Anna")

    assert game.add_player("Late", "conn-late") == (True, None)

    assert game.get_player("Late").score == 700  # type: ignore[union-attr]


# --- team mode -----------------------------------------------------------------


@pytest.fixture
def teams() -> tuple[QuizifyGameState, str, str]:
    game = _game("Anna", "Ben", "Clara", "Deniz")
    sofa = game.create_team("Sofa", "Anna")
    kitchen = game.create_team("Kitchen", "Clara")
    assert sofa is not None
    assert kitchen is not None
    game.join_team(sofa["team_id"], "Ben")
    game.join_team(kitchen["team_id"], "Deniz")
    _start(game)
    return game, sofa["team_id"], kitchen["team_id"]


def test_a_team_goes_out_only_when_nobody_is_left(
    teams: tuple[QuizifyGameState, str, str],
) -> None:
    game, sofa, _ = teams
    _gone(game, "Anna")
    _round(game)

    flag = serialize_scoring_status(game)["flags"][sofa]
    assert flag["can_take_out"] is False
    assert (flag["absent"], flag["present"], flag["total"]) == (["Anna"], 1, 2)
    assert game.take_out_of_scoring(sofa) is False

    _gone(game, "Ben")
    assert game.take_out_of_scoring(sofa) is False, "Ben has not missed a round yet"
    _round(game)

    flag = serialize_scoring_status(game)["flags"][sofa]
    assert flag["can_take_out"] is True
    assert flag["present"] == 0
    assert game.take_out_of_scoring(sofa) is True


def test_members_stay_on_the_team_and_a_member_fetches_it_back(
    teams: tuple[QuizifyGameState, str, str],
) -> None:
    game, sofa, kitchen = teams
    _gone(game, "Anna")
    _gone(game, "Ben")
    _round(game)
    team = game.team_registry.get(sofa)
    assert team is not None
    team.score = 500
    assert game.take_out_of_scoring(sofa) is True
    _round(game)

    assert [p.team_id for p in game.get_standings()] == [kitchen]
    assert team.members == ["Anna", "Ben"], "#936: the members stay"
    assert team.score == 500

    assert game.start_lightning_round(auto=True) is True
    lightning = game.lightning
    assert lightning is not None
    assert set(lightning.scores) == {kitchen}, "her team's members entered alone"
    game._lightning_controller.reset()
    game.phase = GamePhase.ANSWER_REVEAL

    ben = game.get_player("Ben")
    assert ben is not None
    assert game.is_out_of_scoring("Ben") is True
    game.bind_player_connection(ben, "conn-new")
    ben.connected = True
    _round(game)

    assert {p.team_id for p in game.get_standings()} == {sofa, kitchen}


def test_a_team_late_joiner_seed_ignores_the_teams_members(
    teams: tuple[QuizifyGameState, str, str],
) -> None:
    game, sofa, _ = teams
    _gone(game, "Anna")
    _gone(game, "Ben")
    _round(game)
    for name, score in (("Anna", 900), ("Ben", 900), ("Clara", 100), ("Deniz", 100)):
        player = game.get_player(name)
        assert player is not None
        player.score = score
        player.rounds_played = 1
    assert game.take_out_of_scoring(sofa) is True
    assert game.add_player("Late", "conn-late") == (True, None)
    assert game.get_player("Late").score == 100  # type: ignore[union-attr]


# --- what the clients ship -----------------------------------------------------


@pytest.mark.parametrize("lang", ["de", "en", "es"])
def test_every_new_string_exists_in_every_language(lang: str) -> None:
    strings = json.loads((WWW / "i18n" / f"{lang}.json").read_text("utf-8"))
    for key in (
        "scoringOut",
        "scoringBack",
        "scoringSection",
        "scoringMissedOne",
        "scoringMissedMany",
        "scoringAllGone",
        "scoringSomeGone",
        "scoringPodium",
    ):
        assert strings["admin"][key]
    assert "{name}" in strings["dashboard"]["scoringLeft"]
    assert "{name}" in strings["dashboard"]["scoringBack"]
    assert "{score}" in strings["game"]["welcomeBackScore"]


def test_the_german_wording_is_the_designs() -> None:
    strings = json.loads((WWW / "i18n" / "de.json").read_text("utf-8"))
    assert strings["admin"]["scoringOut"] == "Aus der Wertung"
    assert strings["admin"]["scoringBack"] == "Zurückholen"
    assert strings["admin"]["scoringSection"] == "Nicht in der Wertung"
    assert strings["admin"]["scoringPodium"] == "Steht sonst auf dem Podium"
    assert strings["dashboard"]["scoringLeft"] == "{name} spielt nicht mehr mit"
    assert strings["dashboard"]["scoringBack"] == "{name} ist wieder dabei"


def test_the_host_cards_kick_button_is_lobby_only() -> None:
    card = (WWW / "cards" / "quizify-host-card.js").read_text("utf-8")
    assert "s && s.phase !== 'LOBBY'" in card
    game_roster = card.split("_gameRosterHtml(players) {", 1)[1]
    game_roster = game_roster.split("// One place", 1)[0]
    assert "data-kick" not in game_roster
    assert "data-scoring-out" in game_roster
    assert "data-scoring-back" in game_roster


def test_the_television_has_no_grey_rows() -> None:
    dashboard = (WWW / "js" / "dashboard.js").read_text("utf-8")
    assert "case 'scoring_notice':" in dashboard
    assert "scoring_status" not in dashboard
    assert "scoringHostRows" not in dashboard
